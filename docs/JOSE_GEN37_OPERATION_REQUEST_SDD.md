# SDD — José / Gênesis 37 offline operation request

## Scope

`src.hybrid.operation_request` materializes a typed, versioned planning request
before any provider, credential, `Executor`, ledger, budget reservation, media
generation, Telegram delivery, or publication path is constructed.  It is a
reusable request-assembly boundary, not a new registry and not a LIVE
capability.

## Immutable request contract

The closed `offline-production-operation-request/v1` request binds:

- literal operation and episode identifiers;
- canonical repository URL and exact 40-character source revision;
- an editorial inventory: measured supplied audio duration, timeline count,
  caption-cue count, beat count, and shot count;
- the existing FAL FLUX 3 text-to-image endpoint only as an `UNQUALIFIED`
  route; and
- the declared voice-publication license state and the two independent human
  gates (video and thumbnail).

A shot count is not an image quota.  The only supported image quota is `null`.
The assembler rejects a descriptor that tries to turn the 45 planned shots into
45 authorized image calls.  The request also fixes `paid_generation: false` and
`dispatch_permitted: false`.

The envelope carries a SHA-256 of the canonical request bytes.  This detects
mutation of the materialized request; it is explicitly not an issuer receipt,
provider quote, approval, price, license proof, or LIVE authority.

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

The durable request at
`C:/Users/meira/Documents/HermesStudio/requests/jose-gen37-20261110/` binds the
current master source revision `8a4f3921b8ea8caef4caf15506aff19b8aff3116` and
records only supplied facts: audio `283.104` seconds, timeline count `63`,
caption cues `15`, beats `45`, and shots `45`.  It preserves
`voice_publication_license: UNKNOWN` and leaves image quota null.

## Next executable command

```bash
uv run python -m src.hybrid.operation_request preflight \
  --request C:/Users/meira/Documents/HermesStudio/requests/jose-gen37-20261110/jose-gen37-operation-request-v1.json \
  --expected-source-revision 8a4f3921b8ea8caef4caf15506aff19b8aff3116
```

A successful command is still non-promotable and cannot dispatch.  The next
platform decision is to provision the fixed trusted issuer/verifier source and
its readback contract; no TEST fixture, self-hash, older Jacó authority, or
EP8 evidence may substitute for it.
