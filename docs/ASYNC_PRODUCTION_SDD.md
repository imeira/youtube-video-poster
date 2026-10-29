# SDD — bounded asynchronous autonomous production

## Scope
This is the first asynchronous vertical slice of the existing `AutonomousTwoGateRunner`. It remains TEST-only and retains the existing `Executor`, `StandingBudgetLedger`, immutable job bindings, QA receipts, and two human delivery gates. It does not create a second runner, activate LIVE providers, change Director/bridge code, or alter canonical episode assets.

## Immutable inputs and scheduling contract
Inputs are the started runner packet hash, persisted job specifications/bindings, policy digest, executor receipts, and persisted scheduler limits. A job may declare `payload.depends_on` as a list of hash-bound request IDs. The complete dependency graph is topologically validated before `runner.json` is written; an unknown edge, duplicate/self edge, or cycle fails before `RUNNING`, executor intent, ledger reservation, or provider work. A job is ready only when every dependency has a completed receipt with immutable QA PASS. Independent jobs are eligible together.

The default provider and CPU limits are both `1`. An explicit bounded TEST caller may opt in to finite positive limits. Provider submission is bounded per endpoint; independent CPU-bound QA is bounded separately. Limits are persisted with the runner and a resumed process rejects changed limits. No unbounded task fan-out is permitted.

## State transitions and recovery
`READY/RUNNING -> MEDIA_PENDING` only after all active jobs have COMPLETE receipts whose output bytes match `result_sha256` and each has a write-once independent QA PASS. Every resumed QA PASS is re-opened against the exact executor receipt, artifact bytes/hash, executor QA field, request ID, reviewer, and non-generator identity before it can be skipped or advanced; any mismatch is durably `REJECTED` before render/delivery. A QA FAIL is persisted against the exact request/result hash, leaves the runner `REJECTED`, and blocks delivery/render gates. Dependent jobs never dispatch after a failed dependency.

Before a fresh ready job is dispatched, the existing executor records its intent and the shared standing ledger atomically reserves its exact cap. Submission then goes through that same executor. Existing COMPLETE receipts are revalidated and never resubmitted. A checkpointed remote ID follows executor recovery, not submit. Any cancellation or ambiguous executor failure marks the corresponding ledger claim `UNKNOWN`; the scheduler does not auto-resubmit it.

Resumption rebuilds only hash-bound unfinished jobs from `runner.json`, retains scheduler limits, and reuses completed QA-bound work. A crash-released per-runner OS lock is acquired asynchronously before the transaction and retained until all cancelled workers unwind; lock order is runner ownership, then executor request ownership. `resume_owned` additionally requires a matching `RunnerOwnership` and a runtime-bound lease assertion before scheduling and immediately before every intent prepare, ledger reserve/bind, executor/provider submit or recovery, executor QA write, and runner-state replacement; that assertion is fencing only, never LIVE authority. A runner constructed with an ownership assertion rejects public `resume` and `stage_delivery` unless a validated active ownership is present; standalone runners without an assertion retain their own OS lock. Executor per-request ownership remains the duplicate-prevention boundary.

## Negative acceptance tests
- Two independent TEST jobs overlap under explicit provider concurrency while the slow job is pending; QA for the fast completion runs before the slow completion, and both then reach the existing two delivery gates.
- Fresh-process resume preserves completed work and does not repeat a provider submit or QA.
- A QA failure blocks a dependent job and all rendering/delivery gates.
- Provider and CPU concurrency never exceed their persisted caps; defaults stay one.
- Every dispatched fresh job has an existing ledger reservation, and cancellation preserves `UNKNOWN` rather than retrying.

## Measurement
The deterministic fixture benchmark compares serial (`1`) and bounded parallel (`2`) elapsed time with local delayed TEST providers. It reports fixture latency only; it is not a production, provider, cost, or paid-performance claim.
