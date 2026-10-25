# Source-bound pre-production SDD

## Scope

Offline pre-production converts immutable local biblical-source bytes into a non-executable script candidate. It does not synthesize audio, select a provider, quote a price, spend money, render media, notify Telegram, or publish.

## Immutable inputs

- Literal prompt bytes and their request ID.
- A local `local-biblical-source-v1` JSON document with matching mode and provenance.
- Structured biblical facts and source references.
- Optional `research.narrative_segments`, in source order. Every segment has a contiguous `SNNN` ID, `kind`, complete `narration`, nonempty fact list, and `motion_need`; biblical segments also have a verse reference. Exactly one family reflection is last. Both `...` and `…` endings are rejected.
- Candidate provider records, which are descriptive only.

## Closed outputs

- `script/script.json` is a projection of segment narration. For a source-bound narrative it has no `target_duration_s`, `closing_duration_s`, segment `duration_s`, narrator binding, or visual-mode claim. It carries `estimated_narration_seconds`, `closing_estimated_duration_s`, and explicit `PENDING_MEASURED_AUDIO` measurement state; the closing estimate must not exceed 10 seconds at the fixed 130 WPM reference.
- `plan/editorial-plan.json` has measured word count, a WPM-derived estimate, source references, and a segment→verse/fact/motion matrix. `prices_usd` is always `null`.
- `qa/script-qa.json` records deterministic script QA. Source-bound QA remains `PENDING_INDEPENDENT_EDITORIAL_REVIEW`, never a synthesized PASS, and fail-closes audio/timeline/storyboard progression.
- Request state advances only to `TTS_REQUIRED` until a real, local, decodable audio file and hash-bound timeline are supplied.

## Validation and rejection

- Source-bound prose is copied verbatim; no truncation marker (`...` or `…`), filler, target duration, audio/visual duration fact, or LLM rewrite is permitted.
- Biblical and family-reflection segments remain structurally distinct. A reflection cannot be represented as a biblical promise.
- Candidate provider records must be `UNQUALIFIED` and commercially `UNKNOWN`. Any `price`, `cost`, `usd`, or `quote` key at any nesting level rejects the source.
- Measured audio duration is the only duration evidence for a storyboard. A word-rate value is explicitly only an estimate.

## Audio boundary (currently fail-closed)

`audio-preflight` is a read-only capability report. It does not read caller
review/authorization JSON, write a QA/stage file, or return `AUDIO_AUTHORIZED`.
`execute_authorized_audio` unconditionally **raises**
`EXTERNAL_AUDIO_SYNTHESIS_BLOCKED` before resolving paths, constructing a
provider, reserving state, or writing an intent. External synthesis remains
unqualified until a separately reviewed trust root, canonical transport
contract, WAL/reconciliation recovery protocol, and independently verifiable
voice/evidence chain are implemented and accepted.

The only useful audio operation in this slice is `import_test_audio_segment`. It accepts only an immutable `bytes` snapshot with
the closed `audio/wav` type; it has no `source_path`/`source_file` fallback and
no CLI route. It decodes, measures, checks PCM silence, and verifies explicit
TEST fixture WordBoundary events against that same snapshot before caching it
under exclusive immutable names. The API receives its existing cache root from
trusted application composition only: payloads, remote receipts, and CLI input
cannot select it. A receipt binds the configured root hash, namespace, exact
derived name, audio/input hashes, measured boundaries/provenance, and literal
`NOT_QUALIFIED` voice state; replay recomputes and compares every field and
rejects tampering, recomputed metadata, wrong-root copies, and incomplete
cache pairs. This is not a claim of protection from an attacker who controls
the configured local cache root.

The importer rejects silence, invalid or oversized WAV data, false/invented
boundary receipts, and every reflection closing. It cannot advance an
independent review/audio gate, qualify a provider, or qualify a voice.

A TEST fixture never qualifies Lorena or any narrator. In particular, the
adult `pt-BR-ThalitaNeural` default must not be routed to a child/reflection
closing. A real closing requires separately canonical child-voice evidence,
actual event receipts, and measured duration at most 10 seconds; none exists
in this entrypoint.

## State transitions and recovery

`SOURCE_REQUIRED → SCRIPT_CANDIDATE → TTS_REQUIRED → PREPRODUCTION_VALIDATED → ASSETS_REQUIRED`.

There is no `AUDIO_AUTHORIZED` transition in the current audio slice. A source-bound
request stays blocked at `TTS_REQUIRED` until a separate, independently reviewed
real-audio integration exists.

Each source SHA-256 creates a distinct revision directory. Existing scripts, QA receipts, and revisions are never overwritten. A changed source creates a successor revision. A failed or interrupted revision is retained for audit and may only be resumed if all persisted hashes still match.

## Director integration

`advance_preproduction` is the sole offline entry point. It invokes `ScriptAgent` with `target_duration_s=None`, writes the immutable artifacts, and stops before TTS. Timeline/storyboard construction remains unavailable without supplied measured local audio.

## Negative acceptance tests

1. A source-bound narrative is emitted as complete, exact prose with no fixed target duration.
2. Segment matrix entries cover every source segment and retain verse, facts, and motion need.
3. Three unqualified candidates remain descriptive and price-free.
4. Nested candidate `price`, `cost`, `usd`, and `quote` keys are rejected.
5. Missing or multiple family reflections, Unicode truncation, and a closing estimate over 10 seconds are rejected.
6. A previous rejected revision remains unchanged when a successor source is advanced.
7. A self-emitted or stale review, or absent/rejected authorization, cannot construct a TTS provider.
8. A TEST non-silent WAV fixture with WordBoundary data reaches the existing storyboard path; a resume reuses every hash-valid segment without synthesis.
