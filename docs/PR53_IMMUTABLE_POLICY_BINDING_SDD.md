# PR53 immutable policy binding SDD

## Authority and modes

The sole operation authority is the existing `jaco-derived-operation-authority/v1` for `JACO_GEN28_STYLE_REMEDIATION_001`.  It binds the fixed preflight, canonical financial source, standing consent message `147620`, and the USD 10.00 episode ceiling.  The factory never accepts a caller-selected root, ledger, policy, payload, or operation ID.  Hashes are canonical SHA-256 lowercase hexadecimal values.

Only the private injected TEST composition may use a TEST qualification and TEST fixture transport.  TEST never promotes to LIVE.  LIVE remains fail-closed: before an already-qualified production composition exists it cannot resolve credentials, create a reservation, perform a paid GET, or dispatch a provider request.

## Dispatch and effect gates

The composition owns one `Executor` and the already-bound `StandingBudgetLedger`.  On a fresh request it performs, in order: `prepare_intent`, canonical `ledger.reserve`, write of the durable final-admission marker, and `Executor.run(submit_prepared=True)`.  Immediately before `PREPARED` becomes `SUBMITTING`, Executor reopens the ledger claim and verifies request, endpoint, mode, scope, reservation, input hash, and marker.  The ledger enforces cumulative actual plus pending plus the next cap against USD 10.00.

The executor checkpoint binds the provider request ID in both durable stores before recovery.  A resumed process only uses its checkpoint/readback route: from the durable counter it makes zero additional fresh-input factory calls, reservations, or POSTs, and exactly one provider GET across concurrent fresh-process contenders.  OS/file locking and the immutable claim make simultaneous contenders a single-claim operation.  Unknown pre-checkpoint submissions are quarantined rather than retried.

## Validation boundary

Protected preflight numeric fields are type-closed before use: `call_limit` and `payload.safety_tolerance` use `type(value) is int`, and `maximum_usd` is a decimal string parsed only after its string type is checked.  Booleans are rejected.  Qualification and authority SHA-256 binding fields are strings matching lowercase hexadecimal `[0-9a-f]{64}`; length-only or truthy values are not accepted.  Scope, payload, financial source, ledger binding, code qualification, and total cap are all revalidated before effects.  A foreign ledger, forged document, changed hash, or TEST/LIVE mismatch fails before dispatch.

## PR53 adapter boundary

`bounded_operation_admission` is a compatibility facade only. It owns no
manifest schema, SQLite schema, operation identifier, request identifier,
authority root, policy hash, or recovery plan. The only protected typed input
is the composition-selected preflight consumed by `OperationAdmissionFactory`.
The factory obtains its sole authority from
`jaco-derived-operation-authority/v1`, verifies consent message `147620`, and
keeps the operation-policy and financial-policy hashes as separate bindings.

The facade delegates to `_execute_prepared` and `_execute_with_composition`.
Consequently a fresh qualified TEST execution performs the factory's
`prepare_intent -> StandingBudgetLedger.reserve -> final-admission-marker ->
Executor.run(submit_prepared=True)` sequence. Durable executor checkpoints and
the ledger claim remain the recovery authority; no adapter result field is an
authority or a substitute receipt.

The previous `bounded-operation-manifest/v1`, private read-only SQLite check,
and `OFFLINE_ADMISSION_ONLY` plan are retired. They did not perform the
operation and are not an acceptance path. LIVE still fails before credential
resolution, provider GET, reservation, or provider construction unless the
already-pinned production qualification is present.
