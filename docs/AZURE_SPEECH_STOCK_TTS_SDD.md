# Azure Speech Stock TTS — Jacó vertical slice SDD

## Scope

This slice adds `AzureSpeechStockTTSProvider` at the existing `TTSProvider`
boundary. It supports only the stock Microsoft voice `pt-BR-ThalitaNeural` and
records the exact submitted transcript, Azure `WordBoundary` offsets and
durations (100-ns ticks), WAV duration, output SHA-256, and stock-voice licence
metadata in the returned receipt.

It does **not** select this adapter in Director, autonomous, or CLI composition.
It does not synthesize cloud audio in this change.

## Modes and transport

- `TEST` requires `OfflineAzureSpeechFixture`; it writes a prebuilt valid,
  non-silent PCM WAV. TEST never invokes the SDK factory and cannot be changed
  to LIVE.
- `LIVE` is explicitly **BLOCKED before SDK factory construction, credential
  access, output-directory creation, or any provider call**. `BudgetGuard`, a
  caller cost estimate, an operation ID, and an in-memory dictionary are not
  authenticated financial authority and cannot reserve spend durably across a
  restart. This slice deliberately does not wire a fake LIVE path.
- The adapter has no worker thread or automatic retry path. Since LIVE cannot
  cross preflight, task cancellation cannot leave an unowned SDK thread or a
  post-cancellation submit. A future LIVE executor must own and retain its
  submit thread through unwind and persist `UNKNOWN` before propagating a
  timeout, cancellation, or provider exception.
- The Python package `azure-cognitiveservices-speech` is intentionally not a
  project dependency. Install it only in this worktree's existing `.venv` when
  an approved production activation needs it. No weights, accounts, or lockfile
  changes belong to this slice.
- `reconcile()` reports that LIVE is blocked; it does not manufacture a local
  operation record. When a durable executor exists, timeout, cancellation, and
  provider exceptions must persist terminal `UNKNOWN_0_OR_1`/`UNKNOWN` state
  before control returns. Recovery must use authenticated provider/billing
  readback and may never resubmit automatically.
- TEST remains usable and non-promotable. It writes only injected local fixture
  WAVs, validates a reparse-safe output path before creating directories or
  files, and never constructs the Azure SDK.

## Exact external prerequisites for a future LIVE activation

1. An Azure Speech resource in a region that currently supports both Speech TTS
   and `pt-BR-ThalitaNeural`; validate it against the official supported-regions
   and voice-availability documentation before approval.
2. The exact resource endpoint must be available from the production network and
   bound to the same resource as its configured credential. A future admitted
   executor may read `AZURE_SPEECH_ENDPOINT` and `AZURE_SPEECH_KEY` only after
   its durable reservation; this blocked adapter reads neither value. Neither
   values nor account configuration are stored in repository artifacts.
3. A fresh, region-specific official Azure price quote for the chosen SKU and
   character count, plus a configured independently authenticated budget
   authority. A caller cost estimate is not authority.
4. A current provider-terms review authorizing the intended production narration
   use. The result metadata declares `license_status=PROVIDER_TERMS_REQUIRED`; it
   is not a claim that a local fixture grants a licence.
5. A reusable existing durable executor/ledger integration that, under a
   multi-process operation claim lock, atomically writes and fsyncs immutable
   intent + operation hash + reservation before SDK construction. It must reopen
   and revalidate that reservation immediately before submit, bind the exact
   output path/voice/text/cap/provider, allow only one transition from local
   `UNASSIGNED` to a validated provider ID, and sanitize all persisted errors.
6. Explicit operator approval of a single production operation and a documented,
   authenticated Azure readback/reconciliation procedure for `UNKNOWN` outcomes.

## Pricing evidence and regional caveat

Azure's official Speech pricing page states that Text to Speech is billed per
character. On the official Australia pricing page retrieved **2026-10-29**, the
pay-as-you-go `Text to Speech / Standard Voice / Neural/Neural HD` entry showed
**US$15 per 1M characters**; the same page separately showed a **US$12 per 1M
characters overage** for a specific 80M-character commitment tier. The latter
is not treated as a universal rate. Azure pricing pages are region/offer/currency
specific, and the operator must capture the current selected-region quote before
setting any cap.

- Pricing: <https://azure.microsoft.com/en-au/pricing/details/cognitive-services/speech-services>
- Supported regions/endpoints: <https://learn.microsoft.com/en-us/azure/ai-services/speech-service/regions?tabs=tts>

## Receipt contract

Successful output includes `metadata.boundary_receipt`:

```json
{
  "provider": "azure-speech",
  "voice": "pt-BR-ThalitaNeural",
  "text": "exact submitted transcript",
  "audio_sha256": "sha256 of returned WAV bytes",
  "word_boundaries": [
    {"word": "…", "offset_100ns": 0, "duration_100ns": 0, "start": 0.0, "end": 0.0}
  ],
  "duration_seconds": 0.0
}
```

The `license` metadata has the closed fields `license_kind`, `voice`,
`license_status`, and `allowed_use`. It identifies a prebuilt stock-voice
fixture/provider declaration without copying any secret, endpoint, key, account,
or raw provider response into the receipt.
