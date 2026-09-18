"""Small durable async executor. SQLite serializes reservations, never network I/O.

Adapters are injected. Recovery is lookup/download only: an ambiguous submission
stays reserved and requires reconciliation; it must never be blindly resubmitted.
"""

import asyncio
import json
import sqlite3
import time
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from src.budget.guard import (
    BudgetAction,
    BudgetGuard,
    CostEstimate,
    CostLedger,
    CostRecord,
)
from src.config.loader import BudgetConfig
from src.hybrid.artifacts import Manifest, digest, sha256
from src.hybrid.cas import ContentAddressedStore, job_content_key
from src.hybrid.locks import file_slot
from src.hybrid.observability import StructuredEventLog
from src.hybrid.planner import Config, money


@dataclass(frozen=True)
class Job:
    scene: str
    category: str
    mode: str
    endpoint: str
    payload: dict
    manifest: Manifest
    cost: Decimal
    predecessor: str = ""

    @property
    def request_id(self):
        return digest(asdict(self))


@dataclass(frozen=True)
class Price:
    endpoint: str
    request_id: str
    amount: Decimal
    valid_until: float
    evidence: str


@dataclass(frozen=True)
class Authorization:
    request_id: str
    reviewer: str
    valid_until: float
    maximum_cost: Decimal


@dataclass(frozen=True)
class ProviderResult:
    path: Path
    actual_cost: Decimal


class Provider(Protocol):
    """LIVE adapters must implement submit and non-submitting remote recovery.

    checkpoint must be called immediately on learning remote ID or partial path.
    Credentials/transports belong to deployment, never manifests or this module.
    """

    mode: str

    async def submit(
        self, job: Job, request_id: str, checkpoint: Callable
    ) -> ProviderResult: ...

    async def recover(
        self,
        job: Job,
        request_id: str,
        provider_id: str,
        partial: str,
        checkpoint: Callable,
    ) -> ProviderResult: ...


class Executor:
    def __init__(
        self,
        database: Path,
        config: Config,
        *,
        prior_spend=Decimal(0),
        cas: ContentAddressedStore | None = None,
        events: StructuredEventLog | None = None,
    ):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.prior_spend = money(prior_spend)
        self.cas = cas
        self.events = events
        self.semaphore = asyncio.Semaphore(config.concurrency)
        self.locks = {}
        self.lock_dir = self.database.with_suffix(".locks")
        self.lock_dir.mkdir(exist_ok=True)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, data TEXT NOT NULL)"
            )
            settings = json.dumps(
                {
                    "limit": str(config.limit),
                    "prior_spend": str(self.prior_spend),
                    "concurrency": config.concurrency,
                }
            )
            db.execute("INSERT OR IGNORE INTO settings VALUES (1, ?)", (settings,))
            if (
                db.execute("SELECT data FROM settings WHERE id=1").fetchone()[0]
                != settings
            ):
                raise ValueError(
                    "persisted budget settings mismatch; explicit migration required"
                )

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def inspect(self, request_id):
        with self._db() as db:
            row = db.execute(
                "SELECT data FROM jobs WHERE id=?", (request_id,)
            ).fetchone()
        return json.loads(row[0]) if row else None

    def sync_cost_ledger(self, ledger_path: Path, *, episode_id: str, budget: BudgetConfig) -> CostLedger:
        """Project immutable completed receipts into the episode's canonical ledger."""
        ledger = CostLedger.load(Path(ledger_path), episode_id, budget)
        if money(ledger.hard_limit) != self.config.limit:
            raise ValueError("canonical ledger hard limit differs from executor limit")
        known = {entry.get("job_id") for entry in ledger.jobs if isinstance(entry, dict)}
        with self._db() as db:
            rows = [json.loads(row[0]) for row in db.execute("SELECT data FROM jobs")]
        for row in rows:
            if row.get("status") != "COMPLETE" or row["request_id"] in known:
                continue
            ledger.record_job(CostRecord(
                job_id=row["request_id"], provider=row["endpoint"], gpu="", model="",
                hourly_price=0.0, job_duration_seconds=0.0,
                estimated_cost=float(money(row["charged"])), actual_cost=float(money(row["actual_cost"])),
                scene_id=row["scene"],
            ))
        ledger.save(Path(ledger_path))
        return ledger

    @staticmethod
    def _put(db, data):
        db.execute(
            "INSERT OR REPLACE INTO jobs VALUES (?, ?)",
            (data["request_id"], json.dumps(data, sort_keys=True, allow_nan=False)),
        )

    def _change(self, request_id, **updates):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = json.loads(
                db.execute(
                    "SELECT data FROM jobs WHERE id=?", (request_id,)
                ).fetchone()[0]
            )
            row.update(updates)
            self._put(db, row)

    def _reserve(self, job, *, reservation_cost=None, cache_key=""):
        reservation_cost = job.cost if reservation_cost is None else money(reservation_cost)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = [json.loads(r[0]) for r in db.execute("SELECT data FROM jobs")]
            for row in rows:
                if row["request_id"] == job.request_id:
                    return False
            rows = [r for r in rows if r["mode"] == job.mode]
            if any(r["status"] == "OVERRUN" for r in rows):
                raise RuntimeError("budget overrun requires reconciliation")
            capacity = {
                **self.config.capacity,
                "hero": self.config.hero_slots,
                "hero_retry": self.config.retry_slots,
            }
            if job.category not in capacity:
                raise ValueError("unknown capacity category")
            if job.category in {"first", "hero"} and any(
                r["scene"] == job.scene and r["category"] == job.category for r in rows
            ):
                raise ValueError(
                    "changed first pass requires rejected QA and retry reserve"
                )
            if job.category == "hero":
                previous = next(
                    (r for r in rows if r["request_id"] == job.predecessor), None
                )
                if (
                    not previous
                    or previous.get("qa") is not True
                    or previous["scene"] != job.scene
                    or previous["status"] != "COMPLETE"
                    or previous["category"] != "first"
                ):
                    raise ValueError("hero requires exact approved baseline predecessor")
                self._verify_receipt(previous)
            elif job.category in {"alternative", "correction", "hero_retry"} or job.predecessor:
                previous = next(
                    (r for r in rows if r["request_id"] == job.predecessor), None
                )
                if (
                    not previous
                    or previous.get("qa") is not False
                    or previous["scene"] != job.scene
                    or previous["status"] != "COMPLETE"
                ):
                    raise ValueError("retry requires exact rejected QA predecessor")
                self._verify_receipt(previous)
                allowed = (
                    {"hero", "hero_retry"}
                    if job.category == "hero_retry"
                    else {"first", "alternative", "correction", "thumbnail"}
                )
                if previous["category"] not in allowed or job.category not in {
                    "alternative",
                    "correction",
                    "hero_retry",
                }:
                    raise ValueError("QA retry category mismatch")
                if any(r["predecessor"] == job.predecessor for r in rows):
                    raise ValueError("QA predecessor already has a retry")
            if (
                sum(r["category"] == job.category for r in rows)
                >= capacity[job.category]
            ):
                raise RuntimeError("capacity reserve exhausted")
            spent = self.prior_spend + sum(money(r["charged"]) for r in rows)
            ledger = CostLedger(
                "hybrid", BudgetConfig(hard_limit_usd=float(self.config.limit))
            )
            ledger.spent = float(spent)
            check = BudgetGuard(ledger).approve_job(
                CostEstimate(provider=job.endpoint, estimated_cost=float(reservation_cost))
            )
            if (
                spent + reservation_cost > self.config.limit
                or check.action == BudgetAction.WAITING_BUDGET_APPROVAL
            ):
                raise RuntimeError("budget reservation blocked")
            self._put(
                db,
                {
                    "request_id": job.request_id,
                    "scene": job.scene,
                    "mode": job.mode,
                    "category": job.category,
                    "predecessor": job.predecessor,
                    "manifest": job.manifest.checksum,
                    "endpoint": job.endpoint,
                    "status": "INTENT",
                    "charged": str(reservation_cost),
                    "provider_id": "",
                    "partial": "",
                    "cache_key": cache_key,
                    "request": json.loads(json.dumps(asdict(job), default=str)),
                },
            )
            return True

    @staticmethod
    def _verify_receipt(row):
        if sha256(row["result"]) != row["result_sha256"]:
            raise ValueError("receipt result hash mismatch")

    def qa(self, request_id, result_sha256, approved, reviewer):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute("SELECT data FROM jobs WHERE id=?", (request_id,)).fetchone()
            row = json.loads(found[0]) if found else None
            if (not row or row["status"] != "COMPLETE" or type(approved) is not bool
                    or not reviewer or result_sha256 != row["result_sha256"]):
                raise ValueError("QA must bind a completed receipt hash and reviewer")
            self._verify_receipt(row)
            if "qa" in row:
                if row["qa"] == approved and row.get("qa_reviewer") == reviewer:
                    return
                raise ValueError("QA receipt is immutable; create a remediation successor")
            row.update(qa=approved, qa_reviewer=reviewer)
            self._put(db, row)

    async def run(self, job: Job, provider: Provider, authorization=None, price=None):
        job = deepcopy(job)  # Caller mutation cannot change identity during await.
        if job.mode not in {"TEST", "LIVE"} or provider.mode != job.mode:
            raise PermissionError("TEST/LIVE provider mode mismatch")
        money(job.cost)
        if not job.scene or not job.endpoint or job.cost <= 0:
            raise ValueError(
                "explicit scene, endpoint and positive maximum cost required"
            )
        def live_gate():
            if job.mode != "LIVE":
                return
            now = time.time()
            if (
                not authorization
                or not price
                or not authorization.reviewer
                or not price.evidence
                or authorization.request_id != job.request_id
                or price.request_id != job.request_id
                or price.endpoint != job.endpoint
                or not price.valid_until > now
                or not authorization.valid_until > now
                or money(price.amount) != job.cost
                or money(authorization.maximum_cost) < job.cost
            ):
                raise PermissionError(
                    "LIVE requires exact authorization and fresh live price evidence"
                )

        lock = self.locks.setdefault(job.request_id, asyncio.Lock())
        async with lock, file_slot([self.lock_dir / (job.request_id + ".lock")]):
            existing = self.inspect(job.request_id)
            if existing and existing["status"] == "COMPLETE":
                self._verify_receipt(existing)
                return existing
            async with self.semaphore, file_slot(
                [
                    self.lock_dir / f"worker-{i}.lock"
                    for i in range(self.config.concurrency)
                ]
            ):
                job.manifest.verify(job.mode)
                existing = self.inspect(job.request_id)
                if existing is None:
                    cache_key = job_content_key(job) if self.cas is not None else ""
                    cached = self.cas.get(cache_key) if self.cas is not None else None
                    if cached is None:
                        live_gate()
                    fresh = self._reserve(
                        job,
                        reservation_cost=Decimal(0) if cached is not None else None,
                        cache_key=cache_key,
                    )
                else:
                    cache_key = existing.get("cache_key") or (
                        job_content_key(job) if self.cas is not None else ""
                    )
                    if self.cas is not None and not existing.get("cache_key"):
                        self._change(job.request_id, cache_key=cache_key)
                    cached = None
                    fresh = False
                row = self.inspect(job.request_id)
                if row["status"] == "COMPLETE":
                    self._verify_receipt(row)
                    return row
                if row["status"] == "OVERRUN":
                    raise RuntimeError("budget overrun requires reconciliation")
                if fresh and cached is not None:
                    zero = Decimal(".000")
                    self._change(
                        job.request_id,
                        status="COMPLETE",
                        result=str(cached.resolve()),
                        result_sha256=sha256(cached),
                        charged=str(zero),
                        actual_cost=str(zero),
                        cache_key=cache_key,
                        cache_hit=True,
                    )
                    return self.inspect(job.request_id)

                def checkpoint(*, provider_id=None, partial=None):
                    updates = {}
                    if provider_id:
                        updates["provider_id"] = provider_id
                    if partial:
                        updates["partial"] = str(Path(partial).resolve())
                    self._change(job.request_id, **updates)

                if fresh:
                    self._change(
                        job.request_id,
                        authorization=json.loads(json.dumps(asdict(authorization), default=str)) if authorization else None,
                        live_price=json.loads(json.dumps(asdict(price), default=str)) if price else None,
                    )
                    result = await provider.submit(job, job.request_id, checkpoint)
                elif row["provider_id"] or row["partial"]:
                    result = await provider.recover(
                        job, job.request_id, row["provider_id"], row["partial"], checkpoint
                    )
                else:
                    raise RuntimeError("unknown submission requires reconciliation; no resubmit")
                actual = money(result.actual_cost)
                result_path = Path(result.path).resolve()
                if actual > job.cost:
                    self._change(job.request_id, status="OVERRUN", result=str(result_path), charged=str(actual), actual_cost=str(actual))
                    raise RuntimeError("budget provider overrun recorded; execution blocked")
                if self.cas is not None:
                    self.cas.put(cache_key, result_path)
                self._change(
                    job.request_id, status="COMPLETE", result=str(result_path),
                    result_sha256=sha256(result_path), charged=str(actual), actual_cost=str(actual),
                    cache_key=cache_key, cache_hit=False,
                )
                return self.inspect(job.request_id)
