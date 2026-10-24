# Telegram inbound approval adapter

`src.approval.telegram_inbound.TelegramInboundAdapter` is an offline-only
transport boundary for inbound approval messages. It makes no HTTP requests,
does not configure a webhook, does not poll (`getUpdates`), and does not send
Telegram messages.

## Trust model

Telegram's webhook mechanism supports a configured shared-secret header
(`X-Telegram-Bot-Api-Secret-Token`). It is **not** a Telegram cryptographic
signature over the request payload. An HTTP integration, which is deliberately
not included here, must obtain that header and pass the raw request body plus
header value to `ingest_webhook`.

The adapter compares the header to `webhook_secret` with `hmac.compare_digest`.
The secret must come from trusted external configuration and must be non-empty;
a missing or incorrect secret fails closed. The secret, token, header value,
and raw request body are never stored in SQLite.

The adapter additionally requires trusted external configuration for:

- `expected_chat_id`: the configured approval destination;
- `trusted_sender_ids`: the sender allowlist;
- `instance_id`: a stable trusted deployment/configuration binding; and
- `journal_path`: the local durable journal location.

All configured IDs are canonical positive decimal strings. A caller-provided
JSON mapping, an imported JSON file, or a prebuilt reply document cannot become
an approval: only a body accepted by `ingest_webhook` with the matching secret
is journaled, and `verified_reply` reads only that authenticated journal.

## Validation and replay behavior

Accepted updates are strict JSON objects containing only `update_id` and
`message`. The reply requires positive integer update/message/sender/chat/reply
IDs, a non-bot sender, an allowlisted sender ID, the configured chat ID, a
reply target, non-empty text of at most 4,096 characters, and a private/group/
supergroup chat. Edited messages and channel posts are rejected because they do
not match the permitted update schema. Payloads over 64 KiB and duplicate JSON
keys are rejected.

SQLite uses WAL, full synchronous writes, and immediate transactions. It stores
only validated reply fields plus canonical SHA-256 binding hashes and an HMAC
integrity value derived from the externally configured secret. The journal is
bound to the configured chat, sender allowlist, instance ID, schema version,
and secret. Opening it under different trusted configuration fails. Tampered
rows fail integrity verification and cannot yield a reply.

A byte/semantic duplicate update is idempotently accepted without another row.
A changed update ID, reused message ID, or second response to the same delivery
binding is rejected. The controller uses three distinct adapter operations:

- `claim_verified_reply` atomically consumes a fresh reply for a delivery;
- `recover_verified_reply` reads only a previously consumed, HMAC-verified row
  for the same delivery after a crash before local receipt persistence; and
- `revalidate_reply` is read-only and compares a persisted receipt document to
  the already-consumed, externally-rooted journal row.

The persisted `inbound_receipt` is therefore an identifier and audit copy, not
a trust root. On every retry the controller requires the same authority checks
(configured destination, trusted sender, reply target, exact internally-derived
command, artifact bytes, and delivery binding) for both a fresh receipt and an
existing one. Legacy local receipts without `inbound_receipt`, caller strings,
and forged dictionaries fail closed.

This separates recovery from replay: a crash after consume and before receipt
persistence recovers the one bound reply without requesting another approval;
a crash after receipt persistence and before the episode state transition
revalidates the persisted receipt without consuming or sending again. The
director performs state transition only after this common controller validation
returns.

## Activation status and limitations

No webhook route, TLS endpoint, Telegram Bot API configuration, `setWebhook`,
`getUpdates`, polling, bot token, or live service was added. Consequently this
adapter is not active in production by itself. A future integration must keep
its HTTP endpoint outside the approval controller, obtain secrets from its
trusted secret provider, pass unmodified body/header values to this adapter,
and preserve the existing fail-closed behavior if configuration is absent.
