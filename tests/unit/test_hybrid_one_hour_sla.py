from decimal import Decimal

import pytest

from src.hybrid.fastlane import OneHourSLA, schedule


def test_schedule_batches_independent_baselines_and_qa_inside_one_hour():
    result = schedule(
        OneHourSLA(
            scene_count=39,
            provider_concurrency=8,
            baseline_seconds=45,
            qa_seconds=30,
            remediation_slots=4,
            remediation_seconds=90,
            render_seconds=600,
            final_qa_seconds=180,
            buffer_seconds=120,
        )
    )

    assert result["eligible"] is True
    assert result["deadline_seconds"] == 3600
    assert result["predicted_seconds"] == 1380
    assert result["baseline"]["waves"] == 5
    assert result["baseline"]["parallelism"] == 8
    assert result["qa"]["parallelism"] == 8
    assert result["remediation"]["slots"] == 4
    assert result["control_plane"] == {
        "run_manifest": 1,
        "per_frame_events": 39,
        "per_frame_authorities": 0,
        "per_frame_scripts": 0,
    }


def test_schedule_fails_closed_when_one_hour_cannot_be_met():
    with pytest.raises(ValueError, match="one-hour SLA"):
        schedule(
            OneHourSLA(
                scene_count=39,
                provider_concurrency=1,
                baseline_seconds=120,
                qa_seconds=120,
                remediation_slots=10,
                remediation_seconds=180,
                render_seconds=1800,
                final_qa_seconds=600,
                buffer_seconds=300,
            )
        )


def test_schedule_rejects_serial_qa_and_unbounded_remediation():
    with pytest.raises(ValueError, match="qa_concurrency"):
        OneHourSLA(scene_count=3, provider_concurrency=3, qa_concurrency=1)
    with pytest.raises(ValueError, match="remediation slots"):
        OneHourSLA(scene_count=3, remediation_slots=0)


def test_schedule_exposes_hard_budget_without_authorizing_provider_calls():
    result = schedule(
        OneHourSLA(scene_count=2, provider_concurrency=2),
        estimated_paid_cost=Decimal("0.44"),
        hard_limit=Decimal("0.50"),
    )

    assert result["budget"] == {
        "estimated_paid_cost": Decimal("0.44"),
        "hard_limit": Decimal("0.50"),
        "within_limit": True,
    }
    assert result["execution_authorized"] is False
    assert result["provider_calls_authorized"] == 0


def test_cli_emits_a_one_hour_schedule_without_provider_authority():
    import json
    import subprocess
    import sys

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.hybrid",
            "--one-hour-sla",
            "--scene-count",
            "39",
            "--provider-concurrency",
            "8",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["one_hour_sla"]["eligible"] is True
    assert result["one_hour_sla"]["provider_calls_authorized"] == 0
