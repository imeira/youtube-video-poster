# SDD — fail-closed LIVE preflight boundary

## Current state: preflight-only, not qualified for execution

`LiveExecutionService` has no provider factory, transport selector, provider import,
reservation, settlement, or call to `Executor.run`. `dispatch()` first revalidates the
write-once preflight hash and exact job binding, then always raises `PermissionError`.
There is no environment flag, policy field, test builder, or package configuration that
can turn this into a dispatch path.

This is intentional. The repository has no existing externally administered signature
verification mechanism for LIVE trust roots/independent reviews and does not integrate
the standalone GET-only FAL billing consumer into a durable authenticated receipt,
reservation, or settlement path. Its transport readback is informational and does not
establish authorization or qualification. Hashes produced by this package are integrity
labels, not authority. The legacy `FalFluxProvider` is not changed or selected by this
service.

## Inputs and persistence

The constructor requires an absolute, non-link `trust_root` outside the package. Its
closed `live-trust-root.json` contract is deliberately limited to
`verification: "UNAVAILABLE"`; claiming a local root is verified is rejected. Identity
and assets QA must be separate persisted regular files below that root, use closed
schemas, bind the exact request and frozen manifest, and name distinct reviewers. Their
bytes are hashed only to retain lineage; independence remains explicitly unverified.

Policy schema 2 and quote schema are closed. A quote is only a **reserved maximum**:
it must bind endpoint and maximum, and expires in at most 300 seconds. The preflight
records `actual_cost_usd: "UNKNOWN"` and `billing_status: "UNAUTHENTICATED"`; it never
maps `Job.cost` or a provider result to actual cost. Evidence is bounded by depth,
items, keys, strings and total serialized bytes, and rejects credential-like keys and
Bearer/basic/API-key-like values before persistence.

Preflight uses a write-once path and readback. Its self-hash covers every other field.
Dispatch rehashes it and validates the request/endpoint/manifest binding before the
unconditional deny, so a later execution implementation cannot consume a changed
preflight unnoticed.

## Required work before any LIVE implementation

1. Add an external, configured trust-root verifier and signed, persisted review/approval
   receipts; prove reviewer independence from a verifier-controlled identity, not names.
2. Add an authenticated billing-evidence adapter. Reserve a conservative maximum before
   a network operation; retain the reservation and block later spend while actual cost is
   `UNKNOWN`; settle only a bound authenticated billing receipt.
3. Build an isolated TEST-only transport factory outside the production package. It must
   be impossible to select it through package policy/configuration and must reject network
   configuration before construction.
4. Integrate the qualified runner with the real coordinator and add WAL/fsync recovery
   tests for crashes before/after reserve, submit, provider receipt, and settlement with
   no resubmit. This SDD does **not** claim that such integration exists.

## Tests exercised now

`tests/unit/test_live_execution_service.py` verifies: preflight persistence and explicit
unqualified status; unconditional no-executor/no-ledger dispatch blocking including
replay; preflight tamper detection; closed schema/finite quote window; inline QA and
Bearer evidence rejection; and absence of a package LIVE factory/test transport selector.
These are preflight/blocking tests, not provider-integration or billing tests.