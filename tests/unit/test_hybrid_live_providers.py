import asyncio
import time
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.execution import Authorization, Executor, Job, Price
from src.hybrid.live import FalFluxProvider, RunPodSeedanceProvider
from src.hybrid.planner import Config


def live_manifest(tmp_path):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (32, 18), "green").save(reference)
    asset = FrozenAsset.approve(reference, "reviewer", "LIVE")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    return Manifest.freeze(
        (asset,), FrozenAsset.approve(sheet, "reviewer", "LIVE"), "reviewer", "LIVE"
    )


def image_job(tmp_path):
    return Job(
        "scene-1",
        "first",
        "LIVE",
        "fal-ai/flux-2/klein/9b/edit",
        {
            "prompt": "A calm landscape",
            "image_urls": [str(tmp_path / "reference.png")],
            "image_size": {"width": 64, "height": 36},
        },
        live_manifest(tmp_path),
        Decimal("0.04"),
    )


class FalClient:
    def __init__(self, result_url):
        self.result_url = result_url
        self.uploads = []
        self.submits = 0
        self.gets = 0

    def upload_file(self, path):
        self.uploads.append(Path(path))
        return "https://temporary.example/upload"

    def submit(self, application, arguments):
        self.submits += 1
        assert application == "fal-ai/flux-2/klein/9b/edit"
        assert arguments["image_urls"] == ["https://temporary.example/upload"]
        return type("Handle", (), {"request_id": "fal-remote-1"})()

    def get(self, request_id):
        self.gets += 1
        assert request_id == "fal-remote-1"
        return {"images": [{"url": self.result_url}]}


class Downloader:
    def __init__(self, source):
        self.source = Path(source)
        self.urls = []

    def __call__(self, url, destination):
        self.urls.append(url)
        destination.write(self.source.read_bytes())


@pytest.mark.asyncio
async def test_fal_stages_exact_references_checkpoints_and_quarantines(tmp_path):
    remote = tmp_path / "remote.png"
    Image.new("RGB", (64, 36), "orange").save(remote)
    client = FalClient("https://signed.example/result")
    download = Downloader(remote)
    provider = FalFluxProvider(tmp_path / "quarantine", client=client, downloader=download)
    checkpoints = []

    result = await provider.submit(
        image_job(tmp_path), "local-request", lambda **values: checkpoints.append(values)
    )

    assert client.submits == 1
    assert client.uploads == [tmp_path / "reference.png"]
    assert checkpoints[0] == {"provider_id": "fal-remote-1"}
    assert checkpoints[1]["partial"].replace("\\", "/").endswith("quarantine/local-request/result.png")
    assert result.path.parent == tmp_path / "quarantine" / "local-request"
    assert result.path.name == "result.png"
    assert result.actual_cost == Decimal("0.04")
    assert download.urls == ["https://signed.example/result"]


@pytest.mark.asyncio
async def test_fal_recovery_is_get_download_only(tmp_path):
    remote = tmp_path / "remote.png"
    Image.new("RGB", (64, 36), "orange").save(remote)
    client = FalClient("https://signed.example/result")
    download = Downloader(remote)
    provider = FalFluxProvider(tmp_path / "quarantine", client=client, downloader=download)

    result = await provider.recover(image_job(tmp_path), "local-request", "fal-remote-1", "", lambda **_: None)

    assert client.submits == 0
    assert client.uploads == []
    assert client.gets == 1
    assert result.path.is_file()


@pytest.mark.asyncio
async def test_fal_rejects_bad_remote_geometry_before_receipt(tmp_path):
    remote = tmp_path / "remote.png"
    Image.new("RGB", (63, 36), "orange").save(remote)
    provider = FalFluxProvider(
        tmp_path / "quarantine", client=FalClient("https://signed.example/result"), downloader=Downloader(remote)
    )

    with pytest.raises(ValueError, match="geometry"):
        await provider.submit(image_job(tmp_path), "local-request", lambda **_: None)


class RunPodTransport:
    def __init__(self, result_url):
        self.result_url = result_url
        self.posts = 0
        self.gets = 0

    def post(self, endpoint, payload):
        self.posts += 1
        assert endpoint == "seedance-endpoint"
        assert payload["input"]["image"] == "https://staging.example/reference.png"
        assert payload["input"]["duration"] == 5
        assert payload["input"]["resolution"] == "720p"
        assert payload["input"]["aspect_ratio"] == "16:9"
        assert payload["input"]["camera_fixed"] is True
        assert payload["input"]["generate_audio"] is False
        return {"id": "runpod-remote-1", "status": "IN_QUEUE"}

    def get(self, endpoint, remote_id):
        self.gets += 1
        assert (endpoint, remote_id) == ("seedance-endpoint", "runpod-remote-1")
        return {
            "status": "COMPLETED",
            "output": {"video_url": self.result_url, "cost": 0.20},
        }


@pytest.mark.asyncio
async def test_runpod_checkpoints_and_recovery_never_posts(tmp_path):
    remote = tmp_path / "remote.mp4"
    remote.write_bytes(b"fake-video")
    transport = RunPodTransport("https://signed.example/video")
    provider = RunPodSeedanceProvider(
        tmp_path / "quarantine",
        transport=transport,
        image_stager=lambda _: "https://staging.example/reference.png",
        downloader=Downloader(remote),
    )
    request = replace(
        image_job(tmp_path),
        category="hero",
        endpoint="seedance-endpoint",
        payload={
            "input": {
                "image": str(tmp_path / "reference.png"),
                "prompt": "subtle movement",
                "duration": 5,
                "resolution": "720p",
                "aspect_ratio": "16:9",
                "camera_fixed": True,
                "generate_audio": False,
            }
        },
        cost=Decimal("0.20"),
    )
    checkpoints = []

    with pytest.raises(ValueError, match="video validation"):
        await provider.submit(request, "local-request", lambda **values: checkpoints.append(values))
    assert checkpoints[0] == {"provider_id": "runpod-remote-1"}
    assert checkpoints[1]["partial"].replace("\\", "/").endswith("quarantine/local-request/result.mp4")
    assert transport.posts == 1

    with pytest.raises(ValueError, match="video validation"):
        await provider.recover(request, "local-request", "runpod-remote-1", "", lambda **_: None)
    assert transport.posts == 1
    assert transport.gets == 2


@pytest.mark.asyncio
async def test_executor_recovers_fal_without_second_submission(tmp_path):
    remote = tmp_path / "remote.png"
    Image.new("RGB", (64, 36), "orange").save(remote)
    client = FalClient("https://signed.example/result")
    provider = FalFluxProvider(tmp_path / "quarantine", client=client, downloader=Downloader(remote))
    request = image_job(tmp_path)
    executor = Executor(tmp_path / "jobs.db", Config())
    price = Price(request.endpoint, request.request_id, request.cost, time.time() + 60, "test")
    authorization = Authorization(request.request_id, "test", time.time() + 60, request.cost)

    original = provider._resolve

    def crash_after_remote(*args):
        original(*args)
        raise TimeoutError("interrupted after remote completion")

    provider._resolve = crash_after_remote
    with pytest.raises(TimeoutError):
        await executor.run(request, provider, authorization, price)
    provider._resolve = original
    receipt = await executor.run(request, provider)

    assert receipt["status"] == "COMPLETE"
    assert client.submits == 1
    assert client.gets == 2
