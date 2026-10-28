# SDD — Billing status snapshot producer/consumer (v2 required design)

## Decision and scope

The current `BillingStatusSnapshotPublisher.publish(job, executor_receipt,
ledger_claim, settlement_receipt)` is rejected. It is caller-owned: it accepts
untrusted-in-process dictionaries, has no shared writer lock, does not publish a
writer marker before a durable mutation, has no durable generation pin, and is
not called by a real admission, checkpoint, or settlement path. Its direct-test
callers are not an integration.

Do **not** add another helper around this API. Replace it with one
`BillingSnapshotCoordinator`, created only from deployment configuration, and
make it the writer boundary used by the existing `Executor`,
`StandingBudgetLedger`, and `FalLedgerSettlementService` paths listed below.
The consumer remains JSON-only and is non-authoritative: it always reports
`billing_readback: READBACK_REQUIRED`, `actual_cost_usd: UNKNOWN`, and it never
enables LIVE dispatch.

This is deliberately a coordinated multi-store protocol rather than a claim of
a cross-SQLite transaction. A crash between the executor and ledger commits
leaves `WRITER_IN_PROGRESS`; recovery must never manufacture a coherent
`CURRENT` snapshot from that state.

## Trusted construction and path contract

`BillingSnapshotCoordinator.from_app_config(app_config, *, executor, ledger)`
accepts only a configured absolute `billing_snapshot_root` and a configured
relative snapshot name from a closed application configuration object. The
root is deployment-owned, not accepted from a job, CLI argument, receipt, or
caller dictionary. Construction must:

1. reject non-absolute roots and every symlink/reparse point in the root and
   every existing ancestor *before* resolving, creating, opening, or writing;
2. require an existing regular root directory (no implicit `mkdir`);
3. construct the fixed files below that root (`billing-status.json`,
   `.billing-status.lock`, and private temporary names), reject lexical escape,
   and re-run reparse/ancestor containment checks before every open, replace,
   and directory fsync;
4. derive the lock path and output path from that same checked root; and
5. bind the coordinator to exactly the supplied `Executor.database` and
   `StandingBudgetLedger.path`. Their parent directories must likewise be
   checked roots configured by composition, never request data.

The old public `BillingStatusSnapshotPublisher` and its `publish(dict-like)`
method are removed. No public method accepts receipt/claim/settlement dicts.
Only coordinator code performs durable readback from its bound stores while it
owns the protocol lock.

## Shared writer protocol

All mutations that can affect a projected request must route through the same
crash-released interprocess lock, in this order:

```
.billing-status.lock
  -> executor SQLite transaction(s)
  -> standing-ledger SQLite transaction(s)
  -> snapshot temp file / replace
```

No code may acquire an individual executor request lock or a SQLite write
transaction and then wait for `.billing-status.lock`; that ordering would allow
deadlock. Existing per-request and worker slots remain inside the coordinator
lock. The coordinator is the only component allowed to perform the
executor/ledger transition pairs below.

For a request transition, the coordinator allocates monotonic `generation =
last_generation + 1` while holding the protocol lock. Before the first SQLite
mutation it atomically writes and fsyncs this closed envelope:

```json
{"schema":2,"kind":"FAL_BILLING_STATUS_SNAPSHOT","status":"WRITER_IN_PROGRESS","generation":7,"request_id":"...","generated_at":123}
```

The envelope itself is SHA-256 canonicalized, contains no financial projection,
and is atomically published through the same output path. A reader maps it to
`STALE/WRITER_IN_PROGRESS` with unknown cost. A previous `CURRENT` snapshot
must never remain visible after the marker is durable.

Every participating durable row has a non-null `billing_snapshot_generation`
set in the same SQLite transaction as its state mutation. The executor and
ledger schema migrations add this field plus a small local generation state.
The coordinator reads the exact executor receipt, ledger claim, and (where
applicable) canonical settlement receipt from those stores after their commits,
while the shared lock prevents another writer. It publishes `CURRENT` only when
all selected records bind the same request/endpoint/mode/auth-scope/provider
ID and exactly the allocated generation. Missing, mismatched, partial,
non-finite, UNKNOWN, or unpinned records retain the marker and return a
non-success state; they never yield a data-bearing snapshot.

Final publication writes a fully closed JSON document to an exclusive temporary
file in the validated root, fsyncs that file, atomically replaces
`billing-status.json`, then fsyncs the parent directory where supported. It
then reopens the published bytes through the strict byte-snapshot loader and
verifies the closed schema, canonical hash, generation and bindings before
returning success. On POSIX, parent-directory `fsync` is required. Windows has
no portable directory-fsync equivalent in Python; after `os.replace` the
implementation must perform the strict readback validation, document that it
cannot claim power-loss directory-entry durability, and leave crash recovery
fail-closed via the marker/version test.

At coordinator construction/restart, an existing `WRITER_IN_PROGRESS`, invalid
output, missing generation, or different executor/ledger generation is
reported as `STALE` with `actual_cost_usd: UNKNOWN`; it is not repaired into
`CURRENT`. A later valid mutation may publish a new generation only after its
own marker.

## Real integration points

The coordinator is not optional on FAL-bound records.

1. **Admission/reserve:** the existing real `FalLedgerSettlementService`
   reservation path becomes `coordinator.prepare_and_reserve(...)`. It writes
   the marker before `Executor.prepare_intent`, performs the matching
   `StandingBudgetLedger.reserve` under the shared lock, pins both rows to one
   generation, and publishes the resulting `PENDING`/`UNAUTHENTICATED`
   snapshot. The public service no longer asks a caller to pass records to a
   publisher.
2. **Provider-ID checkpoint:** replace `Executor.run(...,
   settlement_checkpoint=...)`'s callback-after-commit arrangement. The
   executor delegates its one-time `UNASSIGNED -> provider_id` checkpoint to
   the coordinator, which marks first, commits the executor binding, commits
   `StandingBudgetLedger.bind_provider_request_id`, pins both, and either
   publishes the final coherent snapshot or leaves `STALE` after interruption.
   A provider submit/recovery must not proceed from a checkpoint that failed
   this durable pair.
3. **Reconciliation:** `FalLedgerSettlementService.reconcile` keeps the
   bounded authenticated FAL GET outside SQLite writes, then calls a
   coordinator settlement method. That method marks first, settles the ledger
   canonical receipt, pins the relevant executor/ledger records, and publishes
   the writer-owned final projection. A pending/missing/ambiguous readback
   invokes the same marker discipline before `mark_unknown`; the resulting
   snapshot has no real-money field.

The existing CLI's `_trusted_settlement_service` is the required composition
point for the real reserve/checkpoint/reconcile path. It must build the
coordinator from application configuration and inject it into the settlement
service/executor pair. If a deployment has no valid trusted snapshot root, the
billing bridge is absent and the existing fail-closed preflight result remains
`UNAUTHENTICATED`/`READBACK_REQUIRED`; it must not construct a publisher from a
caller path.

### TEST CLI composition slice

The FAL CLI may compose a coordinator only through a deployment-owned
application configuration object supplied outside the episode/request path. The
closed TEST-only `billing_snapshot` mapping is exactly
`{"root": absolute-path-string, "name": "billing-status.json"}`.
The pure configuration validator checks schema, exact types and lexical path
absoluteness without filesystem I/O. Filesystem existence, directory/reparse
checks and durable owner matching belong to the subsequent configured factory;
they are not claims made by the pure validator.
Missing, extra, non-TEST or lexically invalid configuration fails before
credential access and transport construction. Durable root/owner mismatches are
rejected by the configured factory after transport construction but before any
provider POST; constructing a transport does not authorize or dispatch a request. `--state-root` remains a legacy durable-state
locator only: it can reopen the executor/ledger pair, but it never supplies the
coordinator owner root and no request/packet field can supply one.

The configured factory binds one real `Executor`, `StandingBudgetLedger`, and
`BillingSnapshotCoordinator`, then injects that exact trio into
`FalLedgerSettlementService`. Its admitted execution path is
`service.run_admitted(job, provider, ...)`; no CLI caller passes a checkpoint
callback. Reconciliation uses that same configured service.

Before a `reconcile` command constructs a FAL transport, the real CLI obtains
application configuration through the existing application configuration
channel and resolves the settlement composition. A declared snapshot section
must be explicitly `mode: TEST` and contain only the closed snapshot mapping;
a `LIVE`, partial, or malformed declaration fails before client construction.
`--state-root` remains only the durable-store locator and cannot select or pin
the owner root. Deployments with no snapshot declaration retain the legacy
non-coordinated readback path for unpinned rows, while generation-pinned rows
fail closed when the compatible configured coordinator is absent or foreign.

The executable proof is a fresh CLI process using an explicit TEST loopback
transport/provider DI: application configuration → service → real coordinator
→ snapshot → JSON-only preflight. It proves no LIVE authority, no real provider,
root mismatch/missing configuration/foreign capability perform zero POSTs, and
a restart follows the durable checkpoint without a second POST.

## Consumer contract

`BillingStatusSnapshot.inspect` reads only the configured validated JSON file;
it never imports or calls `sqlite3`, opens executor/ledger database files,
WAL/SHM sidecars, creates directories, creates locks, or performs a checkpoint.
It accepts only a fully closed `CURRENT` v2 envelope with a matching configured
byte SHA-256 and a fresh non-boolean integer UTC timestamp. All other outcomes
(missing, marker, invalid, future/expired, pin mismatch, malformed settlement,
or reparse path) are `STALE`/`UNKNOWN`. Even a valid authenticated settlement
projection remains non-authoritative to LIVE: preflight says
`READBACK_REQUIRED` and `dispatch` stays denied.

## Required TDD slices and proof

Implement as vertical RED → GREEN slices, preserving the test output in the
Hermes scratch directory:

1. **Consumer isolation RED:** install a `sqlite3.connect` trap in a fresh CLI
   process; create executor DB, WAL and SHM first; inspect a valid snapshot;
   prove zero connects and fingerprint source DB/WAL/SHM/snapshot/parent entries
   before and after. GREEN only when the consumer is JSON-only.
2. **Real admission-to-preflight RED:** use a real TEST executor/ledger and the
   configured coordinator; call the real reserve/admission path with no receipt
   dictionary; read back the snapshot and run application preflight. Assert
   auto-publication, `READBACK_REQUIRED`, unknown money, and denied LIVE
   dispatch.
3. **Concurrent/restart RED:** fresh spawned processes contend on the same
   root; inject interruption after marker and after only one store commit.
   Assert exactly one coherent `CURRENT` publication or `STALE`, never partial
   data, and `UNKNOWN` remains non-zero risk in the ledger.
4. **Path and durability RED:** symlink/reparse roots, ancestors, output and
   temporary destinations fail before any write. Assert file fsync is called,
   POSIX directory fsync is called, and the Windows limitation is explicitly
   tested/documented rather than claimed away.
5. **No caller-owned API RED:** direct import/use of the old publisher fails
   because the class no longer exists; search production paths to prove the
   configured coordinator is reached from the real FAL service rather than a
   test helper.

Run only focused tests for these slices, `uv tool run ruff check` on touched
Python files, and `git diff --check`. Do not run the full suite, commit, push,
change `uv.lock`, touch production configuration/provider spend paths, or send
Telegram.

## Implementation status — TEST-only local scope

Implemented locally: `BillingSnapshotCoordinator` owns marker → executor/ledger
mutation → coherent CURRENT publication for admission, remote-ID checkpoint, and
canonical FAL settlement. `FalLedgerSettlementService.admit` requires injected
trusted coordinator composition; its coordinator-backed reconcile path publishes
both SETTLED and UNKNOWN transitions through the same marker protocol. Executor
recognizes that coordinator callback and does not perform a pre-callback local
checkpoint, avoiding the former callback-after-commit bypass.

The tested integration is TEST-only with a loopback FAL fixture. It does not
qualify LIVE, authenticate remote billing, provide distributed atomicity, or
claim Windows directory-entry power-loss durability. Legacy direct executor and
ledger APIs remain for their existing non-coordinator callers; they are not
evidence that every historical writer has migrated.
