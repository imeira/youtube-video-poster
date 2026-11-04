"""Acceptance coverage for the actual-runner TEST throughput benchmark."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_actual_runner_benchmark_measures_bounded_scheduler_and_cache_integrity(tmp_path):
    from src.hybrid.runner_benchmark import run_actual_runner_benchmark

    result = await run_actual_runner_benchmark(
        tmp_path / "benchmark",
        repeats=3,
        fake_latency_s={"fast": 0.1, "slow": 0.5},
    )

    assert result["schema"] == "autonomous-two-gate-runner-benchmark/v1"
    assert result["mode"] == "TEST_ONLY_FAKE_PROVIDER_LATENCY"
    assert result["measurement_scope"] == "LOCAL_MONOTONIC_WALL_TIME"
    assert result["repeats"] == 3
    assert result["real_cost_gain"] == "NOT_MEASURED"
    assert result["same_dag_input_assets"] is True
    assert result["median_by_provider_concurrency"]["1"]["wall_time_s"] > result["median_by_provider_concurrency"]["2"]["wall_time_s"]

    parallel_samples = result["samples"]["provider_concurrency_2"]
    assert len(parallel_samples) == 3
    assert all(sample["submit_count"] == 2 for sample in parallel_samples)
    assert all(sample["max_inflight"] <= 2 for sample in parallel_samples)
    assert all(sample["qa_before_unrelated_slow_end"] for sample in parallel_samples)
    assert all(sample["exact_hash_resume_submit_count"] == 0 for sample in parallel_samples)
    assert all(sample["tamper_rejected_without_submit"] for sample in parallel_samples)
    assert all(sample["queue_wait_s"] for sample in parallel_samples)
    assert all(sample["qa_times_s"] for sample in parallel_samples)
