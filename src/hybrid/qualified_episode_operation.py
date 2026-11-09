"""Fixed-root resolver for one qualified episode operation.

The public entrypoints accept only a literal operation ID.  They never accept a
root, provider, credential, callback, quote, packet, or ledger from callers.
An absent deployment composition is a normal fail-closed state, not an import
fallback.
"""
from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.hybrid.artifacts import digest
from src.hybrid.execution import Executor, Job, ProviderResult
from src.hybrid.planner import Config
from src.hybrid.standing_budget import StandingBudgetLedger

_PROTECTED_ROOT = Path("C:/HermesStudio/protected/qualified-episode-operations")
_ID = re.compile(r"[A-Z][A-Z0-9_-]{2,159}\Z", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class QualifiedEpisodeOperationError(PermissionError):
    """The fixed protected composition cannot admit this operation."""


@dataclass(frozen=True)
class QualifiedGenericJob(Job):
    """Generic-only request identity with its declared protected origin.

    ``Job`` retains its historical identity unchanged.  This specialized
    factory product prevents two closed generic documents with otherwise equal
    provider inputs from sharing a durable executor or ledger request.
    """

    operation_id: str = ""
    episode_id: str = ""
    ledger_policy_sha256: str = ""

    @property
    def request_id(self) -> str:
        return digest({
            "schema": "qualified-generic-request/v1",
            "operation_id": self.operation_id,
            "episode_id": self.episode_id,
            "ledger_policy_sha256": self.ledger_policy_sha256,
            "job": {
                "scene": self.scene,
                "category": self.category,
                "mode": self.mode,
                "endpoint": self.endpoint,
                "payload": self.payload,
                "manifest": self.manifest,
                "cost": self.cost,
                "predecessor": self.predecessor,
                "input_kind": self.input_kind,
            },
        })


@dataclass(frozen=True)
class ProtectedOperationLocator:
    """Compiled deployment locator; production callers cannot supply a root."""

    root: Path

    @classmethod
    def production(cls) -> ProtectedOperationLocator:
        return cls(_PROTECTED_ROOT)


def _literal_operation_id(operation_id: object) -> str:
    if not isinstance(operation_id, str) or not _ID.fullmatch(operation_id):
        raise QualifiedEpisodeOperationError("literal ASCII operation ID is required")
    return operation_id


def _no_reparse(path: Path) -> None:
    """Reject a reparse point before reading any protected path."""
    current = path
    while True:
        try:
            info = current.lstat()
        except OSError as error:
            raise QualifiedEpisodeOperationError("protected operation path is unavailable") from error
        if stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise QualifiedEpisodeOperationError("protected operation path must not be a reparse point")
        if current.parent == current:
            return
        current = current.parent


def _relative(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise QualifiedEpisodeOperationError(f"{label} must be a literal relative protected path")
    candidate = Path(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise QualifiedEpisodeOperationError(f"{label} escapes protected root")
    path = root.joinpath(*candidate.parts)
    _no_reparse(root)
    # Do not call resolve before checking every named path segment.
    parent = root
    for part in candidate.parts:
        parent = parent / part
        # Stateful executor/ledger destinations may not exist until after the
        # immutable operation document has passed validation. Existing parents
        # are still checked before any later open can create beneath them.
        if parent.exists():
            _no_reparse(parent)
        else:
            break
    return path


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise QualifiedEpisodeOperationError("duplicate protected JSON key")
        result[key] = value
    return result


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], str]:
    _no_reparse(path)
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(QualifiedEpisodeOperationError("non-finite protected JSON")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise QualifiedEpisodeOperationError(f"{label} is not readable strict JSON") from error
    if not isinstance(data, dict):
        raise QualifiedEpisodeOperationError(f"{label} must be a JSON object")
    return data, sha256(raw).hexdigest()


def _hash(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise QualifiedEpisodeOperationError(f"{label} must be lowercase SHA-256")
    return value


def _operation_path(root: Path, operation_id: str) -> Path:
    _no_reparse(root)
    return _relative(root, f"operations/{operation_id}.json", "operation document")


def load_qualified_operation(operation_id: str) -> dict[str, Any]:
    """Load and validate a closed operation document from the compiled root."""
    operation_id = _literal_operation_id(operation_id)
    root = ProtectedOperationLocator.production().root
    document, _ = _read_json(_operation_path(root, operation_id), "protected operation")
    required = {
        "schema", "operation_id", "episode_id", "mode", "executor_database",
        "ledger_database", "ledger_policy_sha256", "endpoint", "scene", "category",
        "input_kind", "payload", "maximum_usd", "provider_kind",
    }
    if set(document) != required or document.get("schema") != "qualified-episode-operation/v1":
        raise QualifiedEpisodeOperationError("closed qualified operation schema is required")
    if (
        not isinstance(document.get("operation_id"), str)
        or document["operation_id"] != operation_id
        or not isinstance(document.get("episode_id"), str)
        or not _ID.fullmatch(document["episode_id"])
    ):
        raise QualifiedEpisodeOperationError("operation and episode identity mismatch")
    if document.get("mode") not in {"TEST", "LIVE"}:
        raise QualifiedEpisodeOperationError("explicit TEST or LIVE operation mode is required")
    if document.get("category") not in {"first", "alternative", "correction", "thumbnail"}:
        raise QualifiedEpisodeOperationError("closed operation category is required")
    if document.get("input_kind") not in {"TEXT_TO_IMAGE", "IMAGE_EDIT"}:
        raise QualifiedEpisodeOperationError("closed input kind is required")
    if not all(isinstance(document.get(key), str) and document[key] for key in ("endpoint", "scene", "executor_database", "ledger_database")):
        raise QualifiedEpisodeOperationError("qualified operation string binding is invalid")
    _relative(root, document["executor_database"], "executor database")
    _relative(root, document["ledger_database"], "ledger database")
    _hash(document.get("ledger_policy_sha256"), "ledger policy")
    payload = document.get("payload")
    if not isinstance(payload, dict) or set(payload) != {"path", "sha256"}:
        raise QualifiedEpisodeOperationError("closed payload binding is required")
    payload_path = _relative(root, payload.get("path"), "payload")
    expected = _hash(payload.get("sha256"), "payload")
    _payload, found = _read_json(payload_path, "payload")
    if found != expected:
        raise QualifiedEpisodeOperationError("payload hash binding changed")
    try:
        cap = Decimal(str(document["maximum_usd"]))
    except Exception as error:
        raise QualifiedEpisodeOperationError("maximum operation cost is invalid") from error
    if not cap.is_finite() or cap <= 0:
        raise QualifiedEpisodeOperationError("maximum operation cost is invalid")
    kind = document["provider_kind"]
    if kind not in {
        "FAL_FLUX_IMAGE_EDIT",
        "FAL_FLUX_TEXT_TO_IMAGE",
        "TEST_CONTROLLED_TRANSPORT",
    }:
        raise QualifiedEpisodeOperationError("qualified provider kind is unsupported")
    if document["mode"] == "LIVE" and kind != "FAL_FLUX_TEXT_TO_IMAGE":
        raise QualifiedEpisodeOperationError(
            "LIVE currently supports only qualified FAL text-to-image"
        )
    if document["mode"] == "TEST" and kind != "TEST_CONTROLLED_TRANSPORT":
        raise QualifiedEpisodeOperationError("TEST operations require controlled transport")
    return document


class _ControlledTransport:
    """TEST-only persisted fake used to exercise executor recovery mechanics."""

    mode = "TEST"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.journal = root / "state" / "transport-journal.json"

    def _count(self, key: str) -> None:
        values = {"gets": 0, "posts": 0}
        if self.journal.exists():
            values.update(json.loads(self.journal.read_text(encoding="utf-8")))
        values[key] += 1
        raw = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        self.journal.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_CREAT | os.O_WRONLY | os.O_TRUNC | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(self.journal, flags, 0o600), "wb") as handle:
            handle.write(raw); handle.flush(); os.fsync(handle.fileno())

    async def submit(self, job: Job, request_id: str, checkpoint) -> ProviderResult:
        self._count("posts")
        checkpoint(provider_id=f"TEST-{request_id[:16]}")
        if os.environ.get("QUALIFIED_TEST_EXIT_AFTER_CHECKPOINT") == "1":
            os._exit(97)
        output = self.root / "state" / f"{request_id}.bin"
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.exists():
            output.write_bytes(b"qualified episode TEST result")
        return ProviderResult(output, job.cost)

    async def recover(self, job: Job, request_id: str, provider_id: str, partial: str, checkpoint) -> ProviderResult:
        self._count("gets")
        output = self.root / "state" / f"{request_id}.bin"
        output.parent.mkdir(parents=True, exist_ok=True)
        if not output.exists():
            output.write_bytes(b"qualified episode TEST recovered result")
        return ProviderResult(output, job.cost)


@dataclass
class QualifiedEpisodeOperation:
    operation_id: str
    document: dict[str, Any]
    root: Path
    executor: Executor
    ledger: StandingBudgetLedger
    job: Job
    provider: object

    async def execute(self) -> dict:
        """Prepare, reserve, mark, and submit through the existing executor."""
        document = validate_public_qualified_operation(self)
        scope = (
            "TEST_LOCAL_ONLY"
            if self.job.mode == "TEST"
            else f"GENERIC_OPERATION:{self.operation_id}:{self.job.ledger_policy_sha256}"
        )
        prepared = self.executor.prepare_intent(self.job, auth_scope=scope)
        validate_prepared_generic_origin(self.job, prepared, self.operation_id)
        self.ledger.reserve(
            self.job.request_id, document["episode_id"], self.job.endpoint, self.job.cost,
            settlement_mode="FAL_READBACK_V2", auth_scope=scope,
            provider_request_id="UNASSIGNED", authority_mode=self.job.mode,
        )
        self.executor.bind_prepared_generic_admission(self.job, operation_id=self.operation_id)
        return await self.executor.run(self.job, self.provider, submit_prepared=True)

    async def recover(self) -> dict:
        validate_public_qualified_operation(self)
        row = self.executor.inspect(self.job.request_id)
        if row is None:
            raise QualifiedEpisodeOperationError("no durable operation exists to recover")
        if row.get("status") != "COMPLETE" and not row.get("provider_id"):
            raise QualifiedEpisodeOperationError("ambiguous operation without remote ID cannot resubmit")
        return await self.executor.run(self.job, self.provider)


def validate_public_qualified_operation(operation: object) -> dict[str, Any]:
    """Rebuild public-operation authority from the fixed loader before state I/O.

    The public dataclass constructor is convenience syntax, not an authority
    boundary.  Its document, root, executor, ledger, job, and provider must all
    agree with a fresh fixed-root load before an entrypoint can inspect state or
    return an idempotent terminal receipt.
    """
    if type(operation) is not QualifiedEpisodeOperation:
        raise QualifiedEpisodeOperationError("canonical qualified operation is required")
    operation_id = _literal_operation_id(operation.operation_id)
    document = load_qualified_operation(operation_id)
    root = ProtectedOperationLocator.production().root
    if (
        _canonical_storage_path(operation.root, "operation root")
        != _canonical_storage_path(root, "protected operation root")
    ):
        raise QualifiedEpisodeOperationError("operation root does not match fixed protected root")
    if type(operation.executor) is not Executor or operation.executor._standing_ledger is not operation.ledger:
        raise QualifiedEpisodeOperationError("canonical executor and attached ledger are required")
    validate_intrinsic_generic_storage(operation.job, operation.executor.database, operation.ledger)
    if type(operation.job) is not QualifiedGenericJob:
        raise QualifiedEpisodeOperationError("qualified generic job is required")
    payload, payload_sha = _read_json(
        _relative(root, document["payload"]["path"], "payload"), "payload"
    )
    if (
        operation.job.operation_id != operation_id
        or operation.job.episode_id != document["episode_id"]
        or operation.job.ledger_policy_sha256 != document["ledger_policy_sha256"]
        or operation.job.scene != document["scene"]
        or operation.job.category != document["category"]
        or operation.job.mode != document["mode"]
        or operation.job.endpoint != document["endpoint"]
        or operation.job.input_kind != document["input_kind"]
        or operation.job.manifest is not None
        or operation.job.payload != payload
        or operation.job.intent_input_sha256 != payload_sha
        or operation.job.cost != Decimal(document["maximum_usd"])
    ):
        raise QualifiedEpisodeOperationError("qualified job does not match fixed operation document")
    if (
        document["mode"] == "TEST"
        and (type(operation.provider) is not _ControlledTransport or operation.provider.root != root)
    ):
        raise QualifiedEpisodeOperationError("fixed TEST controlled transport is required")
    return document


def build_qualified_episode_operation(operation_id: str) -> QualifiedEpisodeOperation:
    """Build only from a validated protected document and fixed provider mapping."""
    document = load_qualified_operation(operation_id)

    root = ProtectedOperationLocator.production().root
    payload_path = _relative(root, document["payload"]["path"], "payload")
    payload, payload_sha = _read_json(payload_path, "payload")
    if payload_sha != document["payload"]["sha256"]:
        raise QualifiedEpisodeOperationError("payload changed while building operation")
    job = QualifiedGenericJob(
        document["scene"], document["category"], document["mode"],
        document["endpoint"], payload, None, Decimal(document["maximum_usd"]),
        input_kind=document["input_kind"], operation_id=operation_id,
        episode_id=document["episode_id"],
        ledger_policy_sha256=document["ledger_policy_sha256"],
    )
    if job.input_kind != "TEXT_TO_IMAGE":
        raise QualifiedEpisodeOperationError(
            "reference manifest qualification is required before IMAGE_EDIT construction"
        )
    executor_path = _relative(root, document["executor_database"], "executor database")
    ledger_path = _relative(root, document["ledger_database"], "ledger database")
    ledger = StandingBudgetLedger(ledger_path, policy_sha256=document["ledger_policy_sha256"],
        episode_limit_usd=str(document["maximum_usd"]), month_limit_usd=str(document["maximum_usd"]),
        experiment_limit_usd=str(document["maximum_usd"]), mode=document["mode"])
    executor = Executor(executor_path, Config(limit=Decimal(document["maximum_usd"]), concurrency=1), standing_ledger=ledger)
    if document["provider_kind"] == "TEST_CONTROLLED_TRANSPORT":
        provider = _ControlledTransport(root)
    else:
        from src.hybrid.live import FalFluxProvider
        provider = FalFluxProvider(root / "quarantine")
    return QualifiedEpisodeOperation(operation_id, document, root, executor, ledger, job, provider)


def _ledger_path(ledger: object) -> str:
    path = getattr(ledger, "path", None)
    if not isinstance(path, Path):
        raise QualifiedEpisodeOperationError("configured canonical ledger path is required")
    return str(path)


def _canonical_storage_path(path: object, label: str) -> str:
    """Return a reparse-free canonical SQLite path without accepting aliases."""
    if not isinstance(path, Path):
        raise QualifiedEpisodeOperationError(f"{label} path is required")
    existing = path
    while not existing.exists():
        if existing.parent == existing:
            raise QualifiedEpisodeOperationError(f"{label} path is unavailable")
        existing = existing.parent
    _no_reparse(existing)
    return os.path.normcase(str(path.resolve(strict=False)))


def validate_intrinsic_generic_storage(
    job: Job, executor_database: object, ledger: object
) -> None:
    """Require the complete closed generic contract before any executor I/O."""
    if type(job) is not QualifiedGenericJob:
        raise QualifiedEpisodeOperationError("generic storage requires a qualified generic job")
    operation_id = _literal_operation_id(job.operation_id)
    document = load_qualified_operation(operation_id)
    root = ProtectedOperationLocator.production().root
    expected_executor = _canonical_storage_path(
        _relative(root, document["executor_database"], "executor database"),
        "canonical executor database",
    )
    expected_ledger = _canonical_storage_path(
        _relative(root, document["ledger_database"], "ledger database"),
        "canonical ledger database",
    )
    if (
        _canonical_storage_path(executor_database, "executor database") != expected_executor
        or _canonical_storage_path(getattr(ledger, "path", None), "ledger database")
        != expected_ledger
    ):
        raise QualifiedEpisodeOperationError(
            "generic operation storage does not match fixed canonical paths"
        )
    payload, payload_sha = _read_json(
        _relative(root, document["payload"]["path"], "payload"), "payload"
    )
    if (
        job.episode_id != document["episode_id"]
        or job.ledger_policy_sha256 != document["ledger_policy_sha256"]
        or job.scene != document["scene"]
        or job.category != document["category"]
        or job.mode != document["mode"]
        or job.endpoint != document["endpoint"]
        or job.input_kind != document["input_kind"]
        or job.manifest is not None
        or job.payload != payload
        or job.intent_input_sha256 != payload_sha
        or job.cost != Decimal(document["maximum_usd"])
    ):
        raise QualifiedEpisodeOperationError(
            "generic job does not match fixed operation document"
        )


def _claim_evidence(claim: object) -> dict[str, Any]:
    required = {
        "request_id", "episode_id", "provider", "month", "reserved_usd", "state",
        "actual_usd", "experiment", "settlement_mode", "auth_scope",
        "provider_request_id", "authority_mode", "billing_snapshot_generation",
    }
    if not isinstance(claim, dict) or set(claim) != required:
        raise QualifiedEpisodeOperationError("closed canonical ledger claim is required")
    strings = required - {"actual_usd", "experiment", "billing_snapshot_generation"}
    if any(type(claim[key]) is not str or not claim[key] for key in strings):
        raise QualifiedEpisodeOperationError("canonical ledger claim type is invalid")
    if claim["actual_usd"] is not None and type(claim["actual_usd"]) is not str:
        raise QualifiedEpisodeOperationError("canonical ledger actual amount type is invalid")
    if type(claim["experiment"]) is not bool or (
        claim["billing_snapshot_generation"] is not None
        and type(claim["billing_snapshot_generation"]) is not int
    ):
        raise QualifiedEpisodeOperationError("canonical ledger claim type is invalid")
    return dict(claim)


def _marker_for_claim(
    operation_id: str, job: Job, executor_database: Path, ledger: object, claim: object
) -> dict[str, Any]:
    """Build the complete immutable record from fixed document and configured ledger."""
    operation_id = _literal_operation_id(operation_id)
    validate_intrinsic_generic_storage(job, executor_database, ledger)
    if getattr(job, "operation_id", None) != operation_id:
        raise QualifiedEpisodeOperationError("operation document does not match prepared job origin")
    document = load_qualified_operation(operation_id)
    binding = getattr(ledger, "binding", None)
    if not isinstance(binding, dict) or set(binding) not in ({
        "schema", "policy_sha256", "mode", "currency", "authority", "episode", "month", "experiment",
    }, {
        "schema", "policy_sha256", "mode", "currency", "authority", "episode", "month", "experiment", "authorization_id",
    }):
        raise QualifiedEpisodeOperationError("configured canonical ledger binding is invalid")
    if (
        document["mode"] != job.mode
        or document["endpoint"] != job.endpoint
        or document["scene"] != job.scene
        or document["category"] != job.category
        or document["input_kind"] != job.input_kind
        or document["payload"]["sha256"] != job.intent_input_sha256
        or binding.get("policy_sha256") != document["ledger_policy_sha256"]
        or binding.get("mode") != job.mode
        or binding.get("currency") != "USD"
    ):
        raise QualifiedEpisodeOperationError("operation immutable inputs do not match prepared job")
    claim = _claim_evidence(claim)
    if (
        claim["request_id"] != job.request_id
        or claim["episode_id"] != document["episode_id"]
        or claim["provider"] != job.endpoint
        or claim["authority_mode"] != job.mode
        or claim["settlement_mode"] != "FAL_READBACK_V2"
        or claim["state"] != "RESERVED"
        or claim["provider_request_id"] != "UNASSIGNED"
        or Decimal(claim["reserved_usd"]) != job.cost
    ):
        raise QualifiedEpisodeOperationError("canonical ledger claim does not bind operation")
    _, document_sha = _read_json(
        _operation_path(ProtectedOperationLocator.production().root, operation_id),
        "protected operation",
    )
    return {
        "schema": 2,
        "kind": "GENERIC_CLOSED_ADMISSION",
        "operation_id": operation_id,
        "operation_sha256": document_sha,
        "operation_policy_sha256": document["ledger_policy_sha256"],
        "request_id": job.request_id,
        "episode_id": document["episode_id"],
        "provider_endpoint": job.endpoint,
        "authority_mode": job.mode,
        "input_kind": job.input_kind,
        "payload_sha256": job.intent_input_sha256,
        "executor_canonical_path": _canonical_storage_path(executor_database, "executor database"),
        "ledger_canonical_path": _ledger_path(ledger),
        "ledger_binding": dict(binding),
        "reservation_claim": claim,
    }


def build_generic_admission_marker(
    operation_id: str, job: Job, executor_database: Path, ledger: object
) -> dict[str, Any]:
    """Create a write-once marker from the configured canonical ledger only."""
    claim_reader = getattr(ledger, "claim", None)
    if not callable(claim_reader):
        raise QualifiedEpisodeOperationError("configured canonical ledger claim reader is required")
    return _marker_for_claim(
        operation_id, job, executor_database, ledger, claim_reader(job.request_id)
    )


def validate_prepared_generic_origin(job: Job, row: object, operation_id: str) -> None:
    """Reject a foreign operation before claim reads or marker persistence."""
    operation_id = _literal_operation_id(operation_id)
    if not isinstance(row, dict):
        raise QualifiedEpisodeOperationError("prepared generic executor row is required")
    if getattr(job, "operation_id", None) != operation_id:
        raise QualifiedEpisodeOperationError("operation document does not match prepared job origin")
    if row.get("generic_operation_id") != operation_id:
        raise QualifiedEpisodeOperationError("operation document does not match prepared row origin")
    document = load_qualified_operation(operation_id)
    if (
        getattr(job, "episode_id", None) != document["episode_id"]
        or getattr(job, "ledger_policy_sha256", None) != document["ledger_policy_sha256"]
    ):
        raise QualifiedEpisodeOperationError("operation document does not match generic job contract")


def validate_generic_admission_marker(
    job: Job, marker: object, executor_database: Path, ledger: object, row: object
) -> None:
    """Validate closed operation, ledger source, and recovery claim before I/O."""
    if not isinstance(marker, dict) or not isinstance(row, dict):
        raise QualifiedEpisodeOperationError("generic admission marker and executor row are required")
    operation_id = _literal_operation_id(marker.get("operation_id"))
    if row.get("generic_operation_id") != operation_id:
        raise QualifiedEpisodeOperationError("operation document does not match prepared job origin")
    reservation_claim = marker.get("reservation_claim")
    expected = _marker_for_claim(
        operation_id, job, executor_database, ledger, reservation_claim
    )
    if marker != expected:
        raise QualifiedEpisodeOperationError("generic admission marker is forged or stale")
    claim_reader = getattr(ledger, "claim", None)
    if not callable(claim_reader):
        raise QualifiedEpisodeOperationError("configured canonical ledger claim reader is required")
    current = _claim_evidence(claim_reader(job.request_id))
    provider_id = row.get("provider_id")
    if provider_id:
        if type(provider_id) is not str:
            raise QualifiedEpisodeOperationError("executor provider identity is invalid")
        settlement = row.get("settlement_binding")
        if not isinstance(settlement, dict) or settlement.get("provider_request_id") != provider_id:
            raise QualifiedEpisodeOperationError("executor provider identity does not bind recovery")
    if current != reservation_claim:
        raise QualifiedEpisodeOperationError("canonical ledger claim changed after admission")


def qualified_operation_readiness(operation_id: str) -> dict[str, Any]:
    """Read exact protected configuration status; never infer provider readiness."""
    try:
        document = load_qualified_operation(operation_id)
    except QualifiedEpisodeOperationError as error:
        return {"operation_id": operation_id, "status": "BLOCKED", "live_enabled": False, "reason": str(error)}
    # A validated route describes deployment input, not executor authority: the
    # current Executor admits LIVE only through its Jacó final-admission branch.
    # Reporting enabled here would turn a protected document into a false LIVE
    # claim before that generic verifier exists.
    return {"operation_id": operation_id, "status": "BLOCKED", "live_enabled": False,
            "mode": document["mode"], "provider_kind": document["provider_kind"],
            "reason": "generic LIVE executor admission verifier is not installed"}
