"""Director does not convert final approval into publication."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from src.agents.base import AgentResult
from src.agents.director import DirectorAgent
from src.approval.receipts import ApprovalReceipt, save_approval_receipt
from src.pipeline.revision import RevisionRegistry
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
    (fs.paths.qa_dir / "post_production_narrative_qa.json").write_text(
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


@pytest.mark.asyncio
async def test_final_render_qa_requires_post_production_narrative_verdict(tmp_path, monkeypatch):
    class PassingQA:
        def review(self, video_path, render_receipt):
            return FinalRenderQAResult(True, (), {})

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.FINAL_QA).save(fs.paths.state_json)
    (fs.paths.qa_dir / "production_evidence_qa.json").write_text(json.dumps({"approved": True}), encoding="utf-8")
    fs.paths.final_video.write_bytes(b"final")

    with pytest.raises(ValueError, match="post-production narrative QA"):
        await director.record_final_render_qa("EP8", video_path=fs.paths.final_video, render_receipt={"hold_seconds": 4}, checker=PassingQA())


def test_director_records_independent_production_evidence_qa(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.FINAL_QA).save(fs.paths.state_json)
    (fs.paths.script_dir / "script.json").write_text(
        json.dumps({
            "audience": {"min_age": 6, "max_age": 10},
            "segments": [
                {"id": "S001", "kind": "biblical_paraphrase", "narration": "Deus prometeu.", "source_refs": ["Gênesis 15"]}
            ],
        }),
        encoding="utf-8",
    )
    (fs.paths.compiled_dir / "manifest.json").write_text(
        json.dumps({"assets": [{"sha256": "a" * 64}]}), encoding="utf-8"
    )
    fs.paths.captions_vtt.write_text("WEBVTT\n\n", encoding="utf-8")
    (fs.paths.metadata_dir / "metadata.json").write_text(
        json.dumps({
            "references": [{"book": "Gênesis"}],
            "licenses": {"visual_assets": "generated_or_canonical", "music": "none"},
        }), encoding="utf-8"
    )

    report = director.record_production_evidence_qa("EP8", published_script_hashes=set())

    assert report["approved"] is True
    assert (fs.paths.qa_dir / "production_evidence_qa.json").is_file()


def test_director_records_independent_post_production_narrative_qa(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.FINAL_QA).save(fs.paths.state_json)
    (fs.paths.script_dir / "script.json").write_text(json.dumps({"audience": {"min_age": 6, "max_age": 10}, "segments": [{"kind": "biblical_paraphrase", "narration": "Deus prometeu.", "source_refs": ["Gênesis 15"]}]}), encoding="utf-8")
    fs.paths.captions_vtt.write_text("WEBVTT\n\n", encoding="utf-8")
    (fs.paths.metadata_dir / "metadata.json").write_text(json.dumps({"references": [{"book": "Gênesis"}]}), encoding="utf-8")

    report = director.record_post_production_narrative_qa("EP8")

    assert report["approved"] is True
    assert (fs.paths.qa_dir / "post_production_narrative_qa.json").is_file()


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


@pytest.mark.asyncio
async def test_director_builds_caption_thumbnail_and_metadata_sidecars_from_compiled_packet(tmp_path, monkeypatch):
    class Captions:
        async def run(self, **kwargs):
            output = Path(kwargs["subtitles_dir"])
            output.mkdir(parents=True, exist_ok=True)
            (output / "captions.vtt").write_text("WEBVTT\n\n", encoding="utf-8")
            return AgentResult(success=True, data={"files": {"vtt": str(output / "captions.vtt")}})

    class Thumbnail:
        async def run(self, **kwargs):
            output = Path(kwargs["thumbnails_dir"]) / "thumbnail.png"
            output.write_bytes(b"thumbnail")
            return AgentResult(success=True, data={"thumbnail_path": str(output)})

    class Metadata:
        async def run(self, **kwargs):
            output = Path(kwargs["metadata_dir"]) / "metadata.json"
            output.write_text(json.dumps({"references": kwargs["research_data"]["references"]}), encoding="utf-8")
            return AgentResult(success=True, data={"metadata_path": str(output)})

    class Frame:
        scene_id = "SC001"

    class Asset:
        path = tmp_path / "scene.png"

    class Manifest:
        assets = (Asset(),)

    class Pipeline:
        episode = type("Episode", (), {"frames": (Frame(),)})()

        def approved_manifest(self):
            return Manifest()

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent()
    director._agents.update(captions=Captions(), thumbnail=Thumbnail(), metadata=Metadata())
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.FINAL_QA).save(fs.paths.state_json)
    Asset.path.write_bytes(b"image")
    fs.paths.request_json.write_text(json.dumps({"theme": "Abraão — Gênesis 15–18", "language": "pt-BR"}), encoding="utf-8")
    (fs.paths.research_dir / "sources.json").write_text(json.dumps({"references": [{"book": "Gênesis"}]}), encoding="utf-8")
    (fs.paths.script_dir / "script.json").write_text(json.dumps({"narration": "Uma promessa.", "segments": [{"id": "S001"}]}), encoding="utf-8")
    (fs.paths.storyboard_dir / "scenes.json").write_text(json.dumps({"scenes": [{"scene_id": "SC001", "start": 0, "end": 2, "narration": "Uma promessa."}]}), encoding="utf-8")

    result = await director.prepare_delivery_sidecars("EP8", Pipeline())

    assert result["thumbnail"] == str(fs.paths.thumbnails_dir / "thumbnail.png")
    assert fs.paths.captions_vtt.is_file()
    assert (fs.paths.metadata_dir / "metadata.json").is_file()


@pytest.mark.asyncio
async def test_compiled_delivery_finalizer_runs_sidecars_after_render():
    director = DirectorAgent()
    director.render_compiled_video = Mock(return_value={"render": "receipt"})
    director.prepare_delivery_sidecars = AsyncMock(return_value={"thumbnail": "thumbnail.png"})

    result = await director.finalize_compiled_delivery("EP8", pipeline="pipeline", renderer="renderer")

    assert result == {"render_receipt": {"render": "receipt"}, "sidecars": {"thumbnail": "thumbnail.png"}}
    director.render_compiled_video.assert_called_once_with("EP8", "pipeline", "renderer", hold=4)
    director.prepare_delivery_sidecars.assert_awaited_once_with("EP8", "pipeline")


@pytest.mark.asyncio
async def test_compiled_finalizer_runs_evidence_and_final_media_qa_after_sidecars():
    director = DirectorAgent()
    director.finalize_compiled_delivery = AsyncMock(return_value={"render_receipt": {"hold_seconds": 4}})
    director.record_production_evidence_qa = Mock(return_value={"approved": True})
    director.record_post_production_narrative_qa = Mock(return_value={"approved": True})
    director.record_final_render_qa = AsyncMock(return_value={"approved": True})

    result = await director.complete_compiled_final_qa(
        "EP8", pipeline="pipeline", renderer="renderer", published_script_hashes={"old-script"}
    )

    assert result["final_render_qa"]["approved"] is True
    director.finalize_compiled_delivery.assert_awaited_once_with("EP8", "pipeline", "renderer", hold=4)
    director.record_production_evidence_qa.assert_called_once_with("EP8", published_script_hashes={"old-script"})
    director.record_post_production_narrative_qa.assert_called_once_with("EP8")
    director.record_final_render_qa.assert_awaited_once()


def test_director_rejection_supersedes_delivery_lineage_and_reopens_assembly(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(
        episode_id="EP8", current_state=EpisodeState.WAITING_THUMBNAIL_APPROVAL
    ).save(fs.paths.state_json)
    thumbnail = fs.paths.thumbnails_dir / "thumbnail.png"
    thumbnail.write_bytes(b"thumbnail")
    video = fs.paths.final_video
    video.write_bytes(b"video")
    registry = RevisionRegistry(fs.paths.qa_dir)
    registry.register("thumbnail-v1", thumbnail, revision=1)
    registry.register("video-v1", video, revision=1, depends_on=["thumbnail-v1"])

    report = director.reject_delivered_artifact("EP8", artifact_id="thumbnail-v1", reason="imagem inadequada")

    assert report["artifact_id"] == "thumbnail-v1"
    assert registry.read("thumbnail-v1")["status"] == "SUPERSEDED"
    assert registry.read("video-v1")["status"] == "SUPERSEDED"
    assert EpisodeStateStore.load(fs.paths.state_json).current_state is EpisodeState.ASSEMBLING


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
