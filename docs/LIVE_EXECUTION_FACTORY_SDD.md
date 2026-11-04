# SDD — configured execution composition

## State and scope

`ConfiguredTestExecutionComposition` is the sole newly-composed execution path. It composes the existing `AuthenticatedBudgetBridge` with its bridge-created `Executor`; it does not accept a raw ledger, policy mapping, provider/license `VERIFIED` string, quote dictionary, or a LIVE adapter. `production_eligible` is always an empty tuple. Consequently no object produced here can issue a usable LIVE `Authorization`, construct a production provider, or change the default LIVE deny.

The historical Jacó USD 10 financial record remains financial-only. `JacoExecutionPolicyResolver` and `DirectorAgent.dispatch_current_policy_baselines()` retain their LIVE fail-closed behavior until the provisioning contract below is met.

## TEST composition contract

The factory interface is:

```python
ConfiguredTestExecutionComposition.from_authenticated_budget_bridge(
    bridge: AuthenticatedBudgetBridge,
    executor_database: Path,
    executor_config: Config,
    provider_factory: Callable[[], Provider],
    capability_root: Path,
    clock: Callable[[], float],
)
```

It first revalidates the bridge's authenticated TEST receipt and makes the executor through `bridge.create_executor`. At execution, it requires an exact `Job(mode="TEST")`, the policy-authorized endpoint, a bounded model field, a fresh finite quote expiry (at most five minutes), and closed QA evidence `{verdict: "PASS", request: job.request_id}`. It writes an exclusive/fsync/read-back `TEST_EXECUTION_CAPABILITY` record containing the request payload hash, endpoint, model, USD currency, quote expiry, authenticated budget scope, and QA-evidence hash. It then calls only:

```
bridge.admit_executor_intent(executor, job)
executor.run(job, factory_provider, submit_prepared=True)
```

The existing bridge therefore performs `PREPARED → reserve → durable admitted marker`; the existing executor rereads the exact claim/marker immediately before provider submission and uses its normal durable recovery logic. The composition creates the provider only after that admission succeeds. It has no direct executor/ledger/provider bypass.

TEST providers are isolated local fakes. A provider returned as `LIVE`, a LIVE job, an expired quote, missing/tampered QA binding, unavailable bridge, or a policy/endpoint mismatch fails before provider submission. Capability receipts are immutable lineage records, not financial settlement, provider qualification, or LIVE authority.

## Provisioning contract required for LIVE eligibility

A production composition remains unavailable until deployment supplies all of the following independently administered stores and adapters:

1. A pinned `PRODUCTION` Ed25519 root containing distinct authorized QA and LICENSE signer keys, revocation data, key validity windows, and an evidence root outside the package.
2. Signed, closed, finite-expiry QA and license attestations bound to the immutable request payload, endpoint/model, manifest, Jacó policy digest, and provider identity. QA provenance is not financial authority.
3. A configured provider-qualification registry signed by the production authority. Every entry must bind adapter implementation hash, endpoint, model, geometry/payload schema, credential source identity, supported recovery/readback contract, and finite qualification expiry. The current registry is empty.
4. An authenticated provider quote/price record in USD, bound to request, endpoint, model, payload hash, quote ID, maximum amount, currency, and expiry; and a provider billing readback adapter that produces the existing canonical settlement receipt bound to the durable remote request ID.
5. A LIVE-capable authenticated budget authority and a composition-owned coordinator that binds the durable intent, reservation, remote-ID checkpoint, recovery, and settlement. TEST budget receipts/fake transports must be rejected here even when their bytes have valid hashes or signatures.

No additional routine human approval is introduced: after those deployment prerequisites are installed, the existing routine gates remain thumbnail and final-video acceptance. Publication remains separately disabled until its existing exact command and receipt contract are satisfied.

## Acceptance tests

`tests/unit/test_live_execution_composition.py` runs the actual authenticated TEST bridge, its executor, its durable reservation/marker gates, and a TEST-only provider. It verifies persisted capability bindings and rejects TEST→LIVE escalation, stale expiry, unavailable/forged bridge, and forged QA before any provider effect. `tests/unit/test_live_execution_policy.py` continues to verify that the Jacó LIVE resolver and Director path remain blocked.
