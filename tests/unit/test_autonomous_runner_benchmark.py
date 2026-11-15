"""Acceptance coverage for the public ``studio next-video`` CLI benchmark."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _short_benchmark_scratch(tmp_path: Path, name: str) -> Path:
    """Keep fixture-only paths under pytest's explicitly selected base."""
    assert name in {"b", "c"}
    return tmp_path.parent / name


def test_public_cli_events_do_not_subtract_parent_and_worker_monotonic_clocks(tmp_path):
    from src.hybrid.runner_benchmark import _public_cli_events

    request_id = "a" * 64
    worker_run_id = "b" * 32
    worker_pid = 4242
    events = [
        {"sequence": 1, "event": "submit_started", "scene": "SLOW", "clock_domain": "fixture-process-monotonic-relative/v1", "process_elapsed_s": 0.10, "tempdir": "C:/scratch", "worker_run_id": worker_run_id, "worker_pid": worker_pid},
        {"sequence": 2, "event": "submit_started", "scene": "FAST", "clock_domain": "fixture-process-monotonic-relative/v1", "process_elapsed_s": 0.12, "tempdir": "C:/scratch", "worker_run_id": worker_run_id, "worker_pid": worker_pid},
        {"sequence": 3, "event": "submit_completed", "scene": "FAST", "clock_domain": "fixture-process-monotonic-relative/v1", "process_elapsed_s": 0.27, "tempdir": "C:/scratch", "worker_run_id": worker_run_id, "worker_pid": worker_pid},
        {"sequence": 4, "event": "qa_completed", "scene": "FAST", "clock_domain": "fixture-process-monotonic-relative/v1", "process_elapsed_s": 0.30, "tempdir": "C:/scratch", "worker_run_id": worker_run_id, "worker_pid": worker_pid},
        {"sequence": 5, "event": "submit_completed", "scene": "SLOW", "clock_domain": "fixture-process-monotonic-relative/v1", "process_elapsed_s": 0.45, "tempdir": "C:/scratch", "worker_run_id": worker_run_id, "worker_pid": worker_pid},
        {"sequence": 6, "event": "qa_completed", "scene": "SLOW", "clock_domain": "fixture-process-monotonic-relative/v1", "process_elapsed_s": 0.50, "tempdir": "C:/scratch", "worker_run_id": worker_run_id, "worker_pid": worker_pid},
    ]
    event_path = tmp_path / "TC" / request_id[:16] / "provider" / "fixture-events.jsonl"
    event_path.parent.mkdir(parents=True)
    event_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")

    queue_wait, worker_elapsed, qa_durations, peak, child_tempdir = _public_cli_events(
        tmp_path, request_id, worker_run_id=worker_run_id, worker_pid=worker_pid
    )

    assert queue_wait == {"FAST": None, "SLOW": None}
    assert worker_elapsed == {"FAST": 0.12, "SLOW": 0.10}
    assert qa_durations == pytest.approx({"FAST": 0.03, "SLOW": 0.05})
    assert peak == 2
    assert child_tempdir == "C:/scratch"


def test_public_cli_events_reject_other_worker_identity_before_metrics(tmp_path):
    from src.hybrid.runner_benchmark import _public_cli_events

    request_id = "a" * 64
    expected_run_id = "b" * 32
    events = [
        {"sequence": sequence, "event": event, "scene": scene,
         "clock_domain": "fixture-process-monotonic-relative/v1",
         "process_elapsed_s": elapsed, "tempdir": "C:/scratch",
         "worker_run_id": "c" * 32 if sequence == 6 else expected_run_id,
         "worker_pid": 4242 if sequence != 6 else 5151}
        for sequence, event, scene, elapsed in (
            (1, "submit_started", "SLOW", 0.10),
            (2, "submit_started", "FAST", 0.12),
            (3, "submit_completed", "FAST", 0.27),
            (4, "qa_completed", "FAST", 0.30),
            (5, "submit_completed", "SLOW", 0.45),
            (6, "qa_completed", "SLOW", 0.50),
        )
    ]
    event_path = tmp_path / "TC" / request_id[:16] / "provider" / "fixture-events.jsonl"
    event_path.parent.mkdir(parents=True)
    event_path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")

    with pytest.raises(AssertionError, match="worker identity"):
        _public_cli_events(
            tmp_path, request_id, worker_run_id=expected_run_id, worker_pid=4242
        )


def test_public_cli_benchmark_uses_explicit_scratch_and_reads_child_tempdir(
    tmp_path, monkeypatch
):
    from src.hybrid.runner_benchmark import run_public_cli_benchmark

    scratch = _short_benchmark_scratch(tmp_path, "b")
    inherited = tmp_path / "nested" / "inherited" / "temp"
    for variable in ("TMPDIR", "TMP", "TEMP", "HERMES_HOME"):
        monkeypatch.setenv(variable, str(inherited / variable.lower()))
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
        scratch_root=_short_benchmark_scratch(tmp_path, "c"),
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
            assert sample["fixture_latency_kind"] == "TEST_FAKE_LATENCY"
            assert set(sample["worker_elapsed_to_submit_s"]) == {"FAST", "SLOW"}
            assert sample["cache_hits"] >= 2

    # The benchmark records observed wall time only. It must not encode a
    # brittle speedup assertion; environments may show serialization instead.
    assert set(result["median_by_concurrency"]) == {"1", "2"}
