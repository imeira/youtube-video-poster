"""Bounded, GET-only consumer for FAL pricing and billed-event readback.

This module intentionally has no inference, reservation, settlement, or dispatch path.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Protocol

_API_ORIGIN = "https://api.fal.ai"
_PRICING_PATH = "/v1/models/pricing"
_BILLING_PATH = "/v1/models/billing-events"
_MAX_ID_LENGTH = 512


class FalConsumerError(RuntimeError):
    """A sanitized transport or provider-contract failure."""


class BillingStatus(str, Enum):
    BILLED = "BILLED"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class QuoteRequest:
    endpoint_id: str
    quantity_cap: Decimal


@dataclass(frozen=True)
class PriceQuote:
    endpoint_id: str
    unit: str
    currency: str
    unit_price_usd: Decimal
    quantity_cap: Decimal
    maximum_cost_usd: Decimal
    authority: str = "TRANSPORT_AUTHENTICATED_QUOTE_NOT_BILLING"


@dataclass(frozen=True)
class BillingReadbackRequest:
    request_id: str
    endpoint_id: str
    start: datetime
    end: datetime


@dataclass(frozen=True)
class BillingReadback:
    status: BillingStatus
    request_id: str
    endpoint_id: str
    actual_cost_usd: Decimal | None
    cost_estimate_nano_usd: int | None
    authority: str
    reason: str | None = None


class FalBillingTransport(Protocol):
    authority: str

    async def pricing(self, params: dict[str, str]) -> dict[str, Any]: ...

    async def billing_events(self, params: dict[str, str]) -> dict[str, Any]: ...


def _opaque_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_ID_LENGTH or any(ord(c) < 32 for c in value):
        raise ValueError(f"invalid {label}")
    return value


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{label} must be timezone-aware UTC")
    return value


def _utc_wire(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def validate_action_request(
    action: object,
    *,
    endpoint_id: object,
    quantity_cap: object = None,
    request_id: object = None,
    start: object = None,
    end: object = None,
) -> str | QuoteRequest | BillingReadbackRequest:
    """Validate literal CLI and consumer inputs before credentials or transport."""
    if action not in {"preflight", "quote", "readback"}:
        raise ValueError("invalid FAL billing action")
    endpoint = _opaque_id(endpoint_id, "endpoint_id")
    if action == "preflight":
        if any(value is not None for value in (quantity_cap, request_id, start, end)):
            raise ValueError("preflight accepts only endpoint_id")
        return endpoint
    if action == "quote":
        if any(value is not None for value in (request_id, start, end)):
            raise ValueError("quote accepts only endpoint_id and quantity_cap")
        quantity = _decimal(quantity_cap, "quantity_cap")
        if quantity is None or quantity == 0:
            raise ValueError("quantity_cap must be positive")
        return QuoteRequest(endpoint, quantity)
    if quantity_cap is not None:
        raise ValueError("readback accepts no quantity_cap")
    identifier = _opaque_id(request_id, "request_id")
    window_start, window_end = _utc(start, "start"), _utc(end, "end")
    if window_start >= window_end:
        raise ValueError("start must precede end")
    return BillingReadbackRequest(identifier, endpoint, window_start, window_end)


def _decimal(value: object, label: str, *, nullable: bool = False) -> Decimal | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise TypeError(f"invalid {label}")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"invalid {label}")
    try:
        result = Decimal(str(value)) if isinstance(value, float) else Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"invalid {label}")
    return result


def _nonempty_string(value: object, label: str, *, nullable: bool = False) -> str | None:
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value or len(value) > _MAX_ID_LENGTH:
        raise ValueError(f"invalid {label}")
    return value


def _page(page: object, items_key: str) -> tuple[list[dict[str, Any]], str | None, bool]:
    if not isinstance(page, dict) or set(page) - {items_key, "next_cursor", "has_more"}:
        raise ValueError("invalid FAL response page")
    items = page.get(items_key)
    cursor = page.get("next_cursor")
    more = page.get("has_more")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise ValueError("invalid FAL response items")
    if cursor is not None and (not isinstance(cursor, str) or not cursor or len(cursor) > _MAX_ID_LENGTH):
        raise ValueError("invalid FAL response cursor")
    if type(more) is not bool or (more != (cursor is not None)):
        raise ValueError("incomplete FAL pagination")
    return items, cursor, more


class AiohttpFalBillingClient:
    """Concrete FAL-only GET transport; construction opens no connection."""

    authority = "TRANSPORT_AUTHENTICATED_FAL_READBACK"

    def __init__(self, api_key: str, *, timeout_s: float = 10.0, max_body_bytes: int = 1_048_576) -> None:
        if not isinstance(api_key, str) or not api_key or len(api_key) > 4096:
            raise ValueError("FAL key is not configured")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0.1 <= timeout_s <= 30:
            raise ValueError("invalid timeout")
        if isinstance(max_body_bytes, bool) or not isinstance(max_body_bytes, int) or not 1024 <= max_body_bytes <= 1_048_576:
            raise ValueError("invalid body limit")
        self._api_key = api_key
        self._timeout_s = float(timeout_s)
        self._max_body_bytes = max_body_bytes

    async def pricing(self, params: dict[str, str]) -> dict[str, Any]:
        return await self._get(_PRICING_PATH, params)

    async def billing_events(self, params: dict[str, str]) -> dict[str, Any]:
        return await self._get(_BILLING_PATH, params)

    async def _get(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        import aiohttp

        if path not in {_PRICING_PATH, _BILLING_PATH}:
            raise FalConsumerError("blocked FAL path")
        timeout = aiohttp.ClientTimeout(total=self._timeout_s)
        headers = {"Authorization": f"Key {self._api_key}"}
        try:
            async with aiohttp.ClientSession(
                timeout=timeout,
                trust_env=False,
                cookie_jar=aiohttp.DummyCookieJar(),
                headers=headers,
            ) as session, session.get(
                _API_ORIGIN + path,
                params=params,
                allow_redirects=False,
                proxy=None,
            ) as response:
                if response.status != 200:
                    raise FalConsumerError(f"FAL returned HTTP {response.status}")
                payload = await response.content.read(self._max_body_bytes + 1)
        except FalConsumerError:
            raise
        except Exception as exc:
            raise FalConsumerError("FAL GET failed") from exc
        if len(payload) > self._max_body_bytes:
            raise FalConsumerError("FAL response exceeded body limit")
        try:
            decoded = json.loads(payload.decode("utf-8"), parse_float=Decimal)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FalConsumerError("FAL returned invalid JSON") from exc
        if not isinstance(decoded, dict):
            raise FalConsumerError("FAL returned invalid JSON object")
        return decoded


class LoopbackFalBillingFixture:
    """TEST-only page fixture. It can never be evidence of LIVE authority."""

    authority = "TEST_NOT_AUTHORITY"

    def __init__(self, *, pricing_pages: list[dict[str, Any]], billing_pages: list[dict[str, Any]], mode: str = "TEST") -> None:
        if mode != "TEST":
            raise ValueError("loopback FAL fixture is TEST-only")
        self._pricing_pages = list(pricing_pages)
        self._billing_pages = list(billing_pages)
        self.pricing_queries: list[dict[str, str]] = []
        self.billing_queries: list[dict[str, str]] = []

    async def pricing(self, params: dict[str, str]) -> dict[str, Any]:
        self.pricing_queries.append(dict(params))
        if len(self.pricing_queries) > len(self._pricing_pages):
            raise FalConsumerError("unexpected TEST pricing page")
        return self._pricing_pages[len(self.pricing_queries) - 1]

    async def billing_events(self, params: dict[str, str]) -> dict[str, Any]:
        self.billing_queries.append(dict(params))
        if len(self.billing_queries) > len(self._billing_pages):
            raise FalConsumerError("unexpected TEST billing page")
        return self._billing_pages[len(self.billing_queries) - 1]


class FalBillingConsumer:
    """Strict quote/readback parser using a caller-supplied bounded transport."""

    def __init__(self, transport: FalBillingTransport, *, page_limit: int = 20) -> None:
        if isinstance(page_limit, bool) or not isinstance(page_limit, int) or not 1 <= page_limit <= 50:
            raise ValueError("invalid page limit")
        self._transport = transport
        self._page_limit = page_limit

    async def quote(self, request: QuoteRequest) -> PriceQuote:
        validated = validate_action_request(
            "quote", endpoint_id=request.endpoint_id, quantity_cap=request.quantity_cap,
        )
        assert isinstance(validated, QuoteRequest)
        endpoint_id, quantity_cap = validated.endpoint_id, validated.quantity_cap
        page = await self._transport.pricing({"endpoint_id": endpoint_id})
        prices, cursor, more = _page(page, "prices")
        if more or cursor is not None:
            raise FalConsumerError("incomplete FAL pricing pagination")
        matches = [price for price in prices if price.get("endpoint_id") == endpoint_id]
        if len(matches) != 1:
            raise FalConsumerError("FAL pricing did not return one endpoint quote")
        price = matches[0]
        if set(price) != {"endpoint_id", "unit_price", "unit", "currency"}:
            raise FalConsumerError("invalid FAL price record")
        unit_price = _decimal(price["unit_price"], "unit_price")
        unit = _nonempty_string(price["unit"], "unit")
        currency = _nonempty_string(price["currency"], "currency")
        if currency != "USD":
            raise FalConsumerError("FAL quote currency is not USD")
        return PriceQuote(endpoint_id, unit or "", currency, unit_price or Decimal(0), quantity_cap, (unit_price or Decimal(0)) * quantity_cap, self._transport.authority)

    async def readback(self, request: BillingReadbackRequest) -> BillingReadback:
        validated = validate_action_request(
            "readback", endpoint_id=request.endpoint_id, request_id=request.request_id,
            start=request.start, end=request.end,
        )
        assert isinstance(validated, BillingReadbackRequest)
        request_id, endpoint_id = validated.request_id, validated.endpoint_id
        start, end = validated.start, validated.end
        params = {
            "source": "billed", "request_id": request_id, "endpoint_id": endpoint_id,
            "start": _utc_wire(start), "end": _utc_wire(end), "limit": "50",
        }
        matches: list[tuple[Decimal, int]] = []
        seen_cursors: set[str] = set()
        try:
            for _ in range(self._page_limit):
                page = await self._transport.billing_events(params)
                events, cursor, more = _page(page, "billing_events")
                for event in events:
                    parsed = self._parse_event(event, start, end)
                    if parsed[0] == request_id and parsed[1] == endpoint_id:
                        matches.append((parsed[2], parsed[3]))
                if not more:
                    if not matches:
                        return BillingReadback(BillingStatus.PENDING, request_id, endpoint_id, None, None, self._transport.authority)
                    if len(matches) != 1:
                        return self._unknown(request_id, endpoint_id, "multiple matching billed events")
                    cost_total, nano = matches[0]
                    return BillingReadback(BillingStatus.BILLED, request_id, endpoint_id, cost_total, nano, self._transport.authority)
                if cursor is None or cursor in seen_cursors:
                    return self._unknown(request_id, endpoint_id, "incomplete billing pagination")
                seen_cursors.add(cursor)
                params = {**params, "cursor": cursor}
            return self._unknown(request_id, endpoint_id, "billing page limit reached")
        except (FalConsumerError, ValueError, KeyError, TypeError):
            return self._unknown(request_id, endpoint_id, "billing readback unavailable")

    def _unknown(self, request_id: str, endpoint_id: str, reason: str) -> BillingReadback:
        return BillingReadback(BillingStatus.UNKNOWN, request_id, endpoint_id, None, None, self._transport.authority, reason)

    @staticmethod
    def _parse_event(event: dict[str, Any], start: datetime, end: datetime) -> tuple[str, str, Decimal, int]:
        required = {
            "request_id", "endpoint_id", "timestamp", "quantity", "output_units", "unit", "unit_price",
            "percent_discount", "cost_subtotal", "cost_discount", "cost_total", "cost_estimate_nano_usd",
        }
        if set(event) != required:
            raise ValueError("invalid FAL billing event")
        request_id = _opaque_id(event["request_id"], "event request_id")
        endpoint_id = _opaque_id(event["endpoint_id"], "event endpoint_id")
        stamp = event["timestamp"]
        if not isinstance(stamp, str):
            raise TypeError("invalid event timestamp")
        try:
            timestamp = datetime.fromisoformat(stamp)
        except ValueError as exc:
            raise ValueError("invalid event timestamp") from exc
        if timestamp.tzinfo is None or timestamp < start or timestamp >= end:
            raise ValueError("event outside readback window")
        _decimal(event["quantity"], "quantity", nullable=True)
        _decimal(event["output_units"], "output_units", nullable=True)
        _nonempty_string(event["unit"], "unit", nullable=True)
        _decimal(event["unit_price"], "unit_price", nullable=True)
        _decimal(event["percent_discount"], "percent_discount", nullable=True)
        _decimal(event["cost_subtotal"], "cost_subtotal")
        _decimal(event["cost_discount"], "cost_discount")
        cost_total = _decimal(event["cost_total"], "cost_total")
        nano = event["cost_estimate_nano_usd"]
        if isinstance(nano, bool) or not isinstance(nano, int) or nano < 0:
            raise ValueError("invalid cost_estimate_nano_usd")
        return request_id, endpoint_id, cost_total or Decimal(0), nano
