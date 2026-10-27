# External trust-root evidence (offline)

`src.hybrid.external_trust_root` verifies QA evidence only. It never creates a budget authorization, billing settlement, human approval, provider transport, or dispatch path.

## Trust-root configuration

The configured absolute `external-trust-root.json` must be outside this package and use this exact schema:

```json
{"schema":1,"kind":"EXTERNAL_SIGNER_TRUST_ROOT","environment":"TEST|PRODUCTION","algorithm":"Ed25519","version":"…","keys":[{"signer_id":"…","role":"QA","scopes":["EVIDENCE:QA"],"public_key_b64":"…","not_before":0,"not_after":0}],"revoked_signers":["…"]}
```

No key in an evidence packet is read as authority. Unknown, duplicate, expired, revoked, symlink/reparse-point, non-regular, relative, duplicate-key, `NaN`, and infinity inputs fail closed before parsing or reading an untrusted target. The root file, its lexical ancestors, the configured scope directory, the evidence file, and its lexical ancestors are checked before `resolve()` or any read.

## Authority boundary and current state

This repository has **no independently provisioned production trust-configuration channel**. Therefore an Ed25519-successful packet is recorded only as `CRYPTOGRAPHICALLY_VALID_UNAUTHENTICATED_ROOT`; it is not `VERIFIED_PRODUCTION_EVIDENCE`, regardless of a caller-selected `environment=PRODUCTION`, caller hash, or caller path. It remains `BLOCKED_NOT_QUALIFIED`, and `dispatch()` is unconditionally denied.

`TEST` remains TEST. No production root is installed by this package, and no root or packet can authorize billing, a provider request, reservation, settlement, publication, or dispatch.

To introduce `PROVENANCE_AUTHORITATIVE` later, a separate owned provisioning service must deliver an immutable root configuration through a configured external location, with: (1) owner identity and rotation procedure, (2) pinned root-file SHA-256 in independently deployed configuration, (3) explicit allowed local evidence scope, and (4) revalidation of the file identity, hash, closed schema, signer validity/revocation, packet signature, scope, and all job bindings immediately before each irreversible operation. That integration is intentionally absent here; adding an environment flag or passing a root path is not provisioning.

## Evidence packet and signature

Evidence has exactly these fields:

```text
schema, kind=SIGNED_QA_EVIDENCE, version, signer_id, role=QA,
scope=EVIDENCE:QA, request_id, episode, source_sha256, assets_sha256,
policy_sha256, issued_at, expires_at, nonce, signature_b64
```

The signature is RFC 8032 Ed25519, verified by `cryptography`. Signed bytes are UTF-8 JSON of every field except `signature_b64`, with lexicographically sorted keys, compact `,`/`:` separators, `ensure_ascii=false`, and `allow_nan=false`. Timestamps are integer UTC epoch seconds and use `issued_at <= now < expires_at`; signer-key validity uses the same half-open rule. An optional caller-owned replay guard rejects a repeated `(signer_id, nonce)`.

`LiveExecutionService.preflight` accepts evidence only from an existing local regular file within the configured trust-root directory. It may record a packet only after it matches the exact request, episode, canonical payload source hash, manifest assets hash, and policy hash. It remains `BLOCKED_NOT_QUALIFIED`; `dispatch()` remains an unconditional denial. QA role/scope cannot authorize financial operations.

Preflight does not call a quote resolver, construct a billing client, issue GETs, or use a quote as authentication. It reports the existing bridge state as `billing_bridge=NOT_CONNECTED` and `billing_status=NOT_AUTHENTICATED`; authenticated settlement requires a separately validated composition with the existing read-only billing bridge.
