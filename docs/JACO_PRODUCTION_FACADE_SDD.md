# Jacó production facade — SDD

## Scope

`JacoProductionFacade` is the one Jacó **TEST-local** operator-status publisher for this acceptance slice. It accepts only an existing compiled pipeline's exact durable TEST executor receipts and reopens them; it never owns a provider, queue, runner, lease, retry, human gate, render, policy admission, or publication path. Any LIVE policy/Director path remains outside this facade's TEST contract.

The existing `DurableEpisodeWorker` stays the sole TEST queue/lease runner. It is not duplicated for LIVE execution. Its cancellation/expired-lease path remains `UNKNOWN_REQUIRES_RECONCILIATION` with no automatic retry.

## Inputs and closed output

The facade accepts only a compiled pipeline whose baseline job IDs resolve to `COMPLETE`, `TEST` receipts in the existing executor. Each receipt must bind its exact request ID and result hash. `actual_cost` and `charged` may be unknown and remain `null`; the facade never invents money, timing, tokens, or quota. It produces four hashed local source documents and uses `CanonicalStatusProducer` to publish exactly one canonical snapshot at the established Jacó request root:

- `manifest/v1` derives candidate hashes from executor receipts;
- `policy-qa/v1` is `PASS` only when the existing write-once executor QA receipts are all true; otherwise `PENDING`;
- `ledger/v1` derives actual/pending amounts only when durable receipt fields measured them, otherwise preserves unknown;
- `provider-eligibility/v1` is `TEST_LOCAL_ONLY` even when receipts contain the expected policy-admission reviewer.

The resulting projection is always `LOCAL_STATUS_ONLY`. It is not provider billing readback, real-provider qualification, financial authorization, QA authority, human approval, or publication authority.

The facade performs no metric write and creates no parallel ledger. The owning
`AutonomousTwoGateRunner` projects receipt usage into its existing
`OperationalMetricsLedger`, keyed idempotently by the durable provider operation
or request ID. Local completion, QA, and executor `actual_cost` remain
`UNKNOWN` billing; only a settled canonical standing-budget claim supplies USD.
The projection preserves measured input/cached/output/reasoning tokens, quota,
stage latency, queue wait, and pending reserved exposure independently. The
rolling/cache benchmark remains explicitly TEST fake latency and makes no
financial claim.

## Transitions and recovery

1. The durable TEST executor checkpoints a local fixture ID and writes its completion receipt before the runner moves to `VISUAL_QA`.
2. The facade reopens and projects that exact TEST receipt; absent QA stays `BLOCKED` rather than fabricating `PASS`.
3. `DirectorAgent.record_compiled_visual_qa()` can reproject only after the existing hash-bound QA write. All-PASS remains local `PENDING_READBACK_REQUIRED`, never approval.
4. A crash after the remote-ID checkpoint is recovered by a fresh executor through `recover`, not another POST. Unknown/cancelled worker ownership remains quarantined.

## Negative acceptance

- foreign/missing scope, hash mismatch, source modification, missing fields, stale source, malformed receipt, non-TEST receipt, duplicate request identity, unknown cost normalization, and non-PASS QA fail closed;
- receipt-derived status cannot be made PASS by caller flags;
- sealed TEST transport proves transaction mechanics only and does not qualify a provider or authorize real production;
- local worker status reads JSON only and never open SQLite.

## Status metrics acceptance (2026-11-03)

- Durable receipt plus canonical claim scope is required; request/provider scope
  mismatches fail before a metric write.
- A `COMPLETE` receipt or `qa=true` cannot authenticate billing. Unsettled and
  unknown claims retain pending exposure and report USD as unknown.
- Replaying the same durable provider operation is idempotent; divergent payload
  evidence remains an append-only conflict, not a second financial total.
- This is TEST/local telemetry integration only. LIVE provider billing readback,
  external price quotes, license evidence, production provider adapters, TTS,
  GPU, and Telegram configuration remain external qualification work.
