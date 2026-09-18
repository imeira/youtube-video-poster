# EP8 Fully Connected Production — SDD

**Status:** implementation contract
**Route:** `studio revision`
**Safety boundary:** production may stop at human gates; publishing is never implicit.

## 1. Goal

Turn the one-line EP8 request and the explicit rejection of the prior video and thumbnail into a new, source-bound, resumable production revision. The public route must connect research, editorial planning, script, independent QA, narration, semantic storyboard, canonical characters, still generation, per-frame visual QA, human visual-freeze approval, local animation, one-pass render, technical/narrative QA, original thumbnail, complete YouTube metadata and transcript, then independent thumbnail and video approval gates. A separately invoked publication command may become eligible only after both artifact approvals and an additional exact publication authorization.

The default route is local/offline. Tests, dry-runs and status commands must not call paid providers, provision a GPU, send Telegram messages, or upload to YouTube.

## 2. Authoritative artifacts and state

`revision.json` is the atomic control document. Every completed stage has immutable input and output hashes. Append-only `events.jsonl` records queue, provider, QA and stage lifecycle events. A revision directory contains:

- `request.json`, `research/sources.json`, `plan/editorial-plan.json`;
- `script/script.json`, `qa/script.json`, `qa/biblical-accuracy.json`, `qa/child-safety.json`;
- `characters/character-bible.json`, `storyboard/scenes.json`, `audio/narration.wav`, `audio/timeline.json`, `qa/audio.json`;
- `compiled/manifest.json`, contact sheet and per-frame QA evidence;
- `approval/visual-freeze.json`, `animation/motion-plan.json`;
- one 1080p H.264/AAC final render, transcript/SRT/VTT, complete metadata and original thumbnail;
- independent hash-bound approval receipts and optional publication authorization.

The historical directory `C:/HermesStudio/episodes/EP8_PROMISE_SON_20260901` is evidence only. It is never modified. Its rejection receipt is converted to predecessor identities and editorial feedback by an explicit read-only bootstrap operation.

## 3. Public state machine

`WAITING_PLAN_APPROVAL → READY → WAITING_VISUAL_FREEZE_APPROVAL → READY_RENDER → WAITING_THUMBNAIL_APPROVAL → READY_VIDEO_DELIVERY → WAITING_VIDEO_APPROVAL → WAITING_PUBLICATION_AUTHORIZATION → READY_FOR_PUBLICATION`.

- `approve-plan` binds the executable plan, code, configuration, references, rejection feedback and price evidence.
- `approve-visual-freeze` binds the exact manifest and contact sheet. Rendering is unreachable before it.
- `approve thumbnail` and `approve video` are independent and sequential.
- `authorize-publication` is a third, exact command that binds metadata plus both approved media hashes. It does not upload.
- `publish` is separate, disabled unless a concrete provider is explicitly configured; TEST mode can only exercise a fake local provider.
- Any rejection supersedes all active media and approvals, preserves history and creates a successor whose editorial brief includes the rejection reason.
- Silence, stale hashes, free-form state edits and Telegram delivery do not approve anything.

## 4. Connected production stages

| # | Stage | Required executable evidence |
|---|---|---|
| 1–5 | request, research, passages, duration, cost | structured request and editorial plan derived from source classifications; adaptive 3–15 minute recommendation; words/scenes/hero candidates/costs |
| 6 | script | structured JSON segments; each segment binds source claims, child-safe narration, emotion, setting, characters, prompt, transition and SFX; rejection feedback changes the successor script identity |
| 7 | narration | provider WordBoundary timestamps, decoded duration, transcript equality and audio QA |
| 8 | storyboard | StoryboardAgent-compatible scene schema, exact semantic timing, visual continuity and motion intent |
| 9 | characters | canonical Abraham/Sarah cards and approved reference hashes; no text-to-image-only authority |
| 10 | images | transactional provider boundary, current price/authorization for paid calls, bounded queue and path-independent content keys |
| 11/15 | visual QA | technical geometry checks before independent semantic review; individual immutable reports and contact sheet |
| 12 | local animation | per-scene pan/zoom/push/pull/parallax-safe motion plan selected from composition and narration, not one repeated preset |
| 13 | cloud hero clips | only HIGH/CRITICAL and explicitly approved; serverless RunPod has submit checkpoint, polling, deadline, recovery and cancellation; local still animation is always a complete fallback |
| 14 | assembly | one bounded FFmpeg H.264 encode, AAC loudnorm, 1920×1080, no burned captions, closing hold 3–5 s |
| 16–18 | thumbnail, metadata, transcript | original thumbnail composition with exact safe copy; complete metadata including description/tags/playlist/category/made-for-kids/captions; transcript/SRT/VTT sidecars |
| 19 | approvals | visual-freeze, thumbnail and video gates have distinct receipts and verified delivery readback |
| 20–21 | publication and notification | separate publication authorization and idempotent provider readback; Telegram URL notice only after verified upload |

## 5. Throughput and recovery contract

- At most `image_workers + prefetch` image jobs may be materialized. Queue state is durable (`QUEUED/RUNNING/COMPLETE/FAILED`) and FIFO within priority.
- Baseline generation and QA overlap. All correction jobs in a wave run concurrently under the same worker budget.
- Content keys exclude absolute paths and reviewer names. Identical immutable bytes and normalized parameters may be reused through a workspace-independent SHA-256 CAS; altered bytes invalidate only dependent stages.
- Technical QA accepts only the provider contract geometry/format before semantic review.
- One render invocation is permitted on every public production path. Resume and delivery perform zero encodes.
- Events record episode, revision, scene, agent/stage, provider/model/version, prompt hash, seed, resolution, queued/start/end, worker, attempt/recovery, bytes, cost, result/error and timestamps. Secrets and signed URLs are forbidden.
- RunPod resources use ownership tags, bounded polling/watchdog and guaranteed cleanup. No substring-based orphan ownership.
- Warm resume revalidates hashes and reuses completed work. Ambiguous paid or delivery operations are recover-only and never blindly repeated.

## 6. EP8 editorial contract

- Audience: children 6–10, Brazilian Portuguese, short narratable sentences and a clear beginning/middle/end.
- Scope: Genesis 15:1–6, 17:1–9/15–21 and 18:1–15. Before Genesis 17 use Abrão/Sarai. Isaac is not born in this episode.
- Distinguish biblical facts, original paraphrase, dramatization, omissions, simplifications and human-review points.
- No invented doctrine, fear manipulation, graphic violence, spiritual threats, comments CTA, purchasing pressure or deceptive clickbait.
- Closing: 3–5 seconds with emotional/theological resolution.
- Thumbnail layers: `UMA PROMESSA IMPOSSÍVEL?`, `A promessa de um filho para Abraão e Sara`, `— Gênesis 15–18`.
- Prior rejection feedback is mandatory input: simpler language and a genuinely new, curiosity-led thumbnail composition.

## 7. Provider and budget contract

All providers implement explicit interfaces. Paid media calls require fresh price evidence, exact request authorization, reserve-before-I/O, hard budget enforcement and durable intent before submission. RunPod is off by default; GPU choice is based on current availability, VRAM, price and measured runtime. Any extra spend above the approved plan blocks for specific financial approval. Tests inject deterministic providers and deny network and secrets.

## 8. Acceptance harness

The implementation is complete only when all of the following pass:

1. A synthetic one-line request traverses every stage and stops at each human gate in order.
2. The historical EP8 bootstrap reads rejection evidence and predecessor identities without changing any source byte.
3. Research claims are bound to every script segment; changing research invalidates script approval.
4. Rejection feedback changes successor script and thumbnail identities.
5. Storyboard, character bible, audio QA, technical image QA and visual-freeze approval are required before render.
6. Six rejected baselines produce a bounded concurrent correction wave.
7. Identical content in different paths has the same CAS key and is reused; a one-byte change invalidates it.
8. Exactly one video encode occurs; resume, gate delivery and publication preparation encode zero times.
9. WordBoundary timing at fractional frame rates has ≤1-frame drift.
10. RunPod fake transport proves one submit, checkpoint-before-poll, backoff, recovery-only, timeout and cleanup.
11. Complete metadata and transcript sidecars pass contract validation.
12. Thumbnail, video and publication authorization are three independent hash-bound receipts.
13. TEST/offline harness observes zero network, paid provider, real Telegram, GPU allocation or YouTube calls.
14. The real EP8 dry-run is read-only and reports every missing LIVE prerequisite before spending.
15. Full non-slow suite, focused media suite, deterministic throughput benchmark and CI pass.
