# SDD — standing budget reservation ledger

## Contract
A shared SQLite ledger serializes reservations across workers and episodes. Policy SHA-256, currency USD, mode and episode/month/experiment ceilings are immutable per database. A database must be located at the studio level, not under each episode. This ledger is an additional admission guard, not provider billing evidence and not a replacement for the transactional Executor.

`reserve(request_id, episode_id, provider, quoted_cap_usd, experiment=False)` records a durable reservation before any network effect. UTC calendar month is taken from the ledger clock. Admission checks episode lifetime exposure, calendar-month exposure and experiment subset exposure in that month within one BEGIN IMMEDIATE transaction. Monetary values are exact integer micro-USD; unknown, negative, nonfinite and excess-precision amounts are rejected.

Exposure is reconciled actual spend for SETTLED rows and reserved maximum for RESERVED/UNKNOWN rows. Unresolved reservations from prior months also consume admission capacity in the current month until reconciled; calendar rollover never erases uncertain liabilities. Reporting keeps compatible original-booking fields (`month_usd` and `experiment_usd`) and additionally reports `carried_liability_usd` plus `current_month_effective_usd`; the latter is exactly the current booking-month exposure plus unresolved prior-month liability used by monthly admission. A retry with identical request inputs returns the existing claim; different inputs under the same ID fail. Missing cost never becomes zero. Repeated UNKNOWN remains reserved. `settle` is idempotent for identical actual amounts; a conflicting second actual is rejected. Actual provider spend exceeding reservation is recorded as reality, sets a durable overrun freeze and blocks new reservations rather than discarding the bill. No automatic release/cancel exists: ambiguous calls cannot reclaim headroom without provider reconciliation.

## Recovery and scope
SQLite WAL + FULL synchronous + transaction rollback provide concurrency/crash safety. No secrets or signed URLs are persisted. Existing metadata mismatch fails closed; no method changes caps or clears an overrun. TEST and LIVE databases are disjoint by mode. Absence of policy/limits blocks construction. No production authority is created by constructing this ledger; callers must validate external authorization and reserve before the existing Executor boundary.

## Tests
Vertical RED/GREEN: cumulative admission; two workers racing for last capacity; monthly aggregation across episodes; lifetime episode total; experiment subset; unknown cost; ambiguous pending exposure; retry identity; policy drift; exact micro-USD; immutable actuals; overrun durable freeze; UTC month rollover; carried liability and effective current-month exposure before and after reconciliation.

## Integration boundary
Expose ledger reservation/settlement to production coordinator before paid submit and after receipt reconciliation. A module-level test PASS is not a claim that all legacy callers use it. LIVE remains disabled in the new runner until reservation and receipt projection are wired and tested end-to-end.
