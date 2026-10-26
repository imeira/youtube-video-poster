"""Read-only CLI for the bounded FAL pricing and billing consumer."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from src.hybrid.fal_billing import (
    AiohttpFalBillingClient,
    BillingReadbackRequest,
    FalBillingConsumer,
    FalBillingTransport,
    QuoteRequest,
    validate_action_request,
)


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


async def main(
    argv: list[str] | None = None,
    *,
    client_factory: Callable[[str], FalBillingTransport] = AiohttpFalBillingClient,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight", "quote", "readback"))
    parser.add_argument("--endpoint-id", required=True)
    parser.add_argument("--readback", action="store_true", help="Explicitly permit one read-only FAL GET operation")
    parser.add_argument("--quantity-cap", type=_positive_decimal)
    parser.add_argument("--request-id")
    parser.add_argument("--start", type=_utc_argument)
    parser.add_argument("--end", type=_utc_argument)
    args = parser.parse_args(argv)

    try:
        validated = validate_action_request(
            args.action,
            endpoint_id=args.endpoint_id,
            quantity_cap=args.quantity_cap,
            request_id=args.request_id,
            start=args.start,
            end=args.end,
        )
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
        else:
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
        return 0
    except (TypeError, ValueError, RuntimeError):
        _report({"mode": "READ_ONLY", "qualification": "NOT_QUALIFIED", "status": "UNKNOWN"})
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
