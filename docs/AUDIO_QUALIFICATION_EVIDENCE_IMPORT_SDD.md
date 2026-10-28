# Audio qualification evidence import SDD

## Decision and capability boundary

This increment imports a closed, immutable evidence bundle for an already
materialized `audio-import` candidate. It is **not** an audio qualification or
promotion mechanism. Every successful result is permanently:

- `status: TTS_REQUIRED`
- `qualification_status: NOT_QUALIFIED`
- `provenance: UNAUTHENTICATED`
- `attestation_status: ATTESTED_NON_PROMOTABLE`

A signed origin document is only a declared claim. It does not authenticate the
provider or authorize production. A `PRODUCTION` root, valid signers, matching
hashes, or a valid license declaration do not alter those values. This code does
not add a provider-auth adapter, trust bootstrap, authority registry, or any
LIVE capability.

## Inputs and replay

The importer snapshots three strict-JSON declarations: origin, editorial review,
and acoustic review. They are copied byte-for-byte into an independent bundle.
The existing candidate importer/verifier remains responsible for revalidating
the materialized WAV, timing and license bytes, license signature, expiry, and
configured-root constraints. Replay never reopens an origin declaration path.

The bundle has a closed index of each declaration path, size, and SHA-256; the
receipt binds the index and candidate receipt. Receipt and index must retain the
canonical bytes written by the importer. Any missing, extra, reparse-point,
tampered declaration/index/receipt, changed candidate byte, failed signature,
or expired license fails closed.

## Publication protocol

Publication is serialized by the parent request's `.advance.lock`, not a
revision-local lock. Under that lock it snapshots and validates the active
source-bound request, replays the materialized candidate, creates a fresh
exclusive-write staging directory, fsyncs files and directory where available,
revalidates the active source immediately before publication, and atomically
renames staging into `audio-qualification-evidence`. Existing matching bundles
are replayed and returned idempotently; conflicting bytes fail. Interrupted
nonempty staging is retained for audit and blocks automatic retry. No partial
published bundle is accepted.

## Explicit non-effects

The importer never calls `_advance_preproduction_locked`, never writes
`request.json`, `stages.json`, script QA, storyboard, timeline, or promoted
audio, and never returns `ASSETS_REQUIRED`. It does not construct providers,
make network calls, spend, render, notify, publish, or modify real config.

## Remaining activation blocker

The consumer can read this immutable bundle, but legitimate promotion remains
blocked until a separately designed and independently authenticated real
provider-auth adapter supplies authority. This SDD intentionally does not close
that production capability.
