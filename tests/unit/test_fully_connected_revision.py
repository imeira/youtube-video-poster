"""Acceptance slices for the fully connected EP8 revision route."""
from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import pytest

from src.hybrid.artifacts import sha256
from src.hybrid.revision import RevisionHarness, read, main as revision_main
from src.hybrid.editorial import bootstrap_ep8_history

MEDIA = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="real FFmpeg required",
)


def _planned(root: Path) -> RevisionHarness:
    harness = RevisionHarness(root)
    harness.__enter__()
    result = harness.create_plan(
        mode="TEST",
        request=(
            "Poste um vídeo no @EraUmaVezBibliaAnimada no idioma Português do Brasil "
            "com o tema: A promessa de um filho para Abraão e Sara — Gênesis 15–18"
        ),
    )
    harness.approve_plan(result["plan_hash"], "human")
    return harness


def test_plan_contains_structured_request_adaptive_editorial_plan_and_feedback(tmp_path):
    with RevisionHarness(tmp_path) as harness:
        result = harness.create_plan(
            mode="TEST",
            request=(
                "Poste um vídeo no @EraUmaVezBibliaAnimada no idioma Português do Brasil "
                "com o tema: A promessa de um filho para Abraão e Sara — Gênesis 15–18"
            ),
        )
        plan = read(tmp_path / "revision.json")["plan"]
    assert result["status"] == "WAITING_PLAN_APPROVAL"
    assert plan["episode_request"]["locale"] == "pt-BR"
    assert 180 <= plan["editorial_plan"]["estimated_duration_seconds"] <= 900
    assert plan["editorial_plan"]["estimated_scene_count"] > 0
    assert plan["editorial_plan"]["hero_candidates"]
    assert plan["publication_authorized"] is False


@MEDIA
def test_visual_freeze_is_a_separate_gate_before_the_only_encode(tmp_path):
    harness = _planned(tmp_path)
    try:
        frozen = asyncio.run(harness.run())
        assert frozen["status"] == "WAITING_VISUAL_FREEZE_APPROVAL"
        control = read(tmp_path / "revision.json")
        assert "images" in control["stages"]
        assert "telegram_visual_freeze" in control["stages"]
        assert "encode" not in control["stages"]
        artifact = frozen["artifacts"]["visual_freeze"]
        approved = harness.approve("visual-freeze", artifact["sha256"], "human")
        assert approved["status"] == "READY_RENDER"
        delivered = asyncio.run(harness.run())
        assert delivered["status"] == "WAITING_THUMBNAIL_APPROVAL"
        control = read(tmp_path / "revision.json")
        assert control["stages"]["encode"]["result"]["render_invocations"] == 1
        motion_path = tmp_path / "r001/EP8/animation/motion-plan.json"
        assert motion_path.is_file()
        motions = json.loads(motion_path.read_text(encoding="utf-8"))["scenes"]
        assert len({scene["operation"] for scene in motions}) >= 3
        assert (tmp_path / "r001/EP8/characters/character-bible.json").is_file()
        assert (tmp_path / "r001/EP8/qa/audio.json").is_file()
        assert (tmp_path / "r001/EP8/qa/storyboard.json").is_file()
    finally:
        harness.__exit__()


@MEDIA
def test_rejection_feedback_is_mandatory_in_successor_plan(tmp_path):
    with RevisionHarness(tmp_path) as harness:
        result = harness.create_plan(mode="TEST", request="New EP8: Abraham and Sarah")
        harness.approve_plan(result["plan_hash"], "human")
        thumb = tmp_path / "thumb.bin"
        video = tmp_path / "video.bin"
        thumb.write_bytes(b"rejected thumbnail")
        video.write_bytes(b"rejected video")
        harness.control["artifacts"] = {
            "thumbnail": {"path": str(thumb), "sha256": sha256(thumb)},
            "video": {"path": str(video), "sha256": sha256(video)},
        }
        harness.control["status"] = "WAITING_THUMBNAIL_APPROVAL"
        harness.save()
        successor = harness.reject(
            "A narração precisa ser mais simples e a thumbnail deve ter nova composição",
            reviewer="operator",
            directives=["simplificar narração", "criar composição inédita"],
        )
        plan = read(tmp_path / "revision.json")["plan"]
        harness.approve_plan(successor["plan_hash"], "human")
        regenerated = asyncio.run(harness.run())
        regenerated_script = read(tmp_path / "r002/EP8/script/script.json")
        regenerated_storyboard = read(tmp_path / "r002/EP8/storyboard/connected.json")
    assert successor["status"] == "WAITING_PLAN_APPROVAL"
    assert regenerated["status"] == "WAITING_VISUAL_FREEZE_APPROVAL"
    assert regenerated_script["feedback_identity"] == plan["successor_brief"]["feedback_identity"]
    assert all(scene["feedback_identity"] == plan["successor_brief"]["feedback_identity"]
               for scene in regenerated_storyboard["scenes"])
    assert plan["successor_brief"]["rejection_feedback"]["directives"] == [
        "simplificar narração", "criar composição inédita"
    ]
    assert plan["successor_brief"]["thumbnail_constraints"]["requires_new_composition"] is True
    assert plan["successor_brief"]["feedback_identity"]


def test_historical_bootstrap_accepts_real_ep8_rejection_schema_without_mutation(tmp_path, capsys):
    source = tmp_path / "EP8_PROMISE_SON_20260901"
    (source / "approval").mkdir(parents=True)
    (source / "thumbnail").mkdir()
    (source / "renders").mkdir()
    (source / "thumbnail/rejected.png").write_bytes(b"old thumbnail")
    (source / "renders/rejected.mp4").write_bytes(b"old video")
    (source / "request.json").write_text(json.dumps({
        "episode_id": "EP8_PROMISE_SON_20260901", "theme": "A promessa de um filho",
        "channel": "@EraUmaVezBibliaAnimada", "language": "pt-BR"}), encoding="utf-8")
    (source / "state.json").write_text(json.dumps({
        "episode_id": "EP8_PROMISE_SON_20260901", "current_state": "GENERATING_IMAGES"}), encoding="utf-8")
    rejection = {
        "episode_id": "EP8_PROMISE_SON_20260901", "revision": 3,
        "status": "REJECTED_ARTIFACTS_INVALIDATED_REWRITE_IN_PROGRESS",
        "human_decision": {"thumbnail": "REJECTED", "video": "REJECTED",
            "reason": "linguagem simples e thumbnail com nova composição"},
        "superseded_artifacts": [
            {"kind": "thumbnail_gate", "path": "thumbnail/rejected.png", "status": "REJECTED_SUPERSEDED",
             "must_not_be_sent_for_approval_again": True},
            {"kind": "video_gate", "path": "renders/rejected.mp4", "status": "REJECTED_SUPERSEDED",
             "must_not_be_sent_for_approval_again": True},
        ],
        "required_rebuild_dependencies": ["narration_v2", "storyboard_v3", "new thumbnail"],
        "publication_authorized": False,
    }
    rejection_path = source / "approval/EP8_revision_v3_operator_rejection.json"
    rejection_path.write_text(json.dumps(rejection), encoding="utf-8")
    before = {path.relative_to(source).as_posix(): sha256(path) for path in source.rglob("*") if path.is_file()}
    imported = bootstrap_ep8_history(source)
    after = {path.relative_to(source).as_posix(): sha256(path) for path in source.rglob("*") if path.is_file()}
    assert before == after
    assert imported["read_only"] is True
    assert imported["brief"]["revision"] == 4
    assert imported["brief"]["rejection_feedback"]["reason"] == rejection["human_decision"]["reason"]
    assert {item["kind"] for item in imported["predecessor_identities"]} == {"video", "thumbnail"}
    bootstrap_workspace = tmp_path / "bootstrap"
    bootstrap_workspace.mkdir()
    predecessors_path = bootstrap_workspace / "predecessors.json"
    brief_path = bootstrap_workspace / "successor-brief.json"
    predecessors_path.write_text(json.dumps(imported["predecessor_identities"]), encoding="utf-8")
    brief_path.write_text(json.dumps(imported["brief"]), encoding="utf-8")
    with RevisionHarness(tmp_path / "new-revision") as harness:
        planned = harness.create_plan(mode="TEST", request="New EP8: Abraham and Sarah",
                                      predecessors=predecessors_path)
        plan = read(tmp_path / "new-revision/revision.json")["plan"]
    assert planned["revision"] == 4
    assert plan["successor_brief"]["successor_identity"] == imported["brief"]["successor_identity"]
    cli_workspace = tmp_path / "bootstrap-cli"
    assert revision_main(["bootstrap-history", "--workspace", str(cli_workspace),
                          "--source-root", str(source)]) == 0
    cli_result = json.loads(capsys.readouterr().out)
    assert cli_result["status"] == "BOOTSTRAPPED"
    assert cli_result["read_only"] is True
    assert "source_bindings" not in cli_result
    assert cli_result["source_manifest_identity"] == imported["source_manifest_identity"]


@MEDIA
def test_complete_metadata_and_three_independent_final_authorities(tmp_path):
    harness = _planned(tmp_path)
    try:
        frozen = asyncio.run(harness.run())
        harness.approve("visual-freeze", frozen["artifacts"]["visual_freeze"]["sha256"], "human")
        thumb_gate = asyncio.run(harness.run())
        artifacts = thumb_gate["artifacts"]
        harness.approve("thumbnail", artifacts["thumbnail"]["sha256"], "human")
        video_gate = asyncio.run(harness.run())
        final = harness.approve("video", artifacts["video"]["sha256"], "human")
        assert final["status"] == "WAITING_PUBLICATION_AUTHORIZATION"
        metadata_path = tmp_path / "r001/EP8/metadata/youtube.json"
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        assert metadata["made_for_kids"] is True
        assert metadata["privacy_status"] == "private"
        assert metadata["playlist"] == "Aventuras do Antigo Testamento"
        assert metadata["category_id"] == "27"
        assert metadata["captions"]["srt"]["sha256"]
        assert metadata["captions"]["vtt"]["sha256"]
        assert metadata["transcript"]["sha256"]
        authorized = harness.authorize_publication(
            command="AUTORIZAR PUBLICAÇÃO EP8",
            reviewer="human",
        )
        assert authorized["status"] == "READY_FOR_PUBLICATION"
        receipt = read(tmp_path / "r001/EP8/approval/publication-authorization.json")
        assert receipt["metadata_sha256"] == sha256(metadata_path)
        assert receipt["thumbnail_sha256"] == artifacts["thumbnail"]["sha256"]
        assert receipt["video_sha256"] == artifacts["video"]["sha256"]
        assert receipt["upload_performed"] is False
    finally:
        harness.__exit__()
