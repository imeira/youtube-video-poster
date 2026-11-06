"""Actual production evidence projection remains bounded and filesystem-only."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

REQUEST = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"


def _write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "production" / "jaco-flux3"
    _write(root / "user-mandate.json", {"request_id": REQUEST, "schema": "JACO_USER_EXECUTION_MANDATE/2", "revision": "r", "release_authorization": "NOT_AUTHORIZED", "license_status": "EVALUATION_ONLY_CONDITIONALLY_SUPPORTABLE"})
    _write(root / "execution" / "packet.json", {"bindings": {"request_id": REQUEST}})
    image = root / "execution" / "provider-receipt.quarantine.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"actual-image-bytes")
    _write(root / "execution" / "provider-receipt.json", {"actual_billed_amount": "UNKNOWN_UNTIL_AUTHENTICATED_BILLING_RECONCILIATION", "sha256": hashlib.sha256(image.read_bytes()).hexdigest()})
    _write(root / "execution" / "qa" / "provider-receipt.visual-qa.v1.json", {"verdict": "NOT_PROMOTED", "human_approval": "ABSENT", "quality_successor": "NOT_EXECUTED"})
    return root


def test_actual_evidence_projection_blocks_unpromoted_quarantine_without_sqlite(tmp_path: Path, monkeypatch):
    from src.hybrid.actual_deployment_status import ActualDeploymentStatus

    root = _root(tmp_path)
    monkeypatch.setattr("sqlite3.connect", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("no SQLite")))
    result = ActualDeploymentStatus(root).publish()

    assert result["status"] == "BLOCKED_DELIVERY_READINESS"
    assert result["actual_cost_usd"] == "UNKNOWN"
    assert result["remaining_usd"] == "UNKNOWN"
    assert result["visual_qa"] == "NOT_PROMOTED"
    assert result["identity"] == "UNKNOWN"
    assert result["license"] == "EVALUATION_ONLY_CONDITIONALLY_SUPPORTABLE"
    assert result["pending_human_approvals"] is False
    assert result["sources"]["execution/packet.json"] == hashlib.sha256((root / "execution" / "packet.json").read_bytes()).hexdigest()


def test_actual_evidence_projection_fails_closed_for_tamper_foreign_request_and_stale_output(tmp_path: Path):
    from src.hybrid.actual_deployment_status import ActualDeploymentStatus

    root = _root(tmp_path)
    status = ActualDeploymentStatus(root)
    assert status.publish()["status"] == "BLOCKED_DELIVERY_READINESS"

    root = _root(tmp_path / "tamper")
    image = root / "execution" / "provider-receipt.quarantine.png"
    image.write_bytes(b"tampered")
    assert ActualDeploymentStatus(root).publish()["status"] == "STALE"

    root = _root(tmp_path / "foreign")
    packet = root / "execution" / "packet.json"
    _write(packet, {"bindings": {"request_id": "other-episode"}})
    assert ActualDeploymentStatus(root).publish()["status"] == "STALE"

    root = _root(tmp_path / "second")
    output = root / "status" / "actual-deployment-status.json"
    _write(output, {"schema": "episode-policy-status/v1", "status": "BLOCKED_DELIVERY_READINESS"})
    assert ActualDeploymentStatus(root).inspect() == {"status": "STALE", "reason": "INVALID"}


def test_inspect_rejects_boolean_generation_even_with_recomputed_hash(tmp_path: Path):
    from src.hybrid.actual_deployment_status import ActualDeploymentStatus

    root = _root(tmp_path)
    status = ActualDeploymentStatus(root)
    document = status.publish()
    document["generation"] = True
    body = {key: value for key, value in document.items() if key != "snapshot_sha256"}
    document["snapshot_sha256"] = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    _write(root / "status" / "actual-deployment-status.json", document)

    assert status.inspect() == {"status": "STALE", "reason": "INVALID"}
