# EP8 local production harness

Run with `studio production` (or `python -m src.cli.main production`). Every
action returns JSON. No command calls a provider, notification service, or
publisher. Source and output must be separate directories.

```powershell
studio production plan --source-root C:\episodes\EP8 --workspace C:\runs\EP8-r1
studio production approve-plan --workspace C:\runs\EP8-r1 --plan-hash <plan_hash> --reviewer <human>
studio production run --workspace C:\runs\EP8-r1
studio production status --workspace C:\runs\EP8-r1
studio production deliver --workspace C:\runs\EP8-r1
```

`plan` accepts `--episode-id`, `--theme`, `--language`, and `--channel`; defaults
select the EP8 Portuguese contract. It imports the existing EP8 adapter packet,
pins every packet file, runs local biblical research and script QA, and records
the immutable request and plan in `production.sqlite3`. It never creates a human
approval. `approve-plan` requires the exact reported plan hash and reviewer.

The source must satisfy `Ep8OfflineAdapter`: 39 approved 1080p RGB PNG frames,
state-bound manifests, contiguous semantic timing, approved audio/script hashes,
and an approved visual freeze. Each storyboard frame additionally supplies
`narration_text` (or `narration_phrase`) and `source_ref`. Supported references
are `Gênesis 15:1-6`, `Gênesis 17:1-9`, `Gênesis 17:15-21`, and
`Gênesis 18:1-15`. Joined phrases must match the approved plain-text script
after whitespace normalization. The harness reuses the source's per-frame
visual approvals and freeze as canonical-character authority; it does not
pretend that technical image checks establish character identity.

Missing approved stills, narration, or semantic caption data return
`ASSETS_REQUIRED`. This implementation has no local still or TTS generator;
it never substitutes fabricated assets or invokes an online fallback.

`run` performs one bounded FFmpeg H.264 encode at 1920×1080/30 fps, concatenating
semantic motion segments in one filtergraph. AAC is a derived output; approved
source audio bytes remain unchanged. The closing hold comes from the approved
packet (3–5 seconds). Captions are separate `captions.srt` and `captions.vtt`
files, with `transcript.txt`, `metadata.json`, and `performance.json` beside
`video.mp4`. FFprobe checks codecs, dimensions, and duration before QA passes.

`deliver` copies that verified MP4 into the existing offline coordinator and
composes the thumbnail with Pillow; it does not encode again. Use the returned
revision and each artifact's own hash with the existing gate commands:

```powershell
studio offline approve --workspace C:\runs\EP8-r1\delivery --kind thumbnail --revision <revision> --sha256 <thumbnail_sha256> --reviewer <human>
studio offline approve --workspace C:\runs\EP8-r1\delivery --kind video --revision <revision> --sha256 <video_sha256> --reviewer <human>
```

Video approval requires thumbnail approval first. `offline reject` requires
`--feedback` and atomically retires both artifacts. Production status reports
`REJECTED`; delivery cannot reactivate the pair. A revised source and new
workspace are required. `READY_FOR_PUBLICATION` conveys eligibility only;
publication remains out of scope.

Operation keys derive from the plan's input hashes, source revision, stage and
route. SQLite events reject updates/deletes; an OS lock serializes workspace
operations. Completed outputs are hash-verified on every resume. Missing or
changed checkpoints fail closed. A crash after an encode starts but before QA
is durably committed leaves `STARTED`; no automatic second encode occurs.
Keep that workspace for inspection and use a new workspace for recovery.

Offline validation (with FFmpeg/ffprobe on PATH):

```powershell
.\.venv\Scripts\python.exe scripts/run_offline_tests.py tests -q
```
