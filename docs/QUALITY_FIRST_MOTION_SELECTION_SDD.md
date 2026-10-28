# Quality-first motion selection SDD

## Scope

This contract selects an offline, deterministic motion route for each compiled storyboard scene. It performs no provider construction, GPU activation, network request, budget reservation, or media dispatch. RunPod is only a compute location; its presence cannot raise a route's quality rank.

## Immutable inputs

`select_motion_inventory` receives a complete semantic scene list and explicit route records. A scene declares `motion_requirement` (`NONE`, `CAMERA`, or `ARTICULATED`) and, except for `NONE`, a nonempty semantic `motion_reason`. A hero scene additionally declares a nonempty `hero_reason`; hero status alone never requires generative video. Audio timing remains external: this contract does not create or change scene duration, clip duration, or narration timing.

Each route declares a stable `route_id`, `route_kind` (`LOCAL_STILL` or `I2V`), explicit endpoint, model, positive width/height geometry, and an explicit evidence mode. An I2V route also declares exact integer `clip_fps`, a finite positive non-boolean `duration_s`, and an audio policy (`REQUIRED` or `FORBIDDEN`). Its duration is part of the closed clip contract, qualification record, route-evidence hash, selected inventory, job payload/request identity, and pre-render comparison; it is never inferred from a universal production default. It carries three independent qualification dimensions: identity, scene adherence, and temporal stability. Each dimension has a PASS verdict, finite 0–1 score, and 64-character evidence hash. The route-evidence hash is recomputed from those immutable values, geometry, clip contract, and closed price evidence. Only local `TEST` fixtures qualify today: a caller-authored `LIVE` record, including one with valid-looking hashes, is `REQUIRED` until a trusted evidence adapter is installed. `LOCAL_STILL` can satisfy `CAMERA`; only `I2V` can satisfy `ARTICULATED`.

Route price is optional evidence, never inferred. A selected paid route must preserve a closed TEST fixture with finite non-negative USD amount, provenance, route ID, endpoint, model, geometry, selected I2V duration where applicable, and evidence hash bound to that route; otherwise it produces `REQUIRED` with `PAID_PRICE_UNKNOWN`. It must not become a fake PASS and must not be replaced by a lower-quality local path. A qualified zero-cost route remains eligible without price evidence.

Inventory validation derives aggregate `PASS` only when every compiled scene has exactly one complete `SELECTED` item. It rejects duplicate/missing scene IDs, incomplete selected route fields, route-evidence mismatch, and any re-digested `PASS` inventory containing `REQUIRED`. Validation runs at binding, operational construction, dispatch, readiness, and rendering.

Before semantic QA or promotion of an opt-in articulated hero, the pipeline ffprobes and fully decodes the exact receipt bytes with FFmpeg `-xerror`. It always fully decodes the selected video stream and, for `REQUIRED`, separately fully decodes the one selected audio stream; an audio decode error rejects even when stream count and probe metadata look valid. `FORBIDDEN` requires no audio stream and never invokes an audio decode. It binds clip hash, one video stream, selected geometry, selected-route duration within one frame, selected FPS, audio policy, and the approved baseline predecessor. PNGs, truncated files, corrupted required AAC transport, duration/geometry/FPS mismatch, predecessor mismatch, and missing approved I2V clips cannot promote or render. Legacy hero behavior remains unchanged unless a quality-first inventory opts in.

## Closed outputs

The inventory is a sorted list by scene ID. Every entry is one of:

* `SELECTED`: route ID, endpoint, model, geometry, route/evidence hash, requirement, semantic reason, and deterministic quality tuple are persisted.
* `REQUIRED`: no eligible qualified route, or a paid selected route lacks price evidence. It includes a closed reason and no executable fallback.

Selection ranks eligible qualified routes by the lexicographic quality tuple `(identity, scene_adherence, temporal_stability)`, descending, then by `route_id` ascending. Provider name/location is not part of rank. This makes a tie deterministic without claiming any provider is universally better.

## Compiled bridge and persistence

`compile_storyboard(..., motion_routes=...)` opts into this contract only when routes are supplied. It rejects a mixed/partial motion contract rather than silently treating missing motion as local camera motion. It writes a `quality_first_motion` record to `CompiledEpisode.source_binding`, the existing generic packet binding. The record contains the exact inventory and its SHA-256-style digest. `CompiledEpisode.checksum` therefore binds the selection. Legacy compiled packets remain valid when this optional binding is absent; they are explicitly `legacy_unbound` and cannot be represented as quality-first selection.

`OperationalPipeline` validates the binding hash, exact compiled-scene coverage, and TEST mode before reusing the existing executor. Any `REQUIRED` item blocks baseline release, dispatch, readiness, and renderer-scene construction. This uses the existing executor and renderer only; no second writer is introduced. The Director façade accepts an explicit `motion_routes` opt-in and closed `{"mode":"TEST"}` policy and passes both through compilation. Absent opt-in remains legacy-compatible.

For a selected articulated scene, the existing hero/clip job is an I2V job whose endpoint, model, geometry, duration, evidence hash, and TEST mode are embedded in the payload and therefore request identity. A caller endpoint mismatch is rejected. Render inputs require the approved clip from that exact job and compare the same duration contract; an articulated scene never reaches the still-image pan fallback.

## Failure and recovery

Malformed scenes/routes, a missing hero reason, absent qualification evidence, incorrect inventory hash, incomplete/tampered coverage, wrong mode, articulated motion without an I2V route, endpoint override, missing approved I2V clip, and unknown price on a paid chosen route fail closed. `REQUIRED` records are audit outcomes, not approvals. A successor compilation with corrected immutable inputs is required; no existing inventory is mutated.

## Acceptance tests

Offline tests cover: hero without reason; missing scene motion contract; provider route with missing qualification dimension; missing identity evidence; invalid/missing/non-finite/non-positive/boolean I2V durations; two valid durations with distinct route hashes, compilation hashes, and hero request IDs; caller-supplied LIVE qualification remaining blocked; articulated motion refusing a local pan/zoom substitute; a fully explicit TEST evidence fixture selecting deterministically without provider invocation; price-unknown paid route becoming `REQUIRED`; REQUIRED blocking baseline/render before a provider call; selected endpoint override rejection; full required-video and required-audio decode including deterministic corrupted-AAC transport rejection; compiled save/load/pipeline persistence; Director propagation; and legacy packet compatibility.
