from __future__ import annotations

import asyncio
import multiprocessing
import sqlite3
from datetime import UTC, datetime

import pytest

from src.hybrid.worker import DurableEpisodeWorker, EpisodeTaskQueue


def _claim_once(database: str, ready, release, output) -> None:
    async def handler(_task):
        ready.set()
        await asyncio.to_thread(release.wait, 2)
        output.put("ran")
        return {"state": "WAITING_THUMBNAIL_APPROVAL", "next_action": "HUMAN_THUMBNAIL", "gate_hash": "a" * 64}

    queue = EpisodeTaskQueue(database)
    worker = DurableEpisodeWorker(queue, "first", handler, lease_ttl_seconds=0.2, poll_seconds=0.01)
    asyncio.run(worker.run_once())


def test_two_process_workers_run_one_durable_episode_once(tmp_path):
    database = str(tmp_path / "worker.sqlite3")
    queue = EpisodeTaskQueue(database)
    queue.enqueue("TEST-EP", str(tmp_path / "episode"), "fixture")
    context = multiprocessing.get_context("spawn")
    ready, release, output = context.Event(), context.Event(), context.Queue()
    first = context.Process(target=_claim_once, args=(database, ready, release, output))
    first.start()
    assert ready.wait(5)

    async def should_not_run(_task):
        raise AssertionError("second worker must not execute held episode")

    second = DurableEpisodeWorker(EpisodeTaskQueue(database), "second", should_not_run, lease_ttl_seconds=0.2, poll_seconds=0.01)
    assert asyncio.run(second.run_once()) is None
    release.set()
    first.join(5)
    assert first.exitcode == 0
    assert output.get(timeout=2) == "ran"
    task = queue.status("TEST-EP")
    assert task["status"] == "WAITING"
    assert task["next_action"] == "HUMAN_THUMBNAIL"


def test_restart_resumes_pending_task_without_resubmit(tmp_path):
    queue = EpisodeTaskQueue(tmp_path / "worker.sqlite3")
    queue.enqueue("TEST-EP", str(tmp_path / "episode"), "fixture")
    calls = []

    async def handler(task):
        calls.append(task["episode_id"])
        return {"state": "WAITING_VIDEO_APPROVAL", "next_action": "HUMAN_VIDEO", "gate_hash": "b" * 64}

    worker = DurableEpisodeWorker(queue, "one", handler, lease_ttl_seconds=1, poll_seconds=0.01)
    assert asyncio.run(worker.run_once())["state"] == "WAITING_VIDEO_APPROVAL"
    assert asyncio.run(DurableEpisodeWorker(queue, "two", handler).run_once()) is None
    assert calls == ["TEST-EP"]


def test_renew_rejects_stale_fence_and_cancellation_keeps_lease(tmp_path):
    queue = EpisodeTaskQueue(tmp_path / "worker.sqlite3")
    queue.enqueue("TEST-EP", str(tmp_path / "episode"), "fixture")
    started = asyncio.Event()
    blocker = asyncio.Event()

    async def handler(_task):
        started.set()
        await blocker.wait()
        return {"state": "DONE", "next_action": "TERMINAL"}

    async def exercise():
        worker = DurableEpisodeWorker(queue, "one", handler, lease_ttl_seconds=0.2, poll_seconds=0.01, shutdown_deadline_seconds=0.01)
        running = asyncio.create_task(worker.run_once())
        await started.wait()
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert queue.status("TEST-EP")["status"] == "UNKNOWN_REQUIRES_RECONCILIATION"
        with pytest.raises(RuntimeError, match="stale"):
            queue.complete("TEST-EP", "other", object(), {"state": "DONE", "next_action": "TERMINAL"})
        blocker.set()

    asyncio.run(exercise())


def test_cancelled_blocked_handler_becomes_unknown_and_is_never_recovered(tmp_path):
    """An ambiguous offloaded handler is quarantined before its lease can expire."""
    queue = EpisodeTaskQueue(tmp_path / "worker.sqlite3")
    queue.enqueue("TEST-EP", str(tmp_path / "episode"), "fixture")
    started, release, second_calls = asyncio.Event(), asyncio.Event(), []

    async def blocked(_task):
        started.set()
        await asyncio.to_thread(lambda: __import__("time").sleep(0.7))
        await release.wait()
        return {"state": "DONE", "next_action": "TERMINAL"}

    async def second(_task):
        second_calls.append(True)
        return {"state": "DONE", "next_action": "TERMINAL"}

    async def exercise():
        first = DurableEpisodeWorker(queue, "one", blocked, lease_ttl_seconds=0.3, poll_seconds=0.01, shutdown_deadline_seconds=0.01)
        task = asyncio.create_task(first.run_once())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert queue.status("TEST-EP")["status"] == "UNKNOWN_REQUIRES_RECONCILIATION"
        await asyncio.sleep(0.4)  # lease is now expired
        assert await DurableEpisodeWorker(queue, "two", second, lease_ttl_seconds=0.3).run_once() is None
        assert second_calls == []
        release.set()
        await asyncio.sleep(0.05)
        assert queue.status("TEST-EP")["status"] == "UNKNOWN_REQUIRES_RECONCILIATION"

    asyncio.run(exercise())


def test_direct_queue_complete_rejects_expired_internal_lease_without_mutation(tmp_path):
    queue = EpisodeTaskQueue(tmp_path / "worker.sqlite3")
    queue.enqueue("TEST-EP", str(tmp_path), "fixture")
    lease = queue.leases.acquire("TEST-EP", "worker:run", now=1.0, ttl_seconds=1.0)
    queue.claim("TEST-EP", "worker", lease, now=1.5)
    with pytest.raises(RuntimeError, match="stale"):
        queue.complete("TEST-EP", "worker", lease, {"state": "DONE", "next_action": "TERMINAL"}, now=3.0)
    assert queue.status("TEST-EP")["status"] == "RUNNING"


def test_wake_gate_requires_persisted_approved_runner_receipt(tmp_path):
    root = tmp_path / "episode"
    root.mkdir()
    queue = EpisodeTaskQueue(tmp_path / "worker.sqlite3")
    queue.enqueue("TEST-EP", str(root), "fixture")
    lease = queue.leases.acquire("TEST-EP", "worker:run", now=1.0, ttl_seconds=10)
    queue.claim("TEST-EP", "worker", lease, now=1.5)
    queue.complete("TEST-EP", "worker", lease, {"state": "WAIT", "next_action": "HUMAN_THUMBNAIL", "gate_hash": "a" * 64}, now=1.5)
    with pytest.raises(RuntimeError, match="receipt"):
        queue.wake_gate("TEST-EP", "thumbnail")
    assert queue.status("TEST-EP")["status"] == "WAITING"


def test_worker_bootstrap_enables_wal_for_queue_and_internal_lease_db(tmp_path):
    queue = EpisodeTaskQueue(tmp_path / "worker.sqlite3")
    import sqlite3
    with sqlite3.connect(queue.database) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    with sqlite3.connect(queue.leases.database) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"


def test_read_status_is_static_and_does_not_bootstrap_missing_database(tmp_path):
    database = tmp_path / "missing" / "worker.sqlite3"
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    result = EpisodeTaskQueue.read_status(database, "TEST-EP")
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert result == {"status": "UNAVAILABLE", "snapshot": "STALE", "reason": "queue snapshot is missing"}
    assert before == after


def test_every_queue_transition_republishes_a_sanitized_status_snapshot(tmp_path):
    queue = EpisodeTaskQueue(tmp_path / "worker.sqlite3")
    queue.enqueue("TEST-EP", str(tmp_path / "episode"), "fixture")
    assert EpisodeTaskQueue.read_status(queue.database, "TEST-EP")["status"] == "PENDING"

    lease = queue.leases.acquire("TEST-EP", "worker:run", now=1.0, ttl_seconds=10)
    queue.claim("TEST-EP", "worker", lease, now=1.5)
    claimed = EpisodeTaskQueue.read_status(queue.database, "TEST-EP", now=datetime.now(UTC))
    assert claimed["status"] == "RUNNING"
    assert claimed["authority"] == "LOCAL_STATUS_ONLY"
    assert "root" not in claimed

    queue.complete("TEST-EP", "worker", lease, {"state": "DONE", "next_action": "TERMINAL"}, now=2.0)
    assert EpisodeTaskQueue.read_status(queue.database, "TEST-EP")["status"] == "DONE"


def test_read_status_uses_no_sql_and_rejects_duplicate_json_keys(tmp_path, monkeypatch):
    queue = EpisodeTaskQueue(tmp_path / "worker.sqlite3")
    queue.enqueue("TEST-EP", str(tmp_path / "episode"), "fixture")
    snapshot = queue.snapshot_path
    before = snapshot.read_bytes()

    def no_connect(*_args, **_kwargs):
        raise AssertionError("read_status must not open SQLite")

    monkeypatch.setattr(sqlite3, "connect", no_connect)
    result = EpisodeTaskQueue.read_status(queue.database, "TEST-EP")
    assert result["authority"] == "LOCAL_STATUS_ONLY"
    assert snapshot.read_bytes() == before

    snapshot.write_bytes(b'{"schema":"episode-task-queue-snapshot-v1","schema":"forged"}')
    assert EpisodeTaskQueue.read_status(queue.database, "TEST-EP")["snapshot"] == "STALE"
