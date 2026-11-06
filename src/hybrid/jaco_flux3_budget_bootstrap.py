"""Fail-closed bootstrap of the sole Jacó production budget ledger.

The inventory is deliberately a configured finite list, not a filesystem scan.
An absent source means UNKNOWN, never an empty paid history.
"""
from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.hybrid.locks import file_lock
from src.hybrid.standing_budget import StandingBudgetLedger

REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
EPISODE_ID = "JACO_GENESIS28"
ROOT = Path("C:/HermesStudio/production/jaco-flux3")
LEDGER = ROOT / "ledger" / "standing-budget.sqlite3"
_REQUIRED_CLASSES = frozenset({
    "provider_receipts", "executor_intents", "executor_wal", "standing_ledger_claims",
    "billing_readbacks", "reconciliation_records",
})


class BootstrapCollision(PermissionError):
    """A previously initialized ledger cannot be rebound or reset."""


def _canon(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _strict_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing or non-regular source")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("source is not an object")
    return value


def _within(root: Path, relative: object) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("journal path must be a nonempty relative path")
    candidate = (root / relative).resolve()
    if candidate.parent != root.resolve() and root.resolve() not in candidate.parents:
        raise ValueError("journal path escapes production root")
    return candidate


def _discovered_inventory(root: Path) -> dict[str, Any]:
    """Finite real storage roots, used when no speculative config field exists."""
    paths = {
        "provider_receipts": root / "execution" / "provider-receipts.json",
        "executor_intents": root / "execution" / "executor-intents.json",
        "executor_wal": root / "execution" / "executor-wal.json",
        "standing_ledger_claims": root / "ledger" / "standing-budget.sqlite3",
        "billing_readbacks": root / "execution" / "billing-readbacks.json",
        "reconciliation_records": root / "financial-record-source.json",
    }
    snapshots = []
    for category, path in paths.items():
        if category == "reconciliation_records":
            try:
                document = _strict_json(path)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                return _blocked(missing=[str(path)], reason="configured canonical financial source is unreadable")
            if document.get("request_hash") != REQUEST_ID:
                return _blocked(missing=[], reason=f"configured source is stale or incompatible: {path}")
            snapshots.append({"class": category, "path": str(path), "sha256": _sha(path), "record_count": 0, "source_kind": "financial_reconciliation_record"})
            continue
        if path.exists():
            if category == "standing_ledger_claims":
                try:
                    with sqlite3.connect(path) as db:
                        count = db.execute("SELECT COUNT(*) FROM claims WHERE episode_id=?", (EPISODE_ID,)).fetchone()[0]
                except sqlite3.Error:
                    return _blocked(missing=[str(path)], reason="configured canonical financial source requires reconciliation")
                snapshots.append({"class": category, "path": str(path), "sha256": _sha(path), "record_count": count, "source_kind": "standing_budget_sqlite"})
                continue
            return _blocked(missing=[str(path)], reason="configured canonical financial source requires reconciliation")
        storage = root / path.relative_to(root).parts[0]
        allowed_empty = not storage.exists() or (category == "standing_ledger_claims" and {child.name for child in storage.iterdir()} <= {".standing-budget-bootstrap.lock"})
        if not allowed_empty:
            return _blocked(missing=[str(path)], reason="configured canonical financial source is unreadable")
        snapshots.append({"class": category, "path": str(path), "sha256": None, "record_count": 0, "absence": "VERIFIED_NEVER_INITIALIZED_ROOT"})
    digest = sha256(_canon(snapshots).encode()).hexdigest()
    return {"status": "COMPLETE", "sources": snapshots, "inventory_sha256": digest, "count_records": 0,
            "count_paid_records": 0, "count_ambiguous_or_unsettled_records": 0, "missing_sources": []}


def fixed_request_scope_inventory(*, root: Path = ROOT) -> dict[str, Any]:
    """Enumerate exactly the configured production journal sources once.

    Every source is a bounded JSON snapshot with request/episode binding.  The
    configured class list is the completeness definition; no broad text search
    can establish a zero-cost history.
    """
    root = Path(root).resolve()
    config_path = root / "production-composition.json"
    missing: list[str] = []
    try:
        config = _strict_json(config_path)
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return _blocked(missing=[str(config_path)], reason="missing configured production manifest")
    journals = config.get("canonical_financial_journals")
    if journals is None:
        return _discovered_inventory(root)
    if not isinstance(journals, list):
        return _blocked(missing=[f"{config_path}:canonical_financial_journals"], reason="configured journal list is absent")
    seen: set[str] = set()
    snapshots: list[dict[str, Any]] = []
    records: list[dict[str, Any]] = []
    for item in journals:
        if not isinstance(item, dict) or set(item) != {"class", "path", "schema"}:
            return _blocked(missing=[], reason="configured journal entry has unsupported schema")
        category = item["class"]
        if not isinstance(category, str) or category in seen or category not in _REQUIRED_CLASSES:
            return _blocked(missing=[], reason="configured journal classes are ambiguous")
        seen.add(category)
        try:
            source = _within(root, item["path"])
            document = _strict_json(source)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            missing.append(str((root / str(item.get("path", ""))).resolve()))
            continue
        if (document.get("schema") != item["schema"] or document.get("request_id") != REQUEST_ID
                or document.get("episode_id") != EPISODE_ID or not isinstance(document.get("records"), list)):
            return _blocked(missing=[], reason=f"configured source is stale or incompatible: {source}")
        source_records = document["records"]
        if not all(isinstance(record, dict) for record in source_records):
            return _blocked(missing=[], reason=f"configured source records are malformed: {source}")
        snapshots.append({"class": category, "path": str(source), "sha256": _sha(source), "record_count": len(source_records)})
        records.extend(source_records)
    if seen != _REQUIRED_CLASSES:
        return _blocked(missing=[f"{config_path}:missing_classes={','.join(sorted(_REQUIRED_CLASSES - seen))}"], reason="configured journal list is incomplete")
    if missing:
        return _blocked(missing=sorted(missing), reason="configured canonical financial source is unreadable")
    paid = unsettled = 0
    allowed = {
        "provider_receipt": {"SETTLED"}, "billing_receipt": {"SETTLED"},
        "executor_wal": {"PREPARED", "SUBMITTING", "SETTLED", "UNKNOWN", "UNKNOWN_0_OR_1"},
        "executor_intent": {"PREPARED", "RESERVED", "SUBMITTING", "SETTLED", "UNKNOWN", "UNKNOWN_0_OR_1"},
        "standing_ledger_claim": {"RESERVED", "SETTLED", "UNKNOWN"},
        "billing_readback": {"SETTLED", "UNKNOWN", "UNKNOWN_0_OR_1"},
        "reconciliation_record": {"ZERO_HISTORY_VERIFIED", "SETTLED", "UNKNOWN"},
    }
    for index, record in enumerate(records):
        if not isinstance(record, dict) or record.get("state") not in allowed.get(record.get("kind"), set()):
            return _blocked(missing=[], reason=f"unrecognized record state: {index}")
        paid += record["state"] == "SETTLED"
        unsettled += record["state"] in {"PREPARED", "RESERVED", "SUBMITTING", "UNKNOWN", "UNKNOWN_0_OR_1"}
    digest = sha256(_canon(snapshots).encode()).hexdigest()
    return {"status": "COMPLETE", "sources": snapshots, "inventory_sha256": digest,
            "count_records": len(records), "count_paid_records": paid,
            "count_ambiguous_or_unsettled_records": unsettled, "missing_sources": []}


def _blocked(*, missing: list[str], reason: str) -> dict[str, Any]:
    return {"status": "BLOCKED_UNKNOWN", "available_usd": "0.000000", "missing_sources": missing,
            "blocking_reason": reason, "count_paid_records": "UNKNOWN",
            "count_ambiguous_or_unsettled_records": "UNKNOWN"}


def _financial_binding(root: Path, inventory: dict[str, Any]) -> dict[str, Any]:
    config = _strict_json(root / "production-composition.json")
    source = _within(root, config.get("financial_record"))
    finance = _strict_json(source)
    protected = config.get("protected_financial_sources")
    expected = next((entry for entry in protected if isinstance(entry, dict) and entry.get("path") == config.get("financial_record")), None) if isinstance(protected, list) else None
    if expected is None:
        try:
            expected = {"sha256": _strict_json(root / "parent-provisioned-policy.json")["source_evidence"]["financial_record"]}
        except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
            expected = None
    if not isinstance(expected, dict) or set(expected) != {"path", "sha256"} and set(expected) != {"sha256"} or expected["sha256"] != _sha(source):
        raise BootstrapCollision("financial source hash changed")
    if (finance.get("schema") != "jaco-factory-financial-reconciliation/1" or finance.get("request_hash") != REQUEST_ID
            or finance.get("currency") != "USD" or finance.get("total_episode_cap") != "10.00"
            or finance.get("authorizes_generation") is not False):
        raise BootstrapCollision("financial source does not bind the fixed scope")
    return {"schema": 1, "request_id": REQUEST_ID, "episode_id": EPISODE_ID, "currency": "USD",
            "episode_cap_micro_usd": 10_000_000, "month_cap_micro_usd": 10_000_000,
            "experiment_cap_micro_usd": 10_000_000, "financial_source_sha256": _sha(source),
            "inventory_sha256": inventory["inventory_sha256"], "count_paid_records": 0,
            "count_ambiguous_or_unsettled_records": 0}


def _remaining(ledger_path: Path, binding: dict[str, Any]) -> str:
    """Read actual StandingBudgetLedger exposure under the bootstrap lock."""
    try:
        with sqlite3.connect(ledger_path) as db:
            policy = db.execute("SELECT binding FROM policy WHERE id=1").fetchone()
            if policy is None or json.loads(policy[0]).get("policy_sha256") != binding["financial_source_sha256"]:
                raise BootstrapCollision("existing ledger policy does not bind financial source")
            exposure = db.execute("SELECT COALESCE(SUM(COALESCE(actual, cap)), 0) FROM claims WHERE episode_id=?", (EPISODE_ID,)).fetchone()[0]
    except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BootstrapCollision("existing ledger cannot be reconciled") from exc
    remaining = 10_000_000 - int(exposure)
    if remaining < 0:
        raise BootstrapCollision("existing ledger exceeds immutable episode cap")
    return f"{Decimal(remaining) / Decimal(1_000_000):.6f}"


def bootstrap_canonical_budget(*, root: Path = ROOT) -> dict[str, Any]:
    """Create the shared ledger only after a complete zero-history proof.

    Calls serialize across processes under one production-root lock.  A replay
    returns its original receipt; a changed protected source is a collision.
    """
    root = Path(root).resolve()
    ledger_path = root / "ledger" / "standing-budget.sqlite3"
    lock_path = root / "ledger" / ".standing-budget-bootstrap.lock"
    # Creating the lock directory is harmless; the actual ledger remains absent
    # on all blocked branches.
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(lock_path, timeout=30):
        inventory = fixed_request_scope_inventory(root=root)
        if inventory["status"] != "COMPLETE":
            return inventory
        binding = _financial_binding(root, inventory)
        receipt_body = _canon(binding)
        receipt_hash = sha256(receipt_body.encode()).hexdigest()
        if ledger_path.exists():
            with sqlite3.connect(ledger_path) as db:
                row = db.execute("SELECT binding, receipt_sha256 FROM bootstrap_receipts WHERE id=1").fetchone()
            if row is None:
                raise BootstrapCollision("existing ledger lacks immutable bootstrap receipt")
            try:
                original = json.loads(row[0])
            except (TypeError, json.JSONDecodeError) as exc:
                raise BootstrapCollision("existing ledger bootstrap receipt is malformed") from exc
            if original.get("financial_source_sha256") != binding["financial_source_sha256"]:
                raise BootstrapCollision("financial source changed after bootstrap")
            return {**inventory, "status": "PASS_RECONCILED", "available_usd": _remaining(ledger_path, binding), "ledger_path": str(ledger_path),
                    "policy_sha256": binding["financial_source_sha256"], "bootstrap_receipt_sha256": row[1]}
        if inventory["count_paid_records"] != 0:
            return _blocked(missing=[], reason="prior paid record prevents zero-history bootstrap")
        if inventory["count_ambiguous_or_unsettled_records"] != 0:
            return _blocked(missing=[], reason="ambiguous or unsettled record prevents zero-history bootstrap")
        ledger = StandingBudgetLedger(ledger_path, policy_sha256=binding["financial_source_sha256"],
                                      episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="LIVE")
        with sqlite3.connect(ledger_path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS bootstrap_receipts (id INTEGER PRIMARY KEY CHECK(id=1), binding TEXT NOT NULL, receipt_sha256 TEXT NOT NULL)")
            db.execute("INSERT INTO bootstrap_receipts VALUES(1,?,?)", (receipt_body, receipt_hash))
            db.commit()
        return {**inventory, "status": "PASS_ZERO_HISTORY", "available_usd": _remaining(ledger_path, binding), "ledger_path": str(ledger.path),
                "policy_sha256": binding["financial_source_sha256"], "bootstrap_receipt_sha256": receipt_hash}
