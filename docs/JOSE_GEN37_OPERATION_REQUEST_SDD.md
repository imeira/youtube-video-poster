# SDD — José / Gênesis 37 offline operation request

## Scope

`src.hybrid.operation_request` materializes a typed, versioned planning request
before any provider, credential, `Executor`, ledger, budget reservation, media
generation, Telegram delivery, or publication path is constructed.  It is a
reusable request-assembly boundary, not a new registry and not a LIVE
capability.

## V2 source-bound inventory

V2 accepts only raw bytes supplied by an existing protected artifact boundary:
`timeline.json`, `subtitles.srt`, `asset_requirements.json`, and
`parent_manifest.json`. It performs no path opening. The parent manifest hashes
must match the supplied timeline, subtitles, and requirements bytes; the
narration-input hash must agree between timeline and parent manifest. Inventory
is derived only after those checks: audio `283.104`, captions `63`, semantic
beats `15`, and shots/asset requirements `45`.

The assembler is keyword-only and has no caller count fields. `timeline_count`
has no semantics and is rejected. The exact user title is `José e sua túnica
especial`; request provenance stores the base revision separately from the code
revision. A self-hash is integrity evidence only and never an issuer receipt or
LIVE authority.

`PREPRODUCTION_READY` means editorial validation only. Provider route remains
`UNQUALIFIED`, no image quota exists, all paid/dispatch fields remain false, and
independent video and thumbnail approval remain human gates. This offline
assembler does not enable LIVE operation, credentials, production reads, sends,
or publication.

## Preflight and executor boundary

`preflight_operation_request` reopens the closed envelope, verifies its byte
integrity and exact source revision, then returns `PREPRODUCTION_READY` only.
It names the unresolved trusted issuer verifier, quote, authenticated billing
readback, provider license provenance, and voice-publication license.  It does
not construct the existing transactional `Executor`; this is intentional:
constructing or reserving through that executor without the fixed external
issuer/verifier source would fabricate an admission path.

Once a fixed, independently authenticated issuer/verifier source is provisioned
outside this request, the existing `QualifiedEpisodeOperation` plus
`Executor.prepare_intent → ledger.reserve → bind_prepared_generic_admission →
Executor.run` chain remains the only admissible submission consumer.  This
request cannot select a provider, ledger, root, transport, quote, capability,
or caller callback for that chain.

## Actual José request

The original v1 request/preflight evidence remains unchanged at
`C:/Users/meira/Documents/HermesStudio/requests/jose-gen37-20261110/`. A
successor v2 report records that v1's fields were positionally misordered.
The corrected inventory is derived from the bound artifacts, not manually
entered: `283.104` seconds, `63` captions, `15` beats, and `45` shots.
The base revision is `8a4f3921b8ea8caef4caf15506aff19b8aff3116`; code revision
is recorded independently in the v2 artifact.

## Invocation boundary

There is intentionally no path-based CLI. A protected upstream reader must
validate its root before it reads the four artifacts, then pass their bytes to
`parse_canonical_editorial_bundle`; assembly and preflight consume that typed
bundle. The API cannot read production paths or convert a self-hashed JSON
claim into authority.
