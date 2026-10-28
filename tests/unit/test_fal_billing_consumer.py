"""Contract tests for the bounded GET-only FAL billing consumer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from src.hybrid.fal_billing import (
    AiohttpFalBillingClient,
    BillingReadbackRequest,
    BillingStatus,
    FalBillingConsumer,
    LoopbackFalBillingFixture,
    QuoteRequest,
)

ENDPOINT = "fal-ai/flux/dev"
REQUEST_ID = "opaque-req_abc123"
START = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)
END = START + timedelta(hours=2)


def event(*, request_id: str = REQUEST_ID, endpoint_id: str = ENDPOINT) -> dict:
    return {
        "request_id": request_id,
        "endpoint_id": endpoint_id,
        "timestamp": "2026-10-25T01:00:00Z",
        "quantity": 2,
        "output_units": 2,
        "unit": "image",
        "unit_price": 0.025,
        "percent_discount": 10,
        "cost_subtotal": 0.05,
        "cost_discount": 0.005,
        "cost_total": 0.045,
        "cost_estimate_nano_usd": 45000000,
    }


@pytest.mark.asyncio
async def test_test_loopback_returns_dynamic_quote_and_bound_billed_readback():
    fixture = LoopbackFalBillingFixture(
        pricing_pages=[{
            "prices": [{"endpoint_id": ENDPOINT, "unit_price": 0.025, "unit": "image", "currency": "USD"}],
            "next_cursor": None,
            "has_more": False,
        }],
        billing_pages=[{
            "billing_events": [event()], "next_cursor": None, "has_more": False,
        }],
    )
    consumer = FalBillingConsumer(fixture)

    quote = await consumer.quote(QuoteRequest(endpoint_id=ENDPOINT, quantity_cap=Decimal(2)))
    receipt = await consumer.readback(BillingReadbackRequest(
        request_id=REQUEST_ID, endpoint_id=ENDPOINT, start=START, end=END,
    ))

    assert fixture.authority == "TEST_NOT_AUTHORITY"
    assert quote.unit_price_usd == Decimal("0.025")
    assert quote.quantity_cap == Decimal(2)
    assert quote.maximum_cost_usd == Decimal("0.050")
    assert receipt.status is BillingStatus.BILLED
    assert receipt.actual_cost_usd == Decimal("0.045")
    assert receipt.request_id == REQUEST_ID
    assert receipt.endpoint_id == ENDPOINT


@pytest.mark.asyncio
async def test_readback_uses_explicit_utc_filters_and_follows_complete_pages():
    fixture = LoopbackFalBillingFixture(
        pricing_pages=[],
        billing_pages=[
            {"billing_events": [], "next_cursor": "cursor-1", "has_more": True},
            {"billing_events": [event()], "next_cursor": None, "has_more": False},
        ],
    )
    result = await FalBillingConsumer(fixture).readback(BillingReadbackRequest(
        request_id=REQUEST_ID, endpoint_id=ENDPOINT, start=START, end=END,
    ))

    assert result.status is BillingStatus.BILLED
    assert fixture.billing_queries == [
        {"source": "billed", "request_id": REQUEST_ID, "endpoint_id": ENDPOINT,
         "start": "2026-10-25T00:00:00Z", "end": "2026-10-25T02:00:00Z", "limit": "50"},
        {"source": "billed", "request_id": REQUEST_ID, "endpoint_id": ENDPOINT,
         "start": "2026-10-25T00:00:00Z", "end": "2026-10-25T02:00:00Z", "limit": "50", "cursor": "cursor-1"},
    ]


@pytest.mark.asyncio
async def test_missing_event_is_pending_and_ambiguous_or_incomplete_readback_is_unknown():
    pending = await FalBillingConsumer(LoopbackFalBillingFixture(
        pricing_pages=[], billing_pages=[{"billing_events": [], "next_cursor": None, "has_more": False}],
    )).readback(BillingReadbackRequest(REQUEST_ID, ENDPOINT, START, END))
    ambiguous = await FalBillingConsumer(LoopbackFalBillingFixture(
        pricing_pages=[], billing_pages=[{"billing_events": [event(), event()], "next_cursor": None, "has_more": False}],
    )).readback(BillingReadbackRequest(REQUEST_ID, ENDPOINT, START, END))
    incomplete = await FalBillingConsumer(LoopbackFalBillingFixture(
        pricing_pages=[], billing_pages=[{"billing_events": [], "next_cursor": "same", "has_more": True},
                                           {"billing_events": [], "next_cursor": "same", "has_more": True}],
    )).readback(BillingReadbackRequest(REQUEST_ID, ENDPOINT, START, END))

    assert pending.status is BillingStatus.PENDING
    assert ambiguous.status is BillingStatus.UNKNOWN
    assert incomplete.status is BillingStatus.UNKNOWN


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("cost_total", True), ("cost_total", float("nan")), ("cost_estimate_nano_usd", True),
])
async def test_invalid_billed_numbers_are_unknown_not_zero(field, value):
    bad = event()
    bad[field] = value
    result = await FalBillingConsumer(LoopbackFalBillingFixture(
        pricing_pages=[], billing_pages=[{"billing_events": [bad], "next_cursor": None, "has_more": False}],
    )).readback(BillingReadbackRequest(REQUEST_ID, ENDPOINT, START, END))
    assert result.status is BillingStatus.UNKNOWN
    assert result.actual_cost_usd is None


@pytest.mark.asyncio
async def test_quote_requires_one_dynamic_usd_price_and_separate_positive_quantity_cap():
    fixture = LoopbackFalBillingFixture(
        pricing_pages=[{"prices": [
            {"endpoint_id": ENDPOINT, "unit_price": 0.10, "unit": "gpu-second", "currency": "USD"},
            {"endpoint_id": "other", "unit_price": 0.01, "unit": "image", "currency": "USD"},
        ], "next_cursor": None, "has_more": False}], billing_pages=[],
    )
    quote = await FalBillingConsumer(fixture).quote(QuoteRequest(ENDPOINT, Decimal("3.5")))
    assert quote.unit == "gpu-second"
    assert quote.maximum_cost_usd == Decimal("0.350")
    with pytest.raises(TypeError, match="quantity_cap"):
        await FalBillingConsumer(fixture).quote(QuoteRequest(ENDPOINT, True))


def test_loopback_fixture_cannot_be_live_or_credential_authority():
    with pytest.raises(ValueError, match="TEST-only"):
        LoopbackFalBillingFixture(pricing_pages=[], billing_pages=[], mode="LIVE")


@pytest.mark.asyncio
async def test_concrete_client_is_fixed_get_only_and_does_not_leak_key(monkeypatch):
    import aiohttp

    captured = {}

    class Content:
        async def read(self, limit):
            captured["read_limit"] = limit
            return b'{"prices":[]}'

    class Response:
        status = 200
        content = Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

    class Session:
        def __init__(self, **kwargs):
            captured["session"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def get(self, url, **kwargs):
            captured["url"] = url
            captured["get"] = kwargs
            return Response()

    monkeypatch.setattr(aiohttp, "ClientSession", Session)
    client = AiohttpFalBillingClient("secret-never-log", timeout_s=2, max_body_bytes=1024)
    response = await client.pricing({"endpoint_id": ENDPOINT})

    assert response == {"prices": []}
    assert captured["url"] == "https://api.fal.ai/v1/models/pricing"
    assert captured["get"]["allow_redirects"] is False
    assert captured["get"]["proxy"] is None
    assert captured["session"]["trust_env"] is False
    assert captured["session"]["cookie_jar"].__class__.__name__ == "DummyCookieJar"
    assert captured["read_limit"] == 1025
    assert "secret-never-log" not in repr(response)
