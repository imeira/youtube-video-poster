# PR44 billing-reconciliation patch review

## Verdict

**PASS — bounded compatibility repair, with no new authority or settlement action.**

The durable executor receipt records its submitted input as the closed
`input_contract` object. The verifier now requires exact equality with the
one permitted contract:

```json
{"schema":1,"input_kind":"TEXT_TO_IMAGE","payload_sha256":"<mandate payload hash>"}
```

It no longer relies on the non-persisted convenience field
`intent_input_sha256`. This remains fail-closed: a missing contract, a wrong
schema, input kind, or payload hash rejects before billing readback.

## Scope reviewed

- `src/hybrid/billing_reconciler.py`
- `tests/unit/test_jaco_fal_billing_binding.py`

The test adds negative cases for forged `input_contract` schema, input kind, and
payload hash; it also rejects a forged admission scope. The related settlement
test proves same-claim replay idempotence and one canonical settlement receipt.

## Settlement provenance preserved

Parent-provided readback evidence was already verified before this patch:

- request state: `SETTLED`
- actual cost: `48000` micro-USD (`0.048000` USD)
- canonical receipt SHA-256:
  `61ae19055f8be54dabdcb0f14348de9d475c523a5c87a9f03bd882512ce3322d`
- remaining budget: `9.952` USD

This patch does not rewrite that receipt or claim, reserve funds, construct a
provider client, resolve credentials, perform a billing GET, or settle again.

## Local verification

- Red check against the pre-patch verifier: the durable `input_contract`
  fixture was rejected because the old verifier required the absent legacy
  `intent_input_sha256` field.
- Offline wrapper:

  ```text
  .venv/Scripts/python.exe scripts/run_offline_tests.py -q \
    tests/unit/test_jaco_fal_billing_binding.py \
    tests/unit/test_fal_ledger_settlement.py::test_bound_test_readback_settles_once_and_replay_is_idempotent \
    tests/unit/test_fal_ledger_settlement.py::test_same_billed_event_with_different_utc_windows_keeps_one_canonical_settlement_receipt \
    tests/unit/test_text_to_image_admission.py \
    tests/unit/test_jaco_flux3_final_admission.py
  18 passed in 3.25s
  ```

- `ruff check src/hybrid/billing_reconciler.py tests/unit/test_jaco_fal_billing_binding.py` — passed.
- `git diff --check` — passed.
- Added-line security review found no hardcoded credentials, shell execution,
  dynamic evaluation, unsafe deserialization, or SQL construction.

## Boundaries

No CI result for this dirty patch is claimed here. CI must be read from the
new pushed PR head. No merge action is authorized by this review.
