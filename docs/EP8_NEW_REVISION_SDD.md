# New EP8 revision vertical path

Public entry point: `python -m src.hybrid.revision <action> --workspace PATH`.
This route never imports the rejected EP8 packet and never calls publication.
The older production/import routes remain compatibility tools, not this route.

## Immutable inputs and authority

`create-plan --mode TEST|LIVE --request "New EP8: Abraham and Sarah"`
records the request, revision, predecessor evidence, reference bindings, mode,
provider contract and throughput budgets in one atomic control document. Optional
`--predecessors FILE` supplies hashes (and perceptual fingerprints for media);
`--references FILE` supplies an approved canonical Abraham/Sarah manifest.
Rejected frames, narration, video and thumbnail may only occur in lineage.
`approve-plan --plan-hash HASH --reviewer NAME` binds exactly that plan.
Changes require a new revision, invalidating both media approvals together.

## States and execution

`run` (also `resume`) validates approval and completed-stage hashes, then runs
script, independent ScriptQA, injected TTS WordBoundary, semantic storyboard,
canonical references, Director compiled activation, transactional Executor image
dispatch, independent visual QA/promotion, freeze/contact sheet, one local FFmpeg
encode, sidecars/thumbnail, production evidence QA, narrative QA, final ffprobe QA,
and separate auditable Telegram sends. It stops at WAITING_THUMBNAIL_APPROVAL.
`approve --kind thumbnail --artifact-hash HASH --reviewer NAME` opens
WAITING_VIDEO_APPROVAL. The equivalent video approval stops at
WAITING_FINAL_APPROVAL; only the existing separate publication path can publish.
`reject --reason TEXT` atomically supersedes both lineages and creates the next
WAITING_PLAN_APPROVAL revision. Silence never approves. `status` is read-only.

## Recovery and throughput

One workspace lock; atomic stage receipts bind every completed output. A resumed
stage validates hashes rather than regenerating. Executor ambiguity uses its
existing recover-only protocol, never blind provider resubmission. Ambiguous
external sends and interrupted encodes fail closed for explicit reconciliation.
An encode with a durable valid completion receipt may advance state on resume.
Persist image/QA worker limits, FFmpeg threads, one correction wave, optional
heroes disabled, and per-stage elapsed timings. Compile prompts once, prepare QA
packets on completion, and overlap bounded QA with image jobs. No wall deadline.

## Modes and acceptance

TEST uses injected deterministic local script/TTS/image/visual-QA/Telegram
fixtures and real FFmpeg when installed. Fixtures are conspicuously test evidence,
never proof of production provider success. LIVE requires a deployment adapter
with current exact price, request authorization, budget and credentials; absent
dependencies block before a provider call. No legacy fake RunPod fallback.

Negative and CLI end-to-end tests cover stale approvals, tampered inputs, rejected
frame leakage, byte/perceptual reuse, mode isolation, no network in TEST, silence,
both gates, rejection, idempotent resume, crashes, bounded workers and no publish.
Run focused tests and the complete non-slow suite; do not commit.

## Public factory and executable acceptance

Python clients use `studio_factory(workspace, dependencies=...)` as a context
manager and await `run()`. The equivalent installed command is
`studio revision <action>`. `revision.json` is this route's authoritative control
document; the Director's compiled activation is the image-generation handoff.
It does not run the legacy episode/import/publication workflow.

Example (substitute hashes printed by each preceding command):

```powershell
python -m src.hybrid.revision create-plan --workspace C:/Temp/ep8-fresh --mode TEST
python -m src.hybrid.revision approve-plan --workspace C:/Temp/ep8-fresh --plan-hash PLAN_HASH --reviewer human
python -m src.hybrid.revision run --workspace C:/Temp/ep8-fresh
python -m src.hybrid.revision approve --workspace C:/Temp/ep8-fresh --kind thumbnail --artifact-hash THUMB_HASH --reviewer human
python -m src.hybrid.revision approve --workspace C:/Temp/ep8-fresh --kind video --artifact-hash VIDEO_HASH --reviewer human
```

The last action returns `WAITING_FINAL_APPROVAL`, with publication still false.
`reject --reason TEXT` retires both artifact identities and approvals atomically,
retains their history, and requires approval of the successor plan. Retired media
includes intermediate image candidates and narration, not only delivery outputs.

`--predecessors` is a JSON array of `{sha256, perceptual?, video_perceptual?}`
identities. No predecessor paths are opened for production. Still fingerprints
are 16-hex-character difference hashes; video fingerprints are three such
hashes sampled at 10%, 50%, and 90%. A Hamming distance of at most four blocks
near-duplicate stills (or all three video samples). This is a conservative reuse
guard, not proof of artistic originality. `identity(path)` prepares these
identities from explicitly selected files. Unknown predecessor files cannot be
compared; supply the rejection evidence rather than silently inspecting the old
EP8 directory. Exact bytes are checked for every supplied predecessor type.

Canonical reference JSON contains `manifest` (an approved `Manifest` file path),
`characters` mapping `abraham` and `sarah` to its approved image hashes, and a
nonempty `authority`. References and their contact-sheet binding are pinned in
the plan. They cannot contain known rejected final frames.

## LIVE deployment boundary

LIVE additionally requires `--deployment FILE`, `--references FILE`, and an
explicit `--chat-id`. The bundled adapter is `src.hybrid.revision_live:factory`.
Its factory accepts `(current_revision_directory, approved_plan)`, validates
local evidence, and composes `FalFluxProvider`, `EdgeTTSProvider`, and a
`TelegramNotificationProvider` subclass that enforces the approved destination
and refuses oversized video uploads instead of substituting a local-path message.
Construction and preflight make no remote requests, including credential probes.

The versioned deployment schema is
[`revision_deployment.schema.json`](../src/hybrid/assets/revision_deployment.schema.json).
The generated LIVE plan schema is
[`revision_plan.schema.json`](../src/hybrid/assets/revision_plan.schema.json).
Unknown deployment fields, unconfigured values and unsupported contracts fail
closed. The legacy four-field contract remains available for custom injected
adapters; dynamically imported factories now receive both root and plan.

### Preparing an actual LIVE deployment

1. Install the project dependencies, including `fal-client`, `edge-tts`, and
   local FFmpeg/ffprobe. Supply `FAL_KEY`, `TELEGRAM_BOT_TOKEN`, and the dedicated
   `EP8_OPENROUTER_REVIEW_KEY` through the process environment. No `.env` file or
   legacy Telegram destination is read. Preflight verifies local presence and
   SDK availability; it cannot prove server acceptance without network access.
2. Copy [`revision_deployment.example.json`](../src/hybrid/assets/revision_deployment.example.json)
   outside the repository. This is deliberately invalid until every `REQUIRED`
   and zero placeholder has been replaced with independently verified evidence.
   It includes the current script hash, not an invented human approval.
3. Review the new 847-word, 25-segment
   [`ep8_promise_v1.json`](../src/hybrid/assets/ep8_promise_v1.json). Record the
   human editor in `script.authority`. The story uses only Genesis 15:1-6,
   17:1-21 and 18:1-15, stops before Isaac's birth, and labels the closing family
   reflections separately. Visual staging is illustrative, not an extra claim
   about the text. No old script/media is loaded. Both `validate_ep8_script` and
   `ScriptQAAgent` run before TTS. Rejecting this script requires genuinely new
   editorial copy and a newly approved deployment, not a numeric revision label.
4. Prepare a LIVE `Manifest` containing exactly two individually approved
   Abraham/Sarah reference images, an approved contact sheet, and its binding
   file. Supply a references JSON with `manifest`, `characters` (the two exact
   hashes), and `authority`. Use absolute paths. Put the manifest checksum in
   `image_price.manifest_checksum`. Provide all known rejection identities with
   `--predecessors`; do not repurpose rejected frames as canonical portraits.
5. Verify the exact FAL `fal-ai/flux-2/klein/9b/edit` price for these reference
   inputs, one 1280x720 PNG, and the schema's safety settings. `image_cost` and
   `image_price.amount` must be identical decimal strings, including all input
   reference charges. Record the evidence URL, reviewer, observation timestamp
   and expiry (UTC Unix seconds). Evidence may live at most 24 hours and must
   still be valid at each authorization. Preflight never invents or refreshes
   prices. Renewing evidence requires another approved plan.
6. Pin a specific OpenRouter vision model and provider endpoint with image and
   strict JSON-schema support. Record the verified provider context ceiling,
   prompt/completion USD per million token prices, the maximum per-image and
   per-request fees, and reviewer authority. Use the provider's actual nonzero
   image/request ceilings instead of zero placeholders; all three submitted
   images (candidate plus two canonical portraits) enter the reserve. Use a
   dedicated deployment key with an account-side spending limit as an
   additional control. The requests pin `provider.only`, disable fallbacks,
   require supported parameters and send explicit `max_price` limits, following
   [OpenRouter provider routing](https://openrouter.ai/docs/guides/routing/provider-selection).
   The image wire fields follow the
   [FAL edit API](https://fal.ai/models/fal-ai/flux-2/klein/9b/edit/api).
7. Set the Telegram numeric destination and its owner approval, TTS authority,
   and visual-use license. Verify free Edge TTS/Telegram authority explicitly;
   this adapter has no paid TTS fallback or runtime LLM script author.
8. Budget for 25 first-pass images plus up to 10 corrections. Reserve at least
   35 independent reviews at the conservative per-review bound
   `(context_tokens * prompt_per_million + max_tokens * completion_per_million)
   / 1,000,000 + 3 * image_per_item + request`. `35 * image_cost +
   non_image_reserve` must fit the $6 plan.
   A model whose full-context bound does not fit must not be silently substituted.
   Uncertain responses consume their reserve and block for reconciliation.

```powershell
python -m src.hybrid.revision create-plan --workspace C:/Temp/ep8-live --mode LIVE --deployment C:/Deploy/ep8.json --references C:/Deploy/references.json --predecessors C:/Deploy/rejected.json --chat-id "-123456789" --request "New EP8: Abraham and Sarah"
# Review revision.json and every bound input before recording actual approval.
python -m src.hybrid.revision approve-plan --workspace C:/Temp/ep8-live --plan-hash EXACT_PRINTED_HASH --reviewer ACTUAL_HUMAN
# This command performs LIVE creation and both Telegram deliveries.
python -m src.hybrid.revision run --workspace C:/Temp/ep8-live
```

An approved plan can be checked locally without running creation:
`factory(Path(workspace) / "r001", read(Path(workspace) / "revision.json")["plan"])`.
Construction checks credentials without printing them and makes no network calls.
Use the actual revision directory for successor revisions. Never call `run`
while merely testing configuration. Changing pinned files invalidates approval;
start a new workspace/plan with predecessor evidence to renew deployment inputs.

### Runtime dependency contract

The injected interface is:

- `mode`, `endpoint`, `image_cost`, `prior_spend`, and `visual_license`;
  `prior_spend` equals the approved conservative `non_image_reserve`, including
  authoring, TTS, visual review/correction and delivery. The adapter must enforce
  that reserve for its non-image operations. Executor reserves image spend under
  the remaining $6 ceiling transactionally.
- `preflight(plan)` must return true for `credentials`, `script_authority`,
  `tts_authority`, `visual_qa_authority`, `telegram`, `current_prices`, and `budget`.
  It runs before any authoring/TTS/image work on every resume.
- `author_script(plan, research)` and `tts.synthesize(...)` are asynchronous;
  TTS must return exact output path, decoded duration and real WordBoundary
  evidence. Ambiguous LIVE authoring/TTS stages are never blindly repeated.
- `images` implements the existing Executor `Provider` protocol with durable
  checkpoint and recover-only behavior. `authorize(jobs, plan)` returns exact
  request-ID maps of `Authorization` and current `Price` objects. Executor checks
  expiry, endpoint, exact price, capacity and budget before each submission.
- `visual_qa(packet, scene, references)` is asynchronous and independent of
  image generation. It receives the exact candidate path/hash and returns
  `{scene_id, result_sha256, approved, reviewer}`. One correction per rejected
  scene is permitted within the existing ten-job correction reserve.
- `messenger.send_photo(chat_id, path, caption)` and `send_video(...)` return
  durable Telegram message IDs. Each send has a separate persisted intent and
  receipt. A lost response remains blocked; rejection starts a new revision.

The bundled authorizer reconstructs exact jobs from the bound script and real
WordBoundary timeline, checks compilation and manifest identity, and records
unique request IDs in a durable budget ledger. Every call issues fresh `Price`
and `Authorization` objects bounded by the original evidence expiry. The actual
FAL geometry, references and settings enter the request hash before approval;
local compilation provenance is removed only at the wire boundary. Existing
FAL durable queue checkpoints and GET-only recovery remain in charge.

The reviewer receives candidate image bytes, both canonical portraits, narration,
action and source references. It must return a single completed JSON answer with
the exact scene/hash, `PASS` or `FAIL`, and a nonempty explanation. Refusals,
truncation, duplicate fields, unknown model, ambiguous verdicts, missing costs,
overruns and network errors block. The adapter returns a boolean plus explicit
reviewer identity and persists response evidence; no synthetic LIVE approval.
Review intents consume a bounded reserve before I/O. Bounded parallel image
workers and overlapping per-frame QA are unchanged, as are the single encode
and separate media approval gates. This module imports no publication route.

The TEST provider is explicitly synthetic. Its narration tones and abstract
drawings validate contracts, timing, recovery, and media assembly, not production
voice, character resemblance, or editorial quality. LIVE cannot consume TEST
manifests, script evidence or WordBoundary evidence.

## Recovery, QA, and test commands

Each complete stage stores output hashes and elapsed seconds. Resume validates
all earlier receipts before provider activity. A started image stage reconstructs
the same jobs and uses the Executor ledger. Completed QA decisions remain
immutable. Visual review has its own per-request intent and durable decision;
an ambiguous LIVE review blocks, while a saved decision repairs a missing
promotion receipt without another review call. A completed encode receipt binds the compilation, frozen manifest and
output bytes; only this receipt permits recovery past an interrupted stage commit.
An encode without that receipt is blocked. No second encode is automatic.

FFmpeg has one filter worker and one encoder thread. Explicit 30fps normalization
before closing-frame padding avoids truncated visual tails. Both audio and video
stream durations are checked, in addition to container duration and 1080p H.264 /
AAC format. Captions remain sidecars. Production evidence, narrative and final
media QA must all pass before either Telegram delivery.

```powershell
.venv/Scripts/python.exe scripts/run_offline_tests.py tests/unit/test_new_ep8_revision.py tests/unit/test_hybrid_compiled.py tests/unit/test_hybrid_operational_pipeline.py tests/unit/test_hybrid_phased_throughput.py tests/unit/test_production_evidence_qa.py tests/unit/test_production_harness.py -q
.venv/Scripts/python.exe scripts/run_offline_tests.py -m "not slow" -q
```

The CLI acceptance test installs the offline audit in each subprocess too,
blocking network, secret reads, and the protected legacy episode directory. It
runs real FFmpeg, both separate gates, idempotent resume and a full successor
revision. Negative tests inject stale LIVE prices, missing credentials/authority,
budget exhaustion, image crashes, lost Telegram responses, encoder interruption,
tampered bindings, mode mismatch and rejected media reuse. FFmpeg-specific tests
skip explicitly if the local tools are unavailable.
