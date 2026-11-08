from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_failed_readonly_guard_never_constructs_ledger_or_resolves_credential(monkeypatch, tmp_path):
    """A missing durable DB blocks before any mutable constructor or credential."""
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    calls = []
    monkeypatch.setattr(settlement, "_readonly_snapshot", lambda: (_ for _ in ()).throw(
        settlement.ProductionAdmissionError("durable model-sheet execution receipt is unavailable")
    ))
    monkeypatch.setattr(settlement, "StandingBudgetLedger", lambda *a, **kw: calls.append("ledger"))
    monkeypatch.setattr(settlement, "Executor", lambda *a, **kw: calls.append("executor"))
    monkeypatch.setattr(settlement, "resolve_default_fal_credential", lambda: calls.append("credential"))
    monkeypatch.setattr(settlement, "AiohttpFalBillingClient", lambda *a, **kw: calls.append("http"))

    with pytest.raises(settlement.ProductionAdmissionError, match="durable"):
        asyncio.run(settlement.reconcile())

    assert calls == []
    assert not (tmp_path / "ledger.sqlite3").exists()
    assert not (tmp_path / "executor.sqlite3").exists()


def _live_completion_job(settlement):
    return SimpleNamespace(
        scene="JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001",
        request_id=settlement.LOCAL_REQUEST_ID,
        mode="LIVE",
        endpoint="blackforestlabs/flux-3/text-to-image",
    )


def test_completion_rejects_an_identical_copy_before_opening_it(tmp_path, monkeypatch):
    """The completion raster has one canonical protected name, never a caller path."""
    import src.hybrid.young_identity_model_sheet_production as production
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    monkeypatch.setattr(production, "ROOT", tmp_path)
    alternate = tmp_path / "same-bytes-copy.png"
    canonical = tmp_path / "execution" / production.OPERATION_ID / "provider-receipt.quarantine.png"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"canonical-fixture-media")
    # The alternate is byte-identical to the fixture's immutable media;
    # identity is still the canonical path, checked before any file read.
    alternate.write_bytes(canonical.read_bytes())
    reads = []
    original = Path.read_bytes

    def reject_alternate_read(path):
        if path == alternate:
            reads.append(path)
            raise AssertionError("alternate media was opened")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", reject_alternate_read)
    with pytest.raises(PermissionError, match="fixed canonical quarantine path"):
        settlement.validate_model_sheet_completion(
            tmp_path / "execution" / production.OPERATION_ID / "executor.sqlite3",
            tmp_path / "ledger" / "standing-budget.sqlite3",
            _live_completion_job(settlement),
            alternate,
        )
    assert reads == []


def test_completion_rejects_a_symlink_at_the_canonical_quarantine_path_before_open(tmp_path, monkeypatch):
    """A canonical spelling cannot conceal a symlink or Windows reparse hop."""
    import src.hybrid.young_identity_model_sheet_production as production
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    monkeypatch.setattr(production, "ROOT", tmp_path)
    canonical = tmp_path / "execution" / production.OPERATION_ID / "provider-receipt.quarantine.png"
    canonical.parent.mkdir(parents=True)
    target = tmp_path / "copied-result.png"
    target.write_bytes(b"identical-but-linked")
    try:
        os.symlink(target, canonical)
    except OSError as exc:
        pytest.skip(f"symlink unavailable in this Windows test environment: {exc}")
    reads = []
    original = Path.read_bytes

    def reject_canonical_read(path):
        if path == canonical:
            reads.append(path)
            raise AssertionError("reparse-point media was opened")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", reject_canonical_read)
    with pytest.raises(PermissionError, match="fixed canonical quarantine path"):
        settlement.validate_model_sheet_completion(
            tmp_path / "execution" / production.OPERATION_ID / "executor.sqlite3",
            tmp_path / "ledger" / "standing-budget.sqlite3",
            _live_completion_job(settlement),
            canonical,
        )
    assert reads == []


def test_reconcile_uses_durable_submission_timestamp_and_current_utc_end(monkeypatch):
    """The caller cannot choose a narrow billing window that omits the claim."""
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    submitted = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    now = submitted + timedelta(minutes=2)
    observed = {}

    class Service:
        async def reconcile(self, request_id, *, start, end):
            observed.update(request_id=request_id, start=start, end=end)
            return {"status": "SETTLED"}

    snapshot = SimpleNamespace(submission_at=submitted, ledger_path="ignored")
    monkeypatch.setattr(settlement, "_readonly_snapshot", lambda: snapshot)
    monkeypatch.setattr(settlement, "_readonly_ledger_snapshot", lambda _path: object())
    monkeypatch.setattr(settlement, "_prove_settlement", lambda _snapshot, _before: None)
    monkeypatch.setattr(settlement, "_construct_service", lambda value, credential: Service())
    monkeypatch.setattr(settlement, "resolve_default_fal_credential", lambda: ("non-secret", "CONFIGURED"))
    monkeypatch.setattr(settlement, "_utc_now", lambda: now)

    async def recover(_value):
        return {"status": "COMPLETE"}

    monkeypatch.setattr(settlement, "_recover_after_settlement", recover)

    result = asyncio.run(settlement.reconcile())

    assert result["status"] == "SETTLED"
    assert observed == {
        "request_id": settlement.LOCAL_REQUEST_ID,
        "start": submitted,
        "end": now,
    }


def test_settlement_runs_only_existing_get_recovery_and_reports_unknown_completion(monkeypatch):
    """Settlement never marks the executor COMPLETE itself or invokes POST/review."""
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    submitted = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    now = submitted + timedelta(minutes=1)
    calls = []

    class Service:
        async def reconcile(self, *_args, **_kwargs):
            calls.append("billing-get")
            return {"status": "SETTLED", "claim": {"state": "SETTLED"}}

    snapshot = SimpleNamespace(submission_at=submitted, ledger_path="ignored")
    monkeypatch.setattr(settlement, "_readonly_snapshot", lambda: snapshot)
    monkeypatch.setattr(settlement, "_readonly_ledger_snapshot", lambda _path: object())
    monkeypatch.setattr(settlement, "_prove_settlement", lambda _snapshot, _before: None)
    monkeypatch.setattr(settlement, "_construct_service", lambda value, credential: Service())
    monkeypatch.setattr(settlement, "resolve_default_fal_credential", lambda: ("non-secret", "CONFIGURED"))
    monkeypatch.setattr(settlement, "_utc_now", lambda: now)

    async def recover(_value):
        calls.append("recovery-get")
        return {"status": "SUBMITTED"}

    monkeypatch.setattr(settlement, "_recover_after_settlement", recover)

    result = asyncio.run(settlement.reconcile())

    assert calls == ["billing-get", "recovery-get"]
    assert result["completion"] == {"status": "SUBMITTED"}


def test_settled_service_return_without_canonical_write_never_reaches_recovery(monkeypatch):
    """A successful service return is not settlement proof; readback must be durable."""
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    submitted = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    calls = []

    class Service:
        async def reconcile(self, *_args, **_kwargs):
            calls.append("billing-get")
            return {"status": "SETTLED"}

    snapshot = SimpleNamespace(submission_at=submitted, ledger_path="ignored")
    monkeypatch.setattr(settlement, "_readonly_snapshot", lambda: snapshot)
    monkeypatch.setattr(settlement, "_readonly_ledger_snapshot", lambda _path: object())
    monkeypatch.setattr(settlement, "_construct_service", lambda _value, _credential: Service())
    monkeypatch.setattr(settlement, "resolve_default_fal_credential", lambda: ("non-secret", "CONFIGURED"))
    monkeypatch.setattr(settlement, "_utc_now", lambda: submitted + timedelta(minutes=1))
    monkeypatch.setattr(settlement, "_prove_settlement", lambda _snapshot, _before: (_ for _ in ()).throw(
        settlement.ProductionAdmissionError("canonical current settlement receipt is unavailable")
    ))

    async def forbidden_recovery(_snapshot):
        calls.append("recovery-get")
        raise AssertionError("recovery must not run")

    monkeypatch.setattr(settlement, "_recover_after_settlement", forbidden_recovery)
    with pytest.raises(settlement.ProductionAdmissionError, match="canonical"):
        asyncio.run(settlement.reconcile())
    assert calls == ["billing-get"]



def test_verified_settled_replay_has_no_credential_or_billing_transport(monkeypatch):
    """A canonical SETTLED replay is local completion recovery, never a new GET."""
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    submitted = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    snapshot = SimpleNamespace(submission_at=submitted, ledger_path="ignored", already_settled=True)
    calls = []
    monkeypatch.setattr(settlement, "_readonly_snapshot", lambda: snapshot)
    monkeypatch.setattr(settlement, "_readonly_ledger_snapshot", lambda _path: object())
    monkeypatch.setattr(settlement, "_prove_settlement", lambda _snapshot, _before: calls.append("proof"))
    monkeypatch.setattr(settlement, "resolve_default_fal_credential", lambda: calls.append("credential"))
    monkeypatch.setattr(settlement, "_construct_service", lambda *_: calls.append("service"))

    async def recover(_snapshot):
        calls.append("recovery")
        return {"status": "COMPLETE", "result_sha256": "a" * 64}

    monkeypatch.setattr(settlement, "_recover_after_settlement", recover)
    result = asyncio.run(settlement.reconcile())

    assert result["status"] == "SETTLED"
    assert result["completion"]["status"] == "COMPLETE"
    assert calls == ["proof", "recovery"]


def test_public_live_fixture_settles_then_completes_in_guarded_fresh_cli_process(tmp_path):
    """A real v3 LIVE fixture reaches validator-backed COMPLETE without transport."""
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    module = "tests.unit.test_young_identity_model_sheet_production"
    submit = [sys.executable, "-c", f"from {module} import _amended_live_recovery_child as f; import sys; f(sys.argv[1], sys.argv[2], sys.argv[3])", str(tmp_path), "submit", settlement.REMOTE_REQUEST_ID]
    first = subprocess.run(submit, text=True, capture_output=True, check=False, timeout=60)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["result"] == "CHECKPOINTED"

    execution = tmp_path / "execution" / "JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001"
    media = execution / "provider-receipt.quarantine.png"
    media.write_bytes(b"hermetic canonical model-sheet media")
    media_sha = sha256(media.read_bytes()).hexdigest()
    (execution / "provider-receipt.json").write_text(json.dumps({"sha256": media_sha, "kind": "PNG_RESULT", "actual_billed_amount": "UNKNOWN_UNTIL_BILLING_RECONCILIATION"}, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    (execution / "provider-receipt.recovery.json").write_text(json.dumps({"schema": 1, "provider_request_id": settlement.REMOTE_REQUEST_ID, "status_url": f"https://queue.fal.run/blackforestlabs/flux-3/requests/{settlement.REMOTE_REQUEST_ID}/status", "response_url": f"https://queue.fal.run/blackforestlabs/flux-3/requests/{settlement.REMOTE_REQUEST_ID}"}, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    ledger_path = tmp_path / "ledger" / "standing-budget.sqlite3"
    with sqlite3.connect(ledger_path) as db:
        scope = db.execute("SELECT auth_scope FROM claims WHERE request_id=?", (settlement.LOCAL_REQUEST_ID,)).fetchone()[0]
        receipt = {"schema": 2, "authority": "TRANSPORT_AUTHENTICATED_FAL_READBACK", "local_request_id": settlement.LOCAL_REQUEST_ID, "provider_request_id": settlement.REMOTE_REQUEST_ID, "endpoint_id": "blackforestlabs/flux-3/text-to-image", "auth_scope": scope, "authority_mode": "LIVE", "currency": "USD", "cost_total_micro_usd": "48000", "evidence_hash": "b" * 64, "provenance": "FAL_BILLING_READBACK"}
        receipt_body = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        db.execute("UPDATE claims SET state='SETTLED', actual=48000 WHERE request_id=?", (settlement.LOCAL_REQUEST_ID,))
        db.execute("INSERT INTO settlement_receipts VALUES(?,?,?,?,?)", (settlement.LOCAL_REQUEST_ID, sha256(receipt_body.encode()).hexdigest(), 48000, receipt_body, "FAL_BILLING_READBACK"))

    child = """
import sys
from hashlib import sha256
from pathlib import Path
from scripts.run_offline_tests import audit
sys.addaudithook(audit)
root = Path(sys.argv[1])
import src.hybrid.jaco_flux3_production as canonical
import src.hybrid.young_identity_model_sheet_production as production
import src.hybrid.young_identity_model_sheet_settlement as settlement
production.ROOT = root
production.AUTHORITY_PATH = root / 'standing-policy' / 'JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001.authority.v2.json'
production.FINANCIAL_SHA256 = sha256((root / 'financial-record-source.json').read_bytes()).hexdigest()
canonical._CANONICAL_STATE_DB = root / 'canonical-state.sqlite3'
media = root / 'execution' / production.OPERATION_ID / 'provider-receipt.quarantine.png'
settlement.APPROVED_EXECUTION_CODE_SHA256 = sha256(Path(production.__file__).read_bytes()).hexdigest()
settlement.APPROVED_QUARANTINED_PNG_SHA256 = sha256(media.read_bytes()).hexdigest()
with __import__('sqlite3').connect(root / 'ledger' / 'standing-budget.sqlite3') as db:
    settlement.APPROVED_SETTLEMENT_RECEIPT_SHA256 = db.execute('SELECT receipt_sha256 FROM settlement_receipts WHERE request_id=?', (settlement.LOCAL_REQUEST_ID,)).fetchone()[0]
raise SystemExit(settlement.main(['--reconcile']))
"""
    replay = subprocess.run([sys.executable, "-c", child, str(tmp_path)], text=True, capture_output=True, check=False, timeout=60, env={**os.environ, "PYTHONPATH": str(Path.cwd())})
    assert replay.returncode == 0, replay.stderr
    result = json.loads(replay.stdout)
    assert result == {"completion": {"request_id": settlement.LOCAL_REQUEST_ID, "result_sha256": media_sha, "status": "COMPLETE"}, "request_id": settlement.LOCAL_REQUEST_ID, "status": "SETTLED"}
    with sqlite3.connect(execution / "executor.sqlite3") as db:
        row = json.loads(db.execute("SELECT data FROM jobs").fetchone()[0])
    assert row["status"] == "COMPLETE"
    assert row["result_sha256"] == media_sha


def test_main_rejects_caller_selected_billing_window():
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    with pytest.raises(SystemExit):
        settlement.main(["--reconcile", "--start", "2026-11-07T10:00:00+00:00"])
