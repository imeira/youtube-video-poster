# SDD — public CLI TEST benchmark event evidence

## Scope
`runner_benchmark` consumes only TEST fixture timing events produced by the fresh `worker` subprocess. It neither changes the LIVE executor nor treats fixture delay as LIVE latency or a production-performance claim.

## Immutable inputs and closed event schema
The harness generates one nonempty 32-hex `worker_run_id` and records the spawned worker PID. Every event has exactly: `sequence`, `event`, `scene`, `clock_domain`, `process_elapsed_s`, `tempdir`, `worker_run_id`, `worker_pid`. The only event values are `submit_started`, `submit_completed`, and `qa_completed`; scenes are `SLOW` and `FAST`; the clock domain is `fixture-process-monotonic-relative/v1`. Event identity must equal the harness expectation, sequence must be unique and contiguous from one, and all elapsed values must be finite and nonnegative.

## Lifecycle and recovery
For each scene the one accepted lifecycle is one start, one completion, then one QA event, in that order. Mixed identity, replay, duplicate event/sequence, missing lifecycle, unknown fields, or non-finite/impossible timing reject before any metric is calculated. Queue wait remains `UNKNOWN_CROSS_PROCESS_CLOCK_DOMAIN` with null values because admission and worker clocks are independent. Fixture delay is labeled `TEST_FAKE_LATENCY`, never LIVE latency.

## Negative acceptance
A replay or other-PID/run-ID event, duplicate scene lifecycle, non-contiguous sequence, missing phase, malformed schema, and non-finite timing each fail closed. The public test exercises fresh `start → worker → status → resume`, bounded C1/C2 samples, explicit fresh scratch/temp environment, and stores argv/exit/hash evidence without asserting a speedup.
