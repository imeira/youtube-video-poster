"""Fail-closed local integration for the supplied Jacó Genesis 28 assets.

This module deliberately materializes only evidence/status JSON.  It does not
construct a provider or a Director execution path.
"""

from __future__ import annotations

import json
import math
import os
from hashlib import sha256
from pathlib import Path
from typing import Any

REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
REVISION = "818556d97edb648f"
EPISODE = "JACO_GENESIS28"


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON object required: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_hash_status(kind: str, path_value: Any, expected: Any) -> dict[str, Any]:
    if (
        not isinstance(path_value, str)
        or not isinstance(expected, str)
        or len(expected) != 64
    ):
        return {"kind": kind, "status": "MISSING_DECLARATION"}
    path = Path(path_value)
    if not path.is_file():
        return {
            "kind": kind,
            "status": "MISSING",
            "declared_path": path_value,
            "expected_sha256": expected,
        }
    actual = _sha256(path)
    return {
        "kind": kind,
        "status": "VERIFIED" if actual == expected else "HASH_MISMATCH",
        "declared_path": path_value,
        "expected_sha256": expected,
        "actual_sha256": actual,
    }


def _validate_scenes(
    packet: dict[str, Any],
    *,
    allow_semantic_tail: bool = False,
    allow_timeline_gaps: bool = False,
) -> list[dict[str, Any]]:
    narration = packet.get("actual_timed_narration")
    if not isinstance(narration, dict):
        raise TypeError("actual_timed_narration required")
    scenes = narration.get("timed_scenes")
    if not isinstance(scenes, list) or len(scenes) != 10:
        raise ValueError("exactly ten timed scenes required")
    previous = 0.0
    normalized: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes, 1):
        if not isinstance(scene, dict) or scene.get("scene_id") != f"S{index:03d}":
            raise ValueError("scene IDs must be contiguous S001 through S010")
        start, end = scene.get("start_sec"), scene.get("end_sec")
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            for value in (start, end)
        ):
            raise ValueError("scene windows must be finite numbers")
        if float(end) <= float(start):
            raise ValueError("scene windows must be positive")
        if allow_timeline_gaps:
            if float(start) < previous:
                raise ValueError("ASR scene windows must not overlap")
        elif abs(float(start) - previous) > 0.001:
            raise ValueError("scene windows must be contiguous")
        refs = scene.get("source_refs")
        if not isinstance(refs, list):
            raise TypeError("scene source references required")
        if index < 10 and (
            not refs
            or any(
                not isinstance(ref, str) or not ref.startswith("Gênesis 28:")
                for ref in refs
            )
        ):
            raise ValueError("biblical scenes must map to Genesis 28")
        if index == 10 and refs:
            raise ValueError(
                "only final family reflection may omit biblical references"
            )
        normalized.append(
            {
                "scene_id": scene["scene_id"],
                "start_sec": start,
                "end_sec": end,
                "source_refs": refs,
            }
        )
        previous = float(end)
    expected_end = 105.048
    if allow_semantic_tail:
        if previous > expected_end or expected_end - previous > 1.0:
            raise ValueError(
                "ASR semantic timeline tail must be bounded by decoded audio"
            )
    elif abs(previous - expected_end) > 0.001:
        raise ValueError("scene timeline must end at 105.048 seconds")
    return normalized


def _first_job(packet: dict[str, Any]) -> dict[str, Any]:
    job = packet.get("canonical_image_request")
    if not isinstance(job, dict):
        raise TypeError("canonical image request required")
    if (
        job.get("request_key") != "JACO_GEN28_CANONICAL_001"
        or job.get("method") != "POST_NOT_EXECUTED"
    ):
        raise ValueError("literal first canonical image request is required")
    if job.get("reference_inputs") != []:
        raise ValueError("first canonical request must have no references")
    successors = job.get("planned_successor_edits")
    if (
        not isinstance(successors, dict)
        or successors.get("count") != 9
        or successors.get("dispatch_state") != "NOT_AUTHORIZED"
    ):
        raise ValueError("nine un-authorized successor edits required")
    return job


def _durable_successor_evidence(packet: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Verify the repaired successor bindings without reviving scratch paths."""
    bindings = packet.get("successor_durable_bindings")
    if bindings is None:
        return None
    if packet.get(
        "schema"
    ) != "jaco-first-canonical-image-input-packet/v2" or not isinstance(bindings, dict):
        raise ValueError("closed successor packet v2 required")
    expected_verdicts = {
        "asr_timeline": "PASS_ALIGNED_RECOGNIZED_WORDS",
        "asr_qa": "PASS_ALIGNED_RECOGNIZED_WORDS",
        "narrative_review": "PASS_BOUNDED_MACHINE_REVIEW",
        "qa_closure": "PASS_BOUNDED_QA_CLOSED_DIRECTOR_IMPORT_BLOCKED",
    }
    evidence: list[dict[str, Any]] = []
    for kind in (
        "audio",
        "script",
        "asr_timeline",
        "asr_qa",
        "narrative_review",
        "qa_closure",
    ):
        binding = bindings.get(kind)
        if not isinstance(binding, dict):
            raise TypeError(f"durable {kind} binding required")
        status = _regular_hash_status(kind, binding.get("path"), binding.get("sha256"))
        if status["status"] != "VERIFIED":
            raise ValueError(f"durable {kind} must exist and hash-verify")
        if kind in expected_verdicts:
            document = _load_object(Path(binding["path"]))
            if document.get("verdict") != expected_verdicts[kind]:
                raise ValueError(f"durable {kind} verdict mismatch")
        evidence.append(status)
    identity = packet.get("identity_reference")
    license_provenance = packet.get("license_provenance")
    if identity != {"status": "NO_APPROVED_IDENTITY_REFERENCE_FOUND"}:
        raise ValueError("do not substitute a noncanonical identity reference")
    if license_provenance != {"status": "MISSING_LICENSE_PROVENANCE"}:
        raise ValueError("license provenance must remain explicit when absent")
    return evidence + [
        {
            "kind": "identity_reference",
            "status": "NO_APPROVED_IDENTITY_REFERENCE_FOUND",
        },
        {"kind": "license", "status": "MISSING_LICENSE_PROVENANCE"},
    ]


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_bytes(_canonical(document) + b"\n")
    os.replace(temporary, path)


def materialize_jaco_asset_integration(
    packet_path: Path, inventory_path: Path, factory_root: Path
) -> dict[str, str]:
    """Write a non-promotable status/manifest from supplied local evidence."""
    packet = _load_object(packet_path)
    inventory = _load_object(inventory_path)
    scope = packet.get("scope")
    if (
        not isinstance(scope, dict)
        or scope.get("request_id") != REQUEST_ID
        or scope.get("active_revision") != REVISION
        or scope.get("episode") != EPISODE
    ):
        raise ValueError("unexpected Jacó request/revision scope")
    if (
        packet.get("schema")
        not in {
            "jaco-first-canonical-image-input-packet/v1",
            "jaco-first-canonical-image-input-packet/v2",
        }
        or packet.get("status") != "NOT_QUALIFIED_NO_PAID_POST"
    ):
        raise ValueError("non-promotable first-image packet required")
    if inventory.get("schema") != "jaco-local-throughput-readiness/v1":
        raise ValueError("Jacó readiness inventory required")

    successor_packet = (
        packet.get("schema") == "jaco-first-canonical-image-input-packet/v2"
    )
    scenes = _validate_scenes(
        packet,
        allow_semantic_tail=successor_packet,
        allow_timeline_gaps=successor_packet,
    )
    first_job = _first_job(packet)
    audio = packet.get("immutable_narration")
    timing = packet.get("actual_timed_narration")
    if not isinstance(audio, dict) or not isinstance(timing, dict):
        raise TypeError("narration bindings required")
    evidence = _durable_successor_evidence(packet) or [
        _regular_hash_status(
            "audio", audio.get("audio_path"), audio.get("audio_sha256")
        ),
        _regular_hash_status(
            "script",
            audio.get("script_path", timing.get("script_path")),
            audio.get("script_sha256", timing.get("script_sha256")),
        ),
        _regular_hash_status(
            "source",
            audio.get("biblical_source_path", timing.get("biblical_source_path")),
            audio.get("biblical_source_sha256", timing.get("biblical_source_sha256")),
        ),
        _regular_hash_status(
            "timing",
            audio.get("sentence_timing_path"),
            audio.get("sentence_timing_sha256"),
        ),
        {"kind": "identity_reference", "status": "MISSING_DECLARATION"},
        {"kind": "license", "status": "MISSING_DECLARATION"},
    ]
    lorena = packet.get("lorena_candidate_import")
    if not isinstance(lorena, dict):
        raise TypeError("Lorena candidate declaration required")
    lorena_evidence = _regular_hash_status(
        "lorena_candidate", lorena.get("candidate_path"), lorena.get("candidate_sha256")
    )
    lorena_status = "CANDIDATE_ONLY_NOT_APPROVED_ASSET"

    root = Path(factory_root) / "requests" / REQUEST_ID / "jaco-asset-integration"
    manifest_path = root / "canonical-input-manifest.json"
    status_path = root / "status.json"
    manifest = {
        "schema": "jaco-canonical-input-manifest/v1",
        "episode": EPISODE,
        "request_id": REQUEST_ID,
        "active_revision": REVISION,
        "source_packet": {"path": str(packet_path), "sha256": _sha256(packet_path)},
        "readiness_inventory": {
            "path": str(inventory_path),
            "sha256": _sha256(inventory_path),
        },
        "scene_mapping": {
            "scene_count": len(scenes),
            "audio_duration_sec": 105.048,
            "semantic_timeline_end_sec": scenes[-1]["end_sec"],
            "scenes": scenes,
        },
        "first_image_job": first_job,
        "artifact_evidence": evidence + [lorena_evidence],
        "lorena": {
            "status": lorena_status,
            "declared_qualification": lorena.get("qualification_status"),
        },
    }
    if packet.get("schema") == "jaco-first-canonical-image-input-packet/v2":
        successor_metadata = packet.get("successor_metadata")
        if not isinstance(successor_metadata, dict):
            raise TypeError("successor metadata required for durable successor packet")
        manifest["successor_metadata"] = successor_metadata
    _atomic_json(manifest_path, manifest)
    blockers = [
        item for item in evidence + [lorena_evidence] if item["status"] != "VERIFIED"
    ]
    status = {
        "schema": "jaco-asset-integration-status/v1",
        "episode": EPISODE,
        "request_id": REQUEST_ID,
        "active_revision": REVISION,
        "qualification_status": "NOT_QUALIFIED",
        "provider_admission": "BLOCKED",
        "director_import": "NOT_ATTEMPTED_INCOMPLETE_UNQUALIFIED_INPUTS",
        "paid_post": "NOT_EXECUTED",
        "lorena": {"status": lorena_status, "technical_evidence": lorena_evidence},
        "blockers": blockers,
        "qa_scope": {
            "inventory_status": inventory.get("assets_status"),
            "technical_qa_is_not_independent_semantic_or_human_approval": True,
        },
        "next_job_input_artifact_path": str(manifest_path),
    }
    _atomic_json(status_path, status)
    return {
        "canonical_input_manifest_path": str(manifest_path),
        "status_path": str(status_path),
    }
