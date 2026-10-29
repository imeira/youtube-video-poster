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
from src.hybrid._billing_coordinator_guard import (
    _require_coordinator_capability,
    _require_coordinator_write,
)
from src.hybrid.artifacts import Manifest, digest, sha256
from src.hybrid.cas import ContentAddressedStore, job_content_key
from src.hybrid.locks import file_lock, file_slot, reject_reparse_path
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


class SettlementCheckpoint(Protocol):
    """Composition-owned capability for an FAL remote-ID checkpoint."""

    def checkpoint_provider_binding(self, request_id: str, provider_request_id: str, *, partial: str | None = None) -> object: ...


class Executor:
    def __init__(
        self,
        database: Path,
        config: Config,
        *,
        prior_spend=Decimal(0),
        cas: ContentAddressedStore | None = None,
        events: StructuredEventLog | None = None,
        standing_ledger=None,
        _authenticated_budget_composition: dict | None = None,
        _authenticated_budget_bridge=None,
    ):
        self.database = Path(database)
        self.database.parent.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.prior_spend = money(prior_spend)
        self.cas = cas
        self.events = events
        self._authenticated_budget_composition = _authenticated_budget_composition
        self._authenticated_budget_bridge = _authenticated_budget_bridge
        if ((_authenticated_budget_composition is None) != (_authenticated_budget_bridge is None)
                or (_authenticated_budget_composition is not None
                    and self._authenticated_budget_composition_for_bridge(_authenticated_budget_bridge)
                    != _authenticated_budget_composition)):
            raise ValueError("authenticated budget composition must come from its configured bridge factory")
        self._standing_ledger = None
        self._billing_snapshot_coordinator = None
        self._billing_snapshot_owner_binding = None
        if standing_ledger is not None:
            self._attach_standing_ledger(standing_ledger)
        self.semaphore = asyncio.Semaphore(config.concurrency)
        self.locks = {}
        self.lock_dir = self.database.with_suffix(".locks")
        self.lock_dir.mkdir(exist_ok=True)
        self._initialize()

    def _initialize(self):
        """Serialize schema/WAL bootstrap without reconfiguring open WAL stores."""
        init_lock = self.database.with_name(f".{self.database.name}.init.lock")
        with file_lock(init_lock, timeout=30), self._db() as db:
            journal_mode = db.execute("PRAGMA journal_mode").fetchone()[0]
            if str(journal_mode).lower() != "wal":
                db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS settings (id INTEGER PRIMARY KEY, data TEXT NOT NULL)"
            )
            settings = json.dumps(
                {
                    "limit": str(self.config.limit),
                    "prior_spend": str(self.prior_spend),
                    "authenticated_budget_composition": self._authenticated_budget_composition,
                    "concurrency": self.config.concurrency,
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

    def _bind_billing_snapshot_coordinator(self, coordinator, owner_binding) -> None:
        """Bind the configured runtime owner; durable rows remain the authority."""
        if not isinstance(owner_binding, dict) or set(owner_binding) != {"schema", "namespace", "config_root"}:
            raise TypeError("billing snapshot owner binding is malformed")
        if (owner_binding.get("schema") != 1
                or owner_binding.get("namespace") != "FAL_BILLING_STATUS_SNAPSHOT"
                or not isinstance(owner_binding.get("config_root"), str)
                or not owner_binding["config_root"]):
            raise TypeError("billing snapshot owner binding is malformed")
        if self._billing_snapshot_coordinator is not None:
            if (self._billing_snapshot_coordinator is coordinator
                    and self._billing_snapshot_owner_binding == owner_binding):
                return
            raise ValueError("executor billing snapshot coordinator binding is immutable")
        self._billing_snapshot_coordinator = coordinator
        self._billing_snapshot_owner_binding = dict(owner_binding)

    def _attach_standing_ledger(self, ledger) -> None:
        """Bind the composition-owned ledger; request payloads never select it."""
        if not callable(getattr(ledger, "claim", None)):
            raise TypeError("configured standing ledger must expose durable claims")
        if self._standing_ledger is not None and self._standing_ledger is not ledger:
            raise ValueError("executor standing ledger is immutable")
        self._standing_ledger = ledger

    def _configured_authenticated_budget_binding(self, job: Job) -> dict | None:
        """Re-read the factory-pinned bridge receipt for this exact TEST job."""
        composition = self._authenticated_budget_composition
        if composition is None:
            return None
        if not isinstance(job, Job) or job.mode != "TEST":
            raise PermissionError("authenticated budget admission is TEST-only")
        bridge = self._authenticated_budget_bridge
        if bridge is None or self._authenticated_budget_composition_for_bridge(bridge) != composition:
            raise PermissionError("configured authenticated budget trust source changed")
        policy = bridge.service.policy_loader().load(bridge.receipt_path)
        if policy.mode != "TEST" or job.endpoint not in policy.allowed_providers:
            raise PermissionError("configured authenticated budget receipt does not authorize executor job")
        return {
            "schema": 1, "kind": "AUTHENTICATED_TEST_BUDGET_ADMISSION",
            "composition_trust_digest": composition["trust_digest"],
            "request_id": policy.request_id, "episode_id": policy.episode_id,
            "policy_sha256": policy.policy_sha256, "provider": job.endpoint,
            "operation_id": job.request_id, "mode": "TEST",
        }

    @staticmethod
    def _authenticated_budget_composition_for_bridge(bridge) -> dict:
        from src.hybrid.budget_authorization import AuthenticatedBudgetBridge
        if not isinstance(bridge, AuthenticatedBudgetBridge):
            raise TypeError("configured authenticated budget bridge is required")
        service_root = reject_reparse_path(Path(bridge.service.root)).resolve()
        receipt_path = reject_reparse_path(Path(bridge.receipt_path)).resolve()
        bridge.service.policy_loader().load(receipt_path)
        source = {
            "schema": 1, "kind": "AUTHENTICATED_TEST_BUDGET_COMPOSITION",
            "config_root": str(service_root), "service_root": str(service_root),
            "receipt_path": str(receipt_path), "receipt_sha256": sha256(receipt_path),
        }
        return {**source, "trust_digest": digest(source)}

    @classmethod
    def _from_authenticated_budget_bridge(cls, database: Path, config: Config, *, bridge, **kwargs):
        composition = cls._authenticated_budget_composition_for_bridge(bridge)
        return cls(database, config, standing_ledger=bridge._ledger,
                   _authenticated_budget_composition=composition,
                   _authenticated_budget_bridge=bridge, **kwargs)

    def _matches_authenticated_budget_bridge(self, bridge) -> bool:
        return (self._authenticated_budget_bridge is bridge
                and self._authenticated_budget_composition is not None
                and self._authenticated_budget_composition_for_bridge(bridge)
                == self._authenticated_budget_composition)

    def _fresh_authenticated_budget_admission(self, job: Job, row: dict) -> dict | None:
        binding = row.get("authenticated_budget_admission")
        if binding is None:
            return None
        configured = self._configured_authenticated_budget_binding(job)
        if configured != binding:
            raise PermissionError("authenticated budget admission no longer authorizes executor job")
        return configured

    def _verify_prepared_fal_admission(self, job: Job, row: dict, *, require_admitted_marker=True) -> None:
        """Require the configured ledger's exact durable reservation before POST."""
        ledger = self._standing_ledger
        if ledger is None:
            raise PermissionError("prepared FAL submit requires standing-ledger admission")
        binding = row.get("settlement_binding")
        required = {
            "schema", "local_request_id", "provider_request_id", "endpoint_id",
            "auth_scope", "authority_mode",
        }
        if (
            not isinstance(binding, dict)
            or set(binding) != required
            or binding.get("schema") != 2
            or binding.get("local_request_id") != job.request_id
            or binding.get("endpoint_id") != job.endpoint
            or binding.get("authority_mode") != job.mode
            or binding.get("provider_request_id") != "UNASSIGNED"
        ):
            raise PermissionError("prepared FAL submit has no durable intent binding")
        try:
            claim = ledger.claim(job.request_id)
        except Exception as exc:
            raise PermissionError("prepared FAL submit requires standing-ledger admission") from exc
        if (
            claim.get("request_id") != job.request_id
            or claim.get("provider") != job.endpoint
            or claim.get("settlement_mode") != "FAL_READBACK_V2"
            or claim.get("auth_scope") != binding["auth_scope"]
            or claim.get("provider_request_id") != "UNASSIGNED"
            or claim.get("authority_mode") != job.mode
            or claim.get("state") != "RESERVED"
        ):
            raise PermissionError("prepared FAL submit ledger reservation does not bind intent")
        try:
            reserved = money(claim.get("reserved_usd"))
        except (TypeError, ValueError):
            raise PermissionError("prepared FAL submit ledger reservation is invalid") from None
        if reserved != job.cost:
            raise PermissionError("prepared FAL submit ledger reservation value mismatch")
        authenticated = self._fresh_authenticated_budget_admission(job, row)
        if authenticated is None:
            # Legacy TEST ledger paths remain mechanically useful, but their
            # fixed local scope cannot be relabeled as bridge authority or LIVE.
            if job.mode != "TEST" or binding["auth_scope"] != "TEST_LOCAL_ONLY":
                raise PermissionError("prepared ledger admission requires authenticated TEST authority")
            return
        admission = row.get("standing_ledger_admission")
        if require_admitted_marker and admission != {
            "schema": 1,
            "kind": "AUTHENTICATED_TEST_LEDGER_ADMISSION",
            "local_request_id": job.request_id,
            "endpoint_id": job.endpoint,
            "auth_scope": binding["auth_scope"],
            "authority_mode": job.mode,
            "reserved_usd": str(job.cost),
            "authenticated_budget_admission": authenticated,
        }:
            raise PermissionError("prepared FAL submit lacks durable admitted marker")

    def bind_prepared_standing_ledger_admission(self, job: Job) -> dict:
        """Durably acknowledge a separately committed canonical reservation.

        The executor and standing ledger are separate SQLite databases, so this
        is deliberately a saga step rather than a fictitious cross-DB atomic
        transaction. Replays first verify the canonical reservation, then write
        this idempotent executor marker. ``run`` accepts no PREPARED row until
        both the marker and a fresh claim read verify.
        """
        job = deepcopy(job)
        row = self.inspect(job.request_id)
        if row is None or row.get("status") != "PREPARED":
            raise RuntimeError("only a prepared executor intent can be admitted")
        if type(row.get("billing_snapshot_generation")) is int:
            _require_coordinator_write(row["billing_snapshot_generation"])
        self._verify_prepared_fal_admission(job, row, require_admitted_marker=False)
        binding = row["settlement_binding"]
        authenticated = self._fresh_authenticated_budget_admission(job, row)
        if authenticated is None:
            raise PermissionError("only bridge-bound authenticated intent can persist admission marker")
        admission = {
            "schema": 1,
            "kind": "AUTHENTICATED_TEST_LEDGER_ADMISSION",
            "local_request_id": job.request_id,
            "endpoint_id": job.endpoint,
            "auth_scope": binding["auth_scope"],
            "authority_mode": job.mode,
            "reserved_usd": str(job.cost),
            "authenticated_budget_admission": authenticated,
        }
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute("SELECT data FROM jobs WHERE id=?", (job.request_id,)).fetchone()
            persisted = json.loads(found[0]) if found else None
            if persisted is None or persisted.get("status") != "PREPARED":
                raise RuntimeError("prepared executor intent changed during admission")
            if persisted.get("settlement_binding") != binding:
                raise PermissionError("prepared executor intent binding changed during admission")
            prior = persisted.get("standing_ledger_admission")
            if prior is not None and prior != admission:
                raise PermissionError("prepared executor admission marker is immutable")
            persisted["standing_ledger_admission"] = admission
            self._put(db, persisted)
        return self.inspect(job.request_id)


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

    def _change(self, request_id, *, _coordinator_capability=None, **updates):
        if "settlement_binding" in updates:
            raise ValueError("settlement binding is immutable outside atomic checkpoint")
        if "provider_id" in updates:
            raise ValueError("provider request id is immutable outside atomic checkpoint")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = json.loads(
                db.execute(
                    "SELECT data FROM jobs WHERE id=?", (request_id,)
                ).fetchone()[0]
            )
            generation = row.get("billing_snapshot_generation")
            if type(generation) is int:
                if _coordinator_capability is None:
                    _require_coordinator_write(generation)
                else:
                    if self._billing_snapshot_owner_binding != row.get("billing_snapshot_owner"):
                        raise PermissionError("generation-pinned FAL owner does not match configured coordinator")
                    _require_coordinator_capability(
                        _coordinator_capability,
                        expected_coordinator=self._billing_snapshot_coordinator,
                        request_id=request_id, executor=self, ledger=self._standing_ledger,
                        config=self.config, owner_binding=row.get("billing_snapshot_owner"),
                    )
            row.update(updates)
            self._put(db, row)

    def _checkpoint_provider_binding(self, request_id, provider_id, *, partial=None,
                                     billing_snapshot_generation=None):
        """Atomically bind the one real provider id learned after dispatch.

        This is the sole transition from the pre-dispatch ``UNASSIGNED`` marker.
        It deliberately validates the immutable executor intent rather than
        accepting a replacement binding supplied by a caller.
        """
        if not isinstance(provider_id, str) or not provider_id or len(provider_id) > 512:
            raise ValueError("provider request id is invalid")
        if billing_snapshot_generation is not None and (
            type(billing_snapshot_generation) is not int
            or billing_snapshot_generation < 1
        ):
            raise ValueError("billing snapshot generation must be a positive integer")
        _require_coordinator_write(billing_snapshot_generation)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute("SELECT data FROM jobs WHERE id=?", (request_id,)).fetchone()
            if found is None:
                raise ValueError("executor request does not exist")
            persisted = json.loads(found[0])
            if type(persisted.get("billing_snapshot_generation")) is int and billing_snapshot_generation is None:
                raise PermissionError("generation-pinned executor checkpoint requires coordinator capability")
            binding = persisted.get("settlement_binding")
            required = {
                "schema", "local_request_id", "provider_request_id", "endpoint_id",
                "auth_scope", "authority_mode",
            }
            if (not isinstance(binding, dict) or set(binding) != required
                    or binding.get("schema") != 2
                    or binding.get("local_request_id") != request_id
                    or binding.get("endpoint_id") != persisted.get("endpoint")
                    or binding.get("authority_mode") != persisted.get("mode")
                    or not isinstance(binding.get("auth_scope"), str)
                    or not binding["auth_scope"]):
                raise ValueError("settlement binding intent is malformed")
            prior = binding["provider_request_id"]
            prior_provider = persisted.get("provider_id", "")
            if prior == provider_id and prior_provider == provider_id:
                return
            if prior != "UNASSIGNED" or prior_provider:
                raise ValueError("provider request id is immutable")
            binding["provider_request_id"] = provider_id
            persisted["provider_id"] = provider_id
            persisted["status"] = "SUBMITTED"
            if billing_snapshot_generation is not None:
                persisted["billing_snapshot_generation"] = billing_snapshot_generation
            if partial:
                persisted["partial"] = str(Path(partial).resolve())
            self._put(db, persisted)

    def _set_billing_snapshot_generation(self, request_id, generation):
        """Coordinator-only pin update after a ledger-side settlement transition."""
        if type(generation) is not int or generation < 1:
            raise ValueError("billing snapshot generation must be a positive integer")
        _require_coordinator_write(generation)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute("SELECT data FROM jobs WHERE id=?", (request_id,)).fetchone()
            if found is None:
                raise ValueError("executor request does not exist")
            persisted = json.loads(found[0])
            persisted["billing_snapshot_generation"] = generation
            self._put(db, persisted)

    def _reserve(self, job, *, reservation_cost=None, cache_key="", auth_scope=None,
                 authenticated_budget_admission=None, billing_snapshot_generation=None,
                 billing_snapshot_owner=None):
        if billing_snapshot_generation is not None and (type(billing_snapshot_generation) is not int or billing_snapshot_generation < 1):
            raise ValueError("billing snapshot generation must be a positive integer")
        if billing_snapshot_generation is not None and (
            not isinstance(billing_snapshot_owner, dict)
            or set(billing_snapshot_owner) != {"schema", "namespace", "config_root"}
            or billing_snapshot_owner.get("schema") != 1
            or billing_snapshot_owner.get("namespace") != "FAL_BILLING_STATUS_SNAPSHOT"
            or not isinstance(billing_snapshot_owner.get("config_root"), str)
            or not billing_snapshot_owner["config_root"]
        ):
            raise ValueError("generation-pinned intent requires durable coordinator owner binding")
        if billing_snapshot_generation is None and billing_snapshot_owner is not None:
            raise ValueError("un-pinned intent cannot carry coordinator owner binding")
        _require_coordinator_write(billing_snapshot_generation)
        reservation_cost = job.cost if reservation_cost is None else money(reservation_cost)
        if auth_scope is None:
            if job.mode != "TEST":
                raise PermissionError("LIVE settlement binding requires authorization scope")
            auth_scope = "TEST_LOCAL_ONLY"
        if not isinstance(auth_scope, str) or not auth_scope or len(auth_scope) > 512:
            raise ValueError("settlement authorization scope is invalid")
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
                    "receipt_schema": 2,
                    "billing_snapshot_generation": billing_snapshot_generation,
                    "billing_snapshot_owner": dict(billing_snapshot_owner) if billing_snapshot_owner else None,
                    "authenticated_budget_admission": authenticated_budget_admission,
                    "settlement_binding": {
                        "schema": 2,
                        "local_request_id": job.request_id,
                        # A local identity is not evidence of a remote FAL identity.
                        "provider_request_id": "UNASSIGNED",
                        "endpoint_id": job.endpoint,
                        "auth_scope": auth_scope,
                        "authority_mode": job.mode,
                    },
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

    def prepare_intent(self, job: Job, *, auth_scope=None, billing_snapshot_generation=None,
                       billing_snapshot_owner=None):
        """Durably record a non-submitting intent for external admission.

        This deliberately performs no provider construction or network I/O.  A
        caller can bind the resulting immutable intent into another durable
        ledger before it later invokes :meth:`run`.
        """
        job = deepcopy(job)
        if job.mode not in {"TEST", "LIVE"}:
            raise PermissionError("explicit TEST/LIVE job mode required")
        money(job.cost)
        if not job.scene or not job.endpoint or job.cost <= 0:
            raise ValueError("explicit scene, endpoint and positive maximum cost required")
        job.manifest.verify(job.mode)
        authenticated_binding = self._configured_authenticated_budget_binding(job)
        if authenticated_binding is None:
            if job.mode != "TEST":
                raise PermissionError("raw prepared intent cannot authorize LIVE admission")
            if auth_scope not in {None, "TEST_LOCAL_ONLY"}:
                raise PermissionError("raw prepared intent cannot forge bridge authorization scope")
            auth_scope = "TEST_LOCAL_ONLY"
        else:
            required_scope = f"AUTHENTICATED_TEST_REQUEST:{authenticated_binding['request_id']}"
            if auth_scope not in {None, required_scope}:
                raise PermissionError("authenticated intent scope is internally bound")
            auth_scope = required_scope
        fresh = self._reserve(job, auth_scope=auth_scope,
                              authenticated_budget_admission=authenticated_binding,
                              billing_snapshot_generation=billing_snapshot_generation,
                              billing_snapshot_owner=billing_snapshot_owner)
        row = self.inspect(job.request_id)
        if fresh:
            self._change(job.request_id, status="PREPARED")
            row = self.inspect(job.request_id)
        if row["status"] not in {"PREPARED", "INTENT", "COMPLETE"}:
            raise RuntimeError("prepared executor intent is not runnable")
        return row

    @staticmethod
    def _verify_receipt(row):
        if sha256(row["result"]) != row["result_sha256"]:
            raise ValueError("receipt result hash mismatch")

    def qa(self, request_id, result_sha256, approved, reviewer):
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            found = db.execute("SELECT data FROM jobs WHERE id=?", (request_id,)).fetchone()
            row = json.loads(found[0]) if found else None
            if row and type(row.get("billing_snapshot_generation")) is int:
                _require_coordinator_write(row["billing_snapshot_generation"])
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

    async def run(self, job: Job, provider: Provider, authorization=None, price=None,
                  settlement_checkpoint: SettlementCheckpoint | None = None,
                  submit_prepared: bool = False):
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
                    auth_scope = (
                        "TEST_LOCAL_ONLY"
                        if job.mode == "TEST"
                        else (
                            f"LIVE_AUTHORIZATION_REQUEST:{authorization.request_id}"
                            if authorization is not None
                            else "LIVE_CACHE_NO_PROVIDER_REQUEST"
                        )
                    )
                    fresh = self._reserve(
                        job,
                        reservation_cost=Decimal(0) if cached is not None else None,
                        cache_key=cache_key,
                        auth_scope=auth_scope,
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
                if fresh and cached is None and job.endpoint.startswith("fal-ai/"):
                    # A fresh FAL request must take the same durable admission
                    # path as a separately prepared intent; direct run is not a bypass.
                    self._change(job.request_id, status="PREPARED")
                    row = self.inspect(job.request_id)
                    fresh = False
                if row["status"] == "COMPLETE":
                    self._verify_receipt(row)
                    return row
                if row["status"] == "OVERRUN":
                    raise RuntimeError("budget overrun requires reconciliation")
                if type(row.get("billing_snapshot_generation")) is int:
                    if self._billing_snapshot_owner_binding != row.get("billing_snapshot_owner"):
                        raise PermissionError("generation-pinned FAL owner does not match configured coordinator")
                    _require_coordinator_capability(
                        settlement_checkpoint,
                        expected_coordinator=self._billing_snapshot_coordinator,
                        request_id=job.request_id, executor=self, ledger=self._standing_ledger,
                        config=self.config, owner_binding=row.get("billing_snapshot_owner"),
                    )
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
                if (
                    row["status"] == "PREPARED"
                    and not submit_prepared
                    and not row["provider_id"]
                    and not row["partial"]
                ):
                    raise RuntimeError(
                        "prepared intent requires admission or reconciliation; no submit"
                    )

                def checkpoint(*, provider_id=None, partial=None):
                    updates = {}
                    if provider_id:
                        updates["provider_id"] = provider_id
                    if partial:
                        updates["partial"] = str(Path(partial).resolve())
                    if provider_id:
                        checkpoint_method = getattr(
                            settlement_checkpoint, "checkpoint_provider_binding", None
                        ) if settlement_checkpoint is not None else None
                        if callable(checkpoint_method):
                            # Coordinator-owned admissions are generation-pinned;
                            # only the explicit capability may transition them.
                            checkpoint_method(job.request_id, provider_id, partial=partial)
                        elif settlement_checkpoint is None:
                            self._checkpoint_provider_binding(
                                job.request_id, provider_id, partial=partial
                            )
                        elif type(row.get("billing_snapshot_generation")) is int:
                            raise TypeError("generation-pinned FAL checkpoint requires coordinator capability")
                        elif callable(settlement_checkpoint):
                            # Legacy unpinned TEST callers remain isolated from
                            # coordinator-owned rows. This compatibility branch
                            # is never selected for a configured generation.
                            self._checkpoint_provider_binding(
                                job.request_id, provider_id, partial=partial
                            )
                            settlement_checkpoint(job.request_id, provider_id)
                        else:
                            raise TypeError("FAL checkpoint requires callable legacy hook or coordinator capability")
                    elif updates:
                        self._change(job.request_id, **updates)

                submit_now = fresh
                if row["status"] == "PREPARED" and submit_prepared:
                    # A boolean is not authority: before the irreversible POST,
                    # re-read the configured ledger's exact durable reservation.
                    self._verify_prepared_fal_admission(job, row)
                    self._change(job.request_id, status="SUBMITTING",
                                 _coordinator_capability=settlement_checkpoint)
                    row = self.inspect(job.request_id)
                    submit_now = True
                elif fresh:
                    # Even direct Executor users must leave a durable marker
                    # before the non-idempotent provider POST.
                    self._change(job.request_id, status="SUBMITTING")
                    row = self.inspect(job.request_id)

                if submit_now:
                    self._change(
                        job.request_id,
                        _coordinator_capability=settlement_checkpoint,
                        authorization=json.loads(json.dumps(asdict(authorization), default=str)) if authorization else None,
                        live_price=json.loads(json.dumps(asdict(price), default=str)) if price else None,
                    )
                    result = await provider.submit(job, job.request_id, checkpoint)
                elif row["provider_id"] or row["partial"]:
                    result = await provider.recover(
                        job, job.request_id, row["provider_id"], row["partial"], checkpoint
                    )
                else:
                    recover_local = getattr(provider, "recover_local", None)
                    if not callable(recover_local):
                        raise RuntimeError("unknown submission requires reconciliation; no resubmit")
                    result = await recover_local(job, job.request_id, checkpoint)
                actual = money(result.actual_cost)
                result_path = Path(result.path).resolve()
                if actual > job.cost:
                    self._change(job.request_id, status="OVERRUN", result=str(result_path), charged=str(actual), actual_cost=str(actual),
                                 _coordinator_capability=settlement_checkpoint)
                    raise RuntimeError("budget provider overrun recorded; execution blocked")
                if self.cas is not None:
                    self.cas.put(cache_key, result_path)
                self._change(
                    job.request_id, status="COMPLETE", result=str(result_path),
                    result_sha256=sha256(result_path), charged=str(actual), actual_cost=str(actual),
                    cache_key=cache_key, cache_hit=False, _coordinator_capability=settlement_checkpoint,
                )
                return self.inspect(job.request_id)
