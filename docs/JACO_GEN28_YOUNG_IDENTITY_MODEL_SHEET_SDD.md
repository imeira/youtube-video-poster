# Young Identity Model Sheet composition SDD

`JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001` remains a separate TEST fixture
composition. `JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_002` is its separately
named production successor in `young_identity_model_sheet_production.py`.
It uses the existing `Executor` and `StandingBudgetLedger` with a protected
qualification, fresh cap-bound quote, fixed FAL provider, and durable recovery.

## Production status: unprovisioned

The LIVE code is implemented, but no authority, protected qualification,
preflight, or deployment root for operation `_002` is provisioned in this
repository. CI and tests use only the private TEST fixture; passing them does
not grant authority, establish price as verified actual cost, reserve a LIVE
budget, or authorize a provider POST. An independently issued, hash-bound LIVE
qualification is required before the public `execute()` route can pass its
first gate.

## Exact admission field map

| Durable record | Required fields |
| --- | --- |
| operation | `id`, `kind`, `endpoint`, `call_limit`, `maximum_usd`, `payload`, `payload_sha256` |
| authority | `schema`, `status`, `operation_id`, `predecessor_operation_id`, `executor_predecessor_request_id`, `executor_category`, `policy_path`, `policy_sha256`, `preflight_path`, `preflight_sha256`, `financial_source`, `financial_policy_sha256`, `maximum_next_cost_usd`, `mode` |
| standing policy | `schema`, `status`, `episode_id`, `canonical_consent.consent_message_id=147620`, `financial_baseline` |
| ledger reservation | request ID, `JACO_GEN28_STYLE_REMEDIATION_001`, fixed Flux endpoint, `0.048`, `FAL_READBACK_V2`, `TEST_LOCAL_ONLY`, `TEST` |
| Executor marker | schema `2`, kind `JACO_FLUX3_FINAL_ADMISSION_MARKER`, authority receipt path/hash, request ID, endpoint, payload hash, `TEXT_TO_IMAGE`, scope, reserved amount, trust identity |

The sequence is exactly prepare intent, canonical reserve, immutable marker,
then `Executor.run(submit_prepared=True)`. A checkpointed remote ID is bound to
the existing claim; a fresh process calls provider recovery only. Optional
TEST settlement takes an existing `FalBillingConsumer` and exact UTC window,
and delegates GET-only readback plus settlement to
`FalLedgerSettlementService`.

The protected LIVE factory remains the only configured LIVE entrypoint. Its
fixed root, endpoint, consent `147620`, parent-review barrier, cumulative
ledger cap, and distinct financial (`916e…`) versus operational (`46bb…`)
bindings remain untouched. This TEST composition cannot provision LIVE work.
