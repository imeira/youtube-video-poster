"""TDD coverage for explicit TEST publication packages."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.execution import Executor, Job, ProviderResult
from src.hybrid.planner import Config


def _policy():
    return {
        "schema": 1,
        "version": "test-v1",
        "mode": "TEST",
        "episode_cap_usd": "1.00",
        "month_cap_usd": "3.00",
        "experiment_cap_usd": "0.20",
        "allowed_providers": ["test-image"],
        "publication": {
            "enabled": True,
            "channel": "test-channel",
            "visibility": "private",
        },
    }


def _runner(tmp_path):
    from src.hybrid.autonomous import AutonomousPolicy, AutonomousTwoGateRunner
    from src.hybrid.standing_budget import StandingBudgetLedger

    policy = AutonomousPolicy.from_dict(_policy())
    ledger = StandingBudgetLedger(
        tmp_path / "budget.sqlite3",
        policy_sha256=policy.digest,
        episode_limit_usd=policy.episode_cap_usd,
        month_limit_usd=policy.month_cap_usd,
        experiment_limit_usd=policy.experiment_cap_usd,
        mode="TEST",
    )
    runner = AutonomousTwoGateRunner(
        tmp_path / "runner", "TEST-episode-1", policy, Executor(tmp_path / "receipts.sqlite3", Config(limit=Decimal("1.00"))), ledger
    )
    reference = tmp_path / "reference.png"
    from PIL import Image

    Image.new("RGB", (16, 16), "green").save(reference)
    frozen = FrozenAsset.approve(reference, "test", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((frozen,), sheet)
    manifest = Manifest.freeze((frozen,), FrozenAsset.approve(sheet, "test", "TEST"), "test", "TEST")
    runner.start({"packet": "test"}, [Job("S001", "first", "TEST", "test-image", {"prompt": "test"}, manifest, Decimal("0.10"))])
    return runner


class _Provider:
    mode = "TEST"

    def __init__(self, root):
        self.root = root

    async def submit(self, job, request_id, checkpoint):
        checkpoint(provider_id="TEST-" + request_id)
        artifact = self.root / (request_id + ".bin")
        artifact.write_bytes(b"TEST artifact")
        return ProviderResult(artifact, job.cost)

    async def recover(self, *args):
        raise AssertionError("fixture resumes only complete receipts")


async def _complete_source_qa(runner, root):
    def independent_qa(receipt):
        return {
            "approved": True,
            "reviewer": "independent-test-qa",
            "reviewer_identity": {"subject": "qa-test", "issuer": "TEST"},
            "request_id": receipt["request_id"],
            "result_sha256": receipt["result_sha256"],
            "codes": [],
        }

    await runner.resume(_Provider(root), independent_qa)


def _media(tmp_path):
    import subprocess

    from PIL import Image

    thumbnail = tmp_path / "thumbnail.png"
    metadata = tmp_path / "metadata.json"
    captions = tmp_path / "captions.vtt"
    video = tmp_path / "video.mp4"
    Image.new("RGB", (64, 64), "blue").save(thumbnail)
    metadata.write_text('{"title":"TEST only"}', encoding="utf-8")
    captions.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nTEST only\n", encoding="utf-8")
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    return thumbnail, video, metadata, captions


def _plan():
    return {
        "schema": "two-gate-publication/v2",
        "episode_id": "TEST-episode-1",
        "channel": "test-channel",
        "playlist_id": "TEST-playlist",
        "visibility": "scheduled",
        "publish_at": "2030-01-02T03:04:05+00:00",
        "timezone": "UTC",
    }


@pytest.mark.asyncio
async def test_stage_publication_package_binds_exact_plan_and_artifacts(tmp_path):
    runner = _runner(tmp_path)
    await _complete_source_qa(runner, tmp_path)
    thumbnail, video, metadata, captions = _media(tmp_path)
    runner.stage_delivery(thumbnail, video, qa={"approved": True, "mode": "TEST", "subject": "technical", "issuer": "TEST"})

    document = runner.stage_publication_package(_plan(), metadata=metadata, captions=captions)

    package = document["publication_package"]
    assert document["state"] == "WAITING_THUMBNAIL_APPROVAL"
    assert package["plan"] == _plan()
    assert package["artifacts"]["video"]["path"] == str(video.resolve())
    assert package["artifacts"]["metadata"]["path"] == str(metadata.resolve())
    assert package["artifacts"]["captions"]["path"] == str(captions.resolve())
    assert package["policy_sha256"] == runner.policy.digest


class _RecoveredUploadPublisher:
    mode = "TEST"

    def __init__(self):
        self.calls = []
        self.package = None
        self.released = False

    def upload_private(self, package):
        self.calls.append("upload_private")
        return {"http_status": 400}

    def recover_upload(self, package):
        self.calls.append("recover_upload")
        return {"video_id": "TEST-existing"}

    def configure(self, video_id, package):
        self.calls.append("configure")
        self.package = package
        return {"video_id": video_id, "configured": True}

    def readback(self, video_id):
        self.calls.append("readback")
        plan = self.package["plan"]
        return {
            "video_id": video_id, "mode": "TEST", "channel": plan["channel"],
            "playlist_id": plan["playlist_id"], "final_visibility": plan["visibility"],
            "publish_at": plan["publish_at"], "timezone": plan["timezone"],
            "artifacts": {key: value["sha256"] for key, value in self.package["artifacts"].items()},
            "visibility": plan["visibility"] if self.released else "private", "released": self.released,
        }

    def release(self, video_id, package):
        self.calls.append("release")
        self.released = True
        return {"video_id": video_id, "released": True}


@pytest.mark.asyncio
async def test_final_approval_recovers_http400_without_duplicate_upload(tmp_path):
    runner = _runner(tmp_path)
    await _complete_source_qa(runner, tmp_path)
    thumbnail, video, metadata, captions = _media(tmp_path)
    runner.stage_delivery(thumbnail, video, qa={"approved": True, "mode": "TEST", "subject": "technical", "issuer": "TEST"})
    runner.stage_publication_package(_plan(), metadata=metadata, captions=captions)
    runner.record_human_gate("thumbnail", thumbnail, approved=True, reviewer="thumbnail-reviewer")
    publisher = _RecoveredUploadPublisher()

    document = runner.approve_final_and_publish(video, reviewer="video-reviewer", publisher=publisher)

    assert document["state"] == "PUBLISHED"
    assert publisher.calls == ["upload_private", "recover_upload", "configure", "readback", "release", "readback"]
    assert document["gates"]["video"]["approved"] is True
    assert document["publication"]["video_id"] == "TEST-existing"
    assert set(document["publication"]) == {"video_id", "upload", "configure", "pre_readback", "release", "post_readback"}


class _NoTransportPublisher:
    """A forged runner document must fail before a publisher boundary."""

    mode = "TEST"

    def __getattr__(self, name):
        if name in {"upload_private", "configure", "readback", "release", "recover_upload"}:
            return lambda *_args, **_kwargs: pytest.fail(f"transport called: {name}")
        raise AttributeError(name)


@pytest.mark.asyncio
async def test_final_approval_rejects_redigested_runner_package_without_independent_receipt(tmp_path):
    """runner.json is mutable operational state, never publication authority."""
    from src.hybrid.autonomous import _digest_json

    runner = _runner(tmp_path)
    await _complete_source_qa(runner, tmp_path)
    thumbnail, video, metadata, captions = _media(tmp_path)
    runner.stage_delivery(
        thumbnail,
        video,
        qa={"approved": True, "mode": "TEST", "subject": "technical", "issuer": "TEST"},
    )
    runner.stage_publication_package(_plan(), metadata=metadata, captions=captions)
    runner.record_human_gate("thumbnail", thumbnail, approved=True, reviewer="thumbnail-reviewer")

    forged = json.loads(runner.path.read_text(encoding="utf-8"))
    forged_package = forged["publication_package"]
    forged_package["plan"]["playlist_id"] = "TEST-attacker-playlist"
    forged_package["receipt_sha256"] = _digest_json(
        {key: value for key, value in forged_package.items() if key != "receipt_sha256"}
    )
    runner.path.write_text(json.dumps(forged), encoding="utf-8")

    with pytest.raises(Exception, match="independent|receipt|package"):
        runner.approve_final_and_publish(
            video, reviewer="video-reviewer", publisher=_NoTransportPublisher()
        )


@pytest.mark.asyncio
async def test_published_reentry_revalidates_approved_video_bytes_before_returning(tmp_path):
    runner = _runner(tmp_path)
    await _complete_source_qa(runner, tmp_path)
    thumbnail, video, metadata, captions = _media(tmp_path)
    runner.stage_delivery(
        thumbnail, video,
        qa={"approved": True, "mode": "TEST", "subject": "technical", "issuer": "TEST"},
    )
    runner.stage_publication_package(_plan(), metadata=metadata, captions=captions)
    runner.record_human_gate("thumbnail", thumbnail, approved=True, reviewer="thumbnail-reviewer")
    publisher = _RecoveredUploadPublisher()
    runner.approve_final_and_publish(video, reviewer="video-reviewer", publisher=publisher)
    video.write_bytes(b"tampered after published")

    with pytest.raises(Exception, match="bytes|artifact|gate"):
        runner.approve_final_and_publish(
            video, reviewer="video-reviewer", publisher=_NoTransportPublisher()
        )


@pytest.mark.asyncio
async def test_published_reentry_rejects_runner_with_revoked_video_gate(tmp_path):
    runner = _runner(tmp_path)
    await _complete_source_qa(runner, tmp_path)
    thumbnail, video, metadata, captions = _media(tmp_path)
    runner.stage_delivery(thumbnail, video, qa={"approved": True, "mode": "TEST", "subject": "technical", "issuer": "TEST"})
    runner.stage_publication_package(_plan(), metadata=metadata, captions=captions)
    runner.record_human_gate("thumbnail", thumbnail, approved=True, reviewer="thumbnail-reviewer")
    runner.approve_final_and_publish(video, reviewer="video-reviewer", publisher=_RecoveredUploadPublisher())
    forged = json.loads(runner.path.read_text(encoding="utf-8"))
    forged["gates"]["video"]["approved"] = False
    runner.path.write_text(json.dumps(forged), encoding="utf-8")

    with pytest.raises(Exception, match="gate|approval|two"):
        runner.approve_final_and_publish(
            video, reviewer="video-reviewer", publisher=_NoTransportPublisher()
        )


class _BindingPublisher:
    """Real stateful TEST transport; readback originates from stored remote state."""

    mode = "TEST"

    def __init__(self, *, pre=None, post=None):
        self.calls = []
        self.records = {}
        self.pre = pre
        self.post = post

    def upload_private(self, package):
        self.calls.append("upload")
        video_id = "TEST-bound"
        self.records[video_id] = {"video_id": video_id, "mode": "TEST", "visibility": "private"}
        return dict(self.records[video_id])

    def recover_upload(self, package):
        self.calls.append("recover")

    def configure(self, video_id, package):
        self.calls.append("configure")
        self.records[video_id].update({
            "channel": package["plan"]["channel"], "playlist_id": package["plan"]["playlist_id"],
            "final_visibility": package["plan"]["visibility"], "publish_at": package["plan"]["publish_at"],
            "timezone": package["plan"]["timezone"],
            "artifacts": {key: value["sha256"] for key, value in package["artifacts"].items()},
        })
        return dict(self.records[video_id])

    def readback(self, video_id):
        self.calls.append("readback")
        value = dict(self.records[video_id])
        override = self.post if self.records[video_id].get("released") else self.pre
        if override:
            value.update(override)
        return value

    def release(self, video_id, package):
        self.calls.append("release")
        self.records[video_id].update({"released": True, "visibility": package["plan"]["visibility"]})
        return dict(self.records[video_id])


async def _ready_publication_runner(tmp_path):
    runner = _runner(tmp_path)
    await _complete_source_qa(runner, tmp_path)
    thumbnail, video, metadata, captions = _media(tmp_path)
    runner.stage_delivery(thumbnail, video, qa={"approved": True, "mode": "TEST", "subject": "technical", "issuer": "TEST"})
    runner.stage_publication_package(_plan(), metadata=metadata, captions=captions)
    runner.record_human_gate("thumbnail", thumbnail, approved=True, reviewer="thumbnail-reviewer")
    return runner, video


@pytest.mark.asyncio
async def test_pre_release_readback_missing_destination_never_releases(tmp_path):
    runner, video = await _ready_publication_runner(tmp_path)
    publisher = _BindingPublisher(pre={"channel": "wrong-channel"})

    with pytest.raises(RuntimeError, match="readback"):
        runner.approve_final_and_publish(video, reviewer="video-reviewer", publisher=publisher)

    assert publisher.calls == ["upload", "configure", "readback"]


@pytest.mark.asyncio
async def test_post_release_readback_mismatch_never_publishes(tmp_path):
    runner, video = await _ready_publication_runner(tmp_path)
    publisher = _BindingPublisher(post={"visibility": "private"})

    with pytest.raises(RuntimeError, match="readback"):
        runner.approve_final_and_publish(video, reviewer="video-reviewer", publisher=publisher)

    assert publisher.calls == ["upload", "configure", "readback", "release", "readback"]
    assert runner._load()["state"] != "PUBLISHED"
