"""Acceptance coverage for the public ``studio next-video`` CLI benchmark."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest


def test_public_cli_events_do_not_subtract_parent_and_worker_monotonic_clocks(tmp_path):
    from src.hybrid.runner_benchmark import _public_cli_events

    request_id = "a" * 64
    events = [
        {"sequence": 1, "event": "submit_started", "scene": "SLOW", "clock_domain": "fixture-process-monotonic-relative/v1", "process_elapsed_s": 0.10, "monotonic_s": 35000.0, "tempdir": "C:/scratch"},
        {"sequence": 2, "event": "submit_started", "scene": "FAST", "clock_domain": "fixture-process-monotonic-relative/v1", "process_elapsed_s": 0.12, "monotonic_s": 35000.2, "tempdir": "C:/scratch"},
        {"sequence": 3, "event": "submit_completed", "scene": "FAST", "clock_domain": "fixture-process-monotonic-relative/v1", "process_elapsed_s": 0.27, "monotonic_s": 35000.35, "tempdir": "C:/scratch"},
        {"sequence": 4, "event": "qa_completed", "scene": "FAST", "clock_domain": "fixture-process-monotonic-relative/v1", "process_elapsed_s": 0.30, "monotonic_s": 35000.38, "tempdir": "C:/scratch"},
        {"sequence": 5, "event": "submit_completed", "scene": "SLOW", "clock_domain": "fixture-process-monotonic-relative/v1", "process_elapsed_s": 0.45, "monotonic_s": 35000.35, "tempdir": "C:/scratch"},
        {"sequence": 6, "event": "qa_completed", "scene": "SLOW", "clock_domain": "fixture-process-monotonic-relative/v1", "process_elapsed_s": 0.50, "monotonic_s": 35000.40, "tempdir": "C:/scratch"},
    ]
    event_path = tmp_path / "TC" / request_id[:16] / "provider" / "fixture-events.jsonl"
    event_path.parent.mkdir(parents=True)
    event_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")

    queue_wait, worker_elapsed, qa_durations, peak, child_tempdir = _public_cli_events(
        tmp_path, request_id
    )

    assert queue_wait == {"FAST": None, "SLOW": None}
    assert worker_elapsed == {"FAST": 0.12, "SLOW": 0.10}
    assert qa_durations == pytest.approx({"FAST": 0.03, "SLOW": 0.05})
    assert peak == 2
    assert child_tempdir == "C:/scratch"


def test_public_cli_benchmark_uses_explicit_scratch_and_reads_child_tempdir(
    tmp_path, monkeypatch
):
    from src.hybrid.runner_benchmark import run_public_cli_benchmark

    scratch = Path(tempfile.gettempdir()).parent / f"bct-{os.getpid()}"
    monkeypatch.setenv("TMPDIR", str(tmp_path / "untrusted-parent-tmp"))
    result = run_public_cli_benchmark(
        tmp_path / "benchmark-output", repeats=1, scratch_root=scratch
    )

    for concurrency in ("1", "2"):
        sample = result["samples"][f"concurrency_{concurrency}"][0]
        assert Path(sample["child_tempdir"]).is_relative_to(scratch.resolve())
        assert sample["child_tempdir_verified"] is True


def test_public_cli_benchmark_exercises_fresh_process_worker_resume_and_cache(tmp_path):
    from src.hybrid.runner_benchmark import run_public_cli_benchmark

    result = run_public_cli_benchmark(
        tmp_path / "benchmark",
        repeats=3,
        scratch_root=Path(tempfile.gettempdir()).parent / f"bct-full-{os.getpid()}",
    )

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
            assert sample["queue_wait_status"] == "UNKNOWN_CROSS_PROCESS_CLOCK_DOMAIN"
            assert sample["queue_wait_s"] == {"FAST": None, "SLOW": None}
            assert sample["fixture_event_clock_domain"] == "fixture-process-monotonic-relative/v1"
            assert set(sample["worker_elapsed_to_submit_s"]) == {"FAST", "SLOW"}
            assert sample["cache_hits"] >= 2

    # The benchmark records observed wall time only. It must not encode a
    # brittle speedup assertion; environments may show serialization instead.
    assert set(result["median_by_concurrency"]) == {"1", "2"}
