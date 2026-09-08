# Production throughput controller

`studio throughput` is a durable single-writer controller for fixed, hash-bound
Python adapters. It consumes persisted review evidence and advances only through
explicit local contracts. It has no shell command field, provider import,
Telegram operation, upload, scheduling, or publication capability.

## Safety contract

- Deployment configuration, adapter paths, adapter SHA-256 values, reviewer
  origin, episode root, request paths, and TEST/live mode are fixed before a run.
- The episode-root lock is the sole writer authority. EP8 requires
  `.ep8_episode_writer.lock`; a controller mutex cannot replace it.
- Mutable state, ledger, authorization, lease, request bytes, source bytes,
  contract bytes, candidates, and prior receipts are re-read before each action.
- Command intent is persisted before execution. A paid/non-idempotent ambiguous
  submission is never retried. An opaque request ID is reconciled without
  resubmission.
- Every adapter must implement the start-claim/start-gate protocol. The
  controller records the exact child PID and process identity in the episode
  lease before opening the gate. A stale controller lease cannot be reclaimed
  while that child remains live, and an ambiguous `STARTING` record is fenced
  for explicit reconciliation.
- Request documents use a closed schema and finite track allowlist. Request,
  verdict, authorization, source, and contract paths must remain under the
  episode root.
- Independent evidence is a closed, hash-bound, temporally ordered record. A
  reviewer label, stdout, process exit, TEST fixture, or parent summary is not
  production PASS evidence.
- Final thumbnail, final video, upload, scheduling, public visibility, and
  publication remain explicit human gates.
- The controller enforces the fresh episode ledger's lower effective cap from
  `hard_limit` and `approved_maximum`; it never increases or rewrites a cap.

## EP8 discovery result

Read-only discovery:

```powershell
$env:PYTHONPATH=''
& C:\Users\meira\hermes-studio-venv\Scripts\python.exe -m src.cli.main throughput inspect `
  --episode C:\HermesStudio\episodes\EP8_PROMISE_SON_20260901
```

The current EP8 R027 boundary is **not runnable** and no deployment is generated:

- the persisted FAIL file is explicitly a parent extract, not the full reviewer
  verdict;
- `r027_verdict_waiter_v2.py` detects the file and then enters an unconditional
  `while True` without consuming PASS or FAIL;
- no R027 PASS handler exists for crop, internal QA, independent QA, promotion,
  and successor handoff;
- `r027_fail_recovery_acquire_lease_v1.py` directly reacquires the episode lock
  and writes mutable state, has no `--input`/`--receipt` protocol or dry-run, and
  stops after acquiring/relabeling a lease instead of materializing the promised
  remediation preflight;
- no reviewed live handler/deployment contract exists.

These are activation blockers, not permission to wrap or invoke the legacy
scripts. Discovery binds their exact bytes and reports the blockers without
writing `state.json`, `costs.json`, the lease, or any episode artifact.

## Real-shape TEST clone

The legacy R027 request/handoff can be exercised end-to-end only in a new output
directory whose basename starts with `TEST_`:

```powershell
$env:PYTHONPATH=''
& C:\Users\meira\hermes-studio-venv\Scripts\python.exe -m src.cli.main throughput clone-test `
  --episode C:\HermesStudio\episodes\EP8_PROMISE_SON_20260901 `
  --output-dir $env:TEMP\TEST_EP8_R027_CONTROLLER `
  --test-mode
```

`clone-test` refuses an existing or ambiguously named destination, forbids a live
config, never executes a legacy episode script, copies only the bound source and
state/cost snapshots, and marks the clone contract, evidence, deployment, state,
and ledger as TEST. The controller additionally requires a `TEST_*` root plus
`test_only: true` in both state and ledger before making any TEST-mode write. It
normalizes `FAIL_PREFLIGHT` to the ordinary controller's
`FAIL` path and proves remediation handoff plus a fresh review wait with the
repository's fixed TEST-only subprocess adapter. It does not create R027 media or
change production state, costs, lease, authorization, or writer processes.

## Commands

```text
studio throughput inspect|discover --episode EPISODE
studio throughput validate-config --episode EPISODE --config DEPLOYMENT
studio throughput run-once --episode EPISODE --config DEPLOYMENT [--test-mode]
studio throughput run --episode EPISODE --config DEPLOYMENT [--test-mode]
studio throughput status|report --episode EPISODE --config DEPLOYMENT [--test-mode]
studio throughput clone-test --episode EPISODE --output-dir TEST_* --test-mode
```

## Live limits

Implemented and exercised in TEST mode does not mean deployed or activated. Live
activation remains blocked until both real R027 PASS and FAIL handlers implement
the fixed `throughput-input/1` → `throughput-command/1` contract under the
controller-owned episode lease (including the mandatory child start gate), a
full reviewer-origin record is available, the
handler/deployment hashes receive independent read-only review, and a bounded
no-cost canary can run without another episode writer. No production worker,
provider call, spend, media generation, promotion, render, upload, or publication
is started by this change.
