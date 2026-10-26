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

## Durable outbox worker lifecycle

The durable registry remains the sole outbox truth. A thumbnail approval and its
hash-bound `video_delivery_outbox` eligibility row commit in the same SQLite
transaction. Only after that transaction completes does the authenticated async
inbound route signal the composition-owned `DeliveryOutboxWorker`. A wakeup is
an in-memory hint only: it is coalesced, carries no authority, and a missed
hint is repaired by the worker's startup drain.

`TelegramInboundLauncher.serve()` is the only opt-in lifecycle for this worker.
After its loopback app has bound, it starts exactly one worker. The worker drains
durable `PENDING` rows fairly across episodes (concurrency one): a revoked or
unavailable thumbnail is durably marked `BLOCKED` with its reason and an
ambiguous send is durably marked `UNKNOWN`; neither can hold the queue head or
cause another POST. Both statuses require explicit operator reconciliation.
The normal manual drain command remains backward-compatible for one explicit
eligible attempt, but it also never retries an `UNKNOWN` row.

Authenticated thumbnail outcomes are the primary wakeup. The callback is never
awaited on the inbound ACK path: synchronous errors and asynchronous failures
are isolated and coalesced. A configured positive, low-frequency reconciliation
period is the deterministic fallback for a lost host wakeup while the process is
alive; it is database-only queue inspection, not an LLM loop or busy poll. The
worker never constructs another provider, runs preflight, or opens external
configuration.

At graceful serve shutdown, the launcher first stops accepting new work and
uses the configured positive shutdown deadline. The concrete Telegram provider
uses cancellable asyncio socket I/O with an explicit transport timeout; it does
not use `to_thread` around `urllib`. If an active send crosses the deadline,
`drain_once` persists `UNKNOWN` before transport cancellation, retains the task
and delivery lock until that cancellation has actually unwound. `stop()` returns
`{"status":"stopping","ownership":"retained"}` at that deadline; it does not
claim the worker stopped and it keeps the worker task reference, so another
worker cannot start or acquire the duplicate-prevention lock. When the owned
task later finishes, its completion callback alone releases that reference;
only a completed stop can report
`{"status":"stopped","ownership":"released"}`. If this happens while
`TelegramInboundLauncher.serve()` exits normally, the launcher finishes aiohttp
site/runner cleanup and raises `RetainedOutboxWorkerError`, exposing both the
retained worker reference and outcome to the host. If the host body already
raised or was cancelled, that original exception/cancellation remains primary
and receives transparent stopping information instead of being suppressed.

This is a bounded host-stop API, not a claim that a remote server can be killed:
a POST already accepted remotely may still complete. The durable UNKNOWN barrier
is the stronger invariant—no worker, restart, or manual drain automatically
dispatches that intent again after the lock releases. Operators recover
`BLOCKED` by fixing the authenticated input and creating a reviewed successor
eligibility row; they recover `UNKNOWN` only by recorded, authenticated provider
or receipt evidence that resolves the original intent. Reconciliation never
fabricates a delivery, deletes the UNKNOWN barrier, or reposts the media.

On startup, the worker drains durable pending entries before waiting for host
wakeups. It reuses `DeliverySessionRunner`'s hash revalidation and Director /
DeliveryController path, so duplicate webhooks, coalesced wakeups, crashes
between provider receipt and outbox acknowledgement, and restart recovery do
not create a second POST. A failed or blocked row is isolated from successful
inbound acknowledgement and does not cause a retry loop. A final video approval
creates no outbox row and ends at `WAITING_FINAL_APPROVAL`; it never activates
publication.

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
