# Two-gate factory integration SDD

Status: IMPLEMENTATION; not installed production authority. Baseline: origin/master f6fc5387a194acb9428cbf892b08ee9c7e9d29f7. User explicitly authorized implementation of the integrated architecture; prior rejected C3/C4/harness/media remain unchanged and are not imported.

## Outcome

Connect existing Director, OperationalPipeline, Executor, autonomous runner and local renderer through the public CLI. Exactly two routine human decisions: thumbnail then explicit final-package approve-and-publish/schedule. No new runner, ledger or paid submit implementation. Production requires real standing-budget, rights, identity, provider and publication authority; TEST never creates it. Unknown billing stays unknown. No real episode or remote-media mutation during implementation/testing.

## Shared ownership

- facade worker: src/agents/director.py, src/hybrid/autonomous_cli.py, src/cli/main.py if needed; focused new tests named test_factory_facade*.py. Own public integration and the E2E gate handoff, not autonomous.py or compiled.py.
- gates worker: src/hybrid/autonomous.py and new src/hybrid/publication_plan.py; focused new tests test_factory_publication*.py. Own v2 package binding, explicit approval/publication, recovery and receipts.
- cache worker: src/hybrid/compiled.py, src/hybrid/technical_qa.py, src/hybrid/render.py plus narrowly needed cache helper; new tests test_factory_cache*.py. Preserve existing external APIs.
- harness worker: scripts/run_offline_tests.py and new tests test_factory_public_e2e.py/test_offline_factory_guard.py. Own safe fixture execution, public process tests, not production collaborators. Do not run a broad suite; integrator owns it.
- parent: integration, AudioAgent candidate-cache seam and test_factory_audio_cache.py, documentation, public CLI acceptance, independent review, one broad suite, feature commit/push/PR.

## Successor integration ownership

The facade/gates checkpoints were preparatory, not completed integration. For the
next executed slice, one core integrator owns their coupled contracts together:
`autonomous.py`, `publication_plan.py`, `autonomous_cli.py`, the Director/public CLI
seams if necessary, and `test_factory_publication*.py`/`test_factory_facade*.py`.
It works in `C:/HermesStudio/worktrees/two-gate-core-r3`. A separate cache worker
owns only the cache files/tests listed above in `two-gate-cache-r3`. The parent
retains guard, public E2E, AudioAgent and broad-suite ownership; independent guard
review is read-only. Original r2 worktrees and receipts remain unchanged.

Use the parent-qualified bounded guard bytes and their recorded SHA-256 before
any project test. Preserve every RED/GREEN separately with the existing collector;
source drift invalidates that invocation. No child can widen the guard, replace a
policy/QA/authority validator with a fixture, add a runner/ledger, or commit.
A rich independent final-QA verdict must retain its media/manifest/sidecar binding;
a static `approved` object is not an adapter. `xfail` is not E2E acceptance.

## Common contracts

1. Existing public entry remains `python -m src.cli.main autonomous`; extend it rather than inventing a standalone runner. Legacy TEST CLI remains compatible, including refusal to upgrade TEST artifacts to LIVE.
2. New CLI start option: `--publication-plan-file PATH`. Only an explicit versioned plan enables the successor behavior. Existing packets/policies without it retain their exact legacy authority boundary.
3. Gates worker supplies `AutonomousTwoGateRunner.stage_publication_package(plan: dict, *, metadata: Path, captions: Path)` after staging QA-approved media and before either human gate. Bind exact episode, policy, channel, playlist, mode, schedule, thumbnail/video/metadata/captions hashes and original staged paths into an immutable package receipt. Publish-plan schema and all missing/unknown fields fail closed.
4. Gates worker supplies `approve_final_and_publish(artifact: Path, *, reviewer: str, publisher, ownership=None)` for the explicit second human action. It records/revalidates exact final package approval and invokes qualified publication without another human decision. Publisher has explicit upload-private/configure/readback/release/recover protocol; no blind re-upload after an ambiguous upload. Expose exact protocol in gates SDD before implementing so facade can adopt it. Ordinary `record_human_gate('video',...)` must not implicitly gain this authority.
5. New CLI final option: `approve --kind video --test-approval --approve-and-publish`. It is TEST-only, requires the plan staged at start, and uses an explicitly TEST publisher that records transport invocations and readbacks. No network YouTube adapter is selected by CLI fixture flags.
6. Facade uses one existing pipeline Executor; renders only canonical active approved manifest via Director render intent/completion path, not direct renderer bypass. Full final evidence/narrative/technical QA remains required. Rehydrate on restart; do not depend on in-memory _episodes for resuming a compiled workflow.
7. QA as each receipt arrives: deterministic technical packet + independent visual decision for those exact bytes. Final global freeze/contact sheet remains mandatory before animation/render. Legacy whole-batch APIs keep behavior unless an explicit incremental API is used.
8. Cache keys bind source bytes, contract, tool/version and output format. Verify artifact and receipt bytes before reuse; reject substitution and invalidate only affected dependencies. Never cache a caller's PASS as independent authority. Keep scene render concurrency and thread budgets explicit. Retain existing telemetry; missing token/cost measurement is not zero.
9. LIVE must use the repository-owned GenericLiveOperationResolver/qualified composition and existing executor. Do not widen caller-controlled TEST factories or merely remove mode checks. If protected authority is unavailable, return precise readiness blockers before credentials, reservation or submit. Lack of deployment inputs blocks activation, not fixture implementation.
10. Supply isolated TEST packet, valid WAV/PNG/MP4 and provider/publisher transport doubles; doubles are permitted only at external boundaries, not as substitutes for policy, state, lineage, QA-binding or gate validators. TEST content must be visibly labeled and never delivered as episode media.

## Required acceptance

TDD vertical slices with retained first meaningful RED and GREEN. Public fresh-process start -> enqueue -> worker -> first gate -> second explicit approval -> TEST publication/readback, plus same request resumed without duplicate submit/upload. Assert exact two gate events and no intermediate manual gate. Negative coverage: wrong order, media/metadata/captions/destination/policy tamper, copied/forged approval receipts, producer QA, missing rights/price/budget/LIVE composition, lost lease, ambiguous upload, source change after cache, concurrent starts. TEST domain remains disjoint from LIVE. A slow scene does not block ready-scene QA. No unmeasured speed/cost claim.

## Execution environment and retention

Use C:/Users/meira/hermes-studio-venv/Scripts/python.exe (already installed Python 3.12) and repo scripts/run_offline_tests.py; HF_HUB_OFFLINE=1, TRANSFORMERS_OFFLINE=1, PYTHONPATH blank, shallow unique temp dirs under C:/Users/meira/AppData/Local/hermes/cache/scratch. Sanitize environment/credentials for subprocess tests and deny real production/episode/request and secret reads before project imports. Do not execute rejected historical harnesses. The harness worker must improve bounded Python protections using inert-spy tests, not claim native containment or start another admission framework. No installs/provider/media API calls. Local fixture generation via PIL/FFmpeg is authorized only under owned TEST scratch.

Every worker retains exact command/cwd/allowlisted env, exit status, complete logs/JUnit and source/test hashes in C:/Users/meira/Documents/HermesStudio/implementations/two_gate_factory_20261115/<role>/. Never overwrite failed runs or manufacture older receipts. Keep durable evidence outside disposable worktrees. Children do not commit/push/merge; parent integrates reviewed diffs. A delivered component is not the completed factory.
