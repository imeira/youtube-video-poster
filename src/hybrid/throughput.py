"""Bounded durable FIFO primitives for image throughput."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from src.hybrid.artifacts import digest
from src.hybrid.locks import file_slot
from src.hybrid.observability import StructuredEventLog

QUEUE_STATES = frozenset({"QUEUED", "RUNNING", "COMPLETE", "FAILED"})


class DurableQueue:
    """A small SQLite queue bounded by workers plus explicit prefetch."""

    def __init__(
        self,
        database: Path,
        *,
        workers: int,
        prefetch: int = 0,
        events: StructuredEventLog | None = None,
        event_context: dict | None = None,
    ):
        if type(workers) is not int or workers < 1:
            raise ValueError("workers must be a positive integer")
        if type(prefetch) is not int or prefetch < 0:
            raise ValueError("prefetch must be a nonnegative integer")
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.workers = workers
        self.prefetch = prefetch
        self.events = events
        self.event_context = dict(event_context or {})
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS queue_items (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT UNIQUE NOT NULL,
                    priority INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    queued_at REAL NOT NULL,
                    started_at REAL,
                    ended_at REAL,
                    worker TEXT,
                    result TEXT,
                    error TEXT
                )
                """
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS queue_settings (id INTEGER PRIMARY KEY, data TEXT NOT NULL)"
            )
            settings = json.dumps(
                {"workers": workers, "prefetch": prefetch},
                sort_keys=True,
                separators=(",", ":"),
            )
            db.execute("INSERT OR IGNORE INTO queue_settings VALUES (1, ?)", (settings,))
            current = db.execute("SELECT data FROM queue_settings WHERE id=1").fetchone()[0]
            if current != settings:
                raise ValueError("persisted queue settings mismatch")

    @contextmanager
    def _db(self):
        database = sqlite3.connect(self.database, timeout=30)
        database.row_factory = sqlite3.Row
        database.execute("PRAGMA synchronous=FULL")
        try:
            with database:
                yield database
        finally:
            database.close()

    @staticmethod
    def _decode(row) -> dict | None:
        if row is None:
            return None
        value = dict(row)
        value["payload"] = json.loads(value["payload"])
        value["result"] = json.loads(value["result"]) if value["result"] else None
        return value

    def inspect(self, item_id: str) -> dict | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM queue_items WHERE id=?", (item_id,)).fetchone()
        return self._decode(row)

    def _emit(self, row: dict) -> None:
        if self.events is None:
            return
        fields = {
            **self.event_context,
            **row["payload"],
            **(row.get("result") or {}),
        }
        fields.update(
            status=row["status"],
            request_id=row["id"],
            queued_at=row.get("queued_at"),
            started_at=row.get("started_at"),
            ended_at=row.get("ended_at"),
            worker=row.get("worker"),
            error=row.get("error"),
        )
        self.events.emit(**fields)

    def items(self) -> tuple[dict, ...]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM queue_items ORDER BY sequence").fetchall()
        return tuple(self._decode(row) for row in rows)

    def enqueue(self, item_id: str, payload: dict, *, priority: int = 0) -> dict:
        if not item_id or type(priority) is not int or not isinstance(payload, dict):
            raise ValueError("queue item requires id, integer priority, and object payload")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT * FROM queue_items WHERE id=?", (item_id,)).fetchone()
            if existing is not None:
                return self._decode(existing)
            materialized = db.execute(
                "SELECT COUNT(*) FROM queue_items WHERE status IN ('QUEUED','RUNNING')"
            ).fetchone()[0]
            if materialized >= self.workers + self.prefetch:
                raise RuntimeError("queue worker + prefetch capacity exhausted")
            db.execute(
                """
                INSERT INTO queue_items
                    (id, priority, payload, status, queued_at)
                VALUES (?, ?, ?, 'QUEUED', ?)
                """,
                (item_id, priority, encoded, time.time()),
            )
            row = db.execute("SELECT * FROM queue_items WHERE id=?", (item_id,)).fetchone()
        decoded = self._decode(row)
        self._emit(decoded)
        return decoded

    def claim(self, worker: str) -> dict | None:
        if not worker:
            raise ValueError("worker identity required")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            running = db.execute(
                "SELECT COUNT(*) FROM queue_items WHERE status='RUNNING'"
            ).fetchone()[0]
            if running >= self.workers:
                return None
            row = db.execute(
                """
                SELECT * FROM queue_items
                WHERE status='QUEUED'
                ORDER BY priority DESC, sequence ASC
                LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            db.execute(
                """
                UPDATE queue_items
                SET status='RUNNING', worker=?, started_at=?
                WHERE id=? AND status='QUEUED'
                """,
                (worker, time.time(), row["id"]),
            )
            claimed = db.execute(
                "SELECT * FROM queue_items WHERE id=?", (row["id"],)
            ).fetchone()
        decoded = self._decode(claimed)
        self._emit(decoded)
        return decoded

    def _finish(self, item_id: str, status: str, *, result=None, error=None) -> dict:
        if status not in {"COMPLETE", "FAILED"}:
            raise ValueError("terminal queue status required")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM queue_items WHERE id=?", (item_id,)).fetchone()
            if row is None or row["status"] != "RUNNING":
                raise ValueError("only a RUNNING queue item can finish")
            db.execute(
                """
                UPDATE queue_items
                SET status=?, result=?, error=?, ended_at=?
                WHERE id=?
                """,
                (
                    status,
                    json.dumps(result, sort_keys=True, allow_nan=False) if result is not None else None,
                    error,
                    time.time(),
                    item_id,
                ),
            )
            finished = db.execute("SELECT * FROM queue_items WHERE id=?", (item_id,)).fetchone()
        decoded = self._decode(finished)
        self._emit(decoded)
        return decoded

    def complete(self, item_id: str, result: dict | None = None) -> dict:
        return self._finish(item_id, "COMPLETE", result=result)

    def fail(self, item_id: str, error: str) -> dict:
        if not error:
            raise ValueError("failed queue item requires an error")
        return self._finish(item_id, "FAILED", error=error)

    def requeue_running(self) -> int:
        """Explicitly recover interrupted workers without inventing completion."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            count = db.execute(
                "UPDATE queue_items SET status='QUEUED', worker=NULL, started_at=NULL "
                "WHERE status='RUNNING'"
            ).rowcount
        return count

    def requeue_failed(self, item_id: str) -> dict:
        """Explicitly retry through an idempotent/recover-only handler."""
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM queue_items WHERE id=?", (item_id,)).fetchone()
            if row is None or row["status"] != "FAILED":
                raise ValueError("only a FAILED queue item can be requeued")
            materialized = db.execute(
                "SELECT COUNT(*) FROM queue_items WHERE status IN ('QUEUED','RUNNING')"
            ).fetchone()[0]
            if materialized >= self.workers + self.prefetch:
                raise RuntimeError("queue worker + prefetch capacity exhausted")
            db.execute(
                """
                UPDATE queue_items
                SET status='QUEUED', worker=NULL, started_at=NULL, ended_at=NULL, error=NULL
                WHERE id=?
                """,
                (item_id,),
            )
            queued = db.execute("SELECT * FROM queue_items WHERE id=?", (item_id,)).fetchone()
        decoded = self._decode(queued)
        self._emit(decoded)
        return decoded


async def run_bounded_wave(
    queue: DurableQueue,
    items,
    handler,
    *,
    recover_failed: bool = False,
) -> dict:
    """Serialize queue recovery while jobs within one wave run concurrently."""
    active: dict[asyncio.Task, str] = {}
    wave_lock = queue.database.with_name(f"{queue.database.name}.wave.lock")
    async with file_slot([wave_lock]):
        try:
            return await _run_bounded_wave(
                queue,
                items,
                handler,
                recover_failed=recover_failed,
                active=active,
            )
        finally:
            for pending in active:
                pending.cancel()
            await asyncio.gather(*active, return_exceptions=True)
            queue.requeue_running()


async def _run_bounded_wave(
    queue: DurableQueue,
    items,
    handler,
    *,
    recover_failed: bool,
    active: dict[asyncio.Task, str],
) -> dict:
    """Run a finite wave while the durable queue enforces workers + prefetch."""
    ordered = tuple(items)
    by_id = {item_id: item for item_id, item in ordered}
    if len(by_id) != len(ordered):
        raise ValueError("wave item IDs must be unique")
    queue.requeue_running()
    next_index = 0
    results = {}

    while len(results) < len(ordered):
        while next_index < len(ordered):
            item_id, item = ordered[next_index]
            existing = queue.inspect(item_id)
            if existing is not None:
                next_index += 1
                if existing["status"] == "FAILED":
                    if not recover_failed:
                        raise RuntimeError(f"queued wave item failed: {item_id}")
                    queue.requeue_failed(item_id)
                if existing["status"] == "COMPLETE":
                    results[item_id] = await handler(item)
                continue
            job_payload = getattr(item, "payload", {})
            prompt = job_payload.get("prompt", "") if isinstance(job_payload, dict) else ""
            image_size = job_payload.get("image_size") if isinstance(job_payload, dict) else None
            try:
                queue.enqueue(
                    item_id,
                    {
                        "scene": getattr(item, "scene", None),
                        "category": getattr(item, "category", None),
                        "provider": getattr(item, "endpoint", None),
                        "prompt_hash": digest(prompt) if prompt else None,
                        "seed": job_payload.get("seed") if isinstance(job_payload, dict) else None,
                        "resolution": image_size,
                        "cost": (
                            str(item.cost)
                            if getattr(item, "cost", None) is not None
                            else None
                        ),
                    },
                )
            except RuntimeError:
                break
            next_index += 1

        while len(active) < queue.workers:
            claimed = queue.claim(f"worker-{len(active) + 1}")
            if claimed is None:
                break
            item_id = claimed["id"]
            if item_id not in by_id:
                queue.fail(item_id, "wave item unavailable after resume")
                raise RuntimeError("durable queue contains an unavailable wave item")
            active[asyncio.create_task(handler(by_id[item_id]))] = item_id

        if not active:
            if len(results) == len(ordered):
                break
            raise RuntimeError("bounded wave made no progress")

        try:
            done, _ = await asyncio.wait(active, return_when=asyncio.FIRST_COMPLETED)
        except BaseException:
            for pending in active:
                pending.cancel()
            await asyncio.gather(*active, return_exceptions=True)
            queue.requeue_running()
            raise
        for task in done:
            item_id = active.pop(task)
            try:
                result = task.result()
            except Exception as exc:
                queue.fail(item_id, f"{type(exc).__name__}: {exc}")
                for pending in active:
                    pending.cancel()
                await asyncio.gather(*active, return_exceptions=True)
                queue.requeue_running()
                raise
            summary = {"request_id": item_id}
            if isinstance(result, dict):
                summary.update(
                    result_sha256=result.get("result_sha256"),
                    cost=result.get("actual_cost"),
                    cache_hit=result.get("cache_hit"),
                )
                result_path = result.get("result")
                if result_path and Path(result_path).is_file():
                    summary["bytes"] = Path(result_path).stat().st_size
            queue.complete(item_id, summary)
            results[item_id] = result
    return results
