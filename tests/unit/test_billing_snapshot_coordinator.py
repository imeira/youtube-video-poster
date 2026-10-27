"""Writer-owned billing status snapshots never claim cross-SQLite atomicity."""

from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import os
import sqlite3
import time
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.execution import Executor, Job
from src.hybrid.planner import Config
from src.hybrid.standing_budget import BudgetDenied, StandingBudgetLedger


def _job(tmp_path):
    image = tmp_path / "reference.png"
    Image.new("RGB", (8, 8), "white").save(image)
    asset = FrozenAsset.approve(image, "test", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "test", "TEST"), "test", "TEST")
    return Job("snapshot-request", "first", "TEST", "fal-ai/flux/dev", {"prompt": "fixture"}, manifest, Decimal("0.05"))


def test_real_admission_writes_coherent_current_snapshot_without_caller_records(tmp_path):
    from src.hybrid.billing_snapshot import BillingSnapshotCoordinator
    from src.hybrid.live_execution import BillingStatusSnapshot

    root = tmp_path / "snapshots"
    root.mkdir()
    ledger = StandingBudgetLedger(tmp_path / "ledger.sqlite3", policy_sha256="a" * 64,
                                 episode_limit_usd="1", month_limit_usd="1", experiment_limit_usd="1", mode="TEST")
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(1)), standing_ledger=ledger)
    job = _job(tmp_path)
    coordinator = BillingSnapshotCoordinator(root, executor=executor, ledger=ledger, clock=lambda: 4_102_444_000)

    coordinator.prepare_and_reserve(job, episode_id="TEST-EP")

    snapshot = root / "billing-status.json"
    document = __import__("json").loads(snapshot.read_text(encoding="utf-8"))
    assert document["status"] == "CURRENT"
    assert document["generation"] == 1
    assert document["executor_receipt"]["billing_snapshot_generation"] == 1
    assert document["ledger_claim"]["billing_snapshot_generation"] == 1
    reader = BillingStatusSnapshot.from_app_config(SimpleNamespace(raw={"billing_status_snapshot": {
        "path": str(snapshot), "sha256": __import__("hashlib").sha256(snapshot.read_bytes()).hexdigest(),
    }}), clock=lambda: 4_102_444_001)
    assert reader.inspect(job)["billing_status"] == "TEST"


def test_marker_survives_partial_store_failure_as_stale(tmp_path, monkeypatch):
    from src.hybrid.billing_snapshot import BillingSnapshotCoordinator
    from src.hybrid.live_execution import BillingStatusSnapshot

    root = tmp_path / "snapshots"
    root.mkdir()
    ledger = StandingBudgetLedger(tmp_path / "ledger.sqlite3", policy_sha256="a" * 64,
                                 episode_limit_usd="1", month_limit_usd="1", experiment_limit_usd="1", mode="TEST")
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(1)), standing_ledger=ledger)
    coordinator = BillingSnapshotCoordinator(root, executor=executor, ledger=ledger, clock=lambda: 4_102_444_000)
    monkeypatch.setattr(ledger, "reserve", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("crash after executor")))

    try:
        coordinator.prepare_and_reserve(_job(tmp_path), episode_id="TEST-EP")
    except RuntimeError:
        pass
    else:
        raise AssertionError("injected interruption must escape")

    snapshot = root / "billing-status.json"
    document = __import__("json").loads(snapshot.read_text(encoding="utf-8"))
    assert document["status"] == "WRITER_IN_PROGRESS"
    reader = BillingStatusSnapshot.from_app_config(SimpleNamespace(raw={"billing_status_snapshot": {
        "path": str(snapshot), "sha256": __import__("hashlib").sha256(snapshot.read_bytes()).hexdigest(),
    }}), clock=lambda: 4_102_444_001)
    assert reader.inspect(_job(tmp_path))["billing_status"] == "STALE"


def test_checkpoint_routes_both_durable_bindings_through_the_marker(tmp_path):
    from src.hybrid.billing_snapshot import BillingSnapshotCoordinator

    root = tmp_path / "snapshots"
    root.mkdir()
    ledger = StandingBudgetLedger(tmp_path / "ledger.sqlite3", policy_sha256="a" * 64,
                                 episode_limit_usd="1", month_limit_usd="1", experiment_limit_usd="1", mode="TEST")
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(1)), standing_ledger=ledger)
    job = _job(tmp_path)
    coordinator = BillingSnapshotCoordinator(root, executor=executor, ledger=ledger, clock=lambda: 4_102_444_000)
    coordinator.prepare_and_reserve(job, episode_id="TEST-EP")

    coordinator.checkpoint_provider_binding(job.request_id, "remote-1")

    document = __import__("json").loads((root / "billing-status.json").read_text(encoding="utf-8"))
    assert document["status"] == "CURRENT"
    assert document["generation"] == 2
    assert document["executor_receipt"]["settlement_binding"]["provider_request_id"] == "remote-1"
    assert document["ledger_claim"]["provider_request_id"] == "remote-1"
    assert document["executor_receipt"]["billing_snapshot_generation"] == 2
    assert document["ledger_claim"]["billing_snapshot_generation"] == 2


def test_direct_fal_generation_mutation_is_rejected_before_it_changes_a_coherent_snapshot(tmp_path):
    from src.hybrid.billing_snapshot import BillingSnapshotCoordinator
    from src.hybrid.standing_budget import BudgetDenied

    root = tmp_path / "snapshots"
    root.mkdir()
    ledger = StandingBudgetLedger(tmp_path / "ledger.sqlite3", policy_sha256="a" * 64,
                                 episode_limit_usd="1", month_limit_usd="1", experiment_limit_usd="1", mode="TEST")
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(1)), standing_ledger=ledger)
    job = _job(tmp_path)
    coordinator = BillingSnapshotCoordinator(root, executor=executor, ledger=ledger, clock=lambda: 4_102_444_000)
    coordinator.prepare_and_reserve(job, episode_id="TEST-EP")

    with __import__("pytest").raises(BudgetDenied, match="coordinator"):
        ledger.mark_unknown(job.request_id, billing_snapshot_generation=2)

    assert ledger.claim(job.request_id)["state"] == "RESERVED"
    assert __import__("json").loads((root / "billing-status.json").read_text(encoding="utf-8"))["status"] == "CURRENT"


def test_consumer_rejects_rehashed_noncanonical_settlement_receipt(tmp_path):
    from src.hybrid.billing_snapshot import BillingSnapshotCoordinator, _hash
    from src.hybrid.live_execution import BillingStatusSnapshot

    root = tmp_path / "snapshots"
    root.mkdir()
    ledger = StandingBudgetLedger(tmp_path / "ledger.sqlite3", policy_sha256="a" * 64,
                                 episode_limit_usd="1", month_limit_usd="1", experiment_limit_usd="1", mode="TEST")
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(1)), standing_ledger=ledger)
    job = _job(tmp_path)
    coordinator = BillingSnapshotCoordinator(root, executor=executor, ledger=ledger, clock=lambda: 4_102_444_000)
    coordinator.prepare_and_reserve(job, episode_id="TEST-EP")
    snapshot = root / "billing-status.json"
    document = __import__("json").loads(snapshot.read_text(encoding="utf-8"))
    document["settlement_receipt"] = {"not": "canonical"}
    document["snapshot_sha256"] = _hash({key: value for key, value in document.items() if key != "snapshot_sha256"})
    snapshot.write_text(__import__("json").dumps(document, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    reader = BillingStatusSnapshot.from_app_config(SimpleNamespace(raw={"billing_status_snapshot": {
        "path": str(snapshot), "sha256": __import__("hashlib").sha256(snapshot.read_bytes()).hexdigest(),
    }}), clock=lambda: 4_102_444_001)

    assert reader.inspect(job)["billing_status"] == "STALE"


async def test_real_service_admit_checkpoint_reconcile_publishes_coordinator_snapshot(tmp_path):
    from src.hybrid.billing_reconciler import FalLedgerSettlementService
    from src.hybrid.billing_snapshot import BillingSnapshotCoordinator
    from src.hybrid.execution import ProviderResult
    from src.hybrid.fal_billing import FalBillingConsumer, LoopbackFalBillingFixture

    class Provider:
        mode = "TEST"

        async def submit(self, job, request_id, checkpoint):
            checkpoint(provider_id="remote-service-1")
            output = tmp_path / "output.bin"
            output.write_bytes(b"fixture")
            return ProviderResult(output, Decimal("0.045"))

        async def recover(self, *args):
            raise AssertionError("not reached")

    root = tmp_path / "snapshots"
    root.mkdir()
    ledger = StandingBudgetLedger(tmp_path / "ledger.sqlite3", policy_sha256="a" * 64,
                                 episode_limit_usd="1", month_limit_usd="1", experiment_limit_usd="1", mode="TEST")
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(1)), standing_ledger=ledger)
    job = _job(tmp_path)
    coordinator = BillingSnapshotCoordinator(root, executor=executor, ledger=ledger, clock=lambda: 4_102_444_000)
    now = datetime(2026, 10, 26, 12, 1, tzinfo=UTC)
    fixture = LoopbackFalBillingFixture(pricing_pages=[], billing_pages=[{"billing_events": [{
        "request_id": "remote-service-1", "endpoint_id": job.endpoint, "timestamp": "2026-10-26T12:00:00Z",
        "quantity": 1, "output_units": 1, "unit": "image", "unit_price": 0.045,
        "percent_discount": 0, "cost_subtotal": 0.045, "cost_discount": 0,
        "cost_total": 0.045, "cost_estimate_nano_usd": 45_000_000,
    }], "next_cursor": None, "has_more": False}])
    service = FalLedgerSettlementService(ledger, executor, FalBillingConsumer(fixture), clock=lambda: now,
                                         coordinator=coordinator)

    service.admit(job, episode_id="TEST-EP")
    await service.run_admitted(job, Provider())
    result = await service.reconcile(job.request_id, start=datetime(2026, 10, 26, 11, 59, tzinfo=UTC), end=now)

    document = __import__("json").loads((root / "billing-status.json").read_text(encoding="utf-8"))
    assert result["status"] == "SETTLED"
    assert document["status"] == "CURRENT"
    assert document["generation"] == 3
    assert document["ledger_claim"]["state"] == "SETTLED"
    assert document["executor_receipt"]["billing_snapshot_generation"] == 3


def _fresh_job(root, request_id):
    """Materialize a TEST-only immutable job that a spawned worker can verify."""
    image = root / f"{request_id}-reference.png"
    Image.new("RGB", (8, 8), "white").save(image)
    asset = FrozenAsset.approve(image, "fresh-process-fixture", "TEST")
    sheet = root / f"{request_id}-sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "fresh-process-fixture", "TEST"), "fresh-process-fixture", "TEST")
    return Job(request_id, "first", "TEST", "fal-ai/flux/dev", {"prompt": "fixture"}, manifest, Decimal("0.05"))


def _fresh_components(root):
    from src.hybrid.billing_snapshot import BillingSnapshotCoordinator

    root = __import__("pathlib").Path(root)
    snapshots = root / "snapshots"
    snapshots.mkdir(exist_ok=True)
    ledger = StandingBudgetLedger(root / "ledger.sqlite3", policy_sha256="a" * 64,
                                 episode_limit_usd="1", month_limit_usd="1", experiment_limit_usd="1", mode="TEST")
    executor = Executor(root / "executor.sqlite3", Config(limit=Decimal(1)), standing_ledger=ledger)
    return BillingSnapshotCoordinator(snapshots, executor=executor, ledger=ledger, clock=lambda: 4_102_444_000), executor, ledger


def _wait_for(paths, timeout=15):
    deadline = time.monotonic() + timeout
    while not all(path.exists() for path in paths):
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out waiting for fresh-process barrier: {paths}")
        # A bounded file barrier is deterministic: children cannot pass until
        # the parent has observed every ready marker and released them together.
        time.sleep(0.01)


def _coordinator_worker(action, root_text, request_id, ready_text, release_text, event_queue):
    """Top-level spawn target: no inherited monkeypatches or SQLite handles."""
    root = __import__("pathlib").Path(root_text)
    ready, release = __import__("pathlib").Path(ready_text), __import__("pathlib").Path(release_text)
    try:
        coordinator, executor, ledger = _fresh_components(root)
        job = _fresh_job(root, request_id)
        if action == "prepare-race":
            ready.write_text("ready", encoding="utf-8")
            _wait_for([release])
            event_queue.put({"action": action, "result": coordinator.prepare_and_reserve(job, episode_id="TEST-EP")})
        elif action == "crash-after-marker":
            executor.prepare_intent = lambda *_args, **_kwargs: os._exit(71)
            coordinator.prepare_and_reserve(job, episode_id="TEST-EP")
        elif action == "crash-after-first-commit":
            def exit_after_executor(*_args, **_kwargs):
                os._exit(72)
            ledger.bind_provider_request_id = exit_after_executor
            coordinator.checkpoint_provider_binding(job.request_id, "remote-after-first-commit")
        elif action == "consumer-trapped":
            from src.hybrid.live_execution import BillingStatusSnapshot
            snapshot = root / "snapshots" / "billing-status.json"
            configured = SimpleNamespace(raw={"billing_status_snapshot": {"path": str(snapshot), "sha256": hashlib.sha256(snapshot.read_bytes()).hexdigest()}})
            original = sqlite3.connect
            sqlite3.connect = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("consumer opened SQLite"))
            try:
                result = BillingStatusSnapshot.from_app_config(configured, clock=lambda: 4_102_444_001).inspect(job)
            finally:
                sqlite3.connect = original
            event_queue.put({"action": action, "result": result})
        elif action == "legacy-direct-mutator":
            coordinator.prepare_and_reserve(job, episode_id="TEST-EP")
            try:
                ledger.mark_unknown(job.request_id, billing_snapshot_generation=2)
            except BudgetDenied as error:
                event_queue.put({"action": action, "error": type(error).__name__, "message": str(error)})
            else:
                event_queue.put({"action": action, "error": None})
        elif action == "admit":
            event_queue.put({"action": action, "result": coordinator.prepare_and_reserve(job, episode_id="TEST-EP")})
        elif action == "submit-crash":
            from src.hybrid.billing_reconciler import FalLedgerSettlementService
            from src.hybrid.fal_billing import (
                FalBillingConsumer,
                LoopbackFalBillingFixture,
            )
            counter = root / "provider-post-count.txt"

            class Provider:
                mode = "TEST"
                async def submit(self, submitted_job, _request_id, checkpoint):
                    count = int(counter.read_text() if counter.exists() else "0") + 1
                    counter.write_text(str(count), encoding="utf-8")
                    checkpoint(provider_id="remote-fresh-1")
                    os._exit(73)
                async def recover(self, *_args):
                    raise AssertionError("crash worker must not recover")
            service = FalLedgerSettlementService(ledger, executor, FalBillingConsumer(LoopbackFalBillingFixture(pricing_pages=[], billing_pages=[])), coordinator=coordinator)
            asyncio.run(service.run_admitted(job, Provider()))
        elif action == "recover":
            from src.hybrid.billing_reconciler import FalLedgerSettlementService
            from src.hybrid.execution import ProviderResult
            from src.hybrid.fal_billing import (
                FalBillingConsumer,
                LoopbackFalBillingFixture,
            )
            counter = root / "provider-post-count.txt"
            class Provider:
                mode = "TEST"
                async def submit(self, *_args):
                    raise AssertionError("recovery must never submit a new POST")
                async def recover(self, *_args):
                    output = root / "recovered.bin"
                    output.write_bytes(b"fixture")
                    return ProviderResult(output, Decimal("0.045"))
            service = FalLedgerSettlementService(ledger, executor, FalBillingConsumer(LoopbackFalBillingFixture(pricing_pages=[], billing_pages=[])), coordinator=coordinator)
            result = asyncio.run(service.run_admitted(job, Provider()))
            event_queue.put({"action": action, "result": result, "posts": int(counter.read_text()) if counter.exists() else 0})
        else:
            raise AssertionError(f"unknown fresh-process action: {action}")
    except (AssertionError, OSError, RuntimeError, TypeError, ValueError) as error:
        event_queue.put({"action": action, "error": f"{type(error).__name__}: {error}"})


def _fresh(action, root, request_id="snapshot-request"):
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    ready, release = root / f"{action}-{request_id}.ready", root / f"{action}-{request_id}.release"
    process = context.Process(target=_coordinator_worker, args=(action, str(root), request_id, str(ready), str(release), queue))
    process.start()
    return process, queue, ready, release


def _join_event(process, queue, *, expected_exit=0):
    try:
        process.join(20)
        assert process.exitcode == expected_exit
        return queue.get(timeout=3)
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)


def test_fresh_process_competing_coordinators_publish_only_coherent_generations(tmp_path):
    first, first_events, first_ready, release = _fresh("prepare-race", tmp_path, "race-one")
    second, second_events, second_ready, _ = _fresh("prepare-race", tmp_path, "race-two")
    try:
        _wait_for([first_ready, second_ready])
        release.write_text("release", encoding="utf-8")
        (tmp_path / "prepare-race-race-two.release").write_text("release", encoding="utf-8")
        events = [_join_event(first, first_events), _join_event(second, second_events)]
    finally:
        for process in (first, second):
            if process.is_alive():
                process.kill()
                process.join(10)
    assert not [event for event in events if "error" in event]
    assert sorted(event["result"]["generation"] for event in events) == [1, 2]
    document = json.loads((tmp_path / "snapshots" / "billing-status.json").read_text(encoding="utf-8"))
    assert document["status"] == "CURRENT"
    assert document["generation"] == 2
    assert document["executor_receipt"]["billing_snapshot_generation"] == document["ledger_claim"]["billing_snapshot_generation"] == 2


def test_fresh_process_crashes_leave_stale_snapshot_and_consumer_never_opens_sqlite(tmp_path):
    process, _queue, _, _ = _fresh("crash-after-marker", tmp_path)
    try:
        process.join(20)
        assert process.exitcode == 71
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)
    snapshot = tmp_path / "snapshots" / "billing-status.json"
    assert json.loads(snapshot.read_text(encoding="utf-8"))["status"] == "WRITER_IN_PROGRESS"
    # Keep real WAL/SHM sidecars alive while a fresh consumer is trapped against sqlite3.connect.
    connection = sqlite3.connect(tmp_path / "executor.sqlite3")
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("BEGIN")
        files = [tmp_path / "executor.sqlite3", tmp_path / "executor.sqlite3-wal", tmp_path / "executor.sqlite3-shm"]
        assert all(path.exists() for path in files)
        before = {path.name: (path.stat().st_size, path.stat().st_mtime_ns) for path in files}
        consumer, events, _, _ = _fresh("consumer-trapped", tmp_path)
        event = _join_event(consumer, events)
        after = {path.name: (path.stat().st_size, path.stat().st_mtime_ns) for path in files}
    finally:
        connection.rollback()
        connection.close()
    assert event["result"]["billing_status"] == "STALE"
    assert after == before


def test_fresh_process_crash_after_first_commit_is_stale_and_recovery_posts_nothing_new(tmp_path):
    admission, events, _, _ = _fresh("admit", tmp_path)
    assert "error" not in _join_event(admission, events)
    crashed, _crash_events, _, _ = _fresh("crash-after-first-commit", tmp_path)
    crashed.join(20)
    assert crashed.exitcode == 72
    assert json.loads((tmp_path / "snapshots" / "billing-status.json").read_text(encoding="utf-8"))["status"] == "WRITER_IN_PROGRESS"
    recovered, recovered_events, _, _ = _fresh("recover", tmp_path)
    event = _join_event(recovered, recovered_events)
    assert "error" not in event, event
    assert event["posts"] == 0


def test_fresh_process_legacy_direct_mutator_rejects_generation_pinned_row(tmp_path):
    process, events, _, _ = _fresh("legacy-direct-mutator", tmp_path)
    event = _join_event(process, events)
    assert event["error"] == "BudgetDenied"
    assert "coordinator" in event["message"]


def test_fresh_process_service_recovery_reuses_checkpoint_without_second_post(tmp_path):
    admitted, events, _, _ = _fresh("admit", tmp_path)
    assert "error" not in _join_event(admitted, events)
    crashed, _crash_events, _, _ = _fresh("submit-crash", tmp_path)
    crashed.join(20)
    assert crashed.exitcode == 73
    assert (tmp_path / "provider-post-count.txt").read_text(encoding="utf-8") == "1"
    recovered, recovery_events, _, _ = _fresh("recover", tmp_path)
    event = _join_event(recovered, recovery_events)
    assert "error" not in event, event
    assert event["posts"] == 1


async def test_generation_pinned_executor_rejects_missing_invalid_or_foreign_capability_before_post_or_mutation(tmp_path):
    """Old direct submit_prepared posted and completed without a snapshot checkpoint."""
    from src.hybrid.billing_snapshot import BillingSnapshotCoordinator
    from src.hybrid.execution import ProviderResult

    class Provider:
        mode = "TEST"

        def __init__(self):
            self.posts = 0

        async def submit(self, _job, _request_id, checkpoint):
            self.posts += 1
            checkpoint(provider_id="direct-uncoordinated-post")
            output = tmp_path / f"output-{self.posts}.bin"
            output.write_bytes(b"fixture")
            return ProviderResult(output, Decimal("0.045"))

        async def recover(self, *_args):
            raise AssertionError("missing capability must fail before recovery")

    root = tmp_path / "snapshots"
    root.mkdir()
    ledger = StandingBudgetLedger(tmp_path / "ledger.sqlite3", policy_sha256="a" * 64,
                                 episode_limit_usd="1", month_limit_usd="1", experiment_limit_usd="1", mode="TEST")
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(1)), standing_ledger=ledger)
    job = _job(tmp_path)
    coordinator = BillingSnapshotCoordinator(root, executor=executor, ledger=ledger, clock=lambda: 4_102_444_000)
    coordinator.prepare_and_reserve(job, episode_id="TEST-EP")
    executor._bind_billing_snapshot_coordinator(coordinator, coordinator.owner_binding)
    initial = executor.inspect(job.request_id)
    initial_claim = ledger.claim(job.request_id)
    original_snapshot = (root / "billing-status.json").read_bytes()

    class ForgedCapability:
        def checkpoint_provider_binding(self, *_args, **_kwargs):
            raise AssertionError("forged capability must never be called")

    foreign_root = tmp_path / "foreign-snapshots"
    foreign_root.mkdir()
    with __import__("pytest").raises(ValueError, match="immutable"):
        BillingSnapshotCoordinator(foreign_root, executor=executor, ledger=ledger, clock=lambda: 4_102_444_000)
    assert Provider().posts == 0
    assert executor.inspect(job.request_id) == initial
    assert ledger.claim(job.request_id) == initial_claim
    assert (root / "billing-status.json").read_bytes() == original_snapshot
    assert list(foreign_root.iterdir()) == []

    foreign_ledger = StandingBudgetLedger(tmp_path / "foreign-ledger.sqlite3", policy_sha256="a" * 64,
                                         episode_limit_usd="1", month_limit_usd="1", experiment_limit_usd="1", mode="TEST")
    foreign_executor = Executor(tmp_path / "foreign-executor.sqlite3", Config(limit=Decimal(1)), standing_ledger=foreign_ledger)
    foreign = BillingSnapshotCoordinator(foreign_root, executor=foreign_executor, ledger=foreign_ledger,
                                         clock=lambda: 4_102_444_000)
    foreign_snapshot_before = set(foreign_root.iterdir())
    for capability in (None, ForgedCapability(), foreign.execution_capability(job.request_id)):
        provider = Provider()
        with __import__("pytest").raises((PermissionError, TypeError), match="coordinator capability"):
            await executor.run(job, provider, settlement_checkpoint=capability, submit_prepared=True)
        assert provider.posts == 0
        assert executor.inspect(job.request_id) == initial
        assert ledger.claim(job.request_id) == initial_claim
        assert (root / "billing-status.json").read_bytes() == original_snapshot
        assert set(foreign_root.iterdir()) == foreign_snapshot_before
