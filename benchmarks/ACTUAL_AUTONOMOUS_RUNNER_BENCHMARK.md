# Public `studio next-video` CLI TEST benchmark

## Scope

Recorded command from the original report (preserved, not a new execution):

```text
python -m src.hybrid.runner_benchmark --output-dir benchmarks/public_cli_benchmark --repeats 3
```

This standalone harness invokes real public CLI subprocesses for every sample:
`start -> worker --once -> status -> fresh-process resume`. It creates the existing two-scene compiled TEST fixture once, reuses its same packet hashes for concurrency `1` and `2`, and runs three samples per setting.

The TEST fixture delay is configured through `HYBRID_TEST_FIXTURE_LATENCY_BY_SCENE_JSON` (`SLOW=0.35s`, `FAST=0.15s`) on the existing `FixtureProvider`. The current harness derives each child's `TMP`, `TEMP`, `TMPDIR`, and `HERMES_HOME` from the selected scratch root, rather than inheriting the caller's temp directories. It rejects `C:/HermesStudio/production`, makes no network/provider/credential call, and is not a production command.

For a new Windows run, add an explicit, shallow `--scratch-root` inside the approved scratch area; invoke its pytest coverage with one explicit, shallow, unique `--basetemp` in that area. Only fixture directory labels are shortened: logical request IDs, full digest values, and existing request-namespace rules are unchanged. This is a test/benchmark scratch requirement, not a claim to repair general Windows long-path infrastructure.

## Historical results — not current acceptance

The table and linked JSON below are preserved from the original recorded run. They do not validate the later event-identity and clock-domain corrections. Current acceptance requires fresh, separately retained evidence from the current code; this historical table must not be used to claim a current speedup.

| Persisted runner concurrency | Samples | Median worker wall time (s) |
|---:|---:|---:|
| 1 | 3 | 12.297000 |
| 2 | 3 | 11.453000 |

Each recorded sample includes the actual public argv, exit codes, command wall times, same packet hashes, fixture queue waits, QA durations, peak in-flight submits, initial submit count, fresh-resume submit count, cache-hit count, and current status snapshot. All six initial workers made two TEST fixture submits. Every fresh-process resume made zero submits. For every sample, tampering with a completed TEST fixture asset caused public `resume` to reject cached QA and made zero additional submits.

The observed median is an environment-specific local TEST result. This benchmark deliberately makes **no speedup assertion**: it records serialization/concurrency evidence instead of asserting flaky timing. It makes no provider throughput, quality, delivery, billing, or production-cost claim.

- [`public_cli_benchmark.json`](public_cli_benchmark/public_cli_benchmark.json)

## Boundary

`production_claim` is `NONE` and `real_cost_gain` is `NOT_MEASURED`. The older direct-runner and thread/sleep comparisons are not façade acceptance evidence and must not be used as a public-CLI throughput claim.
