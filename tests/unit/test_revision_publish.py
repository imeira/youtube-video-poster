"""The public revision publish command is a separate, recoverable operation."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from src.approval.receipts import ApprovalReceipt, save_approval_receipt
from src.hybrid.artifacts import atomic_json, digest, sha256
from src.hybrid.revision import RevisionHarness
import src.hybrid.revision as revision_module
from src.providers.base import PublishResult


class FakePublisher:
    def __init__(self, *, ambiguous=False, fail=False):
        self.uploads = 0
        self.ambiguous = ambiguous
        self.fail = fail
        self.remote = {}

    async def upload(self, video_path, metadata, thumbnail="", captions=""):
        self.uploads += 1
        if self.ambiguous:
            raise TimeoutError("provider response ambiguous")
        if self.fail:
            return PublishResult(False, error="provider rejected")
        self.remote["remote-1"] = {"video_id": "remote-1", "title": metadata["title"]}
        return PublishResult(True, video_id="remote-1", video_url="https://example.invalid/remote-1")

    async def readback(self, video_id):
        return self.remote.get(video_id)


def prepared_harness(tmp_path: Path, monkeypatch, *, publisher: FakePublisher, mode="TEST"):
    root = tmp_path / "workspace"
    revision = root / "r001" / "EP8"
    approval_dir = revision / "approval"
    metadata_dir = revision / "metadata"
    subtitle_dir = revision / "subtitles"
    approval_dir.mkdir(parents=True)
    metadata_dir.mkdir()
    subtitle_dir.mkdir()
    video, thumbnail = revision / "video.mp4", revision / "thumbnail.png"
    video.write_bytes(b"video")
    thumbnail.write_bytes(b"thumbnail")
    metadata = metadata_dir / "youtube.json"
    metadata.write_text(json.dumps({"title": "EP8"}), encoding="utf-8")
    captions = subtitle_dir / "captions.srt"
    captions.write_text("1\n00:00:00,000 --> 00:00:01,000\nOi\n", encoding="utf-8")
    captions_vtt = subtitle_dir / "captions.vtt"
    captions_vtt.write_text("WEBVTT\n\n00:00.000 --> 00:01.000\nOi\n", encoding="utf-8")
    transcript = subtitle_dir / "transcript.txt"
    transcript.write_text("Oi", encoding="utf-8")
    visual = approval_dir / "approval-visual-freeze.json"
    atomic_json(visual, {"artifact_kind": "visual-freeze", "artifact_path": "frozen", "artifact_sha256": "0" * 64, "approver": "human", "plan_hash": "plan"})
    video_receipt = ApprovalReceipt.approve("video", video, "human")
    thumb_receipt = ApprovalReceipt.approve("thumbnail", thumbnail, "human")
    save_approval_receipt(approval_dir / "approval-video.json", video_receipt)
    save_approval_receipt(approval_dir / "approval-thumbnail.json", thumb_receipt)
    artifacts = {name: {"path": str(path.resolve()), "sha256": sha256(path)} for name, path in {
        "video": video, "thumbnail": thumbnail, "metadata": metadata, "captions_srt": captions,
        "captions_vtt": captions_vtt, "transcript": transcript,
    }.items()}
    package = {"schema_version": 1, "plan_hash": "plan", "revision": 1,
               "plan": {"route": "ep8-new-revision-v1", "mode": mode, "episode_id": "EP8"},
               "artifacts": artifacts,
               "approval_receipts": [{"kind": kind, "path": str(path.resolve()), "sha256": sha256(path)} for kind, path in {
                   "visual-freeze": visual, "thumbnail": approval_dir / "approval-thumbnail.json", "video": approval_dir / "approval-video.json"}.items()],
               "publication_authorized": False, "upload_performed": False}
    package["package_hash"] = digest(package)
    atomic_json(revision / "publication-package.json", package)
    authorization = {"command": "AUTORIZAR PUBLICAÇÃO EP8", "video_sha256": video_receipt.artifact_sha256,
                     "thumbnail_sha256": thumb_receipt.artifact_sha256, "metadata_sha256": sha256(metadata),
                     "authorized_at": "2026-01-01T00:00:00+00:00", "reviewer": "human", "plan_hash": "plan", "upload_performed": False}
    atomic_json(approval_dir / "publication-authorization.json", authorization)
    harness = RevisionHarness(root)
    harness.control = {"status": "READY_FOR_PUBLICATION", "plan": {"revision": 1, "mode": mode}, "plan_hash": "plan", "stages": {}}
    monkeypatch.setattr(harness, "load", lambda: harness.control)
    monkeypatch.setattr(harness, "save", lambda: None)
    monkeypatch.setattr(harness, "event", lambda *args, **kwargs: None)
    deployment = tmp_path / "deployment.json"
    factory = "tests.fake_publish_provider:factory"
    import sys, types
    module = types.ModuleType("tests.fake_publish_provider")
    module.factory = lambda **kwargs: publisher
    monkeypatch.setitem(sys.modules, "tests.fake_publish_provider", module)
    provider_contract = {"adapter": factory, "mode": mode}
    if mode == "TEST":
        provider_contract.update(local_fake=True, capability="local-only-v1")
    deployment.write_text(json.dumps({"publication_provider": provider_contract}), encoding="utf-8")
    return harness, deployment, revision


def test_publish_success_updates_package_receipt_status_and_notifies(tmp_path, monkeypatch):
    publisher = FakePublisher()
    harness, deployment, revision = prepared_harness(tmp_path, monkeypatch, publisher=publisher)
    notified = []
    monkeypatch.setattr(harness, "notify_publication", lambda receipt: notified.append(receipt))

    result = asyncio.run(harness.publish(command="PUBLICAR EP8", deployment=deployment))

    assert result["status"] == "PUBLISHED"
    assert publisher.uploads == 1
    assert harness.control["status"] == "PUBLISHED"
    assert json.loads((revision / "publication-package.json").read_text())["upload_performed"] is True
    assert (revision / "approval" / "publication-receipt.json").is_file()
    assert notified and notified[0]["video_id"] == "remote-1"



def test_revision_publish_cli_uses_separate_action(tmp_path, monkeypatch, capsys):
    publisher = FakePublisher()
    harness, deployment, _revision = prepared_harness(tmp_path, monkeypatch, publisher=publisher)

    class Session:
        def __enter__(self):
            return harness
        def __exit__(self, *args):
            return False

    monkeypatch.setattr(revision_module, "studio_factory", lambda workspace: Session())
    assert revision_module.main(["publish", "--workspace", str(harness.root), "--deployment", str(deployment),
                                 "--command", "PUBLICAR EP8"]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "PUBLISHED"
    assert publisher.uploads == 1


def test_publish_rejects_tampered_package_before_provider_io(tmp_path, monkeypatch):
    publisher = FakePublisher()
    harness, deployment, revision = prepared_harness(tmp_path, monkeypatch, publisher=publisher)
    (revision / "video.mp4").write_bytes(b"tampered")

    with pytest.raises(ValueError, match="package artifact hash mismatch"):
        asyncio.run(harness.publish(command="PUBLICAR EP8", deployment=deployment))

    assert publisher.uploads == 0


def test_publish_validates_tampered_package_before_importing_or_calling_provider_factory(tmp_path, monkeypatch):
    publisher = FakePublisher()
    harness, deployment, revision = prepared_harness(tmp_path, monkeypatch, publisher=publisher)
    factory_calls = []
    import sys
    sys.modules["tests.fake_publish_provider"].factory = lambda **kwargs: factory_calls.append(kwargs) or publisher
    (revision / "video.mp4").write_bytes(b"tampered")

    with pytest.raises(ValueError, match="package artifact hash mismatch"):
        asyncio.run(harness.publish(command="PUBLICAR EP8", deployment=deployment))

    assert factory_calls == []
    assert publisher.uploads == 0


def test_publish_blocks_missing_provider_before_io_for_test_and_live(tmp_path, monkeypatch):
    for mode in ("TEST", "LIVE"):
        publisher = FakePublisher()
        harness, deployment, _revision = prepared_harness(tmp_path / mode, monkeypatch, publisher=publisher, mode=mode)
        deployment.write_text("{}", encoding="utf-8")
        with pytest.raises(ValueError, match="explicit publication provider"):
            asyncio.run(harness.publish(command="PUBLICAR EP8", deployment=deployment))
        assert publisher.uploads == 0


def test_test_publish_rejects_unmarked_or_network_provider_before_factory_side_effect(tmp_path, monkeypatch):
    publisher = FakePublisher()
    harness, deployment, _revision = prepared_harness(tmp_path, monkeypatch, publisher=publisher)
    factory_calls = []
    import sys
    sys.modules["tests.fake_publish_provider"].factory = lambda **kwargs: factory_calls.append(kwargs) or publisher

    for provider in (
        {"adapter": "tests.fake_publish_provider:factory", "mode": "TEST"},
        {"adapter": "tests.fake_publish_provider:factory", "mode": "TEST",
         "local_fake": True, "capability": "local-only-v1",
         "config": {"endpoint": "https://remote.invalid/upload"}},
    ):
        deployment.write_text(json.dumps({"publication_provider": provider}), encoding="utf-8")
        with pytest.raises(ValueError, match="local fake"):
            asyncio.run(harness.publish(command="PUBLICAR EP8", deployment=deployment))

    assert factory_calls == []
    assert publisher.uploads == 0


def test_ambiguous_upload_requires_readback_and_never_reposts(tmp_path, monkeypatch):
    publisher = FakePublisher(ambiguous=True)
    harness, deployment, revision = prepared_harness(tmp_path, monkeypatch, publisher=publisher)

    with pytest.raises(ValueError, match="readback/recovery"):
        asyncio.run(harness.publish(command="PUBLICAR EP8", deployment=deployment))
    assert publisher.uploads == 1
    assert json.loads((revision / "approval" / "publication-intent.json").read_text())["status"] == "AMBIGUOUS"

    with pytest.raises(ValueError, match="readback/recovery"):
        asyncio.run(harness.publish(command="PUBLICAR EP8", deployment=deployment))
    assert publisher.uploads == 1


def test_failed_upload_records_intent_and_never_reposts(tmp_path, monkeypatch):
    publisher = FakePublisher(fail=True)
    harness, deployment, _revision = prepared_harness(tmp_path, monkeypatch, publisher=publisher)

    with pytest.raises(ValueError, match="PROVIDER_ERROR"):
        asyncio.run(harness.publish(command="PUBLICAR EP8", deployment=deployment))
    with pytest.raises(ValueError, match="readback/recovery"):
        asyncio.run(harness.publish(command="PUBLICAR EP8", deployment=deployment))
    assert publisher.uploads == 1


def test_publish_intent_replaces_provider_error_before_persistence(tmp_path, monkeypatch):
    publisher = FakePublisher(fail=True)
    harness, deployment, revision = prepared_harness(tmp_path, monkeypatch, publisher=publisher)
    publisher.upload = lambda *args, **kwargs: asyncio.sleep(0, result=PublishResult(
        False, error="https://upload.invalid/?token=SIGNED Authorization: Bearer SECRET"))

    with pytest.raises(ValueError):
        asyncio.run(harness.publish(command="PUBLICAR EP8", deployment=deployment))

    persisted = (revision / "approval" / "publication-intent.json").read_text(encoding="utf-8")
    assert json.loads(persisted)["error"] == "PROVIDER_ERROR"
    for forbidden in ("https://", "SIGNED", "SECRET", "Authorization"):
        assert forbidden not in persisted
