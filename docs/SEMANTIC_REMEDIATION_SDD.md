# Semantic remediation SDD

## Scope

This slice closes compiled-image remediation against arbitrary correction prompts. It is local and does not construct providers, submit jobs, contact messaging, or change paid authority.

## Immutable inputs

A remediation accepts the active compiled frame, its rejected predecessor receipt, and exactly one independent-QA finding: `{"code": CODE}`. `CODE` is one of `CAST`, `GARMENT`, `ACTION`, `IDENTITY`, `SOURCE`, or `TIMING`.

## Closed output and transitions

Only a completed active predecessor with hash-bound `qa == false` can produce one correction successor. The successor binds the predecessor request ID, base compiled prompt, and deterministic constraint derived from the closed code in `payload.semantic_remediation`. A second request for the scene must have the same finding or fail immutable-successor validation. The correction replaces the active image lineage; it cannot promote the rejected bytes.

## Recovery and negative acceptance

The request identity includes the deterministic prompt and remediation payload, so restart/replay resolves through the existing executor receipt path. Free-text prompts, extra finding fields, unknown codes, unknown scenes, missing rejection receipts, and mismatched repeat findings fail before queue admission or provider submission.

## Revision receipt propagation

An independent reviewer FAIL is the only revision-side source of a remediation finding. The review decision persists an optional `failure_code` only when `approved == false`; it must be one of the closed codes above. The revision correction map carries that exact one-field finding into `ProductionRun.remediation_job()`. No adapter may manufacture `IDENTITY` (or any other code) because a receipt is absent, stale, malformed, or unrecognized.

A completed rejection receipt with absent, unknown, non-string, or extra failure-code data is non-remediable and must fail closed before correction-job construction, authorization, queue admission, or provider creation. A PASS receipt must not carry a failure code. The exact finding is therefore bound into `payload.semantic_remediation`, the correction prompt, and its request identity.

## Integration point

`ProductionRun.remediation_job()` is the single compiled image-successor constructor. `OperationalPipeline`, the autonomous compiled adapter, benchmark fixture, and revision adapters pass only the closed finding shape recovered from the independent-QA receipt. Independent visual QA remains the authority that records the predecessor rejection; this slice does not infer a PASS or submit a provider request.
