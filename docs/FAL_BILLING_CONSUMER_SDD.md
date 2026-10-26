# SDD — FAL GET-only pricing and billing consumer

## Scope and non-goals

This slice adds a bounded, read-only consumer for FAL's documented Platform API endpoints:

- `GET https://api.fal.ai/v1/models/pricing`
- `GET https://api.fal.ai/v1/models/billing-events`

It creates no provider factory for inference and has no POST, queue submission, media download, budget reservation, ledger settlement, dispatch, RunPod, Telegram, or EP12 path. Construction is inert. The CLI makes no network request unless the caller explicitly supplies `--readback` and a configured `FAL_KEY`.

## Current qualification status

This consumer can retrieve and parse transport-authenticated FAL data when explicitly invoked with a configured credential. It does **not** establish LIVE qualification, financial authorization, a reservation, settlement, or independent approval. Local loopback fixtures are labeled `TEST_NOT_AUTHORITY`; self-authored JSON is not authority. Documentation and code must continue to describe LIVE as unqualified until an actual authenticated readback is combined with the separate authorization policy and recovery contracts.

## FAL documentation contract

Sources retrieved 2026-10-26:

1. [Billing Events](https://fal.ai/docs/platform-apis/v1/models/billing-events): requires a key with `BILLING` permissions; supports `source=billed`, request/endpoint filters, an explicit UTC `start`/exclusive `end` window, and cursor pagination. `cost_total` is the USD amount charged after discounts. `cost_estimate_nano_usd` is documented as its precision-preserving nano-USD representation; the consumer validates it as a non-negative integer but does not make it an authorization source or silently replace `cost_total`.
2. [Pricing](https://fal.ai/docs/platform-apis/v1/models/pricing): requires an API-capable key and returns dynamic `unit_price`, `unit`, and `currency` per endpoint. Pricing is a quote only, not a cost receipt.

The documented request ID example is opaque (`abc123`); the parser therefore accepts bounded non-empty opaque strings and does not impose UUID syntax.

## Boundary and transport

`AiohttpFalBillingClient` has no configurable base URL: its only remote origin is the literal HTTPS `api.fal.ai`, and it builds only the two documented paths. It uses one finite timeout, finite response-byte limit, finite page/limit bounds, `GET` only, no redirects, no proxy environment, and no cookies. It sends a credential only in the in-memory `Authorization: Key …` header. Returned errors are status-only/sanitized and neither credentials nor raw remote bodies are logged or returned.

A separately named `LoopbackFalBillingFixture` implements the same protocol for tests. It is constructor-guarded as `TEST`, reports `TEST_NOT_AUTHORITY`, and cannot assert LIVE qualification. It is not selected by command-line configuration.

## Parsed contracts

A quote requires exactly one matching endpoint row, a finite non-boolean non-negative decimal unit price, a non-empty unit, and `USD` currency. The caller separately supplies a finite non-boolean positive `quantity_cap`; the computed maximum is a quote bound, never actual billed cost.

Billing readback always supplies `source=billed`, exact request and endpoint filters, and a caller-provided timezone-aware UTC interval. It never relies on FAL's default 24-hour start. It follows every page until the response conclusively ends (`has_more=false`, `next_cursor=null`); repeated cursors, contradictory pagination, malformed pages, status/transport errors, and page-bound exhaustion return `UNKNOWN`.

Only exactly one event matching both request ID and endpoint can yield `BILLED`; zero matching events yields `PENDING`; two or more matching events is `UNKNOWN` rather than silently choosing one. A billed event requires a finite non-boolean non-negative `cost_total` and a non-negative integer `cost_estimate_nano_usd`; actual billed cost is the documented `cost_total` from the authenticated billed feed. Event timestamp must lie in the supplied half-open UTC window.

## CLI

`python -m src.cli.main fal-billing preflight …` is informational and never creates a client or network connection. A single pure action-specific validator is shared by the CLI and consumer: it preserves literal opaque request IDs, requires a valid endpoint, requires finite non-boolean positive quote quantity, and requires an ordered timezone-aware UTC readback window before any `FAL_KEY` lookup or client construction. `readback` then requires `--readback`, a configured `FAL_KEY`, explicit `--request-id`, `--endpoint-id`, `--start`, and `--end`; it has no activation or qualification outcome and emits only a sanitized JSON report. It has no quote-only fake price path.

## Verification plan

Tests run an end-to-end fresh-process CLI readback against the TEST-only loopback fixture through dependency injection, as well as real parsing/transport boundary tests: no network by default, exact paths/query, no redirects/cookies/proxy, bounded body/pages/status, explicit UTC windows, complete pagination, `PENDING` for absence, `UNKNOWN` for ambiguity or incomplete pagination, opaque request IDs, quote quantity caps, and malformed numeric rejection. The fixture’s result remains `TEST_NOT_AUTHORITY`.
