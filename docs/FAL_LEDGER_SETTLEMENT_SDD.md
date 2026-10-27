# SDD — FAL billing readback to standing-ledger settlement

## Boundary

This slice settles only a durable `Executor` receipt whose schema-v2 `settlement_binding` is written before submit with `provider_request_id=UNASSIGNED`. The checkpoint transaction replaces that placeholder with the provider-returned remote ID before recovery/polling can occur; a subsequent different ID fails closed. The binding contains the local request ID, remote request ID, exact endpoint and submit-time auth scope. The reconciler reads it directly from the executor database; it never accepts a caller-provided receipt or binding dictionary.

## Submit authority boundary

`Executor.run(..., submit_prepared=True)` treats the flag as a request to check admission, never as admission itself. Before its first provider POST it re-reads the standing ledger fixed by trusted runner/executor composition—not a payload, receipt, dictionary, callback, or caller-selected ledger—and requires one `FAL_READBACK_V2` claim in `RESERVED` state. That claim must bind the immutable executor intent's local request ID, endpoint, scope, mode, `UNASSIGNED` remote ID, and exact reserved value. Missing, wrong-request, wrong-endpoint/scope/mode/value, `UNKNOWN`/settled, or otherwise invalid claims leave the executor `PREPARED` and cause zero POSTs. The `TEST_LOCAL_ONLY` scope remains explicit TEST compatibility, but does not waive this FAL-bound reservation check. After POST, `SUBMITTING` is durable before network I/O; the exact remote ID and executor `SUBMITTED` checkpoint are atomic, and recovery never requires a new POST or replacement reservation.

The reconciler supplies the persisted provider request and endpoint to the existing bounded FAL GET-only consumer. A settlement requires its complete-page `BILLED` result, exact returned request/endpoint, a fresh explicit UTC window, the configured transport authority, and exact USD conversion of provider `cost_total` to the ledger's six micro-USD units. `cost_estimate_nano_usd` is optional typed metadata, never an actual-cost authority or equality check. `PENDING`, `UNKNOWN`, malformed/old/inconsistent evidence, missing legacy binding, or a ledger/receipt mismatch leaves the reservation `UNKNOWN`; it never becomes zero or settled.

## Modes and durability

`LoopbackFalBillingFixture` is admitted only for a `TEST` ledger and remains `TEST_NOT_AUTHORITY`. A configured FAL transport is the only non-TEST path; this code does not claim LIVE qualification or enable dispatch. FAL claims are always `FAL_READBACK_V2`; the prior legacy/unbound fallback cannot settle through this reconciler. The standing ledger independently parses the closed canonical schema-2 receipt, recomputes its SHA-256, and checks local/remote request, endpoint, scope, mode, currency, and actual against the durable claim in the same `BEGIN IMMEDIATE` / WAL transaction. The receipt identity excludes caller readback windows and observation time; those are audit metadata only. Replaying the same receipt is idempotent; a different receipt for a settled claim fails closed. A billed overrun is recorded as reality and preserves the ledger's durable freeze.

Python's underscore-private ledger settlement method is a trusted-composition API, not an impossible defense against hostile arbitrary code running in the same Python process (which can mutate objects, imports, and local files). The externally reachable authority boundary is the configured reconciler/CLI plus reparse-safe local state root; no amount, hash, body, or provenance argument is an authority path.

The CLI rejects symlinks/Windows reparse points for the state root, every ancestor, both databases, and any existing WAL/SHM sidecars before it opens SQLite or reads bindings. That local-root constraint prevents accidental redirected filesystem reads, but is documented as a trusted local boundary rather than a sandbox against code already running with the user's privileges.

## Consumer

`FalLedgerSettlementService` is an explicit async readback consumer. It takes only a local request ID plus a caller-supplied UTC window; endpoint, provider request, ledger claim, and auth scope are derived and checked from durable state. Its optional CLI action is read-only toward the provider but may mutate the local ledger only after all checks pass. No provider POST, credential fixture, or package-configured transport exists.

## Verification

Focused tests cover a real TEST `Executor.run` receipt plus test ledger and loopback consumer: bound billed settlement, repeated receipt idempotency, legacy/mismatched bindings, fixture-to-LIVE rejection, pending/ambiguous evidence preservation, stale windows, exact micro-USD/nano consistency, and overrun freeze. Existing runner completion no longer treats a local provider receipt as billing settlement.