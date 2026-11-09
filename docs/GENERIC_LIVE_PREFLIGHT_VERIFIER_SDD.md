# SDD — bounded generic LIVE preflight verifier

## Scope

`GenericLivePreflightVerifier` is a read-only, data-driven verifier used before a future generic factory composition. It accepts only a literal ASCII operation ID. `production()` has no caller-provided root and consults the repository-owned `generic_live_canonical_config.GENERIC_CANONICAL_BINDINGS` SSOT before any protected-root access. That registry is intentionally empty today, so production readiness returns `BLOCKED: generic canonical binding is not provisioned` without opening a production file, SQLite transcript, ledger, credential, or episode authority. `for_test()` is a hermetic fixture seam and cannot make TEST evidence production authority.

It does **not** import or construct an executor, ledger, provider, credential, transport, billing client, reservation, GET/POST client, callback, or readiness token. Its output is a structured `generic-live-verified-bound-preflight/v1` artifact with `authority: NONE` and `dispatch_permitted: false`.

## Immutable input schema

The TEST-only closed `generic-live-operation-packet/v1` contains exactly: operation ID, mode `TEST`, episode ID, and path/SHA-256 descriptors for canonical consent, standing financial policy, independent local-process review, finite quote, license provenance, and exact wire payload. All descriptors are single-file literal names under the TEST root; no caller path is accepted. SHA-256 must be lowercase 64-hex. A packet whose mode is `LIVE` is rejected before any supporting evidence is read.

The verifier requires all evidence to bind the same operation/episode/payload. It validates:

- canonical consent scope/status;
- standing-policy consent hash, USD cap, operation and episode;
- independent local-process review PASS plus all six evidence hashes;
- finite quote's provider, endpoint, model, currency, cap, payload hash, and expiry;
- license provenance's provider/model/license/payload status.

Missing, malformed, hash-mismatched, expired, foreign-scope, forged-review, or mismatched-license evidence raises `GenericLivePreflightBlocked` before any external side effect.

## Output schema

A successful TEST result has exactly the immutable operation/episode and hashes for packet, consent, financial policy, review, quote, license, and payload, plus `status: VERIFIED`, `domain: TEST`, `verification: STRUCTURAL_TEST_FIXTURE_ONLY`, `authority: NONE`, and `dispatch_permitted: false`. It is deliberately not an admission receipt, LIVE verification, or authority token.

## Integration point and next step

A future composition-owned generic factory must add a reviewed operation-specific fixed canonical binding to the SSOT, then call only pure parameterized validators selected by that binding. It must not reuse a Jacó authority, factory, transcript, qualification, or ledger as generic proof. It must bind a resulting artifact to its protected admission workflow before an executor can be wired. That mapping and wiring remain intentionally absent: generic LIVE builder/dispatch stays BLOCKED and no LIVE verifier is claimed complete.

## Acceptance evidence

Hermetic TEST fixtures are non-secret static documents. Tests cover canonical TEST acceptance and reject LIVE packets before supporting-evidence reads, forged consent/reviewer, expired and wire-mismatched quote, wrong financial scope, mismatched license payload, malformed operation ID, and immutable hash changes. Tests also patch Jacó factory/validator entrypoints to raise and guard production-root opens, proving unprovisioned generic readiness cannot call them or open production state. TEST evidence never crosses the production locator.
