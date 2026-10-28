# Audio Alignment Attestation SDD

## Scope

One offline, non-promotable bridge from a source-bound audio candidate to the
Director import entrypoint. It verifies an independently signed alignment
attestation and materializes its exact bytes with the existing audio packet.
It does not synthesize audio, derive acoustic findings, construct a provider, or
clear any production gate.

## Activation input

`DirectorAgent.import_preproduction_audio_candidate(...)` accepts the optional
`alignment_attestation_path`. The document is accepted only through the
application-configured `ExternalTrustRootVerifier` and only when all bindings
match the already measured candidate:

- `source_sha256`, `script_sha256`, `audio_sha256`, and `timing_sha256`
- `provider_id` and `voice_id`
- `role=QA`, `scope=EVIDENCE:QA`, a configured signer, validity window, and
  Ed25519 signature

The attestation schema is `audio-alignment-attestation-v1`; it has no caller
controlled verdict or `PASS` field. The external QA signer, not the input
packet, supplies the authority for the signature check.

## Materialized contract

When present, the packet includes `alignment-attestation.json`, its SHA-256 in
the immutable index, and `alignment_attestation_sha256` in the receipt.
Replay rechecks the file set, index, bindings, signature, signer role/scope,
and expiry from the materialized bytes.

A verified attestation records only
`alignment_verification=EXTERNALLY_ATTESTED_NON_PROMOTABLE`. The receipt remains
`provenance=UNAUTHENTICATED`, `qa_qualification=NOT_QUALIFIED`,
`qualification_status=NOT_QUALIFIED`, `status=TTS_REQUIRED`, and every Director
operation stays blocked. A TEST root exercises the verifier mechanically only;
it cannot become REAL or LIVE authority.

## Tests

`test_director_entrypoint_materializes_externally_verified_alignment_attestation_without_qualification`
uses the Director entrypoint with distinct TEST license and QA signing keys. It
asserts materialization plus the permanent qualification barrier; it does not
claim acoustic evidence or authority for production.
