from decimal import Decimal

import pytest
from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.execution import Job
from src.hybrid.live import FalFluxProvider, RunPodSeedanceProvider, _url


def job(tmp_path):
    image = tmp_path / "image.png"
    Image.new("RGB", (1280, 720), "green").save(image)
    asset = FrozenAsset.approve(image, "reviewer", "LIVE")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "reviewer", "LIVE"), "reviewer", "LIVE")
    return Job(
        "scene", "first", "LIVE", "seedance-v1-5-pro-i2v",
        {"input": {"image": str(image), "prompt": "wind moves grass", "duration": 5,
                   "resolution": "720p", "aspect_ratio": "16:9", "camera_fixed": True,
                   "generate_audio": False}}, manifest, Decimal(".26")
    )


def test_fal_current_documented_cdn_host_is_allowed():
    assert _url(
        "https://v3b.fal.media/files/b/example/result.png",
        hosts=FalFluxProvider.allowed_result_hosts,
    ) == "https://v3b.fal.media/files/b/example/result.png"


def test_runpod_current_cloudfront_result_host_is_allowed():
    url = "https://d2h7xmz5gqybh9.cloudfront.net/output/example.mp4?signature=opaque"
    assert _url(url, hosts=RunPodSeedanceProvider.allowed_result_hosts) == url


class AsyncTransport:
    def __init__(self):
        self.posts = 0
        self.gets = 0

    def post(self, endpoint, payload):
        self.posts += 1
        assert endpoint == "seedance-v1-5-pro-i2v"
        return {"id": "async-id", "status": "IN_QUEUE"}

    def get(self, endpoint, provider_id):
        self.gets += 1
        assert (endpoint, provider_id) == ("seedance-v1-5-pro-i2v", "async-id")
        return {"id": "async-id", "status": "COMPLETED", "output": {"result": "https://video.runpod.ai/result.mp4", "cost": 0.26}}


@pytest.mark.asyncio
async def test_async_run_checkpoints_then_recovers_same_id_without_second_post(tmp_path):
    remote = tmp_path / "remote.mp4"
    remote.write_bytes(b"not a video")
    transport = AsyncTransport()
    provider = RunPodSeedanceProvider(
        tmp_path / "quarantine", transport=transport,
        image_stager=lambda _: "https://staging.example/image.png",
        downloader=lambda _, stream: stream.write(remote.read_bytes()),
    )
    checkpoints = []

    with pytest.raises(ValueError, match="video validation"):
        await provider.submit(job(tmp_path), "request-id", lambda **item: checkpoints.append(item))

    assert transport.posts == 1
    assert transport.gets == 1
    assert checkpoints[0] == {"provider_id": "async-id"}
    assert checkpoints[1]["partial"].replace("\\", "/").endswith("quarantine/request-id/result.mp4")


@pytest.mark.asyncio
async def test_runpod_rejects_unsafe_remote_identifier_before_checkpoint(tmp_path):
    class UnsafeTransport:
        def post(self, endpoint, payload):
            return {"id": "remote/../escape", "status": "IN_QUEUE"}

        def get(self, endpoint, provider_id):
            raise AssertionError("unsafe identifier must not be used for recovery")

    provider = RunPodSeedanceProvider(
        tmp_path / "quarantine", transport=UnsafeTransport(),
        image_stager=lambda _: "https://staging.example/image.png",
    )
    checkpoints = []

    with pytest.raises(ValueError, match="safe durable request ID"):
        await provider.submit(job(tmp_path), "request-id", lambda **item: checkpoints.append(item))

    assert checkpoints == []
