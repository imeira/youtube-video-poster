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

## FLUX 3 canonical canary preflight

The single candidate is `JACO_GEN28_CANONICAL_001` at
`blackforestlabs/flux-3/text-to-image`.  Its adapter is selected only by the
composition-owned endpoint allowlist; no caller boolean selects a FAL queue
adapter.  Before any provider object can be submitted, the adapter accepts only
this closed text-to-image payload shape: non-empty `prompt`, `aspect_ratio`
`4:3`, `resolution` `1k`, `output_format` `png`,
`enable_prompt_expansion` false, and integer `safety_tolerance` 0–4.  It strips
local geometry/provenance fields, rejects references and unknown fields, and
does not upload a frozen manifest asset.  The exact FAL OpenAPI schema is
hash-bound in the benchmark packet.

The pre-generation review is a distinct request, not approval of an image that
does not exist.  It must independently review the literal prompt, source and
identity constraints, the closed payload, one-output/cost envelope, and safety
constraints.  Until a configured independent reviewer service returns a
trust-rooted PASS bound to that packet, the existing `LiveExecutionService`
remains the boundary and no reservation or POST is created.

The authenticated FAL pricing GET is catalog evidence only, not billing
readback and not a transaction quote. Its exact current row has unit
`megapixels` and unit price USD 0.048. The official model page separately says
that a `1k` image is 1 MP, costs USD 0.048, and has one flat price per image by
resolution. Thus this review binds USD 0.048 only for this closed one-output
`1k` payload, by comparing the fresh authenticated catalog row with that
published model-price contract. It never represents `observed_at` or a local
refresh age as provider-issued price validity or expiry. A later paid admission
must refresh the authenticated GET and re-check both facts immediately before
POST; any different price/unit or unavailable evidence blocks it.

## LOCAL_PROCESS_REVIEW successor policy

`LOCAL_PROCESS_REVIEW_POLICY/1` is the authorized successor only for
`JACO_FLUX3_ONE_CANARY_EVALUATION`: one `blackforestlabs/flux-3/text-to-image`
candidate, maximum USD 0.048, no reference upload, nine successor edits,
rendering, publication, gateway writes, reservation, or provider POST.  It does
not alter the historical financial record or mint a production external
signature.

The parent verifies strict, immutable, non-reparse-point inputs before spawning
the reviewer: packet, review request, authenticated price snapshot, OpenAPI
snapshot, and financial record.  Every byte length and SHA-256 is pinned; the
review request must bind the literal Jacó request, endpoint, packet hash,
payload hash, and prohibitions.  The reviewer independently reopens those exact
bytes and checks the semantic prompt, one-image closed payload, quote/ceiling,
OpenAPI endpoint, and financial-only USD 10 mandate.  It emits a write-once,
read-back-verified PASS only as `READY_FOR_PARENT_VERIFICATION_ONLY`.

The reviewer is a fresh Python process with a scrubbed minimal environment;
`FAL_KEY`, spend credentials, gateway credentials, and publish credentials are
not passed.  Its implementation imports no provider, ledger, gateway, or
publication path, accepts only the hash-bound allowlist, and writes only the
review output.  This is process and environment separation on the same Windows
host/user, **not** hostile-code isolation or an independent organization claim.
No caller `VERIFIED` string, `mode` boolean, local root, or arbitrary expiry can
promote it.

The authenticated catalog snapshot records the provider's actual `observed_at`
time and explicitly records `provider_expiry: null`, because this documented
pricing GET does not issue a transaction-quote expiry. `observed_at` is checked
against a local 300-second refresh policy labelled
`OBSERVED_AT_MAX_AGE_SECONDS_300_NOT_PROVIDER_PRICE_VALIDITY`; it is only an
admission freshness control, never a claim that FAL guarantees a price for that
period. A stale catalog observation, changed input/request hash, symlink/reparse
input, or replay blocks before reviewer spawn. Only a parent-owned later
admission path may perform the one durable `prepare → reserve USD 0.048 →
checkpoint → POST` attempt after independently revalidating the review output
and the fresh catalog/model-price comparison. That path remains unimplemented
and is intentionally not called by this review policy. Post-generation visual QA
is a separate pixel-based review and can never be self-approved by this
pre-generation reviewer.
