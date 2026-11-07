from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _authority(root: Path) -> tuple[Path, Path]:
    protected = root / "standing-policy"
    preflight = root / "preflight" / "JACO_GEN28_STYLE_REMEDIATION_001.preflight.json"
    _write(preflight, {"schema": "jaco-episode-operation-preflight/v1", "episode_id": "JACO_GENESIS28", "request_id": "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9", "operation": {"id": "JACO_GEN28_STYLE_REMEDIATION_001", "endpoint": "blackforestlabs/flux-3/text-to-image", "call_limit": 1, "maximum_usd": "0.048", "payload": {"prompt": "remediate", "aspect_ratio": "16:9", "resolution": "1k", "output_format": "png", "enable_prompt_expansion": False, "safety_tolerance": 2}}})
    policy = _write(protected / "JACO_GENESIS28_STANDING_OPERATION_POLICY.v1.json", {"episode_id": "JACO_GENESIS28", "canonical_consent": {"session_id": "local-712d007e4f3efba4d8c6cfc52f947e69023fed6f445a54206a6c3ede9b317fbb", "consent_message_id": 147620, "prompt_message_id": 147618}, "financial_baseline": {"episode_cap_usd": "10.000000", "remaining_usd": "9.952000", "pending_usd": "0.000000"}})
    authority = _write(protected / "JACO_GEN28_STYLE_REMEDIATION_001.authority.v1.json", {"schema": "jaco-derived-operation-authority/v1", "status": "READY_FOR_INDEPENDENT_PARENT_REVIEW_NO_RESERVATION_NO_POST", "operation_id": "JACO_GEN28_STYLE_REMEDIATION_001", "episode_id": "JACO_GENESIS28", "maximum_next_cost_usd": "0.048000", "policy_path": str(policy), "policy_sha256": _sha(policy), "preflight_path": str(preflight), "preflight_sha256": _sha(preflight), "budget_check": {"actual_usd": "0.048000", "pending_usd": "0.000000", "episode_cap_usd": "10.000000", "next_cap_usd": "0.048000", "post_reservation_exposure_usd": "0.096000"}})
    return policy, authority


def test_operation_factory_makes_fresh_immutable_review_packet_from_fixed_authority(tmp_path):
    from src.hybrid.jaco_style_remediation_operation import OperationAdmissionFactory

    _policy, authority = _authority(tmp_path)
    factory = OperationAdmissionFactory(root=tmp_path, authority_path=authority)
    artifact = factory.build_review_packet(quote={"price": "0.048"}, openapi={"endpoint": "blackforestlabs/flux-3/text-to-image"})

    assert artifact["status"] == "READY_FOR_INDEPENDENT_PARENT_REVIEW_NO_RESERVATION_NO_POST"
    assert artifact["operation_id"] == "JACO_GEN28_STYLE_REMEDIATION_001"
    assert artifact["budget"] == {"actual_usd": "0.048000", "pending_usd": "0.000000", "next_cap_usd": "0.048000", "episode_cap_usd": "10.000000", "post_reservation_exposure_usd": "0.096000"}
    assert Path(artifact["packet_path"]).is_file()
    assert artifact["packet_sha256"] == _sha(Path(artifact["packet_path"]))


@pytest.mark.parametrize("change", ["old_packet", "consumed_authority", "wrong_operation", "scope", "per_job_reset", "replay"])
def test_operation_factory_rejects_old_or_mutated_authority_before_review_artifact(tmp_path, change):
    from src.hybrid.jaco_style_remediation_operation import (
        OperationAdmissionError,
        OperationAdmissionFactory,
    )

    _policy, authority_path = _authority(tmp_path)
    authority = json.loads(authority_path.read_text())
    if change == "old_packet":
        authority["preflight_sha256"] = "0" * 64
    elif change == "consumed_authority":
        authority["status"] = "CONSUMED"
    elif change == "wrong_operation":
        authority["operation_id"] = "JACO_GEN28_CANONICAL_001"
    elif change == "scope":
        authority["episode_id"] = "OTHER"
    elif change == "per_job_reset":
        authority["budget_check"]["actual_usd"] = "0.000000"
    else:
        run = tmp_path / "operation-runs" / "existing"
        run.mkdir(parents=True)
        (run / "review-packet.json").write_text("{}", encoding="utf-8")
    _write(authority_path, authority)

    with pytest.raises(OperationAdmissionError):
        OperationAdmissionFactory(root=tmp_path, authority_path=authority_path).build_review_packet(quote={"price": "0.048"}, openapi={"endpoint": "blackforestlabs/flux-3/text-to-image"})
    assert not list((tmp_path / "operation-runs").glob("*/review-packet.json")) if change != "replay" else True
