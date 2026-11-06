# Episode Policy Status Projection SDD

## Scope

`EpisodePolicyStatusProjection` is the sole policy-aware augmentation of the existing `EpisodeTaskQueue.read_status` snapshot. It is a deployment-owned, read-only JSON projection. It never constructs a queue, opens SQLite, creates locks/directories/WAL/SHM files, calls an executor/provider, wakes a gate, promotes an asset, sends Telegram, renders media, or publishes.

## Immutable inputs

A closed deployment configuration pins absolute regular JSON paths and SHA-256 digests for: policy, worker queue snapshot, runner receipt, QA receipt, narrative receipt, asset-integration receipt, and optional billing snapshot. Each source is read through a reparse-safe strict byte snapshot with duplicate keys and non-finite values rejected. Every required source binds exactly one `episode_id`; foreign, altered, malformed, stale, future, replayed, or cross-request source is rejected. The billing input is an existing snapshot only, never a ledger database.

## Output and precedence

The atomically published closed projection contains generation, episode ID, source digests, an evidence binding digest, `live_dispatch: false`, `pending_human_approvals`, and exactly one status from:

- `TEST_ELIGIBLE`
- `WAITING_HUMAN_GATE`
- `BLOCKED_POLICY`
- `BLOCKED_QUALIFICATION`
- `BLOCKED_BILLING_READBACK`
- `UNKNOWN_REQUIRES_RECONCILIATION`
- `STALE`

`RUNNING`, unknown, stale, conflicting, or malformed worker evidence is `UNKNOWN_REQUIRES_RECONCILIATION` or `STALE`, never an automatic retry and never a zero-cost result. A queue `WAITING` maps to `WAITING_HUMAN_GATE`; it cannot wake a gate. A LIVE policy without externally trusted qualification remains `BLOCKED_QUALIFICATION`; local billing evidence cannot change `live_dispatch`.

The Jacó canary projection preserves the settled amount `0.048` and remaining `9.952` only when supplied by the pinned existing ledger projection snapshot. Its visual QA verdict remains `NOT_PROMOTED`; narrator is not final approved; identity and license are unavailable; thumbnail, video, and publication remain explicitly absent. CI success is not a human gate and never produces an all-green status.

## Publication and recovery

The producer publishes `WRITER_IN_PROGRESS` before replacement and then `CURRENT` after all inputs have been revalidated. The final document has a monotonically increasing generation and SHA-256 evidence binding over exact source bytes. Any invalid/missing/stale input produces a fresh `STALE` projection rather than returning a cached `CURRENT` document. `pending_human_approvals` is always a literal boolean and is false only if the closed evidence explicitly proves no pending human gates.

## Director/CLI integration

The existing `worker-status` read path remains the queue consumer. When deployment configuration supplies a projection declaration, it augments that JSON-only read with this projection; absent configuration preserves the existing worker-status result. Neither path modifies `DurableEpisodeWorker` ownership or lease behavior.

## Negative acceptance

Tests cover altered bytes after hash pin, other-episode source, queue/runner disagreement, local billing unable to enable LIVE, stale/malformed/replayed source, unknown worker state, source fingerprints before/after a fresh CLI process, and a `sqlite3.connect` trap proving the CLI reader never touches a source database.
