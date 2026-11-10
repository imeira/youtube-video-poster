# Hermes trusted-issuer admission

The Jose Genesis 37 identity-frame operation uses three independent trust
inputs. None substitutes for another:

- **Hermes receipt trust** proves that Hermes issued a current, unrevoked
  receipt for the exact operation-consent descriptor and authenticated
  Telegram reply. Studio reads that snapshot through Hermes' authenticated
  local `receipt.current` IPC export. The Hermes receipt does not contain or
  authorize a provider price quote or provider license.
- **Financial and quote trust** comes from Studio's protected standing and
  financial policies plus a separately hash-bound, fresh provider quote. It
  binds the exact endpoint, USD ceiling, operation, observation time, and
  expiry. A valid Hermes receipt is not price evidence.
- **License trust** is a separate protected, hash-bound capability for the
  exact episode scope with its own status and expiry. Neither Telegram consent
  nor a quote proves licensing.

Production discovery is fixed at `C:/HermesStudio/production/`. Callers can
provide only the literal operation selector; they cannot supply a root,
endpoint, socket, token, provider, quote, license, or clock. The Hermes bearer
token and published endpoint are read from
`C:/HermesStudio/production/hermes/studio-consent-ipc/`. AF_UNIX is preferred;
the only TCP form accepted is Hermes' protected publication of
`tcp://127.0.0.1:<port>`. On Windows the capability files must pass an effective
ACL check; an unverifiable or broader ACL fails closed.

If the protected production deployment is absent or unprovisioned,
`JoseTrustedIssuerAdmission.production()` reports `BLOCKED`. TEST composition
is explicit and is the only API that accepts fixture paths, a fixture provider
factory, or a fixture clock.

Before executor intent, standing-ledger reservation, provider construction,
POST, and recovery GET, Studio reopens and revalidates the protected documents
and asks Hermes for current receipt/revocation state. The existing durable
Executor and StandingBudgetLedger preserve the provider request ID so a fresh
process performs recovery GET without repeating POST.
