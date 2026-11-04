"""Director does not convert final approval into publication."""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.agents.base import AgentResult
from src.agents.director import DirectorAgent
from src.approval.inbound import AuthenticatedApprovalReply
from src.approval.receipts import ApprovalReceipt, save_approval_receipt
from src.config.loader import get_config
from src.hybrid.render import commit_renderer_completion
from src.pipeline.revision import RevisionRegistry
from src.providers.base import PublishResult
from src.qa.final_render import FinalRenderQAResult
from src.state.machine import EpisodeState, EpisodeStateStore
from src.storage.episode_fs import EpisodeFS


def delivery_director() -> DirectorAgent:
    class TestInboundSource:
        def _reply(self, chat_id, message_id):
            return AuthenticatedApprovalReply(
                transport="TEST", chat_id=chat_id, sender_id="trusted-user",
                message_id=message_id + 100, reply_to_message_id=message_id,
                text="APROVAR THUMBNAIL EP8",
            )

        def claim_verified_reply(self, *, chat_id, message_id):
            return self._reply(chat_id, message_id)

        def recover_verified_reply(self, *, chat_id, message_id):
            return self._reply(chat_id, message_id)

        def revalidate_reply(self, document):
            return None

    return DirectorAgent(
        config=replace(
            get_config(), approval_delivery_chat_id="test-chat",
            approval_delivery_trusted_sender_ids=frozenset({"trusted-user"}),
        ),
        inbound_approval_source=TestInboundSource(),
    )


def compiled_render_inputs():
    """Provide the immutable surface Director binds into a render contract."""
    manifest = SimpleNamespace(checksum="manifest-sha256")
    scene = SimpleNamespace(image=SimpleNamespace(sha256="image-sha256"), clip=None)
    return SimpleNamespace(
        episode=SimpleNamespace(audio=SimpleNamespace(sha256="audio-sha256")),
        approved_manifest=lambda: manifest,
        render_scenes=lambda received_manifest: [scene],
    )


def compiled_renderer(render=None):
    return SimpleNamespace(
        width=1920,
        height=1080,
        fps=30,
        filter_complex_threads=1,
        scene_workers=1,
        encoder_threads=1,
        render_compiled=render,
    )


def test_compiled_render_recovery_rejects_legacy_receipt_without_renderer_completion(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = delivery_director()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.FINAL_QA).save(fs.paths.state_json)
    fs.paths.final_video.write_bytes(b"existing-final")
    pipeline = compiled_render_inputs()
    renderer = compiled_renderer()
    contract = director._compiled_render_contract("EP8", pipeline, renderer, 4)
    receipt = {
        "hold_seconds": 4,
        "output_sha256": hashlib.sha256(fs.paths.final_video.read_bytes()).hexdigest(),
        "render_contract": contract,
        "render_contract_sha256": director._compiled_render_contract_sha256(contract),
    }
    (fs.paths.qa_dir / "compiled_render_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")

    with pytest.raises(ValueError, match="quarantined"):
        director.render_compiled_video("EP8", pipeline=pipeline, renderer=renderer)


@pytest.mark.asyncio
async def test_final_approval_waits_for_separate_publication_command(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = delivery_director()
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
    director = delivery_director()
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
    director = delivery_director()
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
    director = delivery_director()
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
    director = delivery_director()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.FINAL_QA).save(fs.paths.state_json)
    (fs.paths.qa_dir / "production_evidence_qa.json").write_text(json.dumps({"approved": True}), encoding="utf-8")
    fs.paths.final_video.write_bytes(b"final")

    with pytest.raises(ValueError, match="post-production narrative QA"):
        await director.record_final_render_qa("EP8", video_path=fs.paths.final_video, render_receipt={"hold_seconds": 4}, checker=PassingQA())


def test_director_records_independent_production_evidence_qa(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = delivery_director()
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
    director = delivery_director()
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
async def test_director_delivers_frozen_video_only_after_thumbnail_approval(tmp_path, monkeypatch):
    class Messenger:
        def __init__(self):
            self.calls = []

        async def send_photo(self, chat_id, photo_path, caption=""):
            self.calls.append("thumbnail")
            return 101

        async def send_video(self, chat_id, video_path, caption=""):
            self.calls.append("video")
            return 202

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = delivery_director()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(
        episode_id="EP8", current_state=EpisodeState.WAITING_THUMBNAIL_APPROVAL
    ).save(fs.paths.state_json)
    fs.paths.final_video.write_bytes(b"video")
    thumbnail = fs.paths.thumbnails_dir / "thumbnail.png"
    thumbnail.write_bytes(b"thumbnail")
    messenger = Messenger()

    thumbnail_delivery = await director.deliver_for_approval(
        "EP8", messenger=messenger, chat_id="test-chat", thumbnail_path=thumbnail
    )

    assert thumbnail_delivery["artifact_kind"] == "thumbnail"
    assert messenger.calls == ["thumbnail"]
    director.confirm_delivered_artifact(
        "EP8", artifact_kind="thumbnail", artifact_path=thumbnail,
        delivery_receipt_path=fs.paths.qa_dir / "delivery" / "thumbnail.json",
    )

    video_delivery = await director.deliver_for_approval(
        "EP8", messenger=messenger, chat_id="test-chat", thumbnail_path=thumbnail
    )

    assert video_delivery["artifact_kind"] == "video"
    assert messenger.calls == ["thumbnail", "video"]
    assert (fs.paths.qa_dir / "delivery" / "thumbnail.json").is_file()
    assert (fs.paths.qa_dir / "delivery" / "video.json").is_file()


@pytest.mark.asyncio
async def test_director_blocks_video_delivery_when_thumbnail_is_mutated_or_rejected(tmp_path, monkeypatch):
    class Messenger:
        def __init__(self):
            self.calls = []

        async def send_photo(self, *args, **kwargs):
            self.calls.append("thumbnail")
            return 101

        async def send_video(self, *args, **kwargs):
            self.calls.append("video")
            return 202

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = delivery_director()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.WAITING_THUMBNAIL_APPROVAL).save(fs.paths.state_json)
    fs.paths.final_video.write_bytes(b"video")
    thumbnail = fs.paths.thumbnails_dir / "thumbnail.png"
    thumbnail.write_bytes(b"thumbnail")
    messenger = Messenger()
    await director.deliver_for_approval("EP8", messenger=messenger, chat_id="test-chat", thumbnail_path=thumbnail)
    director.confirm_delivered_artifact(
        "EP8", artifact_kind="thumbnail", artifact_path=thumbnail,
        delivery_receipt_path=fs.paths.qa_dir / "delivery" / "thumbnail.json",
    )
    thumbnail.write_bytes(b"mutated")

    with pytest.raises(Exception, match="no longer binds|delivery path changed"):
        await director.deliver_for_approval("EP8", messenger=messenger, chat_id="test-chat", thumbnail_path=thumbnail)

    assert messenger.calls == ["thumbnail"]


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
async def test_compiled_delivery_finalizer_runs_sidecars_after_render(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = delivery_director()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.PLANNING_ANIMATION).save(fs.paths.state_json)
    pipeline = compiled_render_inputs()
    render_calls = []

    def render_compiled(*args, **kwargs):
        render_calls.append((args, kwargs))
        fs.paths.final_video.write_bytes(b"rendered-final")
        receipt = {
            "hold_seconds": kwargs["hold"],
            "output_sha256": hashlib.sha256(fs.paths.final_video.read_bytes()).hexdigest(),
            "subtitles_sha256": None,
        }
        commit_renderer_completion(
            kwargs["completion_receipt_path"],
            intent_sha256=kwargs["render_intent_sha256"],
            render_contract_sha256=kwargs["render_contract_sha256"],
            receipt=receipt,
        )
        return receipt

    renderer = compiled_renderer(render_compiled)
    director.prepare_delivery_sidecars = AsyncMock(return_value={"thumbnail": "thumbnail.png"})

    result = await director.finalize_compiled_delivery("EP8", pipeline=pipeline, renderer=renderer)

    assert result["render_receipt"]["renderer_receipt"]["output_sha256"] == hashlib.sha256(b"rendered-final").hexdigest()
    assert result["sidecars"] == {"thumbnail": "thumbnail.png"}
    assert len(render_calls) == 1
    assert (fs.paths.qa_dir / "compiled_render_receipt.json").is_file()
    director.prepare_delivery_sidecars.assert_awaited_once_with("EP8", pipeline)


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
    director = delivery_director()
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


@pytest.mark.asyncio
async def test_director_rejection_closes_delivery_gate_without_sending_video(tmp_path, monkeypatch):
    class Messenger:
        def __init__(self):
            self.calls = []

        async def send_photo(self, *args, **kwargs):
            self.calls.append("thumbnail")
            return 101

        async def send_video(self, *args, **kwargs):
            self.calls.append("video")
            return 202

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = delivery_director()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.WAITING_THUMBNAIL_APPROVAL).save(fs.paths.state_json)
    thumbnail = fs.paths.thumbnails_dir / "thumbnail.png"
    thumbnail.write_bytes(b"thumbnail")
    fs.paths.final_video.write_bytes(b"video")
    RevisionRegistry(fs.paths.qa_dir).register("thumbnail-v1", thumbnail, revision=1)
    director.reject_delivered_artifact("EP8", artifact_id="thumbnail-v1", reason="rejected")
    messenger = Messenger()

    with pytest.raises(ValueError, match="active thumbnail or video gate"):
        await director.deliver_for_approval("EP8", messenger=messenger, chat_id="test-chat", thumbnail_path=thumbnail)

    assert messenger.calls == []


def test_director_records_only_exact_approval_of_delivered_media(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = delivery_director()
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
        json.dumps({"episode_id": "EP8", "artifact_kind": "thumbnail", "approval": delivered.__dict__, "chat_id": "test-chat", "message_id": 1}),
        encoding="utf-8",
    )

    receipt = director.confirm_delivered_artifact(
        "EP8", artifact_kind="thumbnail", artifact_path=thumbnail,
        delivery_receipt_path=delivery_path,
    )

    assert receipt.artifact_kind == "thumbnail"
    assert (fs.paths.qa_dir / "approval-thumbnail.json").is_file()
    assert EpisodeStateStore.load(fs.paths.state_json).current_state is EpisodeState.WAITING_VIDEO_APPROVAL


def test_director_rejects_thumbnail_approval_outside_its_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = delivery_director()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.WAITING_VIDEO_APPROVAL).save(fs.paths.state_json)
    thumbnail = fs.paths.thumbnails_dir / "approved.png"
    thumbnail.write_bytes(b"thumbnail")
    delivered = ApprovalReceipt.approve("thumbnail", thumbnail, "delivery")
    delivery_path = fs.paths.qa_dir / "delivery-thumbnail.json"
    delivery_path.write_text(
        json.dumps({"episode_id": "EP8", "artifact_kind": "thumbnail", "approval": delivered.__dict__, "chat_id": "test-chat", "message_id": 1}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="WAITING_THUMBNAIL_APPROVAL"):
        director.confirm_delivered_artifact(
            "EP8", artifact_kind="thumbnail", artifact_path=thumbnail,
            delivery_receipt_path=delivery_path,
        )

    assert not (fs.paths.qa_dir / "approval-thumbnail.json").exists()


@pytest.mark.parametrize(
    ("kind", "state_name"),
    [("thumbnail", EpisodeState.WAITING_THUMBNAIL_APPROVAL), ("video", EpisodeState.WAITING_VIDEO_APPROVAL)],
)
def test_director_legacy_persisted_receipt_without_source_never_advances_gate(tmp_path, monkeypatch, kind, state_name):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    config = replace(
        get_config(), approval_delivery_chat_id="test-chat",
        approval_delivery_trusted_sender_ids=frozenset({"trusted-user"}),
    )
    director = DirectorAgent(config=config, inbound_approval_source=None)
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="EP8", current_state=state_name).save(fs.paths.state_json)
    artifact = fs.paths.thumbnails_dir / "legacy.png" if kind == "thumbnail" else fs.paths.final_video
    artifact.write_bytes(kind.encode())
    delivery = fs.paths.qa_dir / f"delivery-{kind}.json"
    delivered = ApprovalReceipt.approve(kind, artifact, "delivery")
    delivery.write_text(json.dumps({
        "episode_id": "EP8", "artifact_kind": kind, "approval": delivered.__dict__,
        "chat_id": "test-chat", "message_id": 42,
    }), encoding="utf-8")
    legacy = ApprovalReceipt.approve(kind, artifact, "local-user")
    (fs.paths.qa_dir / f"approval-{kind}.json").write_text(
        json.dumps(legacy.__dict__), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="authenticated"):
        director.confirm_delivered_artifact(
            "EP8", artifact_kind=kind, artifact_path=artifact, delivery_receipt_path=delivery
        )

    assert EpisodeStateStore.load(fs.paths.state_json).current_state is state_name
