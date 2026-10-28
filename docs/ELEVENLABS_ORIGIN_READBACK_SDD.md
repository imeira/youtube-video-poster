# ElevenLabs History Origin Readback SDD

## Scope

`src/hybrid/elevenlabs_origin_readback.py` implements a separate, GET-only origin
readback producer. It does not synthesize speech, list/search history, discover IDs,
retry a submission, alter Director/CLI state, import into an existing audio candidate,
or promote any pipeline stage.

The caller supplies an already-persisted `ElevenLabsHistoryBinding`:

- `history_item_id` is required and remains the only provider recovery/download key.
- local `request_id` is required and never becomes a provider request ID.
- `provider_request_id` is copied only when History metadata supplies a string;
  non-string/null provider values become `null` and are never inferred.
- literal script SHA-256, exact voice ID, provider/source literals, and optional exact
  prior audio SHA-256 are validated before the transport factory, credential supplier,
  client, request, or quarantine write is touched.

## Fixed origin and request surface

The concrete `ElevenLabsProductionTransport` has only `get_json(path=...)` and
`get_bytes(path=...)`. It permits exactly:

```text
GET https://api.elevenlabs.io/v1/history/{history_item_id}
GET https://api.elevenlabs.io/v1/voices/{voice_id}
GET https://api.elevenlabs.io/v1/history/{history_item_id}/audio
```

Opaque identifiers are bounded `[A-Za-z0-9_-]`; URL fragments, query parameters,
absolute URLs, traversal, arbitrary headers/methods, listing/discovery routes, and
POST are absent from the API. The production transport checks an allowlisted path
before calling its credential-owning supplier. It fixes the HTTPS origin, sets
`trust_env=False`, uses `proxy=None`, disables redirects, applies a 15-second total
timeout, rejects non-2xx responses, and caps JSON at 1 MiB and audio at 64 MiB.
Provider error bodies, credentials, and arbitrary provider metadata are not retained
or returned.

No live transport is instantiated by this slice's tests. The injected-service entry
point always classifies every injected object as `TEST_NOT_AUTHORITY`, even if a
fixture, spoofed mutable `authority` string, or subclass claims production authority.
Only `readback_existing_tts_origin_from_elevenlabs(...)` internally constructs the
concrete production transport and can emit `API_KEY_AUTHENTICATED_GET`. This is a
Python host trust boundary, not a cryptographic capability: in-process tests can
modify Python objects, so fixture injection is permanently non-promotable.

## Readback sequence and validation

The public entry point is:

```python
await readback_existing_tts_origin(binding, transport, quarantine)
```

It validates the existing targets and then durably claims the operation before the
factory, credential supplier, client, or GET. The quarantine root must be absolute.
Before the barrier, every existing ancestor and audio, receipt, and claim target is
checked with `lstat` for symlinks and Windows reparse points; before every directory
creation, exclusive write, read, and hash those checks are repeated. The claim is an
`O_CREAT|O_EXCL`, flushed and fsynced JSON file. It is retained after every failure,
including a process crash, so a later invocation fails closed rather than retrying an
ambiguous external read. The per-absolute-root async lock remains an in-process
optimization; the durable claim is the cross-process authority. The admitted reader
then performs this ordered sequence:

1. `GET /v1/history/{history_item_id}`. Require matching ID, `source == "TTS"`, exact
   voice, nonempty model/text/output format, and finite positive `date_unix`; hash the
   returned UTF-8 text without normalization and require the supplied script hash.
2. `GET /v1/voices/{voice_id}`. Require matching ID and type-check the allowlisted
   projection: `voice_id`, `category`, `permission_on_resource`, `is_owner`, and
   `verified_languages`. Unknown permission shapes fail closed.
3. `GET /v1/history/{history_item_id}/audio`. Require bounded nonempty bytes; if a
   prior byte hash was supplied, require exact equality. Create a quarantine audio
   file with `O_CREAT|O_EXCL`, flush and fsync it, then write the canonical receipt
   with the same exclusive policy.

An existing quarantine target or claim is never overwritten. A failure does not
trigger any fallback, conversion, retry-submit, import, render, messaging,
publication, or promotion. A partial exclusive artifact or claim is retained as
quarantine/audit evidence.

## Receipt contract

The returned and persisted canonical JSON receipt is
`elevenlabs-history-origin-readback-v1`. It hashes only allowlisted history and voice
projections plus exact downloaded bytes. `receipt_sha256` hashes every prior receipt
field using UTF-8 JSON with sorted keys and compact separators.

Production transport receipts state:

- `authenticated_readback: API_KEY_AUTHENTICATED_GET`
- `origin_provenance: EXTERNAL_API_READBACK`

Fixture receipts instead state:

- `authenticated_readback: TEST_NOT_AUTHORITY`
- `origin_provenance: TEST_EXTERNAL_READBACK_FIXTURE`

Both forms retain the permanent safety conclusions:

```text
commercial_license: NOT_VERIFIED
acoustic_alignment: NOT_VERIFIED
qualification_status: NOT_QUALIFIED
status: TTS_REQUIRED
provider_dispatch/render/telegram/publication: BLOCKED
```

`EXTERNAL_API_READBACK` means only that the credential-owning provider boundary
returned a history object and bytes. It is not a provider output signature, legal
license proof, acoustic/timing verification, billing settlement, authorization, or
production promotion. Audio is preserved exactly as received; this adapter performs
no decode, resample, transcode, or silent conversion.

## Director TEST-only facade

`DirectorAgent.readback_elevenlabs_origin_from_submission(...)` is the sole
consumer connection. It accepts no `history_item_id` argument and does not construct
the production transport, credential supplier, provider configuration, renderer,
Telegram sender, budget reservation, or LIVE pipeline. Before it calls an injected
service, it strictly snapshots both files below the application-configured
`external_trust_root.evidence_root`:

- immutable candidate fixture receipt: source SHA-256, literal script SHA-256, exact
  audio SHA-256, ElevenLabs voice/provider, request ID, TEST class/mode, and its
  canonical receipt hash;
- durable submission fixture receipt: the pre-persisted history ID plus each of those
  exact candidate bindings and the candidate receipt hash.

The façade permits only the explicit `TEST` fixture contracts
`elevenlabs-origin-readback-candidate-fixture-v1` and
`elevenlabs-history-submission-receipt-fixture-v1`. The injected service must return
`TEST_NOT_AUTHORITY`, `NOT_QUALIFIED`, `TTS_REQUIRED`, and all four blocked
operations. The Director accepts neither those callback claims nor the callback
receipt self-hash as authority: it reopens the exact expected materialized receipt
and audio paths under the configured quarantine through reparse-safe snapshots. The
receipt bytes must be canonical, its self-hash must cover its closed existing-service
schema, and its request/history/provider/source/script/voice/audio bindings must
exactly match both fixture receipts and the snapshot audio bytes. A missing/mutated
receipt, stale source, swapped audio/hash, path escape, wrong source/script/voice,
absent history ID, invalid/missing configured evidence root, existing quarantine
target, or mismatched raw bytes fails closed.
The raw provider bytes are never decoded, converted, resampled, or substituted: a
raw MP3 cannot satisfy a candidate's exact PCM/WAV hash.

There is currently **no real submission-receipt contract producer**. Therefore
non-TEST candidate or submission mode is explicitly blocked before any service,
factory, credential, or GET. This is not a production ElevenLabs activation; a future
producer must define and persist an independently authoritative receipt before the
Director can expose a production factory path.

## Tests

`tests/unit/test_elevenlabs_origin_readback.py` exercises the public service end to
end with injected metadata/audio transport: exact GET ordering, nullable provider
request ID, literal text/audio hashes, exclusive quarantine bytes, canonical receipt,
and blocked states. It also proves invalid IDs fail before the fixture is called,
history consistency failures stop before voice/audio, unknown voice permission shapes
fail closed, pre-existing quarantine bytes are preserved, repeated calls fail before an
additional GET or production factory, symlink and Windows-junction escapes fail before
transport use, and concurrent calls admit one exclusive writer. Fresh synchronized
subprocesses prove a loser is blocked before credentials and all GETs; a crash after
the durable claim cannot cause an automatic credential or GET retry. Production path
allowlisting occurs before credential access. No test reads environment credentials,
constructs a live client, or sends network traffic.

## Integration boundary

This is evidence production only. The parent integration must choose an explicit
consumer and independently maintain the existing non-promotable audio/import gates.
It must not use this module to infer a history ID from text/date/voice, to activate a
provider configuration, or to replace existing source, timing, legal, QA, Director,
or CLI controls.
