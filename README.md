# Hybrid AI Animation Studio

## Offline EP8 finishing

Import the current EP8 authority into a separate workspace without changing the
episode directory or generating media:

```text
studio offline import-ep8 --source-root C:/HermesStudio/episodes/EP8_PROMISE_SON_20260901 --workspace delivery --dry-run
studio offline import-ep8 --source-root C:/HermesStudio/episodes/EP8_PROMISE_SON_20260901 --workspace delivery --source-revision REVISION_FROM_DRY_RUN
```

The dry run writes nothing and reports `VERIFIED` only after checking all 39
current state-bound frames, the visual freeze, narration manifest, script,
contact sheet and timeline. The old cached import report is not authoritative.
The build requires that exact source revision and writes `compiled.json`,
`manifest.json`, `copy.json` and a copy of the existing contact sheet under the
returned `packet` path. No provider, publishing, generation or approval occurs.
Use those three JSON paths with the coordinator commands below and the reported
closing hold. Source changes invalidate plan preparation and delivery decisions;
rerun the dry run and import the new revision, then obtain a new plan receipt.
Existing packets are never overwritten. The source root must remain available
for verification; output workspaces inside it (including resolved links) are rejected.

`studio offline` (or `python -m src.hybrid.offline`) consumes an existing
`CompiledEpisode` JSON, an approved `Manifest` JSON in compiled frame order,
and a `ThumbnailContract` JSON. All inputs must already have been reviewed.
It uses local FFmpeg and Pillow only, copies approved audio without transcoding,
does not burn captions, and has no publication or provider operation.
The current offline route requires contiguous still-image windows; hero jobs
must be resolved outside this route. EP8's exact title and subtitle are enforced.

```text
studio offline approve-plan --workspace delivery --compiled compiled.json --manifest manifest.json --copy copy.json --reviewer HUMAN
studio offline prepare --workspace delivery --compiled compiled.json --manifest manifest.json --copy copy.json --approve-plan RECEIPT_ID
studio offline status --workspace delivery
studio offline approve --workspace delivery --kind thumbnail --revision REVISION --sha256 THUMBNAIL_HASH --reviewer HUMAN
studio offline approve --workspace delivery --kind video --revision REVISION --sha256 VIDEO_HASH --reviewer HUMAN
```

`approve-plan` records an explicit human decision; `prepare --approve-plan`
only references that persisted receipt and cannot create one. Its binding
includes the compilation, manifest, exact thumbnail copy and closing hold
(`--hold`, default 4 seconds). The legacy resume path also requires a persisted
plan receipt, created through `src.approval.receipts.record_plan_approval` after
human review; its boolean CLI flag alone grants nothing.

Use `reject` with the same kind/revision/hash arguments and `--feedback TEXT`
to retire both active outputs atomically. Historical receipts remain immutable
in `delivery.sqlite3`. Correct the inputs or local rendering options, obtain a
new plan receipt, and prepare again: both successor outputs must have different
hashes and paths and pass both gates again. Unchanged regeneration is refused.
Restarting with the same active inputs reuses the verified output pair. Failed
renders can leave unreferenced revision files but never advance a gate.
`READY_FOR_PUBLICATION` records human acceptance only; this coordinator cannot
publish. Keep the SQLite ledger with its revision directory for resumption.

Fábrica local-first para episódios bíblicos infantis em pt-BR, destinada a crianças de 6–10 anos.

## Princípios operacionais

- A passagem bíblica é a fonte narrativa; paráfrases, inferências e reflexões são identificadas separadamente.
- Nenhum vídeo é publicado sem aprovações hash-bound independentes de thumbnail e vídeo, seguidas de comando explícito e separado de publicação.
- Imagens finais são referência/edição; áudio aprovado, frames e recibos são imutáveis.
- Custos, leases, QA e recuperação de provedores seguem os contratos em `docs/`.

## Verificação local

The fresh EP8 revision route now has a concrete LIVE adapter:
`src.hybrid.revision_live:factory(root, plan)`. It composes FAL image editing,
pt-BR Edge WordBoundary TTS, independent OpenRouter visual review, and separate
Telegram thumbnail/video delivery. Publication remains unreachable.

Follow the [LIVE deployment procedure](docs/EP8_NEW_REVISION_SDD.md#preparing-an-actual-live-deployment)
before creation: review and pin the new script and canonical references, provide
fresh exact price evidence and a bounded budget, configure the three environment
credentials, create a LIVE plan, then explicitly approve its exact hash. Start
from the deliberately incomplete
[deployment template](src/hybrid/assets/revision_deployment.example.json) and
[JSON schema](src/hybrid/assets/revision_deployment.schema.json). No credentials,
default prices, reviewer model, destination, or human approval are supplied.
`run`/`resume` start provider work; construction/preflight are local checks.
The first run freezes both media, sends only the thumbnail and waits for its exact
approval. Thumbnail approval enters `READY_VIDEO_DELIVERY`; a later `resume`
sends the frozen video, then waits for its independent approval. Video approval
stops at `WAITING_FINAL_APPROVAL`, with no publication action available.
Plans bind implementation bytes. Final encoded audio must measure -16 LUFS ±1 LU
and <= -1 dBTP, with no subtitle streams or burned captions.

```powershell
.venv/Scripts/python.exe scripts/run_offline_tests.py tests/unit/test_revision_live_adapter.py tests/unit/test_new_ep8_revision.py -q
```

```bash
uv run --extra dev pytest tests/unit -m "not slow" -q
```

Não coloque credenciais em arquivos versionados. Copie `.env.example` para um ambiente seguro e informe apenas o status `CONFIGURED`/`NOT CONFIGURED` em logs ou relatórios.
