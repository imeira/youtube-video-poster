"""Crash-fail-closed writer-owned billing status snapshots.

This module deliberately coordinates two SQLite stores under one OS lock; it
never claims that their commits are one distributed transaction.  A marker is
published before either mutation, so interruption is observable as stale.
"""
from __future__ import annotations

import json
import os
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.hybrid._billing_coordinator_guard import (
    _coordinator_capability,
    _coordinator_write,
)
from src.hybrid.execution import Executor, Job
from src.hybrid.locks import file_lock, reject_reparse_path
from src.hybrid.standing_budget import StandingBudgetLedger


class BillingSnapshotError(ValueError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _hash(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def validate_test_billing_snapshot_config(app_config: object) -> tuple[Path, str]:
    """Validate the closed TEST snapshot configuration without filesystem I/O.

    This boundary intentionally only inspects the already-loaded application
    mapping.  Callers must use it before opening state, reading credentials, or
    constructing clients and durable-store objects.
    """
    raw = getattr(app_config, "raw", None)
    if not isinstance(raw, dict) or raw.get("mode") != "TEST":
        raise BillingSnapshotError("explicit TEST billing snapshot application configuration is required")
    configured = raw.get("billing_snapshot")
    if not isinstance(configured, dict) or set(configured) != {"root", "name"}:
        raise BillingSnapshotError("closed billing_snapshot configuration is required")
    root, name = configured["root"], configured["name"]
    if type(root) is not str or type(name) is not str or name != "billing-status.json":
        raise BillingSnapshotError("configured snapshot name must be billing-status.json")
    root_path = Path(root)
    if not root_path.is_absolute():
        raise BillingSnapshotError("configured snapshot root must be absolute")
    return root_path, name


class BillingSnapshotCoordinator:
    """The only writer of the non-authoritative billing JSON read model."""

    def __init__(self, root: Path, *, executor: Executor, ledger: StandingBudgetLedger, clock=lambda: int(time.time())):
        if not isinstance(executor, Executor) or not isinstance(ledger, StandingBudgetLedger):
            raise TypeError("configured executor and standing ledger are required")
        root = Path(root)
        if not root.is_absolute():
            raise BillingSnapshotError("billing snapshot root must be absolute")
        try:
            self.root = reject_reparse_path(root)
            self.executor_root = reject_reparse_path(executor.database.parent)
            self.ledger_root = reject_reparse_path(ledger.path.parent)
        except ValueError as exc:
            raise BillingSnapshotError("billing snapshot paths reject symlink or reparse points") from exc
        if not self.root.is_dir() or not self.executor_root.is_dir() or not self.ledger_root.is_dir():
            raise BillingSnapshotError("configured snapshot and store roots must already exist")
        self.path = self.root / "billing-status.json"
        self.lock_path = self.root / ".billing-status.lock"
        self.executor, self.ledger, self.clock = executor, ledger, clock
        self._owner_binding = {
            "schema": 1,
            "namespace": "FAL_BILLING_STATUS_SNAPSHOT",
            "config_root": str(self.root),
        }
        self.executor._bind_billing_snapshot_coordinator(self, self._owner_binding)

    def execution_capability(self, request_id: str):
        if not isinstance(request_id, str) or not request_id:
            raise BillingSnapshotError("coordinator capability request id is required")
        return _coordinator_capability(
            self, request_id, self.executor, self.ledger, self.executor.config, self._owner_binding
        )

    @property
    def owner_binding(self) -> dict[str, object]:
        return dict(self._owner_binding)

    @classmethod
    def from_app_config(cls, app_config: object, *, executor: Executor, ledger: StandingBudgetLedger, clock=lambda: int(time.time())):
        root, _name = validate_test_billing_snapshot_config(app_config)
        return cls(root, executor=executor, ledger=ledger, clock=clock)

    def _checked(self, path: Path) -> Path:
        try:
            checked = reject_reparse_path(path)
            checked.relative_to(self.root)
        except ValueError as exc:
            raise BillingSnapshotError("snapshot path escapes or uses a reparse point") from exc
        return checked

    def _generation(self) -> int:
        try:
            data = json.loads(self._checked(self.path).read_text(encoding="utf-8"))
            value = data.get("generation") if isinstance(data, dict) else 0
            return value if type(value) is int and value >= 0 else 0
        except (OSError, ValueError, json.JSONDecodeError):
            return 0

    def _publish(self, document: dict[str, Any]) -> str:
        body = dict(document)
        body["snapshot_sha256"] = _hash(document)
        encoded = _canonical(body)
        temporary = self._checked(self.root / f".billing-status.{os.getpid()}.{time.time_ns()}.tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._checked(self.path))
            if os.name != "nt":
                directory = os.open(self._checked(self.root), os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        # Strict byte re-read detects a torn/redirected publish. Windows has no
        # portable directory-fsync primitive; this validation is fail-closed.
        reread = self._checked(self.path).read_bytes()
        if sha256(reread).hexdigest() != sha256(encoded).hexdigest() or json.loads(reread) != body:
            raise BillingSnapshotError("published snapshot readback failed")
        return sha256(encoded).hexdigest()

    def _marker(self, request_id: str, generation: int) -> None:
        generated_at = self.clock()
        if type(generated_at) is not int:
            raise BillingSnapshotError("snapshot clock must be integer UTC seconds")
        self._publish({"schema": 2, "kind": "FAL_BILLING_STATUS_SNAPSHOT", "status": "WRITER_IN_PROGRESS",
                       "generation": generation, "request_id": request_id, "generated_at": generated_at})

    def _current(self, request_id: str, generation: int, *, settlement_receipt: dict[str, Any] | None = None) -> str:
        """Read both stores only after their coordinated commits under our lock."""
        receipt, claim = self.executor.inspect(request_id), self.ledger.claim(request_id)
        if not isinstance(receipt, dict) or not isinstance(claim, dict):
            raise BillingSnapshotError("durable records are missing")
        binding = receipt.get("settlement_binding")
        if (receipt.get("billing_snapshot_generation") != generation or claim.get("billing_snapshot_generation") != generation
                or not isinstance(binding, dict) or binding.get("local_request_id") != request_id
                or binding.get("endpoint_id") != receipt.get("endpoint") or binding.get("authority_mode") != receipt.get("mode")
                or claim.get("request_id") != request_id or claim.get("provider") != receipt.get("endpoint")
                or claim.get("auth_scope") != binding.get("auth_scope")
                or claim.get("provider_request_id") != binding.get("provider_request_id")):
            raise BillingSnapshotError("durable records are not coherently pinned")
        generated_at = self.clock()
        if type(generated_at) is not int:
            raise BillingSnapshotError("snapshot clock must be integer UTC seconds")
        return self._publish({"schema": 2, "kind": "FAL_BILLING_STATUS_SNAPSHOT", "status": "CURRENT", "generation": generation,
                              "generated_at": generated_at, "request_id": request_id, "endpoint_id": receipt["endpoint"], "mode": receipt["mode"],
                              "executor_receipt": receipt, "ledger_claim": claim, "settlement_receipt": settlement_receipt,
                              "provenance": "LOCAL_SNAPSHOT_UNAUTHENTICATED"})

    def prepare_and_reserve(self, job: Job, *, episode_id: str, experiment: bool = False) -> dict[str, Any]:
        """Mark, prepare the executor, reserve the ledger, then publish readback."""
        with file_lock(self._checked(self.lock_path)):
            generation = self._generation() + 1
            with _coordinator_write(generation):
                self._marker(job.request_id, generation)
                prepared = self.executor.prepare_intent(
                    job, billing_snapshot_generation=generation, billing_snapshot_owner=self._owner_binding
                )
                claim = self.ledger.reserve(job.request_id, episode_id, job.endpoint, job.cost, experiment=experiment,
                    settlement_mode="FAL_READBACK_V2", auth_scope=prepared["settlement_binding"]["auth_scope"],
                    provider_request_id="UNASSIGNED", authority_mode=job.mode, billing_snapshot_generation=generation)
                digest = self._current(job.request_id, generation)
                return {"request_id": job.request_id, "generation": generation, "snapshot_sha256": digest, "claim": claim}

    def checkpoint_provider_binding(self, request_id: str, provider_request_id: str, *, partial: str | None = None) -> dict[str, Any]:
        """Pair the durable remote-ID checkpoint with its claim under the marker.

        This intentionally serializes independent SQLite commits; it does not
        claim distributed atomicity.  Any interruption leaves the marker as the
        only externally visible snapshot state.
        """
        with file_lock(self._checked(self.lock_path)):
            generation = self._generation() + 1
            with _coordinator_write(generation):
                self._marker(request_id, generation)
                self.executor._checkpoint_provider_binding(
                    request_id, provider_request_id, partial=partial,
                    billing_snapshot_generation=generation,
                )
                claim = self.ledger.bind_provider_request_id(
                    request_id, provider_request_id,
                    billing_snapshot_generation=generation,
                )
                digest = self._current(request_id, generation)
                return {"request_id": request_id, "generation": generation, "snapshot_sha256": digest, "claim": claim}

    def mark_unknown(self, request_id: str, reason: str) -> dict[str, Any]:
        """Preserve ambiguous liability and publish only a fresh local projection."""
        with file_lock(self._checked(self.lock_path)):
            generation = self._generation() + 1
            with _coordinator_write(generation):
                self._marker(request_id, generation)
                self.executor._set_billing_snapshot_generation(request_id, generation)
                claim = self.ledger.mark_unknown(request_id, billing_snapshot_generation=generation)
                digest = self._current(request_id, generation)
                return {"request_id": request_id, "status": "UNKNOWN", "reason": reason,
                        "generation": generation, "snapshot_sha256": digest, "claim": claim}

    def settle_verified_receipt(self, receipt_body: str, settlement_receipt: dict[str, Any]) -> dict[str, Any]:
        """Persist the canonical ledger receipt under the same observable protocol."""
        request_id = settlement_receipt.get("local_request_id") if isinstance(settlement_receipt, dict) else None
        if not isinstance(request_id, str) or not request_id:
            raise BillingSnapshotError("canonical settlement receipt request id is required")
        with file_lock(self._checked(self.lock_path)):
            generation = self._generation() + 1
            with _coordinator_write(generation):
                self._marker(request_id, generation)
                self.executor._set_billing_snapshot_generation(request_id, generation)
                claim = self.ledger._settle_verified_receipt(
                    receipt_body, billing_snapshot_generation=generation,
                )
                # An idempotent replay has an existing canonical receipt; its row
                # was not mutated by settlement, so pin it explicitly.
                claim = self.ledger._set_billing_snapshot_generation(request_id, generation)
                digest = self._current(request_id, generation, settlement_receipt=settlement_receipt)
                return {"request_id": request_id, "status": "SETTLED", "generation": generation,
                        "snapshot_sha256": digest, "claim": claim}
