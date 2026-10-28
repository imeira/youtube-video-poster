# Hermes single-owner inbound bridge SDD

## Status and activation

This is a standalone plugin template and Studio ingress adapter. Both are **disabled by default**: importing them has no listener, connection, Telegram operation, configuration read, or registration side effect. `register()` returns false without explicit `HermesBridgeConfig`, local transport, and approval-reply recognizer. Activation, secret provisioning, plugin installation, and gateway restart are intentionally out of scope.

Hermes remains the only Telegram update owner. The plugin uses the documented `ctx.register_platform_handler("telegram", factory)` API and registers one PTB handler in group `-1`; it neither calls `getUpdates`, `setWebhook`, nor sends Telegram messages. A deployment supplies an explicit `recognize(raw_update) -> bool` binding for currently delivered approval prompt replies. The handler returns normally for an unrecognized reply, so Hermes core group `0` receives ordinary chat. For a recognized approval reply it performs exactly one local bridge attempt, then raises PTB `ApplicationHandlerStop` before group `0`, including when the local result is a replay/rejection or local completion is ambiguous. This prevents a sensitive approval text from reaching core LLM dispatch; it does not retry or send an acknowledgement.

## Protocol

A normalized, strict subset of an Hermes-admitted text-reply update is serialized once as raw JSON bytes. The plugin accepts only configured chat/sender identities, non-bot text replies, and positive update/message/reply IDs. It posts one signed local envelope to Studio. No LLM reconstructs or interprets an approval message.

Envelope is canonical JSON with the exact fields `instance`, `nonce`, `raw_update_b64`, `timestamp`, and `version`. HMAC-SHA256 input is `hermes-studio-approval-bridge/v1\\0 || envelope_bytes`; headers bind version, instance, and `v1=<hex>` signature. Studio rejects unknown fields, duplicate keys, invalid Base64, bodies over 64 KiB, stale timestamps (five minutes), wrong instance/version/signature, and nonmatching durable nonce replays. Exact replay is passed to the existing idempotent Studio journal; altered same-nonce bytes are rejected.

The bridge HMAC is explicit deployment configuration and distinct from the Studio webhook/journal secret. The ingress constructor checks this with a constant-time comparison before creating or opening its replay database; equal keys are rejected before bridge-side effects. Neither secret is logged or persisted. Studio uses the existing `TelegramDirectorService` admission path after bridge verification, retaining its existing strict sender/chat/reply checks, HMAC journal, delivery/artifact binding, atomic claim/recovery, thumbnail approval transition, and durable video outbox.

The replay nonce is claimed durably before the existing service is invoked. That early claim is deliberately not terminal consumption: an exact signed retry with the same nonce and envelope is admitted again to the existing idempotent journal, so a process crash between nonce claim and journal completion does not silently lose an approval. A different envelope using the claimed nonce is rejected.

## Boundaries

The adapter does not manufacture a webhook secret, construct a listener, configure Telegram, create a second poller, use a relay, or call providers. A missing Studio service secret or missing bridge key fails before ingress accepts anything. A deployment must bind any HTTP transport to loopback and enforce peer validation at that host boundary; this isolated adapter intentionally has no HTTP server.

## Test evidence

`tests/unit/test_hermes_inbound_bridge.py` exercises real fake-PTB handler registration and the local adapter into existing Studio approval entrypoints. Its multi-group dispatcher proves a recognized approval stops before core group `0` on success, replay-style failure, and ambiguous local failure, while an unrelated reply reaches core. It also verifies a thumbnail approval and video outbox, tamper/wrong sender/chat/reply/stale rejection, duplicate/replay behavior, secret separation before replay/journal mutation, and disabled-default registration. No Telegram network or credentials are used.
