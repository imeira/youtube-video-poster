"""Durable TEST-only episode queue with fenced ownership."""
from __future__ import annotations

import asyncio
import json
import math
import os
import sqlite3
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

from src.hybrid.lease import EpisodeLeaseStore
from src.hybrid.locks import file_lock, reject_reparse_path


class EpisodeTaskQueue:
    _SNAPSHOT_SCHEMA = "episode-task-queue-snapshot-v1"
    _SNAPSHOT_MAX_AGE_SECONDS = 300
    _FIELDS = ("episode_id", "root", "kind", "status", "next_action", "fencing_token", "worker_id", "lease_epoch", "gate_hash", "attempts", "updated_at", "state")

    def __init__(self, database: Path | str):
        self.database = Path(database)
        self.snapshot_path = self.database.with_suffix(".snapshot.json")
        self.writer_lock = self.database.with_suffix(self.database.suffix + ".writer.lock")
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.leases = EpisodeLeaseStore(self.database.with_name(self.database.stem + ".leases.sqlite3"))
        with file_lock(self.writer_lock), file_lock(self.database.with_suffix(self.database.suffix + ".bootstrap.lock")), self._db() as db:
                if db.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() != "wal":
                    raise RuntimeError("queue database did not enter WAL mode")
                db.execute("""CREATE TABLE IF NOT EXISTS episode_tasks (
                    episode_id TEXT PRIMARY KEY, root TEXT NOT NULL, kind TEXT NOT NULL,
                    status TEXT NOT NULL, next_action TEXT NOT NULL, fencing_token INTEGER,
                    worker_id TEXT, lease_epoch INTEGER, gate_hash TEXT,
                    attempts INTEGER NOT NULL, updated_at REAL NOT NULL,
                    runner_state TEXT NOT NULL DEFAULT 'READY')""")
                columns = {r[1] for r in db.execute("PRAGMA table_info(episode_tasks)")}
                for name, kind in (("worker_id", "TEXT"), ("lease_epoch", "INTEGER"), ("gate_hash", "TEXT"), ("runner_state", "TEXT NOT NULL DEFAULT 'READY'")):
                    if name not in columns:
                        db.execute(f"ALTER TABLE episode_tasks ADD COLUMN {name} {kind}")
                self._write_snapshot("CURRENT", self._rows(db))

    @contextmanager
    def _db(self):
        """Commit/rollback and close every writer connection deterministically."""
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
    def _canonical(value) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")

    def _write_snapshot(self, status: str, tasks=None) -> None:
        document = {"schema": self._SNAPSHOT_SCHEMA, "status": status, "generated_at_utc": datetime.now(UTC).isoformat()}
        if tasks is not None:
            document["tasks"] = tasks
        document["snapshot_sha256"] = sha256(self._canonical(document)).hexdigest()
        with NamedTemporaryFile("wb", dir=self.snapshot_path.parent, prefix=self.snapshot_path.name + ".", delete=False) as output:
            output.write(self._canonical(document)); output.flush(); os.fsync(output.fileno()); temporary = Path(output.name)
        try:
            os.replace(temporary, self.snapshot_path)
        finally:
            temporary.unlink(missing_ok=True)

    def _rows(self, db):
        return [dict(zip(self._FIELDS, row)) for row in db.execute("SELECT episode_id,root,kind,status,next_action,fencing_token,worker_id,lease_epoch,gate_hash,attempts,updated_at,runner_state FROM episode_tasks ORDER BY episode_id")]

    def _publish(self, db) -> None:
        db.commit()
        # Make the committed projection stable before a filesystem-only status
        # reader can inspect its replacement snapshot.  This preserves WAL mode;
        # PASSIVE never waits for or forces another reader.
        db.execute("PRAGMA wal_checkpoint(PASSIVE)")
        self._write_snapshot("CURRENT", self._rows(db))

    @classmethod
    def _strict_snapshot_json(cls, raw: bytes) -> dict:
        """Parse one closed snapshot without accepting duplicate or non-finite JSON."""
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate snapshot JSON key")
                result[key] = value
            return result

        def reject_constant(_value):
            raise ValueError("non-finite snapshot JSON number")

        document = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject_constant)
        if not isinstance(document, dict):
            raise TypeError("queue snapshot must be an object")
        return document

    @classmethod
    def _valid_snapshot_row(cls, row: object, episode_id: str) -> bool:
        if not isinstance(row, dict) or set(row) != set(cls._FIELDS) or row.get("episode_id") != episode_id:
            return False
        nullable_ints = ("fencing_token", "lease_epoch")
        nullable_strings = ("worker_id", "gate_hash")
        if (not isinstance(row["episode_id"], str) or not row["episode_id"]
                or not isinstance(row["root"], str) or not row["root"]
                or row["kind"] not in {"fixture", "compiled"}
                or row["status"] not in {"PENDING", "RUNNING", "WAITING", "DONE", "UNKNOWN_REQUIRES_RECONCILIATION"}
                or not isinstance(row["next_action"], str)
                or not isinstance(row["state"], str)
                or type(row["attempts"]) is not int or row["attempts"] < 0
                or type(row["updated_at"]) not in {int, float}
                or not math.isfinite(row["updated_at"])):
            return False
        if any(row[name] is not None and (type(row[name]) is not int or row[name] < 0) for name in nullable_ints):
            return False
        if any(row[name] is not None and not isinstance(row[name], str) for name in nullable_strings):
            return False
        return row["gate_hash"] is None or (len(row["gate_hash"]) == 64 and all(char in "0123456789abcdef" for char in row["gate_hash"]))

    @classmethod
    def read_status(cls, database: Path | str, episode_id: str, *, now: datetime | None = None) -> dict:
        """Read only writer-owned JSON; never open queue SQLite or create a sidecar."""
        path = Path(database).with_suffix(".snapshot.json")
        if not path.is_file():
            return {"status": "UNAVAILABLE", "snapshot": "STALE", "reason": "queue snapshot is missing"}
        try:
            document = cls._strict_snapshot_json(path.read_bytes())
            body = {key: value for key, value in document.items() if key != "snapshot_sha256"}
            supplied_hash = document.get("snapshot_sha256")
            if (set(document) != {"schema", "status", "generated_at_utc", "tasks", "snapshot_sha256"}
                    or document["schema"] != cls._SNAPSHOT_SCHEMA or document["status"] != "CURRENT"
                    or not isinstance(supplied_hash, str) or len(supplied_hash) != 64
                    or any(char not in "0123456789abcdef" for char in supplied_hash)
                    or supplied_hash != sha256(cls._canonical(body)).hexdigest()):
                raise ValueError
            generated, current = datetime.fromisoformat(document["generated_at_utc"]), (datetime.now(UTC) if now is None else now)
            if (not isinstance(document["generated_at_utc"], str) or not isinstance(current, datetime)
                    or generated.tzinfo is None or generated.utcoffset() != UTC.utcoffset(generated)
                    or current.tzinfo is None or not 0 <= (current.astimezone(UTC) - generated.astimezone(UTC)).total_seconds() <= cls._SNAPSHOT_MAX_AGE_SECONDS):
                raise ValueError
            rows = document["tasks"]
            if not isinstance(rows, list):
                raise TypeError("queue snapshot tasks must be a list")
            row = next((item for item in rows if cls._valid_snapshot_row(item, episode_id)), None)
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, json.JSONDecodeError, AttributeError):
            return {"status": "UNAVAILABLE", "snapshot": "STALE", "reason": "queue snapshot is invalid"}
        if row is None:
            return {"status": "UNAVAILABLE", "snapshot": "STALE", "reason": "episode task is not queued"}
        # This verifies byte integrity only.  It is intentionally a local status
        # projection, never approval, provider, billing, or publication authority.
        return {key: value for key, value in row.items() if key != "root"} | {"snapshot": "CURRENT", "authority": "LOCAL_STATUS_ONLY"}

    def _row(self, episode_id):
        with self._db() as db:
            row = db.execute("SELECT episode_id,root,kind,status,next_action,fencing_token,worker_id,lease_epoch,gate_hash,attempts,updated_at,runner_state FROM episode_tasks WHERE episode_id=?", (episode_id,)).fetchone()
        if row is None:
            raise ValueError("episode task is not queued")
        return dict(zip(self._FIELDS, row))

    def status(self, episode_id: str) -> dict:
        return self._row(episode_id)

    def enqueue(self, episode_id: str, root: str, kind: str) -> dict:
        if not episode_id or not root or kind not in {"fixture", "compiled"}:
            raise ValueError("episode ID, root, and known TEST task kind are required")
        with file_lock(self.writer_lock), self._db() as db:
            self._write_snapshot("WRITER_IN_PROGRESS")
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT root,kind FROM episode_tasks WHERE episode_id=?", (episode_id,)).fetchone()
            if row and row != (root, kind):
                raise ValueError("episode task conflicts with durable queue binding")
            if not row:
                db.execute("INSERT INTO episode_tasks(episode_id,root,kind,status,next_action,fencing_token,worker_id,lease_epoch,gate_hash,attempts,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (episode_id,root,kind,"PENDING","RESUME",None,None,None,None,0,time.time()))
            self._publish(db)
        return self.status(episode_id)

    def next_pending(self):
        with self._db() as db:
            row = db.execute("SELECT episode_id FROM episode_tasks WHERE status IN ('PENDING','RUNNING') ORDER BY updated_at,episode_id LIMIT 1").fetchone()
        return None if row is None else self.status(row[0])

    def _active(self, lease, episode_id, worker_id, now):
        if getattr(lease, "episode_id", None) != episode_id or not getattr(lease, "run_id", "").startswith(worker_id + ":"):
            raise RuntimeError("stale worker/lease cannot mutate task")
        self.leases.assert_active(lease, now=now)

    def claim(self, episode_id: str, worker_id: str, lease, *, now=None) -> dict:
        now = time.time() if now is None else now
        with file_lock(self.writer_lock), self._db() as db:
            self._write_snapshot("WRITER_IN_PROGRESS")
            db.execute("BEGIN IMMEDIATE")
            self._active(lease, episode_id, worker_id, now)
            row = db.execute("SELECT status FROM episode_tasks WHERE episode_id=?", (episode_id,)).fetchone()
            if row is None or row[0] not in {"PENDING", "RUNNING"}:
                raise RuntimeError("task is not eligible for recovery")
            db.execute("UPDATE episode_tasks SET status='RUNNING',next_action='RESUME',fencing_token=?,worker_id=?,lease_epoch=?,attempts=attempts+1,updated_at=? WHERE episode_id=?", (lease.fencing_token,worker_id,lease.fencing_token,now,episode_id))
            self._publish(db)
        return self.status(episode_id)

    def complete(self, episode_id: str, worker_id: str, lease, outcome: dict, *, now=None) -> dict:
        state, action = outcome.get("state"), outcome.get("next_action")
        if not isinstance(state, str) or not isinstance(action, str):
            raise TypeError("worker outcome requires state and next_action")
        status = "WAITING" if action.startswith("HUMAN_") else "DONE" if action == "TERMINAL" else "PENDING"
        gate_hash = outcome.get("gate_hash") if status == "WAITING" else None
        if status == "WAITING" and (not isinstance(gate_hash, str) or len(gate_hash) != 64):
            raise ValueError("waiting human gate requires exact durable artifact hash")
        with file_lock(self.writer_lock), self._db() as db:
            self._write_snapshot("WRITER_IN_PROGRESS")
            db.execute("BEGIN IMMEDIATE")
            now = time.time() if now is None else now
            self._active(lease, episode_id, worker_id, now)
            changed = db.execute("UPDATE episode_tasks SET status=?,next_action=?,runner_state=?,gate_hash=?,updated_at=? WHERE episode_id=? AND status='RUNNING' AND worker_id=? AND lease_epoch=?", (status,action,state,gate_hash,now,episode_id,worker_id,lease.fencing_token)).rowcount
            if changed != 1:
                raise RuntimeError("stale worker/epoch cannot mutate task")
            self._publish(db)
        return self.status(episode_id)

    def retry(self, episode_id, worker_id, lease, action="RESUME"):
        with file_lock(self.writer_lock), self._db() as db:
            self._write_snapshot("WRITER_IN_PROGRESS")
            db.execute("BEGIN IMMEDIATE")
            self._active(lease, episode_id, worker_id, time.time())
            db.execute("UPDATE episode_tasks SET status='PENDING',next_action=?,updated_at=? WHERE episode_id=? AND status='RUNNING' AND worker_id=? AND lease_epoch=?", (action,time.time(),episode_id,worker_id,lease.fencing_token))
            self._publish(db)

    def unknown(self, episode_id, worker_id, lease):
        with file_lock(self.writer_lock), self._db() as db:
            self._write_snapshot("WRITER_IN_PROGRESS")
            db.execute("BEGIN IMMEDIATE"); self._active(lease, episode_id, worker_id, time.time())
            if db.execute("UPDATE episode_tasks SET status='UNKNOWN_REQUIRES_RECONCILIATION',next_action='RECONCILE_REQUIRED',updated_at=? WHERE episode_id=? AND status='RUNNING' AND worker_id=? AND lease_epoch=?", (time.time(),episode_id,worker_id,lease.fencing_token)).rowcount != 1:
                raise RuntimeError("stale worker/epoch cannot quarantine task")
            self._publish(db)

    def finalize_unknown(self, episode_id, worker_id, lease, outcome):
        try:
            return self.complete(episode_id, worker_id, lease, outcome)
        except RuntimeError:
            return None

    def wake_gate(self, episode_id: str, gate: str) -> dict:
        expected = {"thumbnail":"HUMAN_THUMBNAIL", "video":"HUMAN_VIDEO"}.get(gate)
        if expected is None: raise ValueError("durable gate kind is required")
        # Do not resolve, glob, read, or hash through a queue-supplied root
        # until every existing lexical ancestor has been rejected as a reparse
        # point.  Repeat immediately before each open because a junction can be
        # swapped after discovery.
        root = reject_reparse_path(Path(self.status(episode_id)["root"]))
        root = reject_reparse_path(root.resolve())
        # Compiled tasks retain the configured workspace as their queue root so
        # handler recovery cannot derive authority from a packet directory.
        # Locate exactly one durable runner by its stored episode identity.
        runner_root = root
        runner_json = reject_reparse_path(runner_root / "runner.json")
        if not runner_json.is_file():
            candidates = []
            candidates_root = reject_reparse_path(root / "TC")
            for runner_json in candidates_root.glob("*/runner/runner.json"):
                candidate_root = reject_reparse_path(runner_json.parent)
                runner_json = reject_reparse_path(candidate_root / "runner.json")
                try:
                    raw = reject_reparse_path(runner_json).read_bytes()
                    if json.loads(raw.decode("utf-8")).get("episode_id") == episode_id:
                        candidates.append(candidate_root)
                except (OSError, json.JSONDecodeError):
                    continue
            if len(candidates) != 1:
                raise RuntimeError("configured workspace has no unique durable runner receipt")
            runner_root = candidates[0]
        try:
            runner_root = reject_reparse_path(runner_root)
            runner_json = reject_reparse_path(runner_root / "runner.json")
            receipt = json.loads(reject_reparse_path(runner_json).read_bytes().decode("utf-8"))["gates"][gate]
            if set(receipt) != {"artifact_path", "artifact_sha256", "approved", "reviewer"}: raise ValueError
            artifact = reject_reparse_path(Path(receipt["artifact_path"]))
            artifact = reject_reparse_path(artifact.resolve())
            approved_hash = receipt["artifact_sha256"]
            if root not in artifact.parents or not artifact.is_file() or sha256(reject_reparse_path(artifact).read_bytes()).hexdigest() != approved_hash or receipt["approved"] is not True or type(receipt["approved"]) is not bool or not isinstance(receipt["reviewer"], str) or not receipt["reviewer"].strip(): raise ValueError
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise RuntimeError("persisted approved runner receipt is missing or invalid") from error
        with file_lock(self.writer_lock), self._db() as db:
            self._write_snapshot("WRITER_IN_PROGRESS")
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("UPDATE episode_tasks SET status='PENDING',next_action='RESUME',gate_hash=NULL,updated_at=? WHERE episode_id=? AND status='WAITING' AND next_action=? AND gate_hash=?", (time.time(),episode_id,expected,approved_hash)).rowcount
            self._publish(db)
        if changed != 1:
            raise RuntimeError("no matching durable human gate is waiting")
        return self.status(episode_id)


class DurableEpisodeWorker:
    def __init__(self, queue: EpisodeTaskQueue, worker_id: str, handler: Callable[[dict], Awaitable[dict]], *, lease_ttl_seconds=30, poll_seconds=1, shutdown_deadline_seconds=10):
        if lease_ttl_seconds <= 0 or poll_seconds <= 0 or shutdown_deadline_seconds <= 0:
            raise ValueError("worker timing bounds must be positive")
        self.queue, self.worker_id, self.handler = queue, worker_id, handler
        self.ttl, self.poll_seconds, self.shutdown_deadline_seconds = lease_ttl_seconds, poll_seconds, shutdown_deadline_seconds
        self.leases = queue.leases

    async def run_once(self):
        task = self.queue.next_pending()
        if task is None:
            return None
        try:
            lease = self.leases.acquire(task["episode_id"], f"{self.worker_id}:{uuid.uuid4().hex}", now=time.time(), ttl_seconds=self.ttl)
        except RuntimeError:
            return None
        claimed = self.queue.claim(task["episode_id"], self.worker_id, lease)
        current, stopped, retain = lease, asyncio.Event(), False
        def assert_current(ownership):
            if (getattr(ownership,"episode_id",None), getattr(ownership,"worker_id",None), getattr(ownership,"fencing_token",None)) != (current.episode_id,self.worker_id,current.fencing_token):
                raise PermissionError("worker ownership does not match active episode lease")
            self.leases.assert_active(current, now=time.time())
        async def beat():
            nonlocal current
            while not stopped.is_set():
                await asyncio.sleep(self.ttl / 3)
                if not stopped.is_set(): current = self.leases.renew(current, now=time.time(), ttl_seconds=self.ttl)
        claimed.update(_worker_id=self.worker_id, _lease_assertion=assert_current)
        invocation, heartbeat = asyncio.create_task(self.handler(claimed)), asyncio.create_task(beat())
        try:
            try:
                outcome = await asyncio.shield(invocation)
            except asyncio.CancelledError:
                # Never cancel an offloaded call.  Its liveness is unknowable; wait
                # boundedly under the retained ownership barrier.
                try:
                    outcome = await asyncio.wait_for(asyncio.shield(invocation), self.shutdown_deadline_seconds)
                except TimeoutError:
                    # Renew immediately before recording ambiguity so a very
                    # short shutdown deadline cannot race the TTL.
                    current = self.leases.renew(current, now=time.time(), ttl_seconds=self.ttl)
                    self.queue.unknown(claimed["episode_id"], self.worker_id, current)
                    retain = True
                    raise asyncio.CancelledError
                else:
                    assert_current(type("Ownership", (), {"episode_id":claimed["episode_id"],"worker_id":self.worker_id,"fencing_token":current.fencing_token})())
                    self.queue.complete(claimed["episode_id"], self.worker_id, current, outcome)
                    raise
            assert_current(type("Ownership", (), {"episode_id":claimed["episode_id"],"worker_id":self.worker_id,"fencing_token":current.fencing_token})())
            return self.queue.complete(claimed["episode_id"], self.worker_id, current, outcome)
        except Exception:
            if not retain:
                try: assert_current(type("Ownership", (), {"episode_id":claimed["episode_id"],"worker_id":self.worker_id,"fencing_token":current.fencing_token})())
                except RuntimeError: retain = True
                else: self.queue.retry(claimed["episode_id"], self.worker_id, current)
            raise
        finally:
            stopped.set(); heartbeat.cancel(); await asyncio.gather(heartbeat, return_exceptions=True)
            if not retain:
                self.leases.release(current, now=time.time())

    async def serve(self):
        while True:
            await self.run_once(); await asyncio.sleep(self.poll_seconds)