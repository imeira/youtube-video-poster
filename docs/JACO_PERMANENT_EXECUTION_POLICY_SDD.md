# Jacó permanent execution policy — SDD

## Immutable scope

The policy is fixed to request `72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9`, revision `818556d97edb648f`, episode `JACO_GENESIS28`, and total episode cap **USD 10.00**. It reads the existing financial reconciliation as historical cap evidence only. Its `authorizes_generation: false` remains historical and is never edited or reinterpreted as provider authority.

Current implementation is **preflight-only and LIVE-blocked**. The historical record does not authorize generation. The only routine human media gates, if a separately reviewed production composition is later deployed, are exactly `thumbnail` and `final_video`. Publication is disabled; it requires a later explicit publication command with separately hash-bound media/metadata receipts.

## Closed admission

`JacoExecutionPolicyResolver` returns historical policy metadata or raises `LiveAdmissionError`. It currently admits **no** job: `trusted_provider_registry()` rejects every caller-supplied adapter and `admit()` fails closed before a provider, reservation, submission, receipt, or recovery action.

LIVE remains blocked because the required composition-owned trust service is not deployed. That service must independently verify all of:

- exact source path and no-symlink snapshot; issuer, pinned root, document hashes, and closed schemas;
- exact endpoint, model and payload; request, currency, quote ID, finite expiry, and price bound;
- actual licensing evidence and prior authority; and
- the existing configured budget/standing-policy bridge as the sole durable admission path.

No caller-controlled Python boolean/attribute, `VERIFIED` string, local financial JSON, or quote evidence string is authority. TEST fixtures remain TEST-only and cannot mint `LIVEAuthorization`. Missing credentials, absent trust composition, malformed financial scope, policy mismatch, or TEST-mode adapter fail before reservation/submission. The resolver does not construct providers, read credentials, make network calls, or publish.

## Director integration and recovery

`DirectorAgent.dispatch_current_policy_baselines()` reaches the same blocked resolver boundary; it cannot bypass `LiveExecutionService` or create a parallel raw executor route. No LIVE `Authorization`/`Price` is created and no executor/provider action occurs. Existing TEST executor, canonical-status, and worker-recovery mechanics remain covered only in TEST mode; they are not evidence of provider, billing, license, or LIVE authority.

A sealed local transport is TEST-only. It provides no policy, financial, license, billing, QA, human approval, credential, provider-delivery, or publication authority. No real paid call is enabled.

## Negative acceptance coverage

- historical scope mismatch or changed cap is denied;
- `authorizes_generation: false` remains false and publication remains disabled;
- missing license and unregistered/TEST-mode provider deny before submit;
- forged adapter attributes (`mode=LIVE`, `test_transport`, arbitrary endpoint), literal `VERIFIED` licensing, arbitrary quote evidence, and a five-minute local expiry cannot register or admit LIVE work; no submit/recover artifact is created;
- TEST transport still exercises TEST executor mechanics without producing LIVE authority;
- tampered/expired/model-or-payload-mismatched evidence must remain blocked by the existing `LiveExecutionService` preflight boundary; no trusted composition is presently available to promote any such evidence.
