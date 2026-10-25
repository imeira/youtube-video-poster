# Production Telegram Gate Composition SDD

## Scope

`src.approval.production_telegram_gate` is a deliberately narrow composition
root for the existing two human-review delivery gates. It does **not** activate
production: it does not start an HTTP server, bind a socket, configure a
Telegram webhook, poll Telegram, send during construction/preflight, invoke a
runner, reserve budget, or publish.

The caller supplies a trusted `ProductionTelegramGateConfig` from deployment
configuration and named external secret sources. The module never reads a
`.env` file. Required external inputs are: bot token, webhook secret, canonical
chat ID, canonical allowed sender IDs, instance ID, absolute journal path,
absolute delivery-registry path, loopback host, and port.

## Composed path

The factory creates only these existing concrete components:

```text
TelegramNotificationProvider
  -> DeliveryController
  -> DirectorAgent.deliver_for_approval(...)
  -> qa/delivery/{thumbnail|video}.json
  -> TelegramDirectorService.register_delivery(...)
  -> create_telegram_inbound_app(...)
```

The Director callback remains the only outbound gate owner. It uses its existing
state machine and `DeliveryController`: thumbnail is delivered before video;
video requires a hash-bound persisted thumbnail approval. The composition then
registers exactly the resulting Director delivery receipt with
`TelegramDirectorService`, so inbound authenticated replies resolve only a
known `chat_id/message_id/episode/artifact SHA-256` mapping.

The existing inbound service owns HMAC header admission, trusted sender/chat
validation, durable reply journal, lock, idempotent replay, recovery after a
receipt/state interruption, and reply-to-delivery hash binding. Outbound
delivery invokes the inbound registry callback *inside* its existing
crash-released receipt lock immediately after atomically persisting the
delivery receipt. The callback runs off the event loop. If a process crashes
in the narrow persisted-receipt/before-registry interval, startup reconciles
only `episodes_dir/<configured episode>/qa/delivery/{thumbnail,video}.json`;
inbound can make the same fixed-slot recovery before returning
`unknown_delivery`. It never sends again, scans arbitrary paths, or trusts a
tampered receipt. Delivery state continues to own write-ahead intent, lock,
journal receipt, `UNKNOWN` on an ambiguous send, and no resend after an
ambiguous outcome.

## Factory and launcher contract

`create_production_telegram_gate` validates all trusted paths with
`reject_reparse_path` before it constructs the provider or aiohttp app. Journal
and registry paths must be absolute and cannot cross a symlink/reparse point.
The configured Director chat and sender allowlist must match these transport
bindings. Host is restricted to `127.0.0.1` or `::1` and port is bounded.

The returned `TelegramInboundLauncher` contains an aiohttp app plus declared
host/port. Its explicit async `serve()` context manager is the sole opt-in
binding operation; it only accepts the already-validated loopback host and
always stops the aiohttp site and cleans up its runner. Factory/preflight do
not call it. It never configures a webhook, tunnel, persistent service, or
external configuration.

`ProductionTelegramGateComposition.deliver_for_approval` calls the existing
Director method and registers the resulting receipt. It does not implement a
parallel delivery protocol or invent a runner callback. If a future autonomous
runner needs delivery, it must call this explicit adapter only after its own
reviewed bridge opens a current Director `WAITING_*_APPROVAL` state; the current
TEST runner is not promoted to LIVE by this composition.

## CLI preflight

`studio telegram-inbound-preflight` is offline only. It requires a deployment
YAML containing the Director approval destination/senders, absolute state paths,
and `--token-env`/`--secret-env` names. It constructs Director + composition and
prints only:

```json
{"network":"disabled","publication":"disabled","status":"ready"}
```

It does not print supplied secret values. Missing variables fail with their
variable names only. It is not a launcher and never contacts Telegram.

## Explicit non-goals / blocked work

- No environment-file discovery, credentials provisioning, server startup,
  `setWebhook`, long polling, Telegram network request, or real provider call
  occurs in factory/preflight tests.
- No financial authorization, caps, billing reconciliation, paid provider call,
  publication transition, or EP12/Jacó work is included.
- No changes are made to `autonomous.py`, `autonomous_cli.py`, standing-budget
  policy, or budget authorization. TEST remains TEST and is not a LIVE bypass.

## Verification

Focused tests prove construction stays offline, named external secrets are not
logged, trusted reparse paths reject before provider/app construction, trusted
chat/sender/secret/token bindings fail closed, Director delivery receipts are
registered under the receipt lock for inbound use, startup/readback recovery
handles a crash after receipt persistence but before registration without a
second POST, tampered recovery fails closed, mocked aiohttp lifecycle cleans
up, and existing inbound/delivery tests cover wrong sender/chat/secret/reply/
hash, registry tamper, concurrent duplicate, timeout-to-UNKNOWN/no-resend,
replay idempotence, and sequential thumbnail/video.
