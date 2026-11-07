# PR49 successor execution results

## Result

`--execute` enters only the pinned `OperationAdmissionFactory.execute()` composition. It validates the fixed authority, deployment qualification, budget, preflight/payload, canonical financial source, and shared-ledger binding before credential resolution or either network read. A stale/unknown price is rejected by the isolated reviewer before a ledger database or reservation is created.

TEST remains a private composition only. A TEST qualification cannot be used by the public factory, and TEST uses an injected `mode == "TEST"` fixture transport rather than the LIVE provider. The fresh-process recovery test uses the real `Executor`, `StandingBudgetLedger`, and local-process reviewer orchestration: after a persisted remote ID it performs one recovery GET and no second POST; an attempt with no remote ID is quarantined with no new reservation.

## Provisioning contract after CI

Do **not** modify immutable standing authority v1. Provision a versioned successor authority only after CI, rooted at:

- authority: `C:/HermesStudio/production/jaco-flux3/standing-policy/JACO_GEN28_STYLE_REMEDIATION_001.authority.v2.json`
- qualification: `C:/HermesStudio/production/jaco-flux3/standing-policy/JACO_GEN28_STYLE_REMEDIATION_001.qualification.v1.json`
- financial source: `C:/HermesStudio/production/jaco-flux3/ledger/financial-record.json`
- shared ledger: `C:/HermesStudio/production/jaco-flux3/ledger/standing.sqlite3`

The current implementation accepts an authority only when it retains the closed `jaco-derived-operation-authority/v1` scope and its `canonical_ledger` binding is exactly:

```json
{
  "path": ".../ledger/standing.sqlite3",
  "policy_sha256": "<current standing-policy SHA-256>",
  "financial_source": {
    "path": ".../ledger/financial-record.json",
    "sha256": "<current financial-source SHA-256>"
  }
}
```

The financial source must exactly be the closed reconciliation record for the fixed request, USD, `10.00` episode cap, and `authorizes_generation: false`; its digest is distinct from the standing-policy digest. The qualification is closed: `schema`, `status`, `operation_id`, `episode_id`, `policy_sha256`, `preflight_sha256`, `mode`, `authorization`, `review_packet_schema` (`jaco-fal-flux3-successor-benchmark/3`), `reviewer` (`LOCAL_PROCESS_REVIEW_AUTOMATED_V1`), `ci_digest_sha256`, and `execution_code_sha256` (the current hash of `jaco_style_remediation_operation.py`; plus `consent_fixture` only for private TEST). LIVE requires `jaco-protected-deployment-qualification/v1`, `QUALIFIED`, mode `LIVE`, and `STANDING_USER_AUTHORIZATION_PROTECTED_POLICY`; it is not created by this PR and no signature is claimed.

The operation additionally creates a closed reviewer packet and request at execution, binding request ID, endpoint, operation payload SHA-256, quote/OpenAPI hashes, the copied hash-pinned financial source, one-call limit, `UNKNOWN_UNTIL_PROVIDER_RECEIPT_OR_BILLING_RECONCILIATION`, and `NO_TEST_AS_LIVE`. The marker binds the qualification digest, request, endpoint, payload, input kind, scope, reserved value, and protected trust identity. The local reviewer is same-host independent review only, not external authority or a self-authenticating caller.

Actual CLI after the qualified successor authority is provisioned and CI has passed:

```text
python -m src.hybrid.jaco_style_remediation_operation --operation JACO_GEN28_STYLE_REMEDIATION_001 --execute
```

No parent qualification root, paid submission, production reservation, credential, or provider request was created by this work.

## Offline verification (2026-11-07)

```text
PYTHONPATH=$PWD C:/HermesStudio/runtime/4cc03a48728e3b23d1626265c542b9a39ffa2c11/.venv/Scripts/python.exe scripts/run_offline_tests.py tests/unit/test_jaco_style_remediation_operation.py -q --tb=short
```

Result: **15 passed**. The test child uses `sys.executable`, scrubs credential/proxy-like environment variables, and blocks non-loopback sockets. It covers missing/public TEST qualification, forged financial source, payload replay, stale quote, unknown budget, persisted-ID recovery, and missing-ID quarantine.
