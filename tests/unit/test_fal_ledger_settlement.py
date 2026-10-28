"""Vertical FAL readback-to-ledger settlement contract."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.hybrid.execution import Executor, Job, ProviderResult
from src.hybrid.fal_billing import FalBillingConsumer, LoopbackFalBillingFixture
from src.hybrid.planner import Config
from src.hybrid.standing_budget import BudgetDenied, StandingBudgetLedger

START = datetime(2026, 10, 26, 11, 59, tzinfo=UTC)
END = datetime(2026, 10, 26, 12, 1, tzinfo=UTC)


class FixtureProvider:
    mode = "TEST"

    def __init__(self, root):
        self.root = root

    async def submit(self, job, request_id, checkpoint):
        checkpoint(provider_id=request_id)
        result = self.root / "output.bin"
        result.write_bytes(b"fixture")
        return ProviderResult(result, Decimal("0.045"))

    async def recover(self, *args):
        raise AssertionError("not reached")


def _manifest(tmp_path):
    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet

    image = tmp_path / "reference.png"
    Image.new("RGB", (8, 8), "white").save(image)
    frozen = FrozenAsset.approve(image, "test", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((frozen,), sheet)
    return Manifest.freeze(
        (frozen,), FrozenAsset.approve(sheet, "test", "TEST"), "test", "TEST"
    )


async def _bound_receipt(tmp_path):
    job = Job(
        "S001",
        "first",
        "TEST",
        "fal-ai/flux/dev",
        {"prompt": "fixture"},
        _manifest(tmp_path),
        Decimal("0.05"),
    )
    ledger = StandingBudgetLedger(
        tmp_path / "admission.sqlite3",
        policy_sha256="a" * 64,
        episode_limit_usd="1",
        month_limit_usd="1",
        experiment_limit_usd="1",
        mode="TEST",
    )
    executor = Executor(
        tmp_path / "executor.sqlite3", Config(limit=Decimal(1)), standing_ledger=ledger
    )
    prepared = executor.prepare_intent(job)
    ledger.reserve(
        job.request_id,
        "TEST-EP",
        job.endpoint,
        job.cost,
        settlement_mode="FAL_READBACK_V2",
        auth_scope=prepared["settlement_binding"]["auth_scope"],
        provider_request_id="UNASSIGNED",
        authority_mode="TEST",
    )
    receipt = await executor.run(
        job,
        FixtureProvider(tmp_path),
        settlement_checkpoint=lambda local_id, remote_id: ledger.bind_provider_request_id(
            local_id, remote_id
        ),
        submit_prepared=True,
    )
    return executor, job, receipt


def _ledger(tmp_path, *, mode="TEST"):
    return StandingBudgetLedger(
        tmp_path / "ledger.sqlite3",
        policy_sha256="a" * 64,
        episode_limit_usd="1",
        month_limit_usd="1",
        experiment_limit_usd="1",
        mode=mode,
    )


def _event(request_id, endpoint, *, cost="0.045", nano=45_000_000):
    return {
        "request_id": request_id,
        "endpoint_id": endpoint,
        "timestamp": "2026-10-26T12:00:00Z",
        "quantity": 1,
        "output_units": 1,
        "unit": "image",
        "unit_price": 0.045,
        "percent_discount": 0,
        "cost_subtotal": 0.045,
        "cost_discount": 0,
        "cost_total": float(cost),
        "cost_estimate_nano_usd": nano,
    }


@pytest.mark.asyncio
async def test_bound_test_readback_settles_once_and_replay_is_idempotent(tmp_path):
    from src.hybrid.billing_reconciler import FalLedgerSettlementService

    executor, job, receipt = await _bound_receipt(tmp_path)
    ledger = _ledger(tmp_path)
    fixture = LoopbackFalBillingFixture(
        pricing_pages=[],
        billing_pages=[
            {
                "billing_events": [_event(job.request_id, job.endpoint)],
                "next_cursor": None,
                "has_more": False,
            },
            {
                "billing_events": [_event(job.request_id, job.endpoint)],
                "next_cursor": None,
                "has_more": False,
            },
        ],
    )
    service = FalLedgerSettlementService(
        ledger, executor, FalBillingConsumer(fixture), clock=lambda: END
    )
    service.reserve_from_executor(job.request_id, episode_id="TEST-EP", quoted_cap_usd=job.cost)

    first, second = await asyncio.gather(
        service.reconcile(job.request_id, start=START, end=END),
        service.reconcile(job.request_id, start=START, end=END),
    )

    assert receipt["receipt_schema"] == 2
    assert first["status"] == "SETTLED"
    assert first == second
    assert ledger.exposure()["episode_usd"] == {"TEST-EP": "0.045000"}


@pytest.mark.asyncio
async def test_same_billed_event_with_different_utc_windows_keeps_one_canonical_settlement_receipt(tmp_path):
    from src.hybrid.billing_reconciler import FalLedgerSettlementService

    executor, job, _ = await _bound_receipt(tmp_path)
    ledger = _ledger(tmp_path)
    pages = [
        {"billing_events": [_event(job.request_id, job.endpoint)], "next_cursor": None, "has_more": False},
        {"billing_events": [_event(job.request_id, job.endpoint)], "next_cursor": None, "has_more": False},
    ]
    service = FalLedgerSettlementService(
        ledger, executor, FalBillingConsumer(LoopbackFalBillingFixture(pricing_pages=[], billing_pages=pages)),
        clock=lambda: END + timedelta(minutes=1),
    )
    service.reserve_from_executor(job.request_id, episode_id="TEST-EP", quoted_cap_usd=job.cost)
    first = await service.reconcile(job.request_id, start=START, end=END)
    second = await service.reconcile(
        job.request_id, start=START - timedelta(minutes=1), end=END + timedelta(minutes=1)
    )

    assert first["receipt_sha256"] == second["receipt_sha256"]
    with ledger._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM settlement_receipts").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_missing_or_untrusted_binding_never_settles_another_claim(tmp_path):
    from src.hybrid.billing_reconciler import (
        FalLedgerSettlementService,
        SettlementDenied,
    )

    executor, job, _ = await _bound_receipt(tmp_path)
    ledger = _ledger(tmp_path)
    ledger.reserve(job.request_id, "TEST-EP", job.endpoint, job.cost)
    with executor._db() as db:
        row = executor.inspect(job.request_id)
        row.pop("settlement_binding")
        executor._put(db, row)
    fixture = LoopbackFalBillingFixture(pricing_pages=[], billing_pages=[])
    service = FalLedgerSettlementService(
        ledger, executor, FalBillingConsumer(fixture), clock=lambda: END
    )

    with pytest.raises(SettlementDenied, match="binding"):
        await service.reconcile(job.request_id, start=START, end=END)
    assert ledger.mark_unknown(job.request_id)["state"] == "UNKNOWN"
    assert fixture.billing_queries == []


@pytest.mark.asyncio
async def test_fixture_cannot_settle_live_ledger_and_pending_preserves_unknown(
    tmp_path,
):
    from src.hybrid.billing_reconciler import (
        FalLedgerSettlementService,
        SettlementDenied,
    )

    executor, job, _ = await _bound_receipt(tmp_path)
    live = _ledger(tmp_path, mode="LIVE")
    with pytest.raises(BudgetDenied, match="authority mode"):
        live.reserve(job.request_id, "EP", job.endpoint, job.cost)
    fixture = LoopbackFalBillingFixture(pricing_pages=[], billing_pages=[])
    service = FalLedgerSettlementService(
        live, executor, FalBillingConsumer(fixture), clock=lambda: END
    )
    with pytest.raises(SettlementDenied, match="reservation required"):
        await service.reconcile(job.request_id, start=START, end=END)


@pytest.mark.asyncio
async def test_inexact_money_or_stale_window_marks_claim_unknown_without_settlement(
    tmp_path,
):
    from src.hybrid.billing_reconciler import FalLedgerSettlementService

    executor, job, _ = await _bound_receipt(tmp_path)
    ledger = _ledger(tmp_path)
    fixture = LoopbackFalBillingFixture(
        pricing_pages=[],
        billing_pages=[
            {
                "billing_events": [
                    _event(
                        job.request_id, job.endpoint, cost="0.045001", nano=45_000_000
                    )
                ],
                "next_cursor": None,
                "has_more": False,
            }
        ],
    )
    service = FalLedgerSettlementService(
        ledger, executor, FalBillingConsumer(fixture), clock=lambda: END
    )
    service.reserve_from_executor(job.request_id, episode_id="TEST-EP", quoted_cap_usd=job.cost)

    result = await service.reconcile(job.request_id, start=START, end=END)
    stale = await FalLedgerSettlementService(
        ledger,
        executor,
        FalBillingConsumer(fixture),
        clock=lambda: END + timedelta(seconds=301),
    ).reconcile(job.request_id, start=START, end=END)
    assert result["status"] == "SETTLED"
    # estimate metadata is non-authoritative; the fresh readback total settled.
    assert stale["status"] == "UNKNOWN"
    assert ledger.exposure()["episode_usd"] == {"TEST-EP": "0.045001"}


@pytest.mark.asyncio
async def test_authenticated_settlement_overrun_freezes_future_reservations(tmp_path):
    from src.hybrid.billing_reconciler import FalLedgerSettlementService

    executor, job, _ = await _bound_receipt(tmp_path)
    ledger = _ledger(tmp_path)
    fixture = LoopbackFalBillingFixture(
        pricing_pages=[],
        billing_pages=[
            {
                "billing_events": [_event(job.request_id, job.endpoint)],
                "next_cursor": None,
                "has_more": False,
            }
        ],
    )
    service = FalLedgerSettlementService(
        ledger, executor, FalBillingConsumer(fixture), clock=lambda: END
    )
    service.reserve_from_executor(job.request_id, episode_id="TEST-EP", quoted_cap_usd="0.04")
    result = await service.reconcile(job.request_id, start=START, end=END)

    assert result["status"] == "SETTLED"
    assert ledger.exposure()["frozen"] is True
    with pytest.raises(BudgetDenied, match="overrun"):
        ledger.reserve("future", "TEST-EP", job.endpoint, "0.01")


@pytest.mark.asyncio
async def test_remote_provider_id_replaces_unassigned_before_recovery_and_is_immutable(tmp_path):
    class RemoteIdProvider(FixtureProvider):
        async def submit(self, job, request_id, checkpoint):
            checkpoint(provider_id="fal-remote-42")
            raise RuntimeError("crash after durable remote checkpoint")

        async def recover(self, job, request_id, provider_id, partial, checkpoint):
            assert provider_id == "fal-remote-42"
            result = self.root / "output.bin"
            result.write_bytes(b"recovered")
            return ProviderResult(result, Decimal("0.045"))

    executor, job, _ = await _bound_receipt(tmp_path)
    # Use a second identity to create the crash state rather than reusing COMPLETE.
    job = Job(job.scene + "B", job.category, job.mode, "test-remote", job.payload, job.manifest, job.cost)
    with pytest.raises(RuntimeError, match="crash"):
        await executor.run(job, RemoteIdProvider(tmp_path))
    interrupted = executor.inspect(job.request_id)
    assert interrupted["settlement_binding"]["provider_request_id"] == "fal-remote-42"
    assert interrupted["provider_id"] == "fal-remote-42"
    completed = await executor.run(job, RemoteIdProvider(tmp_path))
    assert completed["settlement_binding"]["provider_request_id"] == "fal-remote-42"
    with pytest.raises(ValueError, match="immutable"):
        executor._change(job.request_id, provider_id="different-remote")


@pytest.mark.asyncio
async def test_generic_mutation_cannot_replace_a_settlement_binding(tmp_path):
    executor, job, _ = await _bound_receipt(tmp_path)

    with pytest.raises(ValueError, match="settlement binding"):
        executor._change(
            job.request_id,
            settlement_binding={
                "schema": 2,
                "local_request_id": job.request_id,
                "provider_request_id": "forged",
                "endpoint_id": job.endpoint,
                "auth_scope": "TEST_LOCAL_ONLY",
                "authority_mode": "TEST",
            },
        )


@pytest.mark.asyncio
async def test_fal_claim_blocks_direct_settle_and_does_not_require_estimate_equal_actual(tmp_path):
    from src.hybrid.billing_reconciler import FalLedgerSettlementService

    executor, job, _ = await _bound_receipt(tmp_path)
    ledger = _ledger(tmp_path)
    fixture = LoopbackFalBillingFixture(
        pricing_pages=[],
        billing_pages=[{
            "billing_events": [_event(job.request_id, job.endpoint, cost="0.043", nano=999)],
            "next_cursor": None, "has_more": False,
        }],
    )
    service = FalLedgerSettlementService(ledger, executor, FalBillingConsumer(fixture), clock=lambda: END)
    service.reserve_from_executor(job.request_id, episode_id="TEST-EP", quoted_cap_usd=job.cost)
    with pytest.raises(BudgetDenied, match="FAL-bound"):
        ledger.settle(job.request_id, "0.043")
    result = await service.reconcile(job.request_id, start=START, end=END)
    assert result["status"] == "SETTLED"
    assert result["claim"]["actual_usd"] == "0.043000"


@pytest.mark.asyncio
async def test_forged_scope_and_fresh_process_ledger_reopen_fail_closed(tmp_path):
    from src.hybrid.billing_reconciler import (
        FalLedgerSettlementService,
        SettlementDenied,
    )

    executor, job, _ = await _bound_receipt(tmp_path)
    ledger = _ledger(tmp_path)
    service = FalLedgerSettlementService(ledger, executor, FalBillingConsumer(LoopbackFalBillingFixture(pricing_pages=[], billing_pages=[])), clock=lambda: END)
    service.reserve_from_executor(job.request_id, episode_id="TEST-EP", quoted_cap_usd=job.cost)
    with executor._db() as db:
        row = executor.inspect(job.request_id)
        row["settlement_binding"]["auth_scope"] = "forged"
        executor._put(db, row)
    with pytest.raises(SettlementDenied, match="auth scope"):
        await service.reconcile(job.request_id, start=START, end=END)
    with pytest.raises(BudgetDenied, match="immutable policy"):
        StandingBudgetLedger(ledger.path, policy_sha256="a" * 64, episode_limit_usd="2", month_limit_usd="1", experiment_limit_usd="1", mode="TEST")
