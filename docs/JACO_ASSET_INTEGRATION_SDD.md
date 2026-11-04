# Jacó asset integration SDD

## Scope

This narrow integration binds the supplied Jacó/Genesis 28 request
`72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9` and
source revision `818556d97edb648f` to a durable, local input manifest and
status report. It reads only the supplied first-image packet, readiness
inventory, and referenced local files. It never synthesizes TTS, calls a
provider, reserves budget, dispatches an image request, alters an episode,
renders, sends media, or changes financial/execution modules.

## Immutable inputs

- First canonical-image packet with the literal `request_id`, source revision,
  Genesis 28 references, ten measured scene windows, and exact first image job.
- Jacó readiness inventory and the Lorena candidate/receipt it names.
- Declared narration, source, script, timing, identity-reference, and license
  paths only after each exists and matches its declared SHA-256.

The integration treats the first-image packet as a request snapshot, not paid
authority. Its POST remains `POST_NOT_EXECUTED` unless the existing admission
facade has independently accepted an authenticated, request-bound authority.

## Closed outputs

Under `C:/HermesStudio/factory/requests/<request_id>/jaco-asset-integration/`:

- `canonical-input-manifest.json`: exact request and revision bindings,
  validated scene map, literal first-image job, and per-artifact availability
  and hash evidence.
- `status.json`: closed qualification status, blocked operations, QA scope, and
  deterministic `next_job_input_artifact_path` for the provider workstream.

Outputs are status/manifest evidence only. No unavailable bytes are copied or
represented as present. Lorena stays `CANDIDATE_ONLY_NOT_APPROVED_ASSET` unless
an existing legitimate approval receipt is independently verified.

## Validation and state transitions

The scene map must contain exactly `S001` through `S010`, have contiguous
finite non-overlapping windows from zero to 105.048 seconds, map biblical
scenes to Genesis 28 references, and reserve only the final reflection scene
for an empty source-reference list. The literal first job must be
`JACO_GEN28_CANONICAL_001`, `POST_NOT_EXECUTED`, with zero references; its
nine successors remain un-authorized.

Each declared file is `VERIFIED` only when it is a regular file and its
SHA-256 matches. Missing, moved, altered, unknown-license, or unknown
identity-reference inputs are explicit blockers. Technical FFmpeg/ffprobe PASS
is recorded only at its actual scope; it is not semantic QA, independent QA,
human approval, license proof, or provider authority.

`DISCOVERED -> MATERIALIZED_NOT_QUALIFIED -> PROVIDER_ADMISSION_BLOCKED`.
The state cannot transition to dispatch, render, or Director import from this
integration. Director import is deliberately not invoked: it requires a
complete, qualified materialized package and an approved asset manifest.

## Recovery

A rerun recomputes hashes and writes a new deterministic status projection.
It does not retry a provider operation or mutate source assets. If an output
write is interrupted, no partial status is an activation receipt; rerun creates
fresh complete JSON documents.

## Negative acceptance tests

1. Missing narration bytes yield `NOT_QUALIFIED` and provider admission stays
   blocked even if the old inventory claimed technical PASS.
2. A Lorena candidate with matching bytes remains a candidate without a
   qualifying human approval receipt.
3. A non-contiguous scene window, wrong request/revision, non-Genesis source
   reference, or altered first-image job is rejected.
4. Materialization preserves the exact first image payload but never calls a
   provider or labels it authorized.
5. Missing identity-reference or license evidence is a precise human-only/
   external-evidence blocker, not a fabricated PASS.
