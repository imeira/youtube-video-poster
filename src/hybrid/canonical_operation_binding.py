"""Pure read-only validation of a fixed canonical operation binding.

This module deliberately returns evidence only.  It does not construct an
executor, reserve money, claim work, load credentials, or dispatch a provider.
Production callers obtain ``CanonicalOperationBinding`` instances only from a
repository-owned literal registry; test fixtures may call the pure verifier to
exercise the same source-owned transcript and ledger reads.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import stat
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_LITERAL = re.compile(r"[A-Z][A-Z0-9_-]{2,159}\Z", re.ASCII)


class CanonicalOperationBindingError(PermissionError):
    """A fixed canonical source did not prove the requested operation binding."""


@dataclass(frozen=True)
class CanonicalOperationBinding:
    """Repository-selected paths and pins for one literal operation ID."""

    operation_id: str
    root: Path
    authority_name: str
    authority_sha256: str
    financial_name: str
    financial_sha256: str
    ledger_name: str
    transcript_path: Path
    operation_scope: str


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise CanonicalOperationBindingError("duplicate JSON key")
        result[key] = value
    return result


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise CanonicalOperationBindingError(f"{label} must be a lower-case SHA-256")
    return value


def _literal(value: object, label: str) -> str:
    if not isinstance(value, str) or _LITERAL.fullmatch(value) is None:
        raise CanonicalOperationBindingError(f"literal {label} is invalid")
    return value


def _money(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise CanonicalOperationBindingError(f"{label} must be a decimal string")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise CanonicalOperationBindingError(f"{label} is invalid") from error
    if not amount.is_finite() or amount <= 0:
        raise CanonicalOperationBindingError(f"{label} is invalid")
    return value


def _no_reparse(path: Path, label: str) -> None:
    for ancestor in (path, *path.parents):
        try:
            info = ancestor.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise CanonicalOperationBindingError(f"{label} traverses a reparse point")


def _identity(path: Path, label: str) -> tuple[int, int, int, int]:
    """Return a stable regular-file/dir identity without following a link."""
    _no_reparse(path, label)
    try:
        info = path.lstat()
    except OSError as error:
        raise CanonicalOperationBindingError(f"{label} is unavailable") from error
    if stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise CanonicalOperationBindingError(f"{label} traverses a reparse point")
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _require_no_sqlite_sidecars(path: Path, label: str) -> None:
    """WAL/SHM means a main-db-only snapshot could silently be stale."""
    for suffix in ("-wal", "-shm"):
        sidecar = path.with_name(path.name + suffix)
        try:
            sidecar.lstat()
        except FileNotFoundError:
            continue
        except OSError as error:
            raise CanonicalOperationBindingError(f"{label} sidecar inspection failed") from error
        raise CanonicalOperationBindingError(f"{label} WAL/SHM sidecar is present")


def _stable_bytes(path: Path, label: str) -> bytes:
    """Capture bytes from one no-follow descriptor and detect replacement."""
    before = _identity(path, label)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise CanonicalOperationBindingError(f"{label} is unavailable") from error
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != before[:2]:
            raise CanonicalOperationBindingError(f"{label} changed before descriptor capture")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after_descriptor = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after = _identity(path, label)
    if before != after or (after_descriptor.st_dev, after_descriptor.st_ino, after_descriptor.st_size) != after[:3]:
        raise CanonicalOperationBindingError(f"{label} changed during snapshot capture")
    return b"".join(chunks)


def _literal_child(root: Path, name: object, label: str) -> Path:
    if not isinstance(name, str) or not name or any(part in name for part in ("/", "\\", ":", "..")):
        raise CanonicalOperationBindingError(f"{label} is not a literal source-owned locator")
    candidate = root / name
    _no_reparse(candidate, label)
    return candidate


def _json(path: Path, label: str) -> tuple[dict[str, Any], str]:
    try:
        raw = _stable_bytes(path, label)
        document = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise CanonicalOperationBindingError(f"{label} is unavailable strict JSON") from error
    if not isinstance(document, dict):
        raise CanonicalOperationBindingError(f"{label} must be an object")
    return document, sha256(raw).hexdigest()


def _closed(document: object, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != fields:
        raise CanonicalOperationBindingError(f"closed {label} schema is required")
    return document


def _read_sqlite_snapshot(path: Path, label: str) -> sqlite3.Connection:
    """Open only verifier-captured bytes; never let SQLite open source paths."""
    _require_no_sqlite_sidecars(path, label)
    raw = _stable_bytes(path, label)
    _require_no_sqlite_sidecars(path, label)
    try:
        database = sqlite3.connect(":memory:")
        database.deserialize(raw)
        return database
    except (sqlite3.Error, ValueError) as error:
        try:
            database.close()
        except UnboundLocalError:
            pass
        raise CanonicalOperationBindingError(f"{label} snapshot is unavailable") from error


def _read_transcript(path: Path, consent: dict[str, Any]) -> None:
    assistant_id = consent["adjacent_assistant_message_id"]
    user_id = consent["user_message_id"]
    if type(assistant_id) is not int or type(user_id) is not int or user_id != assistant_id + 1:
        raise CanonicalOperationBindingError("canonical transcript message IDs are not adjacent")
    try:
        database = _read_sqlite_snapshot(path, "canonical transcript")
        try:
            rows = database.execute(
                "SELECT id, session_id, role, content FROM messages WHERE id IN (?, ?) ORDER BY id",
                (assistant_id, user_id),
            ).fetchall()
        finally:
            database.close()
    except sqlite3.Error as error:
        raise CanonicalOperationBindingError("canonical transcript is unavailable") from error
    if len(rows) != 2:
        raise CanonicalOperationBindingError("canonical transcript records are incomplete")
    expected = (
        (assistant_id, "assistant", consent["assistant_message_sha256"]),
        (user_id, "user", consent["user_message_sha256"]),
    )
    for row, (message_id, role, digest) in zip(rows, expected, strict=True):
        actual_id, session_id, actual_role, content = row
        if (
            actual_id != message_id
            or session_id != consent["session_id"]
            or actual_role != role
            or not isinstance(content, str)
            or sha256(content.encode("utf-8")).hexdigest() != digest
        ):
            raise CanonicalOperationBindingError("canonical transcript binding rejected")


def _read_ledger(path: Path, financial_sha256: str) -> None:
    try:
        database = _read_sqlite_snapshot(path, "canonical financial ledger")
        try:
            row = database.execute("SELECT binding FROM policy WHERE id = 1").fetchone()
        finally:
            database.close()
        binding = json.loads(row[0], object_pairs_hook=_pairs) if row is not None else None
    except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as error:
        raise CanonicalOperationBindingError("canonical financial ledger is unavailable") from error
    if not isinstance(binding, dict) or set(binding) != {"financial_policy_sha256"}:
        raise CanonicalOperationBindingError("canonical financial ledger binding is invalid")
    if binding["financial_policy_sha256"] != financial_sha256:
        raise CanonicalOperationBindingError("canonical financial ledger policy hash mismatch")


def verify_canonical_operation_binding(
    binding: CanonicalOperationBinding, *, transcript_path: Path
) -> dict[str, Any]:
    """Verify one complete canonical chain and return immutable bound evidence.

    ``binding`` is a data parameter to keep the verifier reusable; production
    must obtain it from the fixed registry. The function has no dispatch or
    mutation capability. SQLite reads deserialize verifier-captured bytes into
    memory after rejecting WAL/SHM sidecars; it never opens source databases
    through SQLite and never writes, checkpoints, or deletes source state. A
    future active-database path needs a separate canonical backup producer.
    """
    if not isinstance(binding, CanonicalOperationBinding):
        raise CanonicalOperationBindingError("canonical binding type is required")
    operation_id = _literal(binding.operation_id, "operation ID")
    operation_scope = _literal(binding.operation_scope, "operation scope")
    root = Path(binding.root)
    _no_reparse(root, "canonical root")
    policy_path = _literal_child(root, binding.authority_name, "operation authority")
    financial_path = _literal_child(root, binding.financial_name, "financial policy")
    ledger_path = _literal_child(root, binding.ledger_name, "financial ledger")
    root_identity = _identity(root, "canonical root")
    _require_no_sqlite_sidecars(Path(transcript_path), "canonical transcript")
    _require_no_sqlite_sidecars(ledger_path, "canonical financial ledger")
    policy, policy_sha = _json(policy_path, "operation authority")
    financial, financial_sha = _json(financial_path, "financial policy")
    if policy_sha != _sha(binding.authority_sha256, "operation authority"):
        raise CanonicalOperationBindingError("operation authority immutable hash mismatch")
    if financial_sha != _sha(binding.financial_sha256, "financial policy"):
        raise CanonicalOperationBindingError("financial policy immutable hash mismatch")
    policy = _closed(policy, {"schema", "operation_id", "episode_id", "operation_scope", "canonical_consent"}, "operation authority")
    episode_id = _literal(policy["episode_id"], "episode ID")
    if (
        policy["schema"] != "generic-canonical-operation-policy/v1"
        or policy["operation_id"] != operation_id
        or policy["operation_scope"] != operation_scope
    ):
        raise CanonicalOperationBindingError("operation authority scope is invalid")
    consent = _closed(
        policy["canonical_consent"],
        {"schema", "session_id", "adjacent_assistant_message_id", "user_message_id", "assistant_message_sha256", "user_message_sha256"},
        "canonical consent",
    )
    if consent["schema"] != "canonical-transcript-provenance/v1" or not isinstance(consent["session_id"], str) or not consent["session_id"]:
        raise CanonicalOperationBindingError("canonical consent provenance is invalid")
    _sha(consent["assistant_message_sha256"], "assistant message")
    _sha(consent["user_message_sha256"], "user message")
    _read_transcript(Path(transcript_path), consent)
    financial = _closed(financial, {"schema", "operation_id", "episode_id", "operation_scope", "currency", "maximum_usd", "status"}, "financial policy")
    maximum_usd = _money(financial["maximum_usd"], "financial maximum")
    if (
        financial["schema"] != "generic-canonical-financial-policy/v1"
        or financial["operation_id"] != operation_id
        or financial["episode_id"] != episode_id
        or financial["operation_scope"] != operation_scope
        or financial["currency"] != "USD"
        or financial["status"] != "ACTIVE"
    ):
        raise CanonicalOperationBindingError("financial policy scope is invalid")
    _read_ledger(ledger_path, financial_sha)
    _require_no_sqlite_sidecars(Path(transcript_path), "canonical transcript")
    _require_no_sqlite_sidecars(ledger_path, "canonical financial ledger")
    if _identity(root, "canonical root") != root_identity:
        raise CanonicalOperationBindingError("canonical root changed during verification")
    return {
        "schema": "generic-canonical-binding-evidence/v1",
        "operation_id": operation_id,
        "episode_id": episode_id,
        "operation_scope": operation_scope,
        "operation_policy_sha256": policy_sha,
        "financial_policy_sha256": financial_sha,
        "canonical_message_ids": {"assistant": consent["adjacent_assistant_message_id"], "user": consent["user_message_id"]},
        "financial": {"currency": "USD", "maximum_usd": maximum_usd},
        "read_only": True,
        "status": "TEST_BOUND_EVIDENCE",
        "authority": "NONE",
        "live_authority": False,
    }


def verify_fixed_canonical_operation_binding(binding: CanonicalOperationBinding) -> dict[str, Any]:
    """Use only the source-owned transcript locator fixed in a trusted binding."""
    return verify_canonical_operation_binding(binding, transcript_path=binding.transcript_path)
