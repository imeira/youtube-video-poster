# External trust-root evidence (offline)

`src.hybrid.external_trust_root` verifies QA evidence only. It never creates a budget authorization, billing settlement, human approval, provider transport, or dispatch path.

## Trust-root configuration

The configured absolute `external-trust-root.json` must be outside this package and use this exact schema:

```json
{"schema":1,"kind":"EXTERNAL_SIGNER_TRUST_ROOT","environment":"TEST|PRODUCTION","algorithm":"Ed25519","version":"…","keys":[{"signer_id":"…","role":"QA","scopes":["EVIDENCE:QA"],"public_key_b64":"…","not_before":0,"not_after":0}],"revoked_signers":["…"]}
```

No key in an evidence packet is read as authority. Unknown, duplicate, expired, revoked, symlink/reparse-point, non-regular, relative, duplicate-key, `NaN`, and infinity inputs fail closed before parsing or reading an untrusted target. The root file, its lexical ancestors, the configured scope directory, the evidence file, and its lexical ancestors are checked before `resolve()` or any read.

## Provisioning boundary and current state

`ExternalTrustRootVerifier.from_app_config()` is the only configured composition
path. It reads `external_trust_root` from the application's existing deployment
configuration, never from an episode packet. The closed trusted-input mapping is:

```text
root_path, root_sha256, evidence_root, environment
```

All paths are absolute, canonicalized only after lexical ancestor reparse-point
checks, and must be outside the package. `root_sha256` pins the root bytes
independently of the root's own version, signer list, revocations, and validity
periods. The configuration is a deployment trusted input; its hash pin is not a
claim that a file hash magically proves ownership of that configuration.

Every `verify()` takes one reparse-safe strict byte snapshot of the configured root and derives its closed-schema parse, pin digest, environment, version, signer list, revocations, validity periods, and signature key from that same snapshot. It checks the canonical path, regular-file identity before/open/after read, pin, signer validity/revocation, signature, scope, expiry, and exact job bindings again. A replaced root, changed pin, expired signer/evidence, revoked signer, reparse point, or TEST root configured as PRODUCTION fails closed. Packet fields cannot select a root, pin, scope, or environment.

When that deployment configuration is absent the service reports
`NOT_CONFIGURED`; when it cannot be validated on this host it reports
`UNAUTHENTICATED`. Both retain a blocked preflight. A successful configured QA check is recorded as
`EXTERNAL_CONFIGURATION_VALIDATED`, not as payment, provider, or dispatch
authority. `TEST` remains TEST; no test root is accepted as production.

No root or packet can authorize billing, a provider request, reservation,
settlement, publication, or dispatch. `dispatch()` remains unconditionally
denied, so this change provides offline loader/preflight validation only.

## Evidence packet and signature

Evidence has exactly these fields:

```text
schema, kind=SIGNED_QA_EVIDENCE, version, signer_id, role=QA,
scope=EVIDENCE:QA, request_id, episode, source_sha256, assets_sha256,
policy_sha256, issued_at, expires_at, nonce, signature_b64
```

The signature is RFC 8032 Ed25519, verified by `cryptography`. Signed bytes are UTF-8 JSON of every field except `signature_b64`, with lexicographically sorted keys, compact `,`/`:` separators, `ensure_ascii=false`, and `allow_nan=false`. Timestamps are integer UTC epoch seconds and use `issued_at <= now < expires_at`; signer-key validity uses the same half-open rule. An optional caller-owned replay guard rejects a repeated `(signer_id, nonce)`.

`LiveExecutionService.from_app_config` composes that loader through the existing
application configuration channel. `preflight` accepts evidence only from an
existing local regular file within the configured external evidence scope. It may
record a packet only after it matches the exact request, episode, canonical
payload source hash, manifest assets hash, and policy hash. It remains
`BLOCKED_NOT_QUALIFIED`; `dispatch()` remains an unconditional denial. QA
role/scope cannot authorize financial operations.

Preflight does not call a quote resolver, construct a billing client, issue GETs, or use a quote as authentication. It reports the existing bridge state as `billing_bridge=NOT_CONNECTED` and `billing_status=NOT_AUTHENTICATED`; authenticated settlement requires a separately validated composition with the existing read-only billing bridge.
