# PR44 text-to-image admission results

## Result

Implemented and verified the bounded TEST-only reference-free admission path in
the existing `Executor` and `FinalAdmissionKernel`.

- `TEXT_TO_IMAGE` accepts no manifest/reference input and hashes canonical
  literal payload bytes as `intent_input_sha256`.
- `IMAGE_EDIT` still requires a verified reference `Manifest`.
- Prepared receipts persist the closed input contract; the FLUX 3 durable marker
  binds request ID, endpoint, payload hash, input kind, authorization scope,
  and `0.048`. The standing-ledger claim is reserved before submit.
- A fresh subprocess recovered from the journaled remote ID with one GET-only
  `recover` call, zero repeat POSTs, and one ledger claim. A submission without
  a remote ID remained `SUBMITTING`/quarantined and was not retried.
- The real production-composition CLI now has an explicit closed TEST-only
  local configuration. It runs the same packet through a fresh local-process
  review, uses the shared durable executor/ledger, simulates one POST and then
  recovers in a fresh process with one GET. Its result is permanently marked
  `LOCAL_ONLY_NON_PROMOTABLE`; it cannot select or override LIVE mode.

## Actual verification

```text
.venv/Scripts/python.exe -m pytest -q \
  tests/unit/test_text_to_image_admission.py \
  tests/unit/test_jaco_flux3_final_admission.py \
  tests/unit/test_hybrid_execution.py \
  tests/unit/test_fal_ledger_settlement.py \
  tests/unit/test_hybrid_live_providers.py \
  tests/unit/test_hybrid_providers.py \
  tests/unit/test_jaco_flux3_production_composition.py
60 passed in 17.47s

.venv/Scripts/python.exe -m ruff check [touched Python files]
All checks passed

git diff --check
passed
```

## Limits

No credentials, network operations, paid requests, LIVE authority, or production
CLI activation were used. The test CLI uses no provider URLs, no image fixture,
and no license fixture; it only simulates the existing provider boundary in the
explicit TEST domain. `jaco_flux3_production --execute` remains fail-closed
pending a genuinely provisioned mandate/license authority and the independent
production prerequisites listed in the security review.
