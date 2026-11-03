# Jacó permanent execution policy — SDD

## Immutable scope

The policy is fixed to request `72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9`, revision `818556d97edb648f`, episode `JACO_GENESIS28`, and total episode cap **USD 10.00**. It reads the existing financial reconciliation as historical cap evidence only. Its `authorizes_generation: false` remains historical and is never edited or reinterpreted as provider authority.

Current implementation policy permits autonomous routine progression only after machine QA. The only routine human media gates are exactly `thumbnail` and `final_video`. Publication is disabled; it requires a later explicit publication command with separately hash-bound media/metadata receipts.

## Closed admission

`JacoExecutionPolicyResolver` returns a policy or raises `LiveAdmissionError`. An admitted job must have all of:

- exact LIVE job/request identity and a configured USD 10 executor plus canonical episode ceiling;
- a non-empty, exact provider quote matching endpoint and job cost;
- `VERIFIED` license evidence;
- a trusted-composition registration for the exact LIVE endpoint;
- a positive, finite quote no greater than the ceiling.

Missing credentials, missing license, unregistered provider, absent price bound, malformed financial scope, policy mismatch, or TEST-mode adapter fail before reservation/submission. The resolver does not construct providers, read credentials, make network calls, or publish.

## Director integration and recovery

`DirectorAgent.dispatch_current_policy_baselines()` resolves policy, builds exact per-job `Authorization`/`Price` objects, and delegates to the existing `dispatch_compiled_baselines()` and existing `Executor.run()` kernel. The kernel still writes intent/reservation, submits once, checkpoints the remote ID, persists receipts, and routes recovery through the existing provider recovery path. It then persists the existing QA packet and stops at `VISUAL_QA`; scene promotion still requires the existing independent hash-bound QA path.

A sealed local transport is accepted only by the composition test factory to exercise LIVE-mode transaction mechanics. It provides no policy, financial, license, billing, QA, human approval, credential, provider-delivery, or publication authority. No real paid call is enabled because no production quote/cost-bound availability has been configured.

## Negative acceptance coverage

- historical scope mismatch or changed cap is denied;
- `authorizes_generation: false` remains false and publication remains disabled;
- missing license and unregistered/TEST-mode provider deny before submit;
- sealed transport exercises the real LIVE executor admission/reserve/submit/receipt path without becoming TEST authority;
- Director path enforces policy → handoff → executor → persisted receipt → QA packet → `VISUAL_QA`.
