# Worker SDD — durable TEST lifecycle queue (ATM-P1-02)

## Scope

`src.hybrid.worker` owns only durable scheduling: TEST task enqueue/status, bounded polling, worker identity, per-episode lease renewal, fencing of queue transitions, and shutdown behavior. It does **not** create an executor, provider adapter, approval, or LIVE path. It runs one caller-supplied handler that must resume the existing `AutonomousTwoGateRunner`/compiled adapter.

## Durable records

`episode_tasks` is keyed by immutable `episode_id` and stores `root`, closed TEST `kind`, `status`, `next_action`, current queue fencing token, attempt count, and update time. Enqueue is idempotent only when root and kind exactly match. `PENDING` is eligible, `RUNNING` is owned, `WAITING` is a human gate/resource/quality pause, and `DONE` is terminal. A task outcome must provide both a runner `state` and durable `next_action`; only `HUMAN_*` stops automatic scheduling.

The worker lease is a separate SQLite store. It is acquired before queue claim, renewed at one-third TTL, and asserted before the queue terminal transition. Release deletes only the active generation; a monotonic epoch tombstone persists, so a later claim cannot reset to epoch 1. Queue claim/complete are bound to `(episode_id, worker_id, epoch)` and compare the active claim before every write. An expired active lease is replaced, not silently deleted.

SQLite bootstrap is serialized by the existing OS lock primitive in `locks.py`; no concurrent WAL/PRAGMA bootstrap protocol is used.

## Runner ownership contract

The queue lease is an outer fence for scheduling. The runner's owned APIs are the inner fence for `runner.json`, executor receipts, ledger, and provider submission. For every fixture or compiled invocation, `DurableEpisodeWorker` injects a runtime-only assertion that binds the exact durable `episode_id`, generated `run_id`, and current fencing token to `EpisodeLeaseStore.assert_active` immediately before each owned runner mutation. The CLI handler constructs `RunnerOwnership` from that same episode ID, worker ID, and token, then calls only `resume_owned` and `stage_delivery_owned` (or the compiled adapter's equivalent owned methods).

`AutonomousTwoGateRunner.record_human_gate_owned(kind, artifact, approved, reviewer, ownership)` takes that existing `RunnerOwnership`, asserts it before and after taking the runner OS lock, and records the exact hash-bound gate without manufacturing or renewing a lease. `AutonomousCompiledTestAdapter.record_human_gate_owned` delegates to it. A lease-wired runner rejects the plain `record_human_gate`; the compiled adapter also rejects its plain gate method whenever it was constructed with an ownership assertion. Standalone TEST runners retain the plain method for compatibility.

Lock order remains **episode lease → runner ownership → executor request ownership**. The worker does not take the runner lock itself and never calls the unowned resume/stage APIs. A renewal failure cancels the handler and preserves `RUNNING` plus the lease until expiry; it does not requeue or allow another mutation. Cancellation has the same ambiguity barrier. The worker never cancels/detaches the owned handler (including `asyncio.to_thread` work): it shields it and waits only a bounded deadline. If the handler is still ambiguous, it records `UNKNOWN_REQUIRES_RECONCILIATION` and retains ownership; unknown work has **no automatic retry**. The implementation does not claim it can detect an orphaned OS thread. Runner OS ownership plus the fence remains the duplicate-prevention proof.

## Worker loop

`run_once()` processes at most one task. `run --once` is the exact CLI form; `serve` sleeps for its positive bounded poll interval after each pass and can be interrupted by the CLI. It has no chat polling, LLM routing, or busy loop. Two operating-system processes may race to discover `PENDING`, but only one can acquire the episode lease and atomically claim it. Restart recovery runs only durable `PENDING` work; human gates remain `WAITING`. A successful explicit TEST `approve --kind thumbnail|video --test-approval` wakes only the corresponding persisted `HUMAN_*` task through `wake_gate`. The queue persists the expected current staged artifact hash, and wake verifies the durable runner gate receipt and exact artifact bytes before comparing that hash; caller flags or an arbitrary hash alone are not proof. Stale, missing, or mismatched gate evidence leaves the queue unchanged.

Status is a read-only projection: writers serialize every queue mutation under the queue writer lock, atomically replace a hash-bound JSON snapshot after durable SQLite commit, and publish `WRITER_IN_PROGRESS` before mutation. `worker-status` routes to `EpisodeTaskQueue.read_status` before queue construction and reads that JSON only: it must not create or open a queue database, parent directory, schema, WAL, SHM, bootstrap lock, or writer lock. It parses a closed duplicate-key/non-finite-free schema, returns no filesystem root, and labels a valid hash only as `LOCAL_STATUS_ONLY`: a local snapshot proves byte integrity, not approval, provider, billing, or publication authority. Missing, invalid, in-progress, future, or expired snapshots are stale/unavailable rather than current.

Compiled TEST queue dispatch is enabled only through the owned Director route. `enqueue` stores the configured workspace, then the worker acquires its exact lease, discovers one hash-bound compiled runner by durable episode identity, resumes the TEST image runner, and calls `DirectorAgent.render_compiled_video_async`. The Director renders only in its configured canonical `EpisodeFS` tree, persists immutable render intent and renderer completion evidence, and the worker revalidates that full contract through `_compiled_render_recovery_evidence` immediately before copying the canonical master into the TEST two-gate runner. Direct `resume`, `remediate`, and `heroes` remain blocked; `approve` only wakes the existing durable gate. There is no LIVE route and no publication route.

## Acceptance evidence

- Spawned-process test: two workers see one task, one handler runs, task reaches `WAITING/HUMAN_THUMBNAIL` once.
- Restart test: completed human wait is not resubmitted.
- Cancellation test: a live invocation leaves queue `RUNNING`; stale queue fencing is rejected.
- Compiled queue integration: one compiled TEST packet enqueues, reaches thumbnail then video gate through the Director canonical render receipt, and resumes after the first gate without a second image provider submission.
- Fresh-process CLI integration: one compiled packet goes through `start → enqueue → run → thumbnail approval → run → video approval → run`; it reaches `DONE`, produces one image fixture and one unchanged Director completion receipt, and an additional worker run is `IDLE`.
- Existing lifecycle tests cover lease expiry/cancellation and stale fencing before queue mutation. The compiled worker uses the same retained-ownership ambiguity barrier.
