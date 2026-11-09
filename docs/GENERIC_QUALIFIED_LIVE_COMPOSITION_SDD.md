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

## TEST integration and recovery

`for_static_test()` is a strictly TEST-domain fixture composition. It admits only a `Job(mode="TEST")` and a `mode="TEST"` transport, then calls the existing `Executor.run`. Its local static bytes exercise real durable executor completion and a fresh registry/executor reopen returns the existing COMPLETE receipt without another submit. This is transaction-mechanics coverage only, not LIVE provider, quality, pricing, billing, TTS, media, or readiness evidence.

## Negative acceptance

- Empty production registry blocks before any operation/factory/executor effect.
- The readiness report lists provider, identity, price/billing, license, budget, image-reference, and TTS blockers, and cannot say LIVE is enabled.
- A worker handler resolving the static TEST operation submits exactly one fake job; a fresh-process-equivalent reopen returns its durable receipt with zero additional submit.
