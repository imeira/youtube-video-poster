# Jacó production facade — SDD

## Scope

`JacoProductionFacade` is the one Jacó operator-status publisher. `DirectorAgent.dispatch_current_policy_baselines()` remains the only LIVE entrypoint: it resolves the immutable permanent policy and delegates to the existing compiled dispatcher and `Executor`. The facade runs only after that durable executor path returns and reopens its receipts; it never owns a provider, queue, runner, lease, retry, human gate, render, or publication path.

The existing `DurableEpisodeWorker` stays the sole TEST queue/lease runner. It is not duplicated for LIVE execution. Its cancellation/expired-lease path remains `UNKNOWN_REQUIRES_RECONCILIATION` with no automatic retry.

## Inputs and closed output

The facade accepts only a compiled pipeline whose baseline job IDs resolve to `COMPLETE`, `LIVE` receipts in the existing executor. Each receipt must bind its exact request ID, result hash, charged cost, and actual cost. It produces four hashed local source documents and uses `CanonicalStatusProducer` to publish exactly one canonical snapshot at the established Jacó request root:

- `manifest/v1` derives candidate hashes from executor receipts;
- `policy-qa/v1` is `PASS` only when the existing write-once executor QA receipts are all true; otherwise `PENDING`;
- `ledger/v1` derives actual/pending amounts from durable receipt fields;
- `provider-eligibility/v1` is `ELIGIBLE` only when each receipt records the existing policy-admission reviewer.

The resulting projection is always `LOCAL_STATUS_ONLY`. It is not provider billing readback, real-provider qualification, financial authorization, QA authority, human approval, or publication authority.

`StatusMetricsLedger.record_executor_receipt()` is the only facade metric write. It derives an idempotent metric identity and actual cost from the receipt; unknown timing, tokens, and quota remain unmeasured. The rolling/cache benchmark remains explicitly TEST fake latency and makes no financial claim.

## Transitions and recovery

1. Policy-qualified Director dispatch calls the existing executor.
2. The executor durably reserves, submits once, checkpoints a remote ID, and writes the completion receipt before the Director moves to `VISUAL_QA`.
3. The facade projects that exact receipt; absent QA stays `BLOCKED` rather than fabricating `PASS`.
4. `DirectorAgent.record_compiled_visual_qa()` reprojects only after the existing hash-bound QA write. All-PASS becomes local `PENDING_READBACK_REQUIRED`, never approval.
5. A crash after the remote-ID checkpoint is recovered by a fresh executor through `recover`, not another POST. Unknown/cancelled worker ownership remains quarantined.

## Negative acceptance

- foreign/missing scope, hash mismatch, source modification, missing fields, stale source, malformed receipt, non-LIVE receipt, duplicate request identity, missing license/policy admission, unknown cost, and non-PASS QA fail closed;
- receipt-derived status cannot be made PASS by caller flags;
- sealed TEST transport proves transaction mechanics only and does not qualify a provider or authorize real production;
- local worker status reads JSON only and never open SQLite.
