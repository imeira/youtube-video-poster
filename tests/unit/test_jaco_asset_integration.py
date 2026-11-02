from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _packet() -> dict:
    scenes = []
    start = 0.0
    for index in range(1, 11):
        end = 105.048 if index == 10 else round(start + 10.0, 3)
        scenes.append(
            {
                "scene_id": f"S{index:03d}",
                "start_sec": start,
                "end_sec": end,
                "duration_sec": end - start,
                "narration": f"Cena {index}.",
                "source_refs": [] if index == 10 else ["Gênesis 28:1-5"],
            }
        )
        start = end
    return {
        "schema": "jaco-first-canonical-image-input-packet/v1",
        "scope": {
            "episode": "JACO_GENESIS28",
            "request_id": "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9",
            "active_revision": "818556d97edb648f",
        },
        "status": "NOT_QUALIFIED_NO_PAID_POST",
        "actual_timed_narration": {
            "references": ["Gênesis 28:1-5,10-22"],
            "timed_scenes": scenes,
        },
        "immutable_narration": {
            "audio_path": "C:/missing/narration.wav",
            "audio_sha256": "a" * 64,
            "audio_duration_sec": 105.048,
            "script_path": "C:/missing/script.json",
            "script_sha256": "b" * 64,
            "biblical_source_path": "C:/missing/source.json",
            "biblical_source_sha256": "c" * 64,
            "sentence_timing_path": "C:/missing/timing.json",
            "sentence_timing_sha256": "d" * 64,
        },
        "canonical_image_request": {
            "request_key": "JACO_GEN28_CANONICAL_001",
            "method": "POST_NOT_EXECUTED",
            "reference_inputs": [],
            "payload": {"prompt": "literal prompt", "num_images": 1},
            "planned_successor_edits": {"count": 9, "dispatch_state": "NOT_AUTHORIZED"},
        },
        "lorena_candidate_import": {
            "candidate_path": "C:/missing/lorena.mp4",
            "candidate_sha256": "e" * 64,
            "qualification_status": "NOT_QUALIFIED_PENDING_HUMAN_GATE",
        },
        "provider_policy_block": {"code": "NO_PAID_POST"},
    }


def test_materialize_blocks_missing_real_assets_but_preserves_exact_first_job(
    tmp_path: Path,
):
    from src.hybrid.jaco_asset_integration import materialize_jaco_asset_integration

    packet_path = tmp_path / "first-job.json"
    inventory_path = tmp_path / "inventory.json"
    packet_path.write_text(json.dumps(_packet()), encoding="utf-8")
    inventory_path.write_text(
        json.dumps({"schema": "jaco-local-throughput-readiness/v1"}), encoding="utf-8"
    )

    result = materialize_jaco_asset_integration(
        packet_path, inventory_path, tmp_path / "factory"
    )

    manifest = json.loads(
        Path(result["canonical_input_manifest_path"]).read_text(encoding="utf-8")
    )
    status = json.loads(Path(result["status_path"]).read_text(encoding="utf-8"))
    assert manifest["first_image_job"] == _packet()["canonical_image_request"]
    assert manifest["first_image_job"]["method"] == "POST_NOT_EXECUTED"
    assert manifest["scene_mapping"]["scene_count"] == 10
    assert status["qualification_status"] == "NOT_QUALIFIED"
    assert status["provider_admission"] == "BLOCKED"
    assert status["lorena"]["status"] == "CANDIDATE_ONLY_NOT_APPROVED_ASSET"
    assert (
        status["next_job_input_artifact_path"]
        == result["canonical_input_manifest_path"]
    )
    assert {item["kind"] for item in status["blockers"]} >= {
        "audio",
        "script",
        "source",
        "timing",
        "lorena_candidate",
        "identity_reference",
        "license",
    }


def test_materialize_rejects_non_genesis_scene_reference(tmp_path: Path):
    from src.hybrid.jaco_asset_integration import materialize_jaco_asset_integration

    packet = _packet()
    packet["actual_timed_narration"]["timed_scenes"][0]["source_refs"] = ["Êxodo 1:1"]
    packet_path = tmp_path / "packet.json"
    inventory_path = tmp_path / "inventory.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    inventory_path.write_text(
        json.dumps({"schema": "jaco-local-throughput-readiness/v1"}), encoding="utf-8"
    )

    with pytest.raises(ValueError, match="Genesis 28"):
        materialize_jaco_asset_integration(
            packet_path, inventory_path, tmp_path / "factory"
        )


def test_materialize_successor_binds_durable_asr_inputs_without_stale_paths(
    tmp_path: Path,
):
    from src.hybrid.jaco_asset_integration import materialize_jaco_asset_integration

    packet = _packet()
    packet["schema"] = "jaco-first-canonical-image-input-packet/v2"
    packet["actual_timed_narration"]["timed_scenes"][-1]["end_sec"] = 105.048
    durable = {}
    for kind, name, verdict in (
        ("audio", "narration.wav", None),
        ("script", "script.txt", None),
        ("asr_timeline", "timeline.json", "PASS_ALIGNED_RECOGNIZED_WORDS"),
        ("asr_qa", "asr-qa.json", "PASS_ALIGNED_RECOGNIZED_WORDS"),
        ("narrative_review", "narrative.json", "PASS_BOUNDED_MACHINE_REVIEW"),
        (
            "qa_closure",
            "closure.json",
            "PASS_BOUNDED_QA_CLOSED_DIRECTOR_IMPORT_BLOCKED",
        ),
    ):
        path = tmp_path / name
        if verdict:
            path.write_text(json.dumps({"verdict": verdict}), encoding="utf-8")
        else:
            path.write_bytes(b"durable " + kind.encode())
        durable[kind] = {"path": str(path), "sha256": _digest(path)}
    packet["successor_durable_bindings"] = durable
    packet["identity_reference"] = {"status": "NO_APPROVED_IDENTITY_REFERENCE_FOUND"}
    packet["license_provenance"] = {"status": "MISSING_LICENSE_PROVENANCE"}
    packet["successor_metadata"] = {"scope": "test only"}
    packet_path = tmp_path / "successor-packet.json"
    inventory_path = tmp_path / "inventory.json"
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    inventory_path.write_text(
        json.dumps({"schema": "jaco-local-throughput-readiness/v1"}), encoding="utf-8"
    )

    result = materialize_jaco_asset_integration(
        packet_path, inventory_path, tmp_path / "factory"
    )
    manifest = json.loads(
        Path(result["canonical_input_manifest_path"]).read_text(encoding="utf-8")
    )
    evidence = {item["kind"]: item for item in manifest["artifact_evidence"]}
    assert all(evidence[kind]["status"] == "VERIFIED" for kind in durable)
    assert (
        evidence["identity_reference"]["status"]
        == "NO_APPROVED_IDENTITY_REFERENCE_FOUND"
    )
    assert evidence["license"]["status"] == "MISSING_LICENSE_PROVENANCE"
    assert manifest["first_image_job"]["reference_inputs"] == []
