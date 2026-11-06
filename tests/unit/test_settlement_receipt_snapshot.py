"""Read-only canonical settlement receipt snapshots and filesystem adapters."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

OUTER_REQUEST = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
LOCAL_REQUEST = "7f19aab26d71464a9bc257f4f566d2c6cb642aab0bc55864bad8dcbaeab0ae47"
REMOTE_REQUEST = "01a1b229-3ac4-78f2-a320-73ee43fc8bc2"
ENDPOINT = "blackforestlabs/flux-3/text-to-image"
AUTH_SCOPE = "JACO_FLUX3_FINAL_ADMISSION:a32871d181a22a45291a526c29934abf3fa2a9aea17f30cf881832dd8cebf170"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _write(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(document))


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "production" / "jaco-flux3"
    _write(root / "user-mandate.json", {"request_id": OUTER_REQUEST, "schema": "JACO_USER_EXECUTION_MANDATE/2", "license_status": "EVALUATION_ONLY_CONDITIONALLY_SUPPORTABLE"})
    _write(root / "execution" / "packet.json", {"bindings": {"request_id": OUTER_REQUEST, "endpoint": ENDPOINT}})
    _write(root / "final-admission-authorization.json", {"request_id": OUTER_REQUEST, "endpoint": ENDPOINT})
    _write(root / "financial-record-source.json", {"request_hash": OUTER_REQUEST, "status": "APPROVED_USD10", "total_episode_cap": "10.00"})
    image = root / "execution" / "provider-receipt.quarantine.png"
    image.parent.mkdir(parents=True, exist_ok=True)
    image.write_bytes(b"actual-image-bytes")
    _write(root / "execution" / "provider-receipt.json", {"actual_billed_amount": "UNKNOWN_UNTIL_AUTHENTICATED_BILLING_RECONCILIATION", "sha256": hashlib.sha256(image.read_bytes()).hexdigest()})
    _write(root / "execution" / "qa" / "provider-receipt.visual-qa.v1.json", {"verdict": "NOT_PROMOTED"})
    return root


def _ledger(root: Path, *, foreign: bool = False, tamper: bool = False) -> Path:
    path = root / "ledger" / "standing-budget.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {"schema": 2, "authority": "TRANSPORT_AUTHENTICATED_FAL_READBACK", "local_request_id": LOCAL_REQUEST, "provider_request_id": REMOTE_REQUEST, "endpoint_id": ENDPOINT, "auth_scope": AUTH_SCOPE, "authority_mode": "LIVE", "currency": "USD", "cost_total_micro_usd": "48000", "evidence_hash": "a" * 64, "provenance": "FAL_BILLING_READBACK"}
    body = _canonical(receipt).decode("utf-8")
    digest = hashlib.sha256(body.encode()).hexdigest()
    if tamper:
        digest = "b" * 64
    if foreign:
        receipt["provider_request_id"] = "foreign"
        body = _canonical(receipt).decode("utf-8")
        digest = hashlib.sha256(body.encode()).hexdigest()
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE claims (request_id TEXT PRIMARY KEY, provider TEXT, state TEXT, actual INTEGER, auth_scope TEXT, provider_request_id TEXT, authority_mode TEXT);
            CREATE TABLE settlement_receipts (request_id TEXT PRIMARY KEY, receipt_sha256 TEXT, actual INTEGER, receipt_body TEXT, provenance TEXT);
        """)
        db.execute("INSERT INTO claims VALUES (?, ?, 'SETTLED', 48000, ?, ?, 'LIVE')", (LOCAL_REQUEST, ENDPOINT, AUTH_SCOPE, REMOTE_REQUEST))
        db.execute("INSERT INTO settlement_receipts VALUES (?, ?, 48000, ?, 'FAL_BILLING_READBACK')", (LOCAL_REQUEST, digest, body))
    return path


def test_read_only_publisher_exports_bound_canonical_settlement_receipt(tmp_path: Path):
    from src.hybrid.settlement_receipt_snapshot import SettlementReceiptSnapshotPublisher

    root = _root(tmp_path)
    _ledger(root)
    result = SettlementReceiptSnapshotPublisher(root).publish()

    assert result["status"] == "BOUNDED_OBSERVED"
    assert result["claim_state"] == "SETTLED"
    assert result["actual_micro_usd"] == "48000"
    assert result["outer_request_id"] == OUTER_REQUEST
    assert result["remote_request_id"] == REMOTE_REQUEST
    assert (root / "status" / "canonical-settlement-receipt.json").is_file()


def test_publisher_rejects_foreign_or_tampered_receipts(tmp_path: Path):
    from src.hybrid.settlement_receipt_snapshot import SettlementReceiptSnapshotPublisher

    root = _root(tmp_path / "foreign")
    _ledger(root, foreign=True)
    assert SettlementReceiptSnapshotPublisher(root).publish()["status"] == "STALE"

    root = _root(tmp_path / "tamper")
    _ledger(root, tamper=True)
    assert SettlementReceiptSnapshotPublisher(root).publish()["status"] == "STALE"


def test_filesystem_status_uses_valid_settlement_over_stale_provider_unknown_without_sqlite(tmp_path: Path, monkeypatch):
    from src.hybrid.actual_deployment_status import ActualDeploymentStatus
    from src.hybrid.settlement_receipt_snapshot import SettlementReceiptSnapshotPublisher

    root = _root(tmp_path)
    _ledger(root)
    SettlementReceiptSnapshotPublisher(root).publish()
    monkeypatch.setattr("sqlite3.connect", lambda *_a, **_kw: (_ for _ in ()).throw(AssertionError("status must not open SQLite")))
    result = ActualDeploymentStatus(root).publish()

    assert result["financial_status"] == "SETTLED"
    assert result["financial_observation"] == "BOUNDED_OBSERVED"
    assert result["actual_cost_usd"] == "0.048"
    assert result["remaining_usd"] == "9.952"
    assert result["status"] == "BLOCKED_DELIVERY_READINESS"
    assert result["pending_human_approvals"] is False


def test_filesystem_status_treats_tampered_or_expired_snapshot_as_unknown(tmp_path: Path):
    from src.hybrid.actual_deployment_status import ActualDeploymentStatus
    from src.hybrid.settlement_receipt_snapshot import SettlementReceiptSnapshotPublisher

    root = _root(tmp_path / "tamper")
    _ledger(root)
    snapshot = SettlementReceiptSnapshotPublisher(root).publish()
    snapshot["canonical_receipt_sha256"] = "0" * 64
    snapshot["snapshot_sha256"] = hashlib.sha256(_canonical({key: value for key, value in snapshot.items() if key != "snapshot_sha256"})).hexdigest()
    _write(root / "status" / "canonical-settlement-receipt.json", snapshot)
    assert ActualDeploymentStatus(root).publish()["financial_status"] == "UNKNOWN"

    root = _root(tmp_path / "expired")
    _ledger(root)
    snapshot = SettlementReceiptSnapshotPublisher(root).publish()
    snapshot["observed_at_utc"] = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    snapshot["snapshot_sha256"] = hashlib.sha256(_canonical({key: value for key, value in snapshot.items() if key != "snapshot_sha256"})).hexdigest()
    _write(root / "status" / "canonical-settlement-receipt.json", snapshot)
    assert ActualDeploymentStatus(root).publish()["financial_status"] == "UNKNOWN"
