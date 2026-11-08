"""Acceptance coverage for the public ``studio next-video`` CLI benchmark."""

from __future__ import annotations


def test_public_cli_benchmark_exercises_fresh_process_worker_resume_and_cache(tmp_path):
    from src.hybrid.runner_benchmark import run_public_cli_benchmark

    result = run_public_cli_benchmark(tmp_path / "benchmark", repeats=3)

    assert result["schema"] == "next-video-public-cli-benchmark/v1"
    assert result["mode"] == "TEST_ONLY_EXPLICIT_FIXTURE_PROVIDER"
    assert result["repeats"] == 3
    assert result["same_hash_packet"] is True
    assert result["production_claim"] == "NONE"
    assert result["real_cost_gain"] == "NOT_MEASURED"

    for concurrency in ("1", "2"):
        samples = result["samples"][f"concurrency_{concurrency}"]
        assert len(samples) == 3
        for sample in samples:
            assert sample["commands"]["start"]["exit_code"] == 0
            assert sample["commands"]["worker"]["exit_code"] == 0
            assert sample["commands"]["status"]["exit_code"] == 0
            assert sample["commands"]["fresh_resume"]["exit_code"] == 0
            assert sample["initial_submit_count"] == 2
            assert sample["fresh_resume_submit_count"] == 0
            assert sample["tamper_cache_rejected"] is True
            assert sample["tamper_submit_count"] == 0
            assert sample["status_snapshot"] == "CURRENT"
            assert sample["qa_durations_s"]
            assert sample["queue_wait_s"]
            assert sample["cache_hits"] >= 2

    # The benchmark records observed wall time only. It must not encode a
    # brittle speedup assertion; environments may show serialization instead.
    assert set(result["median_by_concurrency"]) == {"1", "2"}
