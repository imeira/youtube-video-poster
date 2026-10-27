"""Read-only CLI for the bounded FAL pricing and billing consumer."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from src.hybrid.billing_reconciler import FalLedgerSettlementService, SettlementDenied
from src.hybrid.execution import Executor
from src.hybrid.fal_billing import (
    AiohttpFalBillingClient,
    BillingReadbackRequest,
    FalBillingConsumer,
    FalBillingTransport,
    QuoteRequest,
    validate_action_request,
)
from src.hybrid.planner import Config
from src.hybrid.standing_budget import BudgetDenied, StandingBudgetLedger


def _utc_argument(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise argparse.ArgumentTypeError("must be an ISO-8601 UTC timestamp")
    return parsed


def _positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError("must be a finite positive decimal") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive decimal")
    return parsed


def _report(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _trusted_local_path(path: Path, *, directory: bool) -> Path:
    """Reject links/reparse points before any SQLite open or policy read.

    The state root is a configured, local trusted-composition boundary.  This
    protects accidental traversal/redirects; it does not claim to confine
    hostile code already running with this user's filesystem privileges.
    """
    candidate = Path(path)
    chain = (candidate, *candidate.parents)
    def redirected(item: Path) -> bool:
        try:
            attributes = item.lstat().st_file_attributes
        except (AttributeError, OSError):
            attributes = 0
        return item.is_symlink() or bool(attributes & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    if any(redirected(item) for item in chain):
        raise ValueError("local settlement paths cannot be links or reparse points")
    if (directory and not candidate.is_dir()) or (not directory and not candidate.is_file()):
        raise ValueError("required local settlement path is missing")
    return candidate


def _trusted_settlement_service(root: Path, consumer: FalBillingConsumer) -> FalLedgerSettlementService:
    """Re-open only a durable TEST executor/ledger pair under one local root."""
    root = _trusted_local_path(root, directory=True)
    executor_path, ledger_path = root / "executor.sqlite3", root / "ledger.sqlite3"
    for path in (executor_path, ledger_path):
        _trusted_local_path(path, directory=False)
        for suffix in ("-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists():
                _trusted_local_path(sidecar, directory=False)
    with sqlite3.connect(executor_path) as db:
        row = db.execute("SELECT data FROM settings WHERE id=1").fetchone()
    if row is None:
        raise ValueError("executor settings are required")
    try:
        settings = json.loads(row[0])
        executor = Executor(executor_path, Config(limit=Decimal(settings["limit"]), concurrency=settings["concurrency"]))
    except (KeyError, TypeError, ValueError, InvalidOperation, sqlite3.Error) as exc:
        raise ValueError("trusted executor settings are invalid") from exc
    with sqlite3.connect(ledger_path) as db:
        row = db.execute("SELECT binding FROM policy WHERE id=1").fetchone()
    if row is None:
        raise ValueError("ledger policy binding is required")
    try:
        binding = json.loads(row[0])
        if binding.get("mode") != "TEST" or binding.get("authority") != "direct":
            raise ValueError("CLI settlement is restricted to durable local TEST state")
        ledger = StandingBudgetLedger(ledger_path, policy_sha256=binding["policy_sha256"],
            episode_limit_usd=Decimal(binding["episode"]) / Decimal(1_000_000),
            month_limit_usd=Decimal(binding["month"]) / Decimal(1_000_000),
            experiment_limit_usd=Decimal(binding["experiment"]) / Decimal(1_000_000), mode=binding["mode"])
    except (KeyError, TypeError, ValueError, InvalidOperation, BudgetDenied, sqlite3.Error) as exc:
        raise ValueError("trusted ledger policy binding is invalid") from exc
    return FalLedgerSettlementService(ledger, executor, consumer)


async def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable[[str], FalBillingTransport] = AiohttpFalBillingClient,
    settlement_factory: Callable[[Path, FalBillingConsumer], FalLedgerSettlementService] = _trusted_settlement_service,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "quote", "readback", "reconcile"))
    parser.add_argument("--endpoint-id")
    parser.add_argument("--readback", action="store_true", help="Explicitly permit one read-only FAL GET operation")
    parser.add_argument("--quantity-cap", type=_positive_decimal)
    parser.add_argument("--request-id")
    parser.add_argument("--start", type=_utc_argument)
    parser.add_argument("--end", type=_utc_argument)
    parser.add_argument("--state-root", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.action == "reconcile":
            if args.endpoint_id is not None or args.quantity_cap is not None or args.state_root is None:
                raise ValueError("reconcile accepts only local request/window/state arguments")
            validated = validate_action_request("readback", endpoint_id=args.request_id, request_id=args.request_id, start=args.start, end=args.end)
        else:
            validated = validate_action_request(args.action, endpoint_id=args.endpoint_id, quantity_cap=args.quantity_cap, request_id=args.request_id, start=args.start, end=args.end)
    except (TypeError, ValueError):
        _report({"mode": "READ_ONLY", "qualification": "NOT_QUALIFIED", "status": "UNKNOWN"})
        return 1

    if args.action == "preflight":
        assert isinstance(validated, str)
        _report({
            "endpoint_id": validated,
            "mode": "READ_ONLY",
            "network": "NOT_REQUESTED",
            "qualification": "NOT_QUALIFIED",
            "status": "PREFLIGHT_ONLY",
        })
        return 0

    if not args.readback:
        parser.error(f"{args.action} requires explicit --readback")
    if args.action == "reconcile" and (not args.state_root.is_dir() or args.state_root.is_symlink()):
        _report({"mode": "READ_ONLY", "qualification": "NOT_QUALIFIED", "status": "UNKNOWN"})
        return 1
    key = os.environ.get("FAL_KEY")
    if not key:
        _report({"mode": "READ_ONLY", "qualification": "NOT_QUALIFIED", "status": "BLOCKED", "error": "FAL_KEY_NOT_CONFIGURED"})
        return 1

    try:
        consumer = FalBillingConsumer(client_factory(key))
        if args.action == "quote":
            quote = await consumer.quote(QuoteRequest(args.endpoint_id, args.quantity_cap))
            _report({
                "authority": quote.authority,
                "currency": quote.currency,
                "endpoint_id": quote.endpoint_id,
                "maximum_cost_usd": str(quote.maximum_cost_usd),
                "mode": "READ_ONLY",
                "qualification": "NOT_QUALIFIED",
                "quantity_cap": str(quote.quantity_cap),
                "status": "QUOTE_ONLY",
                "unit": quote.unit,
                "unit_price_usd": str(quote.unit_price_usd),
            })
        elif args.action == "readback":
            receipt = await consumer.readback(BillingReadbackRequest(
                args.request_id, args.endpoint_id, args.start, args.end,
            ))
            _report({
                "actual_cost_usd": None if receipt.actual_cost_usd is None else str(receipt.actual_cost_usd),
                "authority": receipt.authority,
                "cost_estimate_nano_usd": receipt.cost_estimate_nano_usd,
                "endpoint_id": receipt.endpoint_id,
                "mode": "READ_ONLY",
                "qualification": "NOT_QUALIFIED",
                "reason": receipt.reason,
                "request_id": receipt.request_id,
                "status": receipt.status.value,
            })
        else:
            assert isinstance(validated, BillingReadbackRequest)
            service = settlement_factory(args.state_root, consumer)
            result = await service.reconcile(validated.request_id, start=validated.start, end=validated.end)
            _report({"claim": result["claim"], "mode": "READ_ONLY", "qualification": "NOT_QUALIFIED", "request_id": result["request_id"], "status": result["status"]})
        return 0
    except (TypeError, ValueError, RuntimeError, SettlementDenied, BudgetDenied, sqlite3.Error):
        _report({"mode": "READ_ONLY", "qualification": "NOT_QUALIFIED", "status": "UNKNOWN"})
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
