# EP8 E2E Production Harness Specification

**Status:** implementation contract

## Goal

Turn one episode request into a resumable, local-first EP8 production package and stop at independent thumbnail and video human-approval gates. Publication is explicitly out of scope.

## Scope and non-goals

The runner must execute research, a source-bound child-safe script, deterministic local narration/timeline fallback, storyboard, canonical-character validation, still-image acquisition, local motion composition, technical QA, thumbnail, metadata, transcript, and delivery-gate preparation. It may choose a cloud operation only through the existing transaction boundary after separate cost approval; the default EP8 route never makes a paid call.

The runner must not publish, use a thumbnail/video rejected revision, overwrite approved inputs, infer an approval from silence, or bypass a hash-bound plan receipt.

## Inputs

- `EpisodeRequest`: `episode_id`, theme, language, channel, source root, output workspace.
- Human plan receipt bound to the current pre-production package.
- For local render: one approved still per timeline frame, approved audio or deterministic local TTS output, and the visual freeze receipt.

## State and recovery

`production.sqlite3` is the per-run authority. Each operation has a deterministic key derived from episode revision, stage, input hashes and route. State transitions are append-only:

`REQUESTED → PLANNED → WAITING_PLAN_APPROVAL → SCRIPT_APPROVED → ASSETS_READY → RENDERED → TECHNICAL_QA_PASSED → WAITING_THUMBNAIL_APPROVAL → WAITING_VIDEO_APPROVAL → READY_FOR_PUBLICATION`.

A crash leaves the last durable operation state. On resume, matching verified output is reused; a missing or mismatched output fails closed. `REJECTED` atomically retires both final artifacts and requires a new revision.

## Fast local route

- Use semantic frame timing, not fixed intervals.
- Each still is animated by FFmpeg filtergraphs (Ken Burns/parallax/push/pull/short fade) and concatenated in a single render invocation.
- Keep the render queue bounded: no more than one full-resolution FFmpeg encode and no unnecessary re-encodes.
- Generate preview at 720p only when explicitly asked; approval artifacts are 1080p H.264/AAC with no burned captions.
- Generate `transcript.txt`, `.srt` and `.vtt` as upload artifacts, never overlay them.
- Any unavailable local still-generation capability produces `ASSETS_REQUIRED`, not a fabricated image.

## EP8 editorial contract

The only scope is Gênesis 15:1–6, 17:1–9/15–21 and 18:1–15. The system must preserve the names Abrão/Sarai before Genesis 17; no Isaac present, born, or implied visually. The mandatory thumbnail text is exactly `A promessa de um filho para Abraão e Sara` and `— Gênesis 15–18`. Curiosity must map to the story truth; urgency/fear/scarcity and deceptive clickbait are disallowed.

## Harness commands

- `studio production plan`: materializes the immutable plan and reports cost/route.
- `studio production approve-plan`: records a human plan receipt.
- `studio production run`: executes only free/local/reused steps and writes a complete status report.
- `studio production status`: validates all checkpoint hashes and prints the next permitted action.
- `studio production deliver`: delegates to the hash-bound offline delivery coordinator only after technical QA.

Each command emits JSON suitable for CI and restart diagnostics.

## Acceptance tests

1. A synthetic, fully approved episode reaches both delivery gates with only FFmpeg/Pillow/WAV fixtures and no network.
2. Missing asset or TTS capability stops at `ASSETS_REQUIRED` with no render and no external side effect.
3. A changed source/asset/audio/script hash invalidates a resume and blocks delivery.
4. A rerun with matching inputs is idempotent and does not create a second render.
5. The output has 1920×1080 H.264/AAC, no subtitle filter, transcript/captions, 3–5 second closing, and independently hash-bound thumbnail/video gates.
6. EP8 contract violations fail before render.
