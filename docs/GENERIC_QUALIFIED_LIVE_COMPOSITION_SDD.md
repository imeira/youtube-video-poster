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

1. `prepare_intent` loads a versioned, closed, repository-fixed operation document before it creates an executor intent or permits a reservation. The document binds literal operation and episode IDs, immutable canonical payload input hash, provider endpoint/kind, license evidence hash, quote identifier and expiry, canonical budget policy hash, authority receipt hashes, and execution-code version.
2. The composition-owned ledger reserves the exact request and scope. A TEST fixture may bridge that reservation through the legacy TEST-only `bind_prepared_standing_ledger_admission`; it never supplies LIVE authority.
3. Before persistence and before `run(..., submit_prepared=True)`, the executor validates and durably writes a versioned generic admission marker that binds the prepared request, exact ledger claim, operation scope, immutable inputs, authority receipts, and execution-code version. The marker is write-once.
4. Immediately before effects, submit and recovery re-open the same fixed configuration and revalidate the marker/claim. A persisted remote ID allows only hash-bound recovery GETs; it cannot create a second reservation or POST.

`LIVE` has no caller-provided callback, dictionary, root, ledger, receipt, provider, private Python capability, credential, quote, license, or verifier grant. Existing Jacó-specific admission remains unchanged and is not reinterpreted as generic authority. Until protected production qualification is provisioned, the production registry remains `BLOCKED` before filesystem effects under a production root, credential construction, reservation, or transport.

## TEST integration and recovery

The fixture uses a versioned static manifest with independently literal SHA-256 pins; it does not calculate approval hashes from candidate runtime bytes. It uses a non-secret local root, a loopback-only fake transport, and a fresh process factory. The asserted path is `prepare_intent → fixture-ledger reserve → durable generic marker → submit_prepared fake POST → remote checkpoint → forced process exit → fresh resolver/executor/fake GET recovery`. The persisted transport journal must prove one POST, one recovery GET, and no second reservation. This is transaction-mechanics coverage only, not LIVE provider, quality, pricing, billing, TTS, media, or readiness evidence.

## Negative acceptance

- Empty or unprovisioned production configuration blocks before any operation/factory/executor/credential effect.
- A malformed operation ID is rejected literally before a protected path lookup.
- Caller dictionaries, callbacks, roots, and private-capability-shaped objects cannot mint LIVE authority.
- A forged marker, copied foreign-ledger claim, mismatched scope/hash, and TEST authority in a LIVE document fail before intent write, credential construction, ledger reserve, or transport.
- The fixture process restart proves one fake POST, one hash-bound recovery GET, and no second reservation; a same-process `COMPLETE` no-op alone is insufficient.
