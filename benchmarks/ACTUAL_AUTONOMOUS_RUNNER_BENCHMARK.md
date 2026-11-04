# Actual autonomous runner TEST benchmark

## Scope

This result was produced by `python -m src.hybrid.runner_benchmark --output-dir benchmarks/actual_runner_benchmark --repeats 3`. It invokes the public `AutonomousTwoGateRunner.start()` and `resume()` APIs with the same two-node independent DAG and the same hash-bound input assets for each sample.

The fixture transport is local-only and TEST-only. Its explicit asynchronous delays were `fast=0.1s` and `slow=0.5s`. The measurements are local monotonic wall time; they are not provider latency, delivery, billing, quality, or production-cost measurements.

## Measured results

| Provider concurrency | Runs | Median wall time (s) | Max in-flight observed | Submit count/run |
|---:|---:|---:|---:|---:|
| 1 | 3 | 0.984000 | 1 | 2 |
| 2 | 3 | 0.750000 | 2 | 2 |

For every concurrency-2 sample, the independent QA timestamp for `fast` preceded completion of the unrelated `slow` transport request. The exact-hash fresh resume made zero submissions in all six samples. After changing a completed asset, the persisted QA was rejected and the fresh fake transport made zero submissions in all six samples.

The JSON records per-sample wall time, queue wait, QA timestamps, peak in-flight work, actual fixture submit counts, resume-cache count, and tamper result:

- [`actual_runner_benchmark.json`](actual_runner_benchmark/actual_runner_benchmark.json)

## Boundary

`real_cost_gain` is `NOT_MEASURED`; no USD saving, provider claim, or production throughput claim is made. The older `benchmark_rolling_vs_barrier` thread/sleep comparison is deprecated because it does not execute the autonomous runner and cannot establish scheduler acceptance.
