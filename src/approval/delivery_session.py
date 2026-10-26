"""Durable delivery of the Director's second Telegram gate.

The inbound service writes a hash-bound video-delivery eligibility row after a
thumbnail approval.  The runner performs one explicit manual-recovery attempt;
the hosted worker below is the routine consumer. Neither starts a server, polls,
generates media, or publishes.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import sqlite3
from pathlib import Path
from typing import Any

from src.delivery.controller import DeliveryController


class DeliverySessionRunner:
    """Drain at most one durable, hash-bound video delivery eligibility item."""

    def __init__(self, *, registry_path: Path | str, composition: Any) -> None:
        self.registry_path = Path(registry_path)
        self.composition = composition

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.registry_path, timeout=10, isolation_level=None)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _ensure_outbox_schema(connection: sqlite3.Connection) -> None:
        """Migrate the original two-state queue without discarding durable rows."""
        sql_row = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'video_delivery_outbox'"
        ).fetchone()
        sql = "" if sql_row is None else str(sql_row[0] or "")
        if sql_row is None:
            connection.execute(
                "CREATE TABLE video_delivery_outbox (episode_id TEXT PRIMARY KEY, thumbnail_path TEXT NOT NULL, "
                "thumbnail_sha256 TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN "
                "('PENDING', 'BLOCKED', 'UNKNOWN', 'DELIVERED')), reason TEXT)"
            )
        elif "reason" not in sql or "BLOCKED" not in sql or "UNKNOWN" not in sql:
            connection.execute("ALTER TABLE video_delivery_outbox RENAME TO video_delivery_outbox_legacy")
            connection.execute(
                "CREATE TABLE video_delivery_outbox (episode_id TEXT PRIMARY KEY, thumbnail_path TEXT NOT NULL, "
                "thumbnail_sha256 TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN "
                "('PENDING', 'BLOCKED', 'UNKNOWN', 'DELIVERED')), reason TEXT)"
            )
            connection.execute(
                "INSERT INTO video_delivery_outbox(episode_id, thumbnail_path, thumbnail_sha256, status) "
                "SELECT episode_id, thumbnail_path, thumbnail_sha256, status FROM video_delivery_outbox_legacy"
            )
            connection.execute("DROP TABLE video_delivery_outbox_legacy")

    @staticmethod
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def enqueue_video(self, *, episode_id: str, thumbnail_path: Path | str, thumbnail_sha256: str) -> None:
        """Idempotently persist eligibility; inbound confirmation is its normal caller."""
        path = Path(thumbnail_path)
        if not episode_id.strip() or len(thumbnail_sha256) != 64 or any(c not in "0123456789abcdef" for c in thumbnail_sha256):
            raise ValueError("video delivery eligibility must be bound to an episode and SHA-256")
        with self._connect() as connection:
            self._ensure_outbox_schema(connection)
            existing = connection.execute(
                "SELECT thumbnail_path, thumbnail_sha256, status FROM video_delivery_outbox WHERE episode_id = ?", (episode_id,)
            ).fetchone()
            expected = (str(path.resolve()), thumbnail_sha256)
            if existing is None:
                connection.execute(
                    "INSERT INTO video_delivery_outbox(episode_id, thumbnail_path, thumbnail_sha256, status) VALUES (?, ?, ?, 'PENDING')",
                    (episode_id, *expected),
                )
            elif existing[:2] != expected:
                raise ValueError("video delivery eligibility is already bound to different thumbnail bytes")

    def peek(self) -> dict[str, str]:
        """Preflight deliberately does not open SQLite or inspect the mutable queue."""
        return {"status": "ready", "queue": "NOT_INSPECTED"}

    async def drain_once(self) -> dict[str, str]:
        """Perform zero or one recovery delivery; no polling or automatic retry loop."""
        async with DeliveryController._gate_lock(self.registry_path.with_suffix(".outbox.lock")):
            with self._connect() as connection:
                self._ensure_outbox_schema(connection)
                row = connection.execute(
                    "SELECT episode_id, thumbnail_path, thumbnail_sha256 FROM video_delivery_outbox "
                    "WHERE status = 'PENDING' ORDER BY episode_id LIMIT 1"
                ).fetchone()
            if row is None:
                return {"status": "idle"}
            episode_id, stored_path, expected_sha256 = row
            thumbnail_path = Path(stored_path)
            try:
                actual_sha256 = await asyncio.to_thread(self._sha256, thumbnail_path)
            except OSError:
                return self._mark_blocked(episode_id, "thumbnail unavailable")
            if actual_sha256 != expected_sha256:
                return self._mark_blocked(episode_id, "thumbnail hash changed")
            try:
                await asyncio.to_thread(
                    self.composition.revalidate_thumbnail_approval,
                    episode_id=episode_id, thumbnail_path=thumbnail_path, thumbnail_sha256=expected_sha256,
                )
            except (AttributeError, OSError, ValueError):
                return self._mark_blocked(episode_id, "thumbnail approval is no longer authenticated")
            self._active_episode_id = episode_id
            try:
                receipt = await self.composition.deliver_for_approval(episode_id, thumbnail_path=thumbnail_path)
            except asyncio.CancelledError:
                self._mark_unknown(episode_id, "delivery cancelled; manual reconciliation required")
                raise
            except Exception:  # noqa: BLE001 - no POST retry is safe after entering the delivery protocol
                return self._mark_unknown(episode_id, "delivery outcome ambiguous; manual reconciliation required")
            finally:
                self._active_episode_id = None
            if (
                not isinstance(receipt, dict)
                or receipt.get("artifact_kind") != "video"
                or type(receipt.get("message_id")) is not int
                or receipt["message_id"] <= 0
            ):
                return self._mark_unknown(episode_id, "delivery receipt invalid; manual reconciliation required")
            try:
                self._mark_delivered(episode_id)
            except Exception:  # noqa: BLE001 - an acknowledgement failure is post-submit ambiguity
                return self._mark_unknown(episode_id, "delivery acknowledgement failed; manual reconciliation required")
            return {"status": "delivered", "episode_id": episode_id, "artifact_kind": "video"}

    def _mark_delivered(self, episode_id: str) -> None:
        """Acknowledge only after the Director's idempotent delivery protocol returned a receipt."""
        with self._connect() as connection:
            self._ensure_outbox_schema(connection)
            connection.execute(
                "UPDATE video_delivery_outbox SET status = 'DELIVERED', reason = NULL WHERE episode_id = ? AND status = 'PENDING'",
                (episode_id,),
            )

    def _mark_blocked(self, episode_id: str, reason: str) -> dict[str, str]:
        with self._connect() as connection:
            self._ensure_outbox_schema(connection)
            connection.execute(
                "UPDATE video_delivery_outbox SET status = 'BLOCKED', reason = ? WHERE episode_id = ? AND status = 'PENDING'",
                (reason, episode_id),
            )
        return {"status": "blocked", "episode_id": episode_id, "reason": reason}

    def _mark_unknown(self, episode_id: str, reason: str) -> dict[str, str]:
        with self._connect() as connection:
            self._ensure_outbox_schema(connection)
            connection.execute(
                "UPDATE video_delivery_outbox SET status = 'UNKNOWN', reason = ? WHERE episode_id = ? AND status = 'PENDING'",
                (reason, episode_id),
            )
        return {"status": "unknown", "episode_id": episode_id, "reason": reason}

    def mark_active_unknown(self) -> None:
        """Persist the cancellation barrier before a hosted worker cancels I/O."""
        episode_id = getattr(self, "_active_episode_id", None)
        if episode_id is not None:
            self._mark_unknown(episode_id, "delivery cancelled; manual reconciliation required")


class DeliveryOutboxWorker:
    """One event-driven hosted consumer; durable SQLite remains the queue truth."""

    def __init__(
        self, runner: DeliverySessionRunner, *, reconcile_period_seconds: float = 60.0,
        shutdown_timeout_seconds: float = 30.0,
    ) -> None:
        for value, label in (
            (reconcile_period_seconds, "outbox reconcile period"),
            (shutdown_timeout_seconds, "outbox shutdown timeout"),
        ):
            if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{label} must be a positive finite number")
        self._runner = runner
        self._reconcile_period_seconds = reconcile_period_seconds
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._wakeup = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._stopping = False
        self._task: asyncio.Task[None] | None = None
        self._last_task_exception: BaseException | None = None

    async def start(self) -> None:
        """Start one worker and request a startup recovery drain exactly once."""
        if self._task is not None:
            raise RuntimeError("delivery outbox worker is already running")
        self._stopping = False
        self._idle.clear()
        self._wakeup.set()
        self._task = asyncio.create_task(self._run(), name="telegram-delivery-outbox")
        self._task.add_done_callback(self._reap_task)

    def _reap_task(self, task: asyncio.Task[None]) -> None:
        """Observe late completion without releasing an owned live task early."""
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except BaseException as error:  # noqa: BLE001 - retain hosted lifecycle failure for inspection
            self._last_task_exception = error
        finally:
            if self._task is task:
                self._task = None

    def wake(self) -> None:
        """Coalesce a post-commit hint; callers never supply queue authority."""
        if self._task is not None and not self._stopping:
            self._wakeup.set()

    async def wait_idle(self) -> None:
        """Deterministic test/host synchronization without a timed polling loop."""
        await self._idle.wait()

    async def stop(self) -> dict[str, str]:
        """Bound host shutdown without claiming an unkillable attempt has stopped.

        The concrete provider uses cancellable asyncio sockets.  At deadline we
        cancel that owned transport only after ``drain_once`` durably marks its
        intent UNKNOWN; its task and interprocess lock remain owned until the
        cancellation unwinds.  A remote server may still finish a POST already
        received, so UNKNOWN rows are never automatically dispatched again.
        """
        task = self._task
        if task is None:
            return {"status": "stopped", "ownership": "none"}
        self._stopping = True
        self._wakeup.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=self._shutdown_timeout_seconds)
        except TimeoutError:
            # Do not clear _task or release ownership merely because the host's
            # deadline elapsed.  asyncio transport cancellation closes the socket;
            # drain_once persists UNKNOWN before its lock unwinds.
            self._runner.mark_active_unknown()
            task.cancel()
            return {"status": "stopping", "ownership": "retained"}
        except asyncio.CancelledError:
            self._runner.mark_active_unknown()
            task.cancel()
            raise
        else:
            outcome = {"status": "stopped", "ownership": "released"}
        # The done callback reaps completion.  Only it releases a retained task
        # reference, so an instance cannot start a second worker while old I/O owns
        # the duplicate-prevention lock.
        self._task = None
        return outcome

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=self._reconcile_period_seconds)
            except TimeoutError:
                pass
            self._wakeup.clear()
            self._idle.clear()
            while True:
                try:
                    result = await self._runner.drain_once()
                except (OSError, sqlite3.Error, RuntimeError, ValueError):
                    # An unacknowledged row is durable; wait for a future explicit hint/restart.
                    break
                if result.get("status") == "idle":
                    break
            self._idle.set()
            if self._stopping:
                return
