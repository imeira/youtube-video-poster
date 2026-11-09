# SDD — bounded generic LIVE preflight verifier

## Scope

`GenericLivePreflightVerifier` is a read-only, data-driven verifier used before a future generic factory composition. It accepts only a literal ASCII operation ID and resolves it through its fixed repository-owned locator. `production()` is pinned to `C:/HermesStudio/protected/generic-live-preflights`; `for_test()` is a hermetic fixture seam and cannot make TEST evidence production authority.

It does **not** import or construct an executor, ledger, provider, credential, transport, billing client, reservation, GET/POST client, callback, or readiness token. Its output is a structured `generic-live-verified-bound-preflight/v1` artifact with `authority: NONE` and `dispatch_permitted: false`.

## Immutable input schema

The closed `generic-live-operation-packet/v1` contains exactly: operation ID, TEST/LIVE mode, episode ID, and path/SHA-256 descriptors for canonical consent, standing financial policy, independent local-process review, finite quote, license provenance, and exact wire payload. All descriptors are single-file literal names under the fixed root; no caller path is accepted. SHA-256 must be lowercase 64-hex.

The verifier requires all evidence to bind the same operation/episode/payload. It validates:

- canonical consent scope/status;
- standing-policy consent hash, USD cap, operation and episode;
- independent local-process review PASS plus all six evidence hashes;
- finite quote's provider, endpoint, model, currency, cap, payload hash, and expiry;
- license provenance's provider/model/license/payload status.

Missing, malformed, hash-mismatched, expired, foreign-scope, forged-review, or mismatched-license evidence raises `GenericLivePreflightBlocked` before any external side effect.

## Output schema

A successful result has exactly the immutable operation/episode and hashes for packet, consent, financial policy, review, quote, license, and payload, plus `status: VERIFIED_BOUND_PREFLIGHT`, `authority: NONE`, and `dispatch_permitted: false`. It is deliberately not an admission receipt or authority token.

## Integration point and next step

A future composition-owned generic factory must call `production().verify(operation_id)` internally and bind this artifact to its protected admission workflow before its existing executor can be wired. That wiring remains intentionally absent: generic LIVE builder/dispatch stays BLOCKED and no factory is complete.

## Acceptance evidence

Hermetic TEST fixtures are non-secret static documents. Tests cover canonical TEST acceptance and reject forged consent/reviewer, expired and wire-mismatched quote, wrong financial scope, mismatched license payload, malformed operation ID, and immutable hash changes. TEST evidence never crosses the production locator.
