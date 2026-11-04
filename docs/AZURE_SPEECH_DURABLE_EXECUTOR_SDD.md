# Azure Speech durable consumer — SDD

## Delivered TEST integration

`src.providers.tts.azure_speech_durable_executor.AzureSpeechDurableConsumer`
consumes, rather than replaces, the existing durable components:

1. `AuthenticatedBudgetBridge.admit_executor_intent()` revalidates the
   authenticated TEST budget receipt, persists the existing `Executor` intent,
   and binds that exact executor request ID to the only financial claim. The
   consumer does not accept a caller-created ledger, cap, policy, or
   authorization.
2. A stable operation ID must equal the durable executor request ID. It is
   guarded by an OS-released lock and a fsynced, write-once
   `azure-operations/<request_id>.json` intent. That intent binds the exact
   Azure endpoint identifier, approved voice, SHA-256 of UTF-8 script bytes,
   exact quoted maximum, and contained absolute output path.
3. Constructor state/output roots and each output path are absolute,
   reparse-safe, and output-contained before an intent or adapter factory can
   be reached. The fixture transport is constructed lazily only after bridge
   admission.
4. The existing `Executor` owns its own durable submission intent and provider
   checkpoint. The adapter fixture reaches the actual Azure-adapter SDK-shaped
   boundary (`OfflineAzureSpeechFixture.synthesize`), not a preflight-only
   branch.
5. Authenticated-bridge admission is an explicit two-database saga, not an
   atomicity claim: receipt-bound `PREPARED` executor intent → canonical ledger
   `RESERVED` claim → executor `standing_ledger_admission` marker. The marker
   binds request ID, endpoint, authorization scope, TEST mode, reserved amount,
   and the immutable persisted authorization receipt/policy binding. `Executor.run`
   reopens the configured ledger and revalidates that receipt via its configured
   trust source immediately before `PREPARED → SUBMITTING`. Raw legacy TEST
   intents remain explicitly `TEST_LOCAL_ONLY`; they cannot forge bridge scope,
   write this marker, or promote to LIVE.
   A stop before reserve leaves only `PREPARED`; a stop after reserve and
   before marker leaves `PREPARED` plus the canonical `RESERVED` claim. Both
   are fail-closed and reconcile idempotently by replaying the same reserve
   then writing/verifying the marker. They are not cross-DB atomic and never
   imply that the reservation was rolled back.
6. A timeout or cancellation after the durable submit boundary marks the
   authenticated bridge claim `UNKNOWN` while retaining its reservation. A new
   process observes that state before fixture transport construction/submission
   and raises `SubmissionUnknown`; it never sends a second request.

The fixture transport is explicit TEST-only. It supplies injected non-silent
WAV bytes through `OfflineAzureSpeechFixture`; no Azure SDK package,
credentials, endpoint, account, network call, GPU, or paid provider action is
used.

## Tested contract

`tests/unit/test_azure_speech_durable_executor.py` proves:

- authenticated bridge admission plus durable executor completion reaches one
  fixture SDK-boundary call and produces audio;
- relative/outside output roots and a native Windows junction are rejected
  before intent writes or transport construction;
- a reused canonical request rejects endpoint/output/voice/script/price swaps
  before another adapter call;
- cancellation or timeout after dispatch marks the authoritative TEST claim
  `UNKNOWN`, and a fresh spawned process makes zero fixture/factory calls rather
  than resubmitting;
- two fresh spawned processes sharing a canonical request produce two COMPLETE
  readbacks but exactly one transport factory/submission.
- fresh spawned fault cases for ledger reserve and the reserve→marker crash
  window make zero factory/SDK calls, retain only their honest durable
  intermediates, and restart through one idempotent claim and one submit;
- a raw prepared intent cannot forge a bridge scope or marker; legacy TEST
  admission remains executable only under its honest `TEST_LOCAL_ONLY` scope.

## LIVE status: blocked

This is **not Azure LIVE activation**. The consumer only accepts `Job.mode ==
TEST`, the existing authenticated budget authority intentionally supports only
TEST, and its ledger settlement contract is named for authenticated FAL billing
readback. Azure LIVE has no configured authenticated price/billing readback or
provider request-ID recovery adapter in this repository.

Before a human can activate LIVE, all of these must exist and be independently
reviewed:

1. Azure subscription/resource and production-network endpoint in a region
   currently supporting `pt-BR-ThalitaNeural`.
2. Production deployment configuration for the Azure endpoint/key, read only
   after a durable LIVE admission; neither secret belongs in source, tests, or
   an artifact.
3. A fresh selected-region Azure Speech price quote for the precise SKU and
   character count, plus a trusted externally authenticated LIVE budget
   authority with an explicit USD cap and Azure provider allowlist.
4. Current Azure terms/licensing approval for the intended narration use.
5. An authenticated Azure request identifier, price/billing readback, and
   recovery contract that can settle or retain `UNKNOWN_0_OR_1` without another
   synthesis POST.
6. A production durable executor/ledger admission extension that supports this
   Azure settlement contract (not a caller-supplied budget and not the
   TEST-only FAL snapshot bridge), followed by an approved single-operation
   canary and independent audio/word-boundary QA.

Until those prerequisites exist, LIVE must remain blocked before Azure SDK
construction, credential access, output mutation, or provider network calls.
