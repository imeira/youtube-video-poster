from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
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

    snapshot = SimpleNamespace(submission_at=submitted)
    monkeypatch.setattr(settlement, "_readonly_snapshot", lambda: snapshot)
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

    snapshot = SimpleNamespace(submission_at=submitted)
    monkeypatch.setattr(settlement, "_readonly_snapshot", lambda: snapshot)
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


def test_main_rejects_caller_selected_billing_window():
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    with pytest.raises(SystemExit):
        settlement.main(["--reconcile", "--start", "2026-11-07T10:00:00+00:00"])
