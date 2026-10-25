"""Explicit, bounded recovery of the Director's second Telegram gate.

The inbound service writes a hash-bound video-delivery eligibility row after a
thumbnail approval.  This runner is the only consumer: one explicit invocation
handles at most one row, and delegates every send to the existing Director /
DeliveryController protocol.  It never starts a server, polls, generates media,
or publishes.
"""
from __future__ import annotations

import asyncio
import hashlib
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
    def _sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def enqueue_video(self, *, episode_id: str, thumbnail_path: Path | str, thumbnail_sha256: str) -> None:
        """Idempotently persist eligibility; inbound confirmation is its normal caller."""
        path = Path(thumbnail_path)
        if not episode_id.strip() or len(thumbnail_sha256) != 64 or any(c not in "0123456789abcdef" for c in thumbnail_sha256):
            raise ValueError("video delivery eligibility must be bound to an episode and SHA-256")
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS video_delivery_outbox ("
                "episode_id TEXT PRIMARY KEY, thumbnail_path TEXT NOT NULL, thumbnail_sha256 TEXT NOT NULL, "
                "status TEXT NOT NULL CHECK(status IN ('PENDING', 'DELIVERED')))"
            )
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
                return {"status": "blocked", "episode_id": episode_id, "reason": "thumbnail unavailable"}
            if actual_sha256 != expected_sha256:
                return {"status": "blocked", "episode_id": episode_id, "reason": "thumbnail hash changed"}
            try:
                await asyncio.to_thread(
                    self.composition.revalidate_thumbnail_approval,
                    episode_id=episode_id, thumbnail_path=thumbnail_path, thumbnail_sha256=expected_sha256,
                )
            except (AttributeError, OSError, ValueError):
                return {
                    "status": "blocked", "episode_id": episode_id,
                    "reason": "thumbnail approval is no longer authenticated",
                }
            receipt = await self.composition.deliver_for_approval(episode_id, thumbnail_path=thumbnail_path)
            if not isinstance(receipt, dict) or receipt.get("artifact_kind") != "video":
                raise ValueError("Director delivery session did not produce the video gate receipt")
            self._mark_delivered(episode_id)
            return {"status": "delivered", "episode_id": episode_id, "artifact_kind": "video"}

    def _mark_delivered(self, episode_id: str) -> None:
        """Acknowledge only after the Director's idempotent delivery protocol returned a receipt."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE video_delivery_outbox SET status = 'DELIVERED' WHERE episode_id = ? AND status = 'PENDING'",
                (episode_id,),
            )
