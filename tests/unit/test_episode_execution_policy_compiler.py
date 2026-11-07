"""TDD contract for immutable Jacó quality-remediation preflight."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _evidence(tmp_path: Path, *, request_id: str = REQUEST_ID, cap: str = "10.00") -> dict[str, Path]:
    candidate = tmp_path / "failed.png"
    candidate.write_bytes(b"not-a-promotable-png-but-hash-bound-evidence")
    reference = tmp_path / "ep8-approved.png"
    reference.write_bytes(b"ep8-approved-style-reference")
    manifest = _write(tmp_path / "ep8-manifest.json", {
        "episode_id": "EP8_PROMISE_SON_20260901", "frame_id": "F001",
        "status": "APPROVED_FINAL_FRAME", "approved_candidate": str(reference), "sha256": _sha(reference),
    })
    packet = _write(tmp_path / "prior-packet.json", {
        "schema": "jaco-fal-flux3-successor-benchmark/3", "kind": "BOUNDED_FAL_FLUX3_CANARY_PREGEN_PACKET",
        "bindings": {"request_id": request_id, "payload_sha256": "a" * 64},
    })
    receipt = _write(tmp_path / "prior-receipt.json", {
        "kind": "PNG_RESULT", "sha256": _sha(candidate), "actual_billed_amount": "UNKNOWN_UNTIL_AUTHENTICATED_BILLING_RECONCILIATION",
    })
    qa = _write(tmp_path / "prior-qa.json", {
        "kind": "BOUNDED_INTERNAL_VISUAL_QA", "verdict": "NOT_PROMOTED",
        "candidate": {"sha256": _sha(candidate)},
        "issues": ["STYLE_MISMATCH_TO_EP8_APPROVED_REFERENCE", "ANGEL_DUPLICATION", "ONE_WAY_DIRECTIONAL_READING"],
        "canonical_style_reference": {"manifest_path": str(manifest), "frame_sha256": _sha(reference)},
    })
    mandate = _write(tmp_path / "mandate.json", {
        "schema": "JACO_USER_EXECUTION_MANDATE/2", "request_id": request_id,
        "budget_cap_usd": cap, "call_limit": 1, "scope": "EVALUATION_GENERATION_ONLY",
        "release_authorization": "NOT_AUTHORIZED",
    })
    return {"candidate": candidate, "reference": reference, "manifest": manifest, "packet": packet, "receipt": receipt, "qa": qa, "mandate": mandate}


def test_compiler_emits_new_operation_scoped_non_dispatchable_successor(tmp_path: Path):
    from src.hybrid.episode_execution_policy import EpisodeExecutionPolicyCompiler

    paths = _evidence(tmp_path)
    result = EpisodeExecutionPolicyCompiler().compile_quality_remediation(
        request_id=REQUEST_ID, prior_packet=paths["packet"], prior_receipt=paths["receipt"],
        failed_candidate=paths["candidate"], prior_qa=paths["qa"], ep8_manifest=paths["manifest"],
        ep8_reference=paths["reference"], user_mandate=paths["mandate"], actual_spend_usd="0.048",
    )

    assert result["status"] == "VALIDATED_PRE_DISPATCH_BLOCKED_MISSING_OPERATION_AUTHORIZATION"
    assert result["operation"]["id"] == "JACO_GEN28_STYLE_REMEDIATION_001"
    assert result["operation"]["call_limit"] == 1
    assert result["operation"]["maximum_usd"] == "0.048"
    assert result["budget"] == {"episode_cap_usd": "10.00", "actual_spend_usd": "0.048", "pending_usd": "0.000", "next_operation_maximum_usd": "0.048", "post_dispatch_exposure_usd": "0.096", "reservation": "NOT_CREATED"}
    assert result["prior_attempt"]["promotion"] == "PERMANENTLY_REJECTED_NOT_REUSABLE"
    assert result["human_gates"] == ["thumbnail", "final_video"]
    assert result["publication"] == "EXPLICIT_SEPARATE_COMMAND_REQUIRED"
    assert "child-stylized 3D illustrative animation" in result["operation"]["payload"]["prompt"]
    assert "realistic/cinematic" in result["operation"]["payload"]["prompt"]
    assert result["operation"]["payload"]["prompt"].count("No duplicated angels") == 1


@pytest.mark.parametrize("mutator, message", [
    (lambda paths: _write(paths["packet"], {"schema": "jaco-fal-flux3-successor-benchmark/3", "kind": "BOUNDED_FAL_FLUX3_CANARY_PREGEN_PACKET", "bindings": {"request_id": "wrong", "payload_sha256": "a" * 64}}), "request"),
    (lambda paths: _write(paths["mandate"], {"schema": "JACO_USER_EXECUTION_MANDATE/2", "request_id": REQUEST_ID, "budget_cap_usd": "10.01", "call_limit": 1, "scope": "EVALUATION_GENERATION_ONLY", "release_authorization": "NOT_AUTHORIZED"}), "cap"),
])
def test_compiler_rejects_wrong_request_and_cap_reauthorization_collision(tmp_path: Path, mutator, message: str):
    from src.hybrid.episode_execution_policy import (
        EpisodeExecutionPolicyCompiler,
        EpisodePolicyError,
    )

    paths = _evidence(tmp_path)
    mutator(paths)
    with pytest.raises(EpisodePolicyError, match=message):
        EpisodeExecutionPolicyCompiler().compile_quality_remediation(
            request_id=REQUEST_ID, prior_packet=paths["packet"], prior_receipt=paths["receipt"],
            failed_candidate=paths["candidate"], prior_qa=paths["qa"], ep8_manifest=paths["manifest"],
            ep8_reference=paths["reference"], user_mandate=paths["mandate"], actual_spend_usd="0.048",
        )


def test_validator_fails_closed_for_test_root_replay_and_rejected_asset_promotion(tmp_path: Path):
    from src.hybrid.episode_execution_policy import (
        EpisodeExecutionPolicyCompiler,
        EpisodePolicyError,
    )

    paths = _evidence(tmp_path)
    compiler = EpisodeExecutionPolicyCompiler()
    preflight = compiler.compile_quality_remediation(
        request_id=REQUEST_ID, prior_packet=paths["packet"], prior_receipt=paths["receipt"],
        failed_candidate=paths["candidate"], prior_qa=paths["qa"], ep8_manifest=paths["manifest"],
        ep8_reference=paths["reference"], user_mandate=paths["mandate"], actual_spend_usd="0.048",
    )
    with pytest.raises(EpisodePolicyError, match="TEST"):
        compiler.validate_for_dispatch(preflight, trust_root_kind="TEST", operation_id=preflight["operation"]["id"])
    with pytest.raises(EpisodePolicyError, match="authorization"):
        compiler.validate_for_dispatch(preflight, trust_root_kind="PRODUCTION", operation_id=preflight["operation"]["id"])
    promoted = json.loads(json.dumps(preflight))
    promoted["prior_attempt"]["promotion"] = "PROMOTED"
    with pytest.raises(EpisodePolicyError, match="rejected"):
        compiler.validate_for_dispatch(promoted, trust_root_kind="PRODUCTION", operation_id=preflight["operation"]["id"])
    with pytest.raises(EpisodePolicyError, match="replay"):
        compiler.validate_for_dispatch(preflight, trust_root_kind="PRODUCTION", operation_id="JACO_GEN28_STYLE_REMEDIATION_000")


def test_preflight_writer_is_exclusive_and_readback_verified(tmp_path: Path):
    from src.hybrid.episode_execution_policy import (
        EpisodeExecutionPolicyCompiler,
        EpisodePolicyError,
    )

    paths = _evidence(tmp_path)
    compiler = EpisodeExecutionPolicyCompiler()
    preflight = compiler.compile_quality_remediation(
        request_id=REQUEST_ID, prior_packet=paths["packet"], prior_receipt=paths["receipt"],
        failed_candidate=paths["candidate"], prior_qa=paths["qa"], ep8_manifest=paths["manifest"],
        ep8_reference=paths["reference"], user_mandate=paths["mandate"], actual_spend_usd="0.048",
    )
    path = tmp_path / "preflight.json"
    assert compiler.write_preflight(path, preflight)["operation"]["id"] == "JACO_GEN28_STYLE_REMEDIATION_001"
    with pytest.raises(EpisodePolicyError, match="write-once"):
        compiler.write_preflight(path, preflight)
