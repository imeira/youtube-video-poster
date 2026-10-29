"""Fenced durable single-writer leases for episode orchestration."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from src.hybrid.locks import file_lock


@dataclass(frozen=True)
class EpisodeLease:
    episode_id: str
    run_id: str
    fencing_token: int
    acquired_at: float
    heartbeat_at: float
    expires_at: float


class EpisodeLeaseStore:
    """One active lease plus an irreversible epoch tombstone per episode."""

    def __init__(self, database: Path | str):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.database.with_suffix(self.database.suffix + ".bootstrap.lock")), self._db() as db:
                if db.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
                    raise RuntimeError("lease database did not enter WAL mode")
                db.execute("CREATE TABLE IF NOT EXISTS episode_leases ("
                           "episode_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, fencing_token INTEGER NOT NULL, "
                           "acquired_at REAL NOT NULL, heartbeat_at REAL NOT NULL, expires_at REAL NOT NULL)")
                db.execute("CREATE TABLE IF NOT EXISTS episode_lease_epochs ("
                           "episode_id TEXT PRIMARY KEY, last_epoch INTEGER NOT NULL)")

    @contextmanager
    def _db(self):
        """Commit/rollback and close every lease connection deterministically."""
        db = sqlite3.connect(self.database, timeout=30)
        try:
            yield db
        except BaseException:
            db.rollback()
            raise
        else:
            db.commit()
        finally:
            db.close()

    @staticmethod
    def _lease(row) -> EpisodeLease:
        return EpisodeLease(*row)

    def acquire(self, episode_id: str, run_id: str, *, now: float, ttl_seconds: float) -> EpisodeLease:
        if not episode_id or not run_id or ttl_seconds <= 0:
            raise ValueError("episode ID, run ID and positive lease TTL are required")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT episode_id, run_id, fencing_token, acquired_at, heartbeat_at, expires_at "
                             "FROM episode_leases WHERE episode_id=?", (episode_id,)).fetchone()
            if row and row[1] != run_id and row[5] > now:
                raise RuntimeError("episode lease is held by another run")
            if row and row[1] == run_id and row[5] > now:
                lease = EpisodeLease(episode_id, run_id, row[2], row[3], now, now + ttl_seconds)
                db.execute("UPDATE episode_leases SET heartbeat_at=?, expires_at=? WHERE episode_id=? AND run_id=? AND fencing_token=?",
                           (lease.heartbeat_at, lease.expires_at, lease.episode_id, lease.run_id, lease.fencing_token))
                return lease
            prior = db.execute("SELECT last_epoch FROM episode_lease_epochs WHERE episode_id=?", (episode_id,)).fetchone()
            epoch = (prior[0] if prior else 0) + 1
            db.execute("INSERT INTO episode_lease_epochs(episode_id,last_epoch) VALUES(?,?) "
                       "ON CONFLICT(episode_id) DO UPDATE SET last_epoch=excluded.last_epoch", (episode_id, epoch))
            lease = EpisodeLease(episode_id, run_id, epoch, now, now, now + ttl_seconds)
            db.execute("INSERT INTO episode_leases VALUES (?, ?, ?, ?, ?, ?) "
                       "ON CONFLICT(episode_id) DO UPDATE SET run_id=excluded.run_id, fencing_token=excluded.fencing_token, "
                       "acquired_at=excluded.acquired_at, heartbeat_at=excluded.heartbeat_at, expires_at=excluded.expires_at",
                       (lease.episode_id, lease.run_id, lease.fencing_token, lease.acquired_at, lease.heartbeat_at, lease.expires_at))
            return lease

    def renew(self, lease: EpisodeLease, *, now: float, ttl_seconds: float) -> EpisodeLease:
        if ttl_seconds <= 0:
            raise ValueError("positive lease TTL is required")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT episode_id, run_id, fencing_token, acquired_at, heartbeat_at, expires_at "
                             "FROM episode_leases WHERE episode_id=?", (lease.episode_id,)).fetchone()
            if row is None or self._lease(row) != lease or lease.expires_at <= now:
                raise RuntimeError("stale episode lease")
            renewed = EpisodeLease(lease.episode_id, lease.run_id, lease.fencing_token, lease.acquired_at, now, now + ttl_seconds)
            db.execute("UPDATE episode_leases SET heartbeat_at=?, expires_at=? WHERE episode_id=? AND run_id=? AND fencing_token=?",
                       (renewed.heartbeat_at, renewed.expires_at, renewed.episode_id, renewed.run_id, renewed.fencing_token))
            return renewed

    def assert_active(self, lease: EpisodeLease, *, now: float) -> None:
        with self._db() as db:
            row = db.execute("SELECT episode_id, run_id, fencing_token, acquired_at, heartbeat_at, expires_at "
                             "FROM episode_leases WHERE episode_id=?", (lease.episode_id,)).fetchone()
        if row is None or self._lease(row) != lease or lease.expires_at <= now:
            raise RuntimeError("stale episode lease")

    def release(self, lease: EpisodeLease, *, now: float) -> None:
        self.assert_active(lease, now=now)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("DELETE FROM episode_leases WHERE episode_id=? AND run_id=? AND fencing_token=?",
                          (lease.episode_id, lease.run_id, lease.fencing_token)).rowcount != 1:
                raise RuntimeError("stale episode lease")