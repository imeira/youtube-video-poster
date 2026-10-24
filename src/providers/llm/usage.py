"""Sanitized invocation telemetry; subscription tokens are not API charges."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import wraps
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

_EPISODE = ContextVar("studio_usage_episode", default="unattributed")


@contextmanager
def usage_scope(episode_id):
    token = _EPISODE.set(str(episode_id))
    try:
        yield
    finally:
        _EPISODE.reset(token)


def track_episode_usage(function):
    @wraps(function)
    async def wrapped(self, episode_id, *args, **kwargs):
        with usage_scope(episode_id):
            return await function(self, episode_id, *args, **kwargs)

    return wrapped


class UsageStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path, timeout=30)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS calls (
              invocation_id TEXT PRIMARY KEY, episode_id TEXT NOT NULL, task TEXT NOT NULL,
              model TEXT NOT NULL, prompt_sha256 TEXT NOT NULL, started_at TEXT NOT NULL,
              status TEXT NOT NULL, usage_json TEXT, latency_s REAL,
              billing_kind TEXT NOT NULL, money_usd TEXT, quota_units REAL)""")
            yield db

    def begin(self, *, model, task, prompt):
        invocation = uuid4().hex
        with self._db() as db:
            db.execute(
                "INSERT INTO calls VALUES(?,?,?,?,?,?,?,NULL,NULL,?,NULL,NULL)",
                (
                    invocation,
                    _EPISODE.get(),
                    task,
                    model,
                    sha256(prompt.encode("utf-8")).hexdigest(),
                    datetime.now(UTC).isoformat(),
                    "STARTED",
                    "subscription_quota",
                ),
            )
        return invocation

    def finish(self, invocation_id, *, status, usage, latency_s):
        if status not in {"COMPLETE", "FAILED", "TIMEOUT", "CANCELLED"}:
            raise ValueError("invalid invocation status")
        if usage is not None and (
            not isinstance(usage, dict)
            or any(type(v) is not int or v < 0 for v in usage.values())
        ):
            raise ValueError("usage must contain nonnegative integer counts")
        with self._db() as db:
            changed = db.execute(
                "UPDATE calls SET status=?,usage_json=?,latency_s=? WHERE invocation_id=? AND status='STARTED'",
                (
                    status,
                    None if usage is None else json.dumps(usage, sort_keys=True),
                    latency_s,
                    invocation_id,
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("invocation absent or already settled")
