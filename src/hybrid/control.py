"""Compact, deterministic episode control plane.

This is the sole scheduler for an episode run. It stores immutable per-frame
attestations in SQLite and emits work identifiers; callers still use the
transactional Executor for paid submission and recovery.
"""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

_HEX = set("0123456789abcdef")


def _sha256(value: str) -> str:
    if type(value) is not str or len(value) != 64 or set(value) - _HEX:
        raise ValueError("sha256 must be lowercase hexadecimal")
    return value


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class EpisodeSpec:
    episode_id: str
    mode: str
    frames: tuple[str, ...]
    storyboard_sha256: str
    audio_sha256: str
    budget_cap_usd: str

    def __post_init__(self):
        if not self.episode_id or self.mode not in {"TEST", "LIVE"}:
            raise ValueError("episode_id and TEST/LIVE mode required")
        if not self.frames or len(set(self.frames)) != len(self.frames):
            raise ValueError("unique frames required")
        if any(type(frame) is not str or not frame for frame in self.frames):
            raise ValueError("nonempty frame identifiers required")
        _sha256(self.storyboard_sha256)
        _sha256(self.audio_sha256)

    @property
    def checksum(self) -> str:
        return _digest(asdict(self))


@dataclass(frozen=True)
class Action:
    kind: str
    frame_id: str | None
    action_id: str


class ControlPlane:
    """One SQLite store per episode; emits eligible work without executing it."""

    def __init__(self, database: Path, spec: EpisodeSpec):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.spec = spec
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execute(
                "CREATE TABLE IF NOT EXISTS candidates (frame_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, producer_id TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS qa (frame_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, approved INTEGER NOT NULL, reviewer_id TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS consumed (action_id TEXT PRIMARY KEY, kind TEXT NOT NULL, frame_id TEXT, predecessor_sha256 TEXT)"
            )
            encoded = json.dumps(asdict(spec), sort_keys=True, separators=(",", ":"))
            prior = db.execute("SELECT value FROM settings WHERE key='spec'").fetchone()
            if prior is None:
                db.execute("INSERT INTO settings VALUES ('spec', ?)", (encoded,))
            elif prior[0] != encoded:
                raise ValueError("episode spec mismatch; explicit migration required")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database)
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _action_id(self, kind: str, frame_id: str | None, predecessor_sha256: str = "") -> str:
        return _digest(
            {
                "spec": self.spec.checksum,
                "kind": kind,
                "frame_id": frame_id,
                "predecessor_sha256": predecessor_sha256,
            }
        )

    def record_candidate(self, frame_id: str, sha256: str, *, producer_id: str) -> None:
        if frame_id not in self.spec.frames or not producer_id:
            raise ValueError("known frame and producer required")
        _sha256(sha256)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM candidates WHERE frame_id=?", (frame_id,)).fetchone():
                raise ValueError("candidate is immutable")
            db.execute("INSERT INTO candidates VALUES (?, ?, ?)", (frame_id, sha256, producer_id))

    def record_qa(self, frame_id: str, sha256: str, *, approved: bool, reviewer_id: str) -> None:
        if frame_id not in self.spec.frames or type(approved) is not bool or not reviewer_id:
            raise ValueError("known frame, boolean verdict and reviewer required")
        _sha256(sha256)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            candidate = db.execute(
                "SELECT sha256, producer_id FROM candidates WHERE frame_id=?", (frame_id,)
            ).fetchone()
            if candidate is None or candidate[0] != sha256:
                raise ValueError("QA requires exact candidate hash")
            if candidate[1] == reviewer_id:
                raise ValueError("QA reviewer must be independent of producer")
            if db.execute("SELECT 1 FROM qa WHERE frame_id=?", (frame_id,)).fetchone():
                raise ValueError("QA attestation is immutable")
            db.execute("INSERT INTO qa VALUES (?, ?, ?, ?)", (frame_id, sha256, approved, reviewer_id))

    def consume(self, kind: str, frame_id: str | None, *, predecessor_sha256: str = "") -> Action:
        if kind not in {"BASELINE", "REMEDIATE", "RENDER"}:
            raise ValueError("unknown action kind")
        if kind == "RENDER":
            frame_id = None
        elif frame_id not in self.spec.frames:
            raise ValueError("known frame required")
        if predecessor_sha256:
            _sha256(predecessor_sha256)
        action = Action(kind, frame_id, self._action_id(kind, frame_id, predecessor_sha256))
        if action.action_id not in {eligible.action_id for eligible in self.tick()}:
            raise ValueError("action is not eligible")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT OR IGNORE INTO consumed VALUES (?, ?, ?, ?)",
                (action.action_id, kind, frame_id, predecessor_sha256),
            )
        return action

    def tick(self) -> list[Action]:
        with self._db() as db:
            candidates = {row[0]: row[1] for row in db.execute("SELECT frame_id, sha256 FROM candidates")}
            qa = {row[0]: (row[1], bool(row[2])) for row in db.execute("SELECT frame_id, sha256, approved FROM qa")}
            consumed = {row[0] for row in db.execute("SELECT action_id FROM consumed")}
        actions = []
        for frame_id in self.spec.frames:
            if frame_id not in candidates:
                action = Action("BASELINE", frame_id, self._action_id("BASELINE", frame_id))
            elif frame_id not in qa:
                continue
            elif not qa[frame_id][1]:
                action = Action("REMEDIATE", frame_id, self._action_id("REMEDIATE", frame_id, qa[frame_id][0]))
            else:
                continue
            if action.action_id not in consumed:
                actions.append(action)
        if len(qa) == len(self.spec.frames) and all(approved for _, approved in qa.values()):
            action = Action("RENDER", None, self._action_id("RENDER", None))
            if action.action_id not in consumed:
                actions.append(action)
        return actions

    def compact_status(self) -> dict:
        with self._db() as db:
            qa = {row[0]: bool(row[1]) for row in db.execute("SELECT frame_id, approved FROM qa")}
        approved = sum(qa.values())
        rejected = len(qa) - approved
        return {
            "episode_id": self.spec.episode_id,
            "mode": self.spec.mode,
            "frames": len(self.spec.frames),
            "approved": approved,
            "rejected": rejected,
            "pending": len(self.spec.frames) - len(qa),
            "eligible": [action.kind for action in self.tick()],
        }
