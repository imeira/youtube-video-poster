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

## State transitions and recovery

`SOURCE_REQUIRED → SCRIPT_CANDIDATE → TTS_REQUIRED → PREPRODUCTION_VALIDATED → ASSETS_REQUIRED`.

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
