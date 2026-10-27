# SDD — Billing status snapshot producer/consumer

## Scope

`BillingStatusSnapshotPublisher` is an immutable JSON read model of a completed
writer-owned executor/ledger observation. It is not financial authority. The
producer receives the same executor receipt, ledger claim, and canonical
settlement receipt that the real admission/reconciliation path has already
captured while it owns its writer/transaction boundary; it never copies or
opens executor/ledger SQLite, WAL, or SHM files. It projects only the closed
fields required by `BillingStatusSnapshot` and atomically replaces a snapshot
whose digest covers the complete projection.

The consumer opens only the configured snapshot through the shared strict
byte-snapshot loader. It never opens SQLite and therefore cannot create,
checkpoint, copy, or mutate a DB/WAL/SHM. Invalid, stale, future, pending,
unknown, mismatched, or non-authoritative data stays fail-closed with
`actual_cost_usd: "UNKNOWN"` and `billing_readback: "READBACK_REQUIRED"`.
No snapshot can enable LIVE dispatch.

## Contract

The envelope has a closed schema and literal `kind`; its UTC timestamp is an
integer, not a bool/float, and must be within five minutes of the consumer
clock. JSON parsing rejects duplicate keys and recursively rejects non-finite
numbers, including `1e999`. The nested executor receipt, ledger claim, and
settlement receipt are each projected into closed schemas. A settlement digest
is copied only from an existing canonical settlement receipt and validated
against its exact canonical bytes; it is never invented from a status view.

The projection binds request ID, endpoint, authority mode, auth scope, and
provider request ID across all records. A non-settled or `UNKNOWN` claim is
published as `PENDING`; the consumer does not claim a zero cost.

## Integration boundary

The application composes a configured, SHA-256-pinned snapshot path into
`LiveExecutionService.from_app_config`. The service consumes it only during
preflight. Dispatch remains unconditionally blocked for LIVE and reports
`NONAUTHORITATIVE`/`READBACK_REQUIRED`; TEST observations are visible for
mechanical verification but are not LIVE authority.

## Tests

The end-to-end test uses the real TEST `Executor`, `StandingBudgetLedger`,
producer, application configuration, and `LiveExecutionService.preflight`.
Negative cases cover tampering, pending and UNKNOWN claims, endpoint/mode/
binding mismatch, stale/future timestamps, unexpected fields, malformed schema
or kind, booleans/floats for timestamps, and `1e999`. No hand-authored positive
snapshot is used as authority.
