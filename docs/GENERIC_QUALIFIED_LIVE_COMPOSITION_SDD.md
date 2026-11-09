# SDD — generic qualified LIVE composition admission

## Scope

`GenericLiveCompositionRegistry` is the one generic connection from a durable worker handler to an existing `Executor`. It does not create a runner, provider, price source, budget ledger, credential reader, billing caller, or publication path. A caller may supply only an operation ID to `admit`; it cannot supply a provider, root, callback, quote, license string, or authority.

The production registry is fixed and currently empty. Thus an unqualified operation fails before executor construction, credential access, intent/reservation, provider POST/GET, billing, media generation, TTS, GPU, delivery, or publication. No TEST receipt is represented as a provider receipt or LIVE qualification.

## Inputs and outputs

- Immutable operation ID resolved against composition-owned registry entries.
- Registered operations own an existing executor and use its durable intent, receipt, idempotency, and recovery behavior.
- `production()` reports no eligible operations. `admit()` raises `LiveCompositionBlocked` for an absent entry.
- `readiness()` is an operator report, not enablement: it names the exact missing independent bindings and returns `live_enabled: false`.

## Required bindings before a production entry may be provisioned

1. Provider qualification and independently attested operator identity.
2. Request-bound USD quote plus authenticated provider billing readback.
3. Independent license evidence.
4. Canonical budget authority/ledger admission accepted by the existing executor.
5. Image-reference capability.
6. A real TTS method; Azure is not selected or implied.

The normal thumbnail and final-video human gates remain unchanged. Production and publication remain frozen.

## Admission contract and state chain

The existing `Executor` remains the sole submission kernel; this composition does not add a runner or a parallel authority path. A generic operation is admitted only through this sequence:

1. The generic factory builds a `QualifiedGenericJob` whose versioned request identity is a canonical hash of its declared generic contract: operation ID, episode ID, ledger-policy SHA-256, and the complete inherited job inputs. Ordinary `Job` request identities are legacy-compatible and unchanged. Before `prepare_intent` can mutate an executor row, and before generic binding can read a claim or write a marker, the executor reopens the fixed closed document and compares its literal root-bounded `executor_database` and `ledger_database` paths against the actual `Executor.database` and attached `StandingBudgetLedger.path`. Both sides reject symlinks/reparse points and compare canonical paths, so a byte-identical copied database or a caller-selected alternate ledger cannot become authority. Generic admission accepts only the concrete canonical `Executor`, whose database path is immutable after construction. The fixed, closed operation document binds literal operation and episode IDs, mode, endpoint, category, input kind, canonical payload hash, maximum cost, and the ledger-policy SHA-256. The currently implemented document does **not** claim a provider model, quote, license, or independent LIVE authority that it does not contain.
2. The composition-owned ledger reserves the exact request and scope. The marker captures the full closed canonical claim record, the configured ledger binding/path, currency, exact amount, mode, settlement mode, authorization scope, document hash, operation-policy hash, and payload/input binding. It rejects unknown keys, wrong types (including boolean-as-integer substitutions), altered policy/scope/mode, and a ledger source other than the one attached to the executor.
3. Before marker persistence, the fixed document operation ID must equal the intrinsic generic request and prepared-row origins; a caller cannot substitute another closed document with the same job inputs. This comparison precedes canonical-ledger claim access, so a rejected foreign operation does not create or mutate a reservation or marker. The marker is write-once.
4. Immediately before effects and before recovery GETs, the executor reopens the same fixed document and canonical attached ledger, revalidates every marker binding, and requires a remote executor identity to match its durable settlement binding. A persisted remote ID allows only recovery; it cannot create a second reservation or POST.

`LIVE` has no caller-provided callback, dictionary, root, ledger, receipt, provider, private Python capability, credential, quote, license, or verifier grant. Existing Jacó-specific admission remains unchanged and is not reinterpreted as generic authority. Until protected production qualification is provisioned, the production registry remains `BLOCKED` before filesystem effects under a production root, credential construction, reservation, or transport.

## TEST integration and recovery

The fixture uses a non-secret local root, an offline controlled transport, and a fresh process factory. The asserted path is `prepare_intent → fixture-ledger reserve → durable generic marker → submit_prepared fake POST → remote checkpoint → forced process exit → fresh resolver/executor/fake GET recovery`. The persisted transport journal proves one POST, one recovery GET, and no second reservation. A paired foreign-document test supplies two valid closed documents with identical job inputs, episode, and policy but distinct operation IDs; cross-binding and tampered recovery perform zero POST/GET and leave the durable marker/claim unchanged. This is TEST transaction-mechanics coverage only, not LIVE provider integration, quality, pricing, billing, TTS, or readiness evidence. Generic LIVE execution remains hard-blocked because no production generic qualification/composition is installed.

## Negative acceptance

- Empty or unprovisioned production configuration blocks before any operation/factory/executor/credential effect.
- A malformed operation ID is rejected literally before a protected path lookup.
- Caller dictionaries, callbacks, roots, and private-capability-shaped objects cannot mint LIVE authority.
- A forged marker, copied foreign-ledger claim, foreign operation document, mismatched scope/hash, and TEST authority in a LIVE document fail before marker write, credential construction, ledger mutation, POST, or recovery GET.
- The fixture process restart proves one fake POST, one hash-bound recovery GET, and no second reservation; a same-process `COMPLETE` no-op alone is insufficient.
