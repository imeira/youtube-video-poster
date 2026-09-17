"""Fenced durable single-writer leases for episode orchestration."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EpisodeLease:
    episode_id: str
    run_id: str
    fencing_token: int
    acquired_at: float
    heartbeat_at: float
    expires_at: float


class EpisodeLeaseStore:
    def __init__(self, database: Path | str):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS episode_leases ("
                "episode_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, fencing_token INTEGER NOT NULL, "
                "acquired_at REAL NOT NULL, heartbeat_at REAL NOT NULL, expires_at REAL NOT NULL)"
            )

    def _db(self):
        return sqlite3.connect(self.database, timeout=30)

    @staticmethod
    def _lease(row) -> EpisodeLease:
        return EpisodeLease(*row)

    def acquire(self, episode_id: str, run_id: str, *, now: float, ttl_seconds: float) -> EpisodeLease:
        if not episode_id or not run_id or ttl_seconds <= 0:
            raise ValueError("episode ID, run ID and positive lease TTL are required")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT episode_id, run_id, fencing_token, acquired_at, heartbeat_at, expires_at "
                "FROM episode_leases WHERE episode_id=?", (episode_id,)
            ).fetchone()
            if row and row[1] != run_id and row[5] > now:
                raise RuntimeError("episode lease is held by another run")
            token = (row[2] + 1) if row and row[1] != run_id else (row[2] if row else 1)
            acquired_at = row[3] if row and row[1] == run_id else now
            lease = EpisodeLease(episode_id, run_id, token, acquired_at, now, now + ttl_seconds)
            db.execute(
                "INSERT OR REPLACE INTO episode_leases VALUES (?, ?, ?, ?, ?, ?)",
                (lease.episode_id, lease.run_id, lease.fencing_token, lease.acquired_at, lease.heartbeat_at, lease.expires_at),
            )
            return lease

    def assert_active(self, lease: EpisodeLease, *, now: float) -> None:
        with self._db() as db:
            row = db.execute(
                "SELECT episode_id, run_id, fencing_token, acquired_at, heartbeat_at, expires_at "
                "FROM episode_leases WHERE episode_id=?", (lease.episode_id,)
            ).fetchone()
        if row is None or self._lease(row) != lease or lease.expires_at <= now:
            raise RuntimeError("stale episode lease")

    def release(self, lease: EpisodeLease, *, now: float) -> None:
        self.assert_active(lease, now=now)
        with self._db() as db:
            db.execute("DELETE FROM episode_leases WHERE episode_id=? AND run_id=? AND fencing_token=?", (lease.episode_id, lease.run_id, lease.fencing_token))
