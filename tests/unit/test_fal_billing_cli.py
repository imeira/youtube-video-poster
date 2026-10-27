"""Offline CLI contract for the GET-only FAL billing consumer."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import types
from pathlib import Path

import pytest

from src.hybrid import fal_billing_cli
from src.hybrid.fal_billing import LoopbackFalBillingFixture
from src.hybrid.fal_billing_cli import main

ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)


def test_fal_billing_preflight_is_fresh_process_offline_and_unqualified():
    completed = subprocess.run(
        [str(PYTHON), "-m", "src.cli.main", "fal-billing", "preflight", "--endpoint-id", "fal-ai/flux/dev"],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": "", "FAL_KEY": ""},
        capture_output=True, text=True, check=False,
    )
    report = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    assert report == {
        "mode": "READ_ONLY", "network": "NOT_REQUESTED", "qualification": "NOT_QUALIFIED",
        "status": "PREFLIGHT_ONLY", "endpoint_id": "fal-ai/flux/dev",
    }


@pytest.mark.parametrize("argv", [
    ["preflight", "--endpoint-id", ""],
    ["quote", "--readback", "--endpoint-id", "", "--quantity-cap", "1"],
    ["readback", "--readback", "--endpoint-id", "fal-ai/flux/dev", "--request-id", "\x01", "--start", "2026-10-25T00:00:00Z", "--end", "2026-10-25T02:00:00Z"],
])
def test_invalid_cli_contract_never_reads_secret_or_constructs_client(monkeypatch, capsys, argv):
    calls = 0

    def factory(_key):
        nonlocal calls
        calls += 1
        raise AssertionError("invalid input must not construct a client")

    original_environ = os.environ

    class NoFalKeyRead:
        def get(self, key, *args, **kwargs):
            if key == "FAL_KEY":
                raise AssertionError("invalid input must not read FAL_KEY")
            return original_environ.get(key, *args, **kwargs)

    monkeypatch.setattr(fal_billing_cli, "os", types.SimpleNamespace(environ=NoFalKeyRead()))
    assert asyncio.run(main(argv, client_factory=factory)) == 1
    assert calls == 0
    assert json.loads(capsys.readouterr().out)["status"] == "UNKNOWN"


def test_explicit_readback_uses_injected_test_loopback_and_never_claims_live(monkeypatch, capsys):
    fixture = LoopbackFalBillingFixture(
        pricing_pages=[],
        billing_pages=[{
            "billing_events": [{
                "request_id": "opaque-id", "endpoint_id": "fal-ai/flux/dev", "timestamp": "2026-10-25T01:00:00Z",
                "quantity": 1, "output_units": 1, "unit": "image", "unit_price": 0.025,
                "percent_discount": None, "cost_subtotal": 0.025, "cost_discount": 0,
                "cost_total": 0.025, "cost_estimate_nano_usd": 25000000,
            }], "next_cursor": None, "has_more": False,
        }],
    )
    monkeypatch.setenv("FAL_KEY", "test-key-never-output")
    code = asyncio.run(main([
        "readback", "--readback", "--request-id", "opaque-id", "--endpoint-id", "fal-ai/flux/dev",
        "--start", "2026-10-25T00:00:00Z", "--end", "2026-10-25T02:00:00Z",
    ], client_factory=lambda _: fixture))
    report = json.loads(capsys.readouterr().out)

    assert code == 0
    assert report["status"] == "BILLED"
    assert report["actual_cost_usd"] == "0.025"
    assert report["authority"] == "TEST_NOT_AUTHORITY"
    assert report["qualification"] == "NOT_QUALIFIED"
    assert "test-key-never-output" not in json.dumps(report)


def test_reconcile_cli_uses_only_local_request_and_constructed_service(monkeypatch, capsys, tmp_path):
    """Reconcile has no caller endpoint, receipt, scope, or authority input."""
    calls = []

    class Service:
        async def reconcile(self, request_id, *, start, end):
            calls.append((request_id, start, end))
            return {"request_id": request_id, "status": "SETTLED", "claim": {"state": "SETTLED"}}

    fixture = LoopbackFalBillingFixture(pricing_pages=[], billing_pages=[])
    monkeypatch.setenv("FAL_KEY", "test-key-never-output")
    code = asyncio.run(main([
        "reconcile", "--readback", "--state-root", str(tmp_path), "--request-id", "local-request",
        "--start", "2026-10-25T00:00:00Z", "--end", "2026-10-25T02:00:00Z",
    ], client_factory=lambda _: fixture, settlement_factory=lambda root, consumer: Service()))
    report = json.loads(capsys.readouterr().out)

    assert code == 0
    assert calls == [("local-request", calls[0][1], calls[0][2])]
    assert report == {
        "claim": {"state": "SETTLED"}, "mode": "READ_ONLY", "qualification": "NOT_QUALIFIED",
        "request_id": "local-request", "status": "SETTLED",
    }
    assert fixture.billing_queries == []


def test_invalid_reconcile_never_reads_key_or_constructs_service(monkeypatch, capsys, tmp_path):
    class NoFalKeyRead:
        def get(self, key, *args, **kwargs):
            if key == "FAL_KEY":
                raise AssertionError("invalid input must not read FAL_KEY")
            return os.environ.get(key, *args, **kwargs)

    monkeypatch.setattr(fal_billing_cli, "os", types.SimpleNamespace(environ=NoFalKeyRead()))
    assert asyncio.run(main([
        "reconcile", "--readback", "--state-root", str(tmp_path), "--request-id", "\x01",
        "--start", "2026-10-25T00:00:00Z", "--end", "2026-10-25T02:00:00Z",
    ], settlement_factory=lambda *_: (_ for _ in ()).throw(AssertionError("must not construct")))) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "UNKNOWN"


def test_reconcile_cli_fresh_process_bootstrap_uses_real_durable_settlement_only(tmp_path):
    """The subprocess fixture is a component transport test, not concrete HTTP evidence."""
    script = r'''
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from PIL import Image
from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.billing_reconciler import FalLedgerSettlementService
from src.hybrid.execution import Executor, Job, ProviderResult
from src.hybrid.fal_billing import FalBillingConsumer, LoopbackFalBillingFixture
from src.hybrid.fal_billing_cli import main
from src.hybrid.planner import Config
from src.hybrid.standing_budget import StandingBudgetLedger


class Provider:
    mode = "TEST"

    def __init__(self, root):
        self.root = root

    async def submit(self, job, request_id, checkpoint):
        checkpoint(provider_id=request_id)
        output = self.root / "output.bin"
        output.write_bytes(b"fixture")
        return ProviderResult(output, Decimal("0.045"))

    async def recover(self, *args):
        raise AssertionError("not reached")


def manifest(root):
    image = root / "reference.png"
    Image.new("RGB", (8, 8), "white").save(image)
    frozen = FrozenAsset.approve(image, "test", "TEST")
    sheet = root / "sheet.png"
    contact_sheet((frozen,), sheet)
    return Manifest.freeze((frozen,), FrozenAsset.approve(sheet, "test", "TEST"), "test", "TEST")


def event(request_id, endpoint, stamp):
    return {"request_id": request_id, "endpoint_id": endpoint, "timestamp": stamp,
            "quantity": 1, "output_units": 1, "unit": "image", "unit_price": 0.045,
            "percent_discount": 0, "cost_subtotal": 0.045, "cost_discount": 0,
            "cost_total": 0.045, "cost_estimate_nano_usd": 45000000}


async def bootstrap(root):
    ledger = StandingBudgetLedger(root / "ledger.sqlite3", policy_sha256="a" * 64,
                                  episode_limit_usd="1", month_limit_usd="1",
                                  experiment_limit_usd="1", mode="TEST")
    executor = Executor(root / "executor.sqlite3", Config(limit=Decimal("1"), concurrency=1),
                        standing_ledger=ledger)
    job = Job("S001", "first", "TEST", "fal-ai/flux/dev", {"prompt": "fixture"}, manifest(root), Decimal("0.05"))
    prepared = executor.prepare_intent(job)
    ledger.reserve(job.request_id, "TEST-EP", job.endpoint, job.cost,
                   settlement_mode="FAL_READBACK_V2",
                   auth_scope=prepared["settlement_binding"]["auth_scope"],
                   provider_request_id="UNASSIGNED", authority_mode="TEST")
    await executor.run(job, Provider(root),
                       settlement_checkpoint=lambda local_id, remote_id: ledger.bind_provider_request_id(
                           local_id, remote_id),
                       submit_prepared=True)
    now = datetime.now(UTC).replace(microsecond=0)
    start, end = now - timedelta(seconds=90), now - timedelta(seconds=10)
    fixture = LoopbackFalBillingFixture(pricing_pages=[], billing_pages=[
        {"billing_events": [event(job.request_id, job.endpoint, (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"))], "next_cursor": None, "has_more": False},
        {"billing_events": [event(job.request_id, job.endpoint, (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"))], "next_cursor": None, "has_more": False},
    ])
    FalLedgerSettlementService(ledger, executor, FalBillingConsumer(fixture)).reserve_from_executor(
        job.request_id, episode_id="TEST-EP", quoted_cap_usd=job.cost)
    return job, fixture, start, end


async def run(case, root):
    if case == "wrong-root":
        fixture = LoopbackFalBillingFixture(pricing_pages=[], billing_pages=[])
        code = await main(["reconcile", "--readback", "--state-root", str(root / "missing"),
                           "--request-id", "local-request", "--start", "2026-10-26T00:00:00Z",
                           "--end", "2026-10-26T00:01:00Z"], client_factory=lambda _: fixture)
        print(json.dumps({"code": code, "queries": fixture.billing_queries, "missing": not (root / "missing").exists()}))
        return
    job, fixture, start, end = await bootstrap(root)
    argv = ["reconcile", "--readback", "--state-root", str(root), "--request-id", job.request_id,
            "--start", start.isoformat(), "--end", end.isoformat()]
    if case == "live-fixture":
        code = await main(argv, client_factory=lambda _: LoopbackFalBillingFixture(pricing_pages=[], billing_pages=[], mode="LIVE"))
        print(json.dumps({"code": code, "queries": fixture.billing_queries}))
        return
    first = await main(argv, client_factory=lambda _: fixture)
    second = await main(argv, client_factory=lambda _: fixture)
    print(json.dumps({"codes": [first, second], "queries": fixture.billing_queries}))


asyncio.run(run(sys.argv[1], Path(sys.argv[2])))
'''

    def fresh(case, root):
        return subprocess.run(
            [str(PYTHON), "-c", script, case, str(root)], cwd=ROOT,
            env={**os.environ, "PYTHONPATH": "", "FAL_KEY": "test-key-never-output"},
            capture_output=True, text=True, check=False,
        )

    positive_root = tmp_path / "positive"
    positive_root.mkdir()
    completed = fresh("positive", positive_root)
    reports = [json.loads(line) for line in completed.stdout.splitlines()]
    assert completed.returncode == 0, completed.stderr
    assert [report["status"] for report in reports[:2]] == ["SETTLED", "SETTLED"]
    assert reports[2]["codes"] == [0, 0]
    assert len(reports[2]["queries"]) == 2  # same event is read back twice; durable receipt remains canonical
    with sqlite3.connect(positive_root / "ledger.sqlite3") as db:
        rows = db.execute("SELECT actual, receipt_body FROM settlement_receipts").fetchall()
    assert len(rows) == 1
    assert rows[0][0] == 45_000
    assert json.loads(rows[0][1])["cost_total_micro_usd"] == "45000"

    wrong_root = tmp_path / "wrong-root"
    wrong_root.mkdir()
    wrong = fresh("wrong-root", wrong_root)
    wrong_reports = [json.loads(line) for line in wrong.stdout.splitlines()]
    assert wrong.returncode == 0, wrong.stderr
    assert wrong_reports == [
        {"mode": "READ_ONLY", "qualification": "NOT_QUALIFIED", "status": "UNKNOWN"},
        {"code": 1, "missing": True, "queries": []},
    ]

    live_root = tmp_path / "live-fixture"
    live_root.mkdir()
    live = fresh("live-fixture", live_root)
    live_reports = [json.loads(line) for line in live.stdout.splitlines()]
    assert live.returncode == 0, live.stderr
    assert live_reports == [
        {"mode": "READ_ONLY", "qualification": "NOT_QUALIFIED", "status": "UNKNOWN"},
        {"code": 1, "queries": []},
    ]
    with sqlite3.connect(live_root / "ledger.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM settlement_receipts").fetchone()[0] == 0
