"""Director does not convert final approval into publication."""

from __future__ import annotations

import json

import pytest

from src.agents.director import DirectorAgent
from src.approval.receipts import ApprovalReceipt, save_approval_receipt
from src.providers.base import PublishResult
from src.qa.final_render import FinalRenderQAResult
from src.state.machine import EpisodeState, EpisodeStateStore
from src.storage.episode_fs import EpisodeFS


@pytest.mark.asyncio
async def test_final_approval_waits_for_separate_publication_command(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    state = EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.WAITING_FINAL_APPROVAL)
    state.save(fs.paths.state_json)

    result = await director.continue_after_approval("EP8", "final")

    assert result["status"] == "awaiting_separate_publication_instruction"
    assert EpisodeStateStore.load(fs.paths.state_json).current_state is EpisodeState.WAITING_FINAL_APPROVAL


class Publisher:
    async def upload(self, video_path, metadata, thumbnail="", captions=""):
        return PublishResult(True, video_id="published-id", video_url="https://example.invalid/published-id")

    async def readback(self, video_id):
        return {"video_id": video_id, "channel": "EraUmaVezBibliaAnimada", "hd_processed": True}


@pytest.mark.asyncio
async def test_director_uses_explicit_publication_command_with_persisted_approvals(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.WAITING_FINAL_APPROVAL).save(fs.paths.state_json)
    video, thumbnail = fs.paths.final_video, fs.paths.thumbnails_dir / "approved.png"
    video.write_bytes(b"video")
    thumbnail.write_bytes(b"thumbnail")
    video_receipt_path = fs.paths.qa_dir / "approval-video.json"
    thumbnail_receipt_path = fs.paths.qa_dir / "approval-thumbnail.json"
    save_approval_receipt(video_receipt_path, ApprovalReceipt.approve("video", video, "human"))
    save_approval_receipt(thumbnail_receipt_path, ApprovalReceipt.approve("thumbnail", thumbnail, "human"))
    metadata = fs.paths.metadata_dir / "metadata.json"
    metadata.write_text(json.dumps({"title": "História fiel"}), encoding="utf-8")

    result = await director.publish_after_explicit_instruction(
        "EP8",
        command="PUBLICAR EP8",
        expected_command="PUBLICAR EP8",
        publisher=Publisher(),
        video_receipt_path=video_receipt_path,
        thumbnail_receipt_path=thumbnail_receipt_path,
        metadata_path=metadata,
    )

    assert result["video_id"] == "published-id"
    assert EpisodeStateStore.load(fs.paths.state_json).current_state is EpisodeState.PUBLISHED


@pytest.mark.asyncio
async def test_director_enters_thumbnail_approval_only_after_independent_final_render_qa(tmp_path, monkeypatch):
    class PassingQA:
        def review(self, video_path, render_receipt):
            return FinalRenderQAResult(True, (), {"video_codec": "h264", "audio_codec": "aac"})

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.FINAL_QA).save(fs.paths.state_json)
    (fs.paths.qa_dir / "production_evidence_qa.json").write_text(
        json.dumps({"approved": True, "findings": [], "report": {}}), encoding="utf-8"
    )
    video = fs.paths.final_video
    video.write_bytes(b"final")

    report = await director.record_final_render_qa(
        "EP8", video_path=video, render_receipt={"hold_seconds": 4}, checker=PassingQA()
    )

    assert report["approved"] is True
    assert (fs.paths.qa_dir / "final_render_qa.json").is_file()
    assert EpisodeStateStore.load(fs.paths.state_json).current_state is EpisodeState.WAITING_THUMBNAIL_APPROVAL


@pytest.mark.asyncio
async def test_final_render_qa_requires_persisted_production_evidence(tmp_path, monkeypatch):
    class PassingQA:
        def review(self, video_path, render_receipt):
            return FinalRenderQAResult(True, (), {})

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.FINAL_QA).save(fs.paths.state_json)
    fs.paths.final_video.write_bytes(b"final")

    with pytest.raises(ValueError, match="production evidence QA"):
        await director.record_final_render_qa(
            "EP8", video_path=fs.paths.final_video, render_receipt={"hold_seconds": 4}, checker=PassingQA()
        )


def test_director_records_independent_production_evidence_qa(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.FINAL_QA).save(fs.paths.state_json)
    (fs.paths.script_dir / "script.json").write_text(
        json.dumps({"narration_segments": [{"id": "S001"}]}), encoding="utf-8"
    )
    (fs.paths.compiled_dir / "manifest.json").write_text(
        json.dumps({"assets": [{"sha256": "a" * 64}]}), encoding="utf-8"
    )
    fs.paths.captions_vtt.write_text("WEBVTT\n\n", encoding="utf-8")
    (fs.paths.metadata_dir / "metadata.json").write_text(
        json.dumps({"references": [{"book": "Gênesis"}]}), encoding="utf-8"
    )

    report = director.record_production_evidence_qa("EP8", published_script_hashes=set())

    assert report["approved"] is True
    assert (fs.paths.qa_dir / "production_evidence_qa.json").is_file()


@pytest.mark.asyncio
async def test_director_delivers_thumbnail_and_video_with_separate_receipts(tmp_path, monkeypatch):
    class Messenger:
        async def send_photo(self, chat_id, photo_path, caption=""):
            return 101

        async def send_video(self, chat_id, video_path, caption=""):
            return 202

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(
        episode_id="EP8", current_state=EpisodeState.WAITING_THUMBNAIL_APPROVAL
    ).save(fs.paths.state_json)
    fs.paths.final_video.write_bytes(b"video")
    thumbnail = fs.paths.thumbnails_dir / "thumbnail.png"
    thumbnail.write_bytes(b"thumbnail")

    receipts = await director.deliver_for_approval("EP8", messenger=Messenger(), chat_id="test-chat", thumbnail_path=thumbnail)

    assert {receipt["artifact_kind"] for receipt in receipts} == {"thumbnail", "video"}
    assert (fs.paths.qa_dir / "delivery" / "thumbnail.json").is_file()
    assert (fs.paths.qa_dir / "delivery" / "video.json").is_file()


def test_director_records_only_exact_approval_of_delivered_media(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(
        episode_id="EP8", current_state=EpisodeState.WAITING_THUMBNAIL_APPROVAL
    ).save(fs.paths.state_json)
    thumbnail = fs.paths.thumbnails_dir / "approved.png"
    thumbnail.write_bytes(b"thumbnail")
    delivered = ApprovalReceipt.approve("thumbnail", thumbnail, "delivery")
    delivery_path = fs.paths.qa_dir / "delivery-thumbnail.json"
    delivery_path.write_text(
        json.dumps({"episode_id": "EP8", "artifact_kind": "thumbnail", "approval": delivered.__dict__, "message_id": 1}),
        encoding="utf-8",
    )

    receipt = director.confirm_delivered_artifact(
        "EP8", artifact_kind="thumbnail", command="APROVAR THUMBNAIL EP8",
        expected_command="APROVAR THUMBNAIL EP8", approver="human", artifact_path=thumbnail,
        delivery_receipt_path=delivery_path,
    )

    assert receipt.artifact_kind == "thumbnail"
    assert (fs.paths.qa_dir / "approval-thumbnail.json").is_file()
    assert EpisodeStateStore.load(fs.paths.state_json).current_state is EpisodeState.WAITING_VIDEO_APPROVAL


def test_director_rejects_thumbnail_approval_outside_its_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.WAITING_VIDEO_APPROVAL).save(fs.paths.state_json)
    thumbnail = fs.paths.thumbnails_dir / "approved.png"
    thumbnail.write_bytes(b"thumbnail")
    delivered = ApprovalReceipt.approve("thumbnail", thumbnail, "delivery")
    delivery_path = fs.paths.qa_dir / "delivery-thumbnail.json"
    delivery_path.write_text(
        json.dumps({"episode_id": "EP8", "artifact_kind": "thumbnail", "approval": delivered.__dict__, "message_id": 1}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="WAITING_THUMBNAIL_APPROVAL"):
        director.confirm_delivered_artifact(
            "EP8", artifact_kind="thumbnail", command="APROVAR THUMBNAIL EP8",
            expected_command="APROVAR THUMBNAIL EP8", approver="human", artifact_path=thumbnail,
            delivery_receipt_path=delivery_path,
        )

    assert not (fs.paths.qa_dir / "approval-thumbnail.json").exists()
