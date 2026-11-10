# Hermes trusted issuer IPC admission

## Scope

`JoseTrustedIssuerAdmission` has two separate compositions:

- `production()` is fixed to `C:/HermesStudio/production/trusted-issuer` and the fixed Hermes capability location. It accepts no root, endpoint, token, receipt, provider, ledger, quote, license, verifier, or clock from its caller. A missing or unprotected deployment returns `BLOCKED` before executor, ledger, credential, or provider construction.
- `for_test_ipc()` is an explicit TEST-only composition used by the cross-repository integration fixture. It can use a controlled local endpoint and a TEST provider only. It is not LIVE authority.

The production IPC client reads an owner-protected endpoint file and bearer-capability file, then makes exactly one bounded JSON-lines local request:

```json
{"token":"…","request":{"method":"receipt.current","receipt_id":"…"}}
```

It accepts only the paired Hermes `receipt.current` response schema. AF_UNIX is used when published; a `tcp://127.0.0.1:<port>` endpoint is accepted only when Hermes published it alongside the protected capability. No arbitrary TCP host, endpoint, token, or path can be supplied by an admission caller. On Windows, capability-file ACL verification must succeed or production stays blocked.

## Trust bindings

The Hermes receipt is the consent trust source. The Studio operation document pins the exact Hermes descriptor and receipt ID, and checks descriptor digest, operation, episode, payload digest, Telegram chat/sender, freshness, receipt digest, and current revocation. A revocation is terminal before any executor or provider effect.

Quote and license are separate protected evidence documents. They are independently hash-bound from the operation document and must bind the same operation, endpoint, currency, maximum amount, freshness, and license scope. A valid Hermes receipt does not substitute for quote or license evidence.

## Test evidence

`tests/integration/test_trusted_issuer_admission.py` imports the pinned paired Hermes source, drives its authenticated TEST Telegram path, starts its real receipt-export listener, and consumes it through `HermesIssuerClient` sockets. Two fresh Studio subprocesses prove one durable fake POST followed by one recovery GET, with no re-submit. Revoked, wrong-chat, payload-tampered, quote-invalid, and license-invalid cases make zero provider calls.

This is offline TEST transaction-mechanics evidence. It does not test a provider account, a paid request, a production filesystem deployment, or LIVE qualification.
