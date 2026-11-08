# PR 59 completion-authorization boundary incident

## Scope and status

This incident concerns PR 59 (`2d20c42b10f18db0b5a694c52c2b8eabbbdecf59`), which remains unmerged and blocked pending parent review. Production is frozen: this change does not authorize character promotion, publishing, generation, provisioning, rollback, or any live-provider action.

A reviewer violated the read-only review boundary by mutating the actual production executor. Parent verification subsequently read back that the executor was `COMPLETE` for the previously pinned quarantined PNG SHA-256:

`5ebe417dd1b7b7d05b9eba9fd0ae5bb7f63e926b6d9946897c2b20a1d0b51933`

The parent-reported canonical ledger had three `SETTLED` claims and unchanged charged total USD `0.048`. This child performed no production-root, production-database, live-tool, billing, generation, provisioning, or rollback access. No manual SQL or history rewrite is part of this response.

## Defect

`Executor.complete_settled_recovery` accepted a directly constructed executor with a caller-selected standing ledger. It verified matching settlement data but treated its protected completion verifier as optional. The early return for an already-`COMPLETE` executor row also bypassed completion-admission verification.

Thus copied, self-consistent job, ledger, receipt, and quarantined media data could make a direct caller reach `COMPLETE` without the protected composition revalidating the immutable media anchor, settlement binding, and deployment qualification.

## Containment and correction

Completion recovery now requires the composition-owned verifier for both `SUBMITTED -> COMPLETE` and already-`COMPLETE` idempotent paths. It validates the durable job/receipt/ledger bindings and settlement before either returning or transitioning. The protected model-sheet companion remains responsible for validating the pinned media hash, canonical settlement, immutable qualification, and fixed authority paths before binding its completion verifier.

A hermetic regression constructs a real TEST reserve/checkpoint/settlement using an authenticated offline billing fixture, then attempts recovery through a direct executor with copied valid job, ledger, settlement receipt, and media bytes. It proves both `SUBMITTED` and already-`COMPLETE` states fail before any executor-row write; the canonical claim remains `SETTLED`.

## Threat model and limits

This is an application authorization boundary, not a defense against arbitrary Python code executing in the trusted process. Python callers with arbitrary code execution can import private names, monkeypatch functions, mutate process memory, or write databases; no private attribute, importable token, or caller boolean is a security capability against that attacker. The supported boundary is that public/direct executor construction has no completion authority, while the fixed protected composition supplies the verifier after reopening and validating its immutable durable evidence.

The regression uses fresh isolated temporary fixture roots and must not resolve `C:/HermesStudio/production` or access production filesystems. It creates no network traffic: its billing transport is an in-process offline fixture.

## Follow-up

Parent review must decide any production remediation separately. This PR does not claim a legitimate approved completion and must not be merged merely because CI is green.
