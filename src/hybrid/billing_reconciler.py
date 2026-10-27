"""Fail-closed bridge from FAL billing readback to the standing budget ledger.

It derives every settlement binding from the executor's durable receipt; caller
JSON is never authority."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256

from src.hybrid.execution import Executor
from src.hybrid.fal_billing import (
    BillingReadbackRequest,
    BillingStatus,
    FalBillingConsumer,
)
from src.hybrid.standing_budget import BudgetDenied, StandingBudgetLedger


class SettlementDenied(ValueError):
    """A durable binding or authenticated billing requirement was not met."""


_MAX_FRESH_SECONDS = 300
_HASH = re.compile(r"[a-f0-9]{64}")


def _canonical_hash(document: dict) -> str:
    return sha256(
        json.dumps(
            document, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def _utc(value: object, label: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        raise SettlementDenied(f"{label} must be timezone-aware UTC")
    return value


def _unknown(ledger: StandingBudgetLedger, request_id: str, reason: str) -> dict:
    try:
        claim = ledger.claim(request_id)
        if claim["actual_usd"] is None:
            claim = ledger.mark_unknown(request_id)
    except BudgetDenied as exc:
        raise SettlementDenied("reservation cannot be preserved as UNKNOWN") from exc
    return {
        "request_id": request_id,
        "status": "UNKNOWN",
        "reason": reason,
        "claim": claim,
    }


class FalLedgerSettlementService:
    """Explicitly reconcile one persisted executor claim using bounded FAL GET readback."""

    def __init__(
        self,
        ledger: StandingBudgetLedger,
        executor: Executor,
        consumer: FalBillingConsumer,
        *,
        clock: Callable[[], datetime] | None = None,
        max_fresh_seconds: int = _MAX_FRESH_SECONDS,
    ) -> None:
        if (
            not isinstance(ledger, StandingBudgetLedger)
            or not isinstance(executor, Executor)
            or not isinstance(consumer, FalBillingConsumer)
        ):
            raise TypeError("standing ledger, executor, and FAL consumer are required")
        if (
            isinstance(max_fresh_seconds, bool)
            or not isinstance(max_fresh_seconds, int)
            or not 1 <= max_fresh_seconds <= 3600
        ):
            raise ValueError("invalid readback freshness bound")
        self.ledger = ledger
        self.executor = executor
        self.consumer = consumer
        self.clock = clock or (lambda: datetime.now(UTC))
        self.max_fresh_seconds = max_fresh_seconds

    def _binding(self, local_request_id: str, *, require_provider_id: bool = True) -> tuple[dict, dict]:
        if not isinstance(local_request_id, str) or not local_request_id:
            raise SettlementDenied("local request id required")
        receipt = self.executor.inspect(local_request_id)
        if not isinstance(receipt, dict) or receipt.get("receipt_schema") != 2:
            raise SettlementDenied("executor receipt schema-v2 binding required")
        binding = receipt.get("settlement_binding")
        required = {
            "schema", "local_request_id", "provider_request_id", "endpoint_id",
            "auth_scope", "authority_mode",
        }
        if not isinstance(binding, dict) or set(binding) != required:
            raise SettlementDenied("executor settlement binding is missing or malformed")
        if binding.get("schema") != 2 or binding.get("local_request_id") != local_request_id:
            raise SettlementDenied("executor settlement binding local request mismatch")
        if not all(isinstance(binding.get(key), str) and binding[key] and len(binding[key]) <= 512
                   for key in ("provider_request_id", "endpoint_id", "auth_scope", "authority_mode")):
            raise SettlementDenied("executor settlement binding is malformed")
        if binding["provider_request_id"] == "UNASSIGNED" and require_provider_id:
            raise SettlementDenied("remote provider request id is not durable")
        if receipt.get("request_id") != local_request_id or receipt.get("endpoint") != binding["endpoint_id"]:
            raise SettlementDenied("executor receipt does not bind settlement endpoint")
        if receipt.get("mode") == "TEST":
            expected_scope = "TEST_LOCAL_ONLY"
        elif receipt.get("mode") == "LIVE":
            authorization = receipt.get("authorization")
            if not isinstance(authorization, dict) or authorization.get("request_id") != local_request_id:
                raise SettlementDenied("LIVE executor receipt authorization binding is missing")
            expected_scope = f"LIVE_AUTHORIZATION_REQUEST:{local_request_id}"
        else:
            raise SettlementDenied("executor receipt mode is invalid")
        if binding["auth_scope"] != expected_scope or binding["authority_mode"] != receipt["mode"]:
            raise SettlementDenied("executor settlement auth scope mismatch")
        return receipt, binding

    def reserve_from_executor(self, local_request_id: str, *, episode_id: str,
                              quoted_cap_usd: object, experiment: bool = False) -> dict:
        """Reserve only from the same durable receipt that later authorizes readback."""
        _, binding = self._binding(local_request_id, require_provider_id=False)
        try:
            return self.ledger.reserve(
                local_request_id, episode_id, binding["endpoint_id"], quoted_cap_usd,
                experiment=experiment, settlement_mode="FAL_READBACK_V2",
                auth_scope=binding["auth_scope"],
                provider_request_id=binding["provider_request_id"],
                authority_mode=binding["authority_mode"],
            )
        except BudgetDenied as exc:
            raise SettlementDenied("FAL reservation rejected") from exc

    async def reconcile(
        self, local_request_id: str, *, start: datetime, end: datetime
    ) -> dict:
        receipt, binding = self._binding(local_request_id)
        try:
            claim = self.ledger.claim(local_request_id)
        except BudgetDenied as exc:
            raise SettlementDenied(
                "matching standing-ledger reservation required"
            ) from exc
        if claim["provider"] != binding["endpoint_id"]:
            raise SettlementDenied(
                "standing-ledger provider does not bind executor endpoint"
            )
        if (claim["settlement_mode"], claim["auth_scope"],
                claim["provider_request_id"], claim["authority_mode"]) != (
                "FAL_READBACK_V2", binding["auth_scope"],
                binding["provider_request_id"], binding["authority_mode"]):
            raise SettlementDenied("standing-ledger settlement binding mismatch")
        transport_authority = getattr(self.consumer._transport, "authority", None)
        if transport_authority == "TEST_NOT_AUTHORITY":
            if self.ledger.binding.get("mode") != "TEST" or receipt["mode"] != "TEST":
                raise SettlementDenied("TEST fixture cannot settle a LIVE claim")
        elif transport_authority != "TRANSPORT_AUTHENTICATED_FAL_READBACK":
            raise SettlementDenied(
                "configured FAL billing transport authority required"
            )

        try:
            window_start, window_end, now = (
                _utc(start, "start"),
                _utc(end, "end"),
                _utc(self.clock(), "clock"),
            )
        except SettlementDenied:
            return _unknown(self.ledger, local_request_id, "invalid readback window")
        if (
            window_start >= window_end
            or window_end > now
            or (now - window_end).total_seconds() > self.max_fresh_seconds
        ):
            return _unknown(
                self.ledger, local_request_id, "readback window is not fresh"
            )

        readback = await self.consumer.readback(
            BillingReadbackRequest(
                binding["provider_request_id"],
                binding["endpoint_id"],
                window_start,
                window_end,
            )
        )
        if readback.status is not BillingStatus.BILLED:
            return _unknown(
                self.ledger,
                local_request_id,
                "billing readback is not one complete billed event",
            )
        if (
            readback.authority != transport_authority
            or readback.request_id != binding["provider_request_id"]
            or readback.endpoint_id != binding["endpoint_id"]
        ):
            return _unknown(
                self.ledger, local_request_id, "billing readback binding mismatch"
            )
        actual = readback.actual_cost_usd
        if not isinstance(actual, Decimal):
            return _unknown(
                self.ledger, local_request_id, "billing money is not exact micro-USD"
            )
        try:
            micros = actual * Decimal(1_000_000)
            if (
                not actual.is_finite()
                or actual < 0
                or micros != micros.to_integral_value()
                or micros > 2**63 - 1
            ):
                raise ValueError
        except (InvalidOperation, ValueError):
            return _unknown(
                self.ledger, local_request_id, "billing money is not exact micro-USD"
            )

        settlement_receipt = {
            "schema": 2,
            "authority": transport_authority,
            "local_request_id": local_request_id,
            "provider_request_id": binding["provider_request_id"],
            "endpoint_id": binding["endpoint_id"],
            "auth_scope": binding["auth_scope"],
            "authority_mode": binding["authority_mode"],
            "currency": "USD",
            "cost_total_micro_usd": str(int(micros)),
            "evidence_hash": _canonical_hash({
                "authority": transport_authority, "provider_request_id": readback.request_id,
                "endpoint_id": readback.endpoint_id, "cost_total": str(actual),
            }),
            "provenance": "FAL_BILLING_READBACK",
        }
        receipt_body = json.dumps(settlement_receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
        receipt_hash = sha256(receipt_body.encode("utf-8")).hexdigest()
        try:
            settled = self.ledger._settle_verified_receipt(receipt_body)
        except BudgetDenied as exc:
            raise SettlementDenied("authenticated ledger settlement rejected") from exc
        return {
            "request_id": local_request_id,
            "status": "SETTLED",
            "receipt_sha256": receipt_hash,
            "claim": settled,
        }
