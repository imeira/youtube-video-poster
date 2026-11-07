# Young Identity Model Sheet composition SDD

`JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001` is the requested three-view
preflight operation in `young_identity_model_sheet_production.py`.  The
similarly named `young_identity_model_sheet_composition.py` is a separately
versioned, TEST-only fixture composition; it is not a LIVE authority or a
consumed predecessor.  `_002` was unprovisioned planning only and is not a
migration, rejection, or execution record.  The protected route uses the
existing `Executor` and `StandingBudgetLedger` only after protected binding and
an independent pre-generation review.

## Production status: unprovisioned

The LIVE code is implemented, but no authority, protected qualification,
preflight, or deployment root for operation `_001` is provisioned in this
repository. CI and tests use only the private TEST fixture; passing them does
not grant authority, establish price as verified actual cost, reserve a LIVE
budget, or authorize a provider POST. An independently issued, hash-bound LIVE
qualification is required before the public `execute()` route can pass its
first gate.

## Exact admission field map

| Durable record | Required fields |
| --- | --- |
| operation | `id`, `kind`, `endpoint`, `call_limit`, `maximum_usd`, `payload`, `payload_sha256` |
| authority | closed v2 schema with operation, explicit initial lineage (`first`, empty predecessor), non-reuse flag, policy/preflight hashes, protected ledger, qualification, cap and mode |
| standing policy | closed v2 schema, canonical consent session/message/prompt plus transcript/source hashes, and bound financial baseline/source hash |
| preflight | closed v2 schema, blocked status, replay prohibition and exact three-view payload hash |
| financial source | closed reconciliation schema/hash, USD 10 episode scope, financial-only/non-generation approval |
| ledger reservation | request ID, `JACO_GENESIS28`, fixed Flux endpoint, `0.048`, `FAL_READBACK_V2`, `TEST_LOCAL_ONLY`, `TEST` |
| Executor marker | schema `2`, kind `JACO_FLUX3_FINAL_ADMISSION_MARKER`, authority receipt path/hash, request ID, endpoint, payload hash, `TEXT_TO_IMAGE`, scope, reserved amount, trust identity |

The sequence is protected read-only binding, authenticated GET-only quote/OpenAPI
fetch when LIVE requires it, then composition creates a fresh-process review run
under its fixed root. It immediately reads the receipt back with the typed local
review validator and revalidates the receipt, packet, request, quote, OpenAPI,
financial record, endpoint, payload and freshness bindings immediately before
prepare intent. `_execute_prepared` has no caller-supplied review path or boolean:
only that composition-owned validated capability can cross the admission boundary.
It is then prepare intent, canonical reserve, immutable marker, and
`Executor.run(submit_prepared=True)`.
No provider POST is permitted until the reviews have completed. A checkpointed remote ID is bound to
the existing claim; a fresh process calls provider recovery only. Optional
TEST settlement takes an existing `FalBillingConsumer` and exact UTC window,
and delegates GET-only readback plus settlement to
`FalLedgerSettlementService`.

The protected LIVE factory remains the only configured LIVE entrypoint. Its
fixed root, endpoint, consent `147620`, parent-review barrier, cumulative
ledger cap, and distinct financial (`916e…`) versus operational (`46bb…`)
bindings remain untouched. This TEST composition cannot provision LIVE work.
