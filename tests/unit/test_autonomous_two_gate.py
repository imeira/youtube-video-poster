"""Vertical acceptance tests for the durable autonomous two-gate runner."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import pytest

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.execution import Executor, Job, ProviderResult
from src.hybrid.planner import Config


def manifest(tmp_path):
    from PIL import Image

    reference = tmp_path / "reference.png"
    Image.new("RGB", (16, 16), "green").save(reference)
    frozen = FrozenAsset.approve(reference, "test-reference", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((frozen,), sheet)
    return Manifest.freeze(
        (frozen,),
        FrozenAsset.approve(sheet, "test-reference", "TEST"),
        "test-reference",
        "TEST",
    )


class FixtureProvider:
    mode = "TEST"

    def __init__(self, root):
        self.root = root
        self.calls = 0

    async def submit(self, job, request_id, checkpoint):
        self.calls += 1
        checkpoint(provider_id="TEST-" + request_id)
        result = self.root / f"{request_id}.bin"
        result.write_bytes(b"test-produced-artifact")
        return ProviderResult(result, job.cost)

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        raise AssertionError("test fixture must resume complete executor receipts")


def job(tmp_path):
    return Job(
        "S001",
        "first",
        "TEST",
        "test-image",
        {"prompt": "test"},
        manifest(tmp_path),
        Decimal("0.10"),
    )


def policy():
    return {
        "schema": 1,
        "version": "test-v1",
        "mode": "TEST",
        "episode_cap_usd": "1.00",
        "month_cap_usd": "3.00",
        "experiment_cap_usd": "0.20",
        "allowed_providers": ["test-image"],
        "publication": {
            "enabled": True,
            "channel": "test-channel",
            "visibility": "private",
        },
    }


def ledger(tmp_path, parsed=None):
    from src.hybrid.autonomous import AutonomousPolicy
    from src.hybrid.standing_budget import StandingBudgetLedger

    parsed = parsed or AutonomousPolicy.from_dict(policy())
    return StandingBudgetLedger(
        tmp_path / "shared-budget.sqlite3",
        policy_sha256=parsed.digest,
        episode_limit_usd=parsed.episode_cap_usd,
        month_limit_usd=parsed.month_cap_usd,
        experiment_limit_usd=parsed.experiment_cap_usd,
        mode="TEST",
    )


def final_media(tmp_path):
    import subprocess

    from PIL import Image

    thumbnail = tmp_path / "thumbnail.png"
    Image.new("RGB", (64, 64), "blue").save(thumbnail)
    video = tmp_path / "video.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x64:d=1",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    return thumbnail, video


@pytest.mark.asyncio
async def test_runner_resumes_executor_and_reaches_two_hash_bound_human_gates(tmp_path):
    from src.hybrid.autonomous import AutonomousPolicy, AutonomousTwoGateRunner

    parsed = AutonomousPolicy.from_dict(policy())
    runner = AutonomousTwoGateRunner(
        tmp_path / "run",
        "TEST-EP",
        parsed,
        Executor(tmp_path / "jobs.db", Config(limit=Decimal("1.00"))),
        ledger(tmp_path, parsed),
    )
    provider = FixtureProvider(tmp_path)
    candidate = job(tmp_path)

    started = runner.start({"packet": "test"}, [candidate])
    assert started["state"] == "READY"
    media_pending = await runner.resume(
        provider,
        lambda receipt: {
            "approved": True,
            "reviewer": "independent-test-qa",
            "reviewer_identity": {"subject": "qa-test", "issuer": "TEST"},
            "request_id": receipt["request_id"],
            "result_sha256": receipt["result_sha256"],
            "codes": [],
        },
    )
    assert media_pending["state"] == "MEDIA_PENDING"
    assert provider.calls == 1

    resumed = await runner.resume(
        provider,
        lambda receipt: (_ for _ in ()).throw(AssertionError("QA must not rerun")),
    )
    assert resumed["state"] == "MEDIA_PENDING"
    assert provider.calls == 1

    thumbnail, video = final_media(tmp_path)
    runner.stage_delivery(
        thumbnail,
        video,
        qa={
            "approved": True,
            "mode": "TEST",
            "subject": "technical-test",
            "issuer": "TEST",
        },
    )
    assert (
        runner.record_human_gate(
            "thumbnail", thumbnail, approved=True, reviewer="human-test"
        )["state"]
        == "WAITING_VIDEO_APPROVAL"
    )
    assert (
        runner.record_human_gate("video", video, approved=True, reviewer="human-test")[
            "state"
        ]
        == "READY_TO_PUBLISH"
    )

    published = runner.publish(
        lambda package: {
            "video_id": "TEST-video-id",
            "readback": {"video_id": "TEST-video-id"},
        }
    )
    assert published["state"] == "PUBLISHED"
    assert published["publication"]["video_id"] == "TEST-video-id"


def test_policy_blocks_unknown_cost_and_publish_without_valid_policy(tmp_path):
    from src.hybrid.autonomous import AutonomousPolicy, PolicyError

    bad = policy()
    bad["publication"] = None
    with pytest.raises(PolicyError, match="publication"):
        AutonomousPolicy.from_dict(bad)

    valid = AutonomousPolicy.from_dict(policy())
    with pytest.raises(PolicyError, match="unknown cost"):
        valid.authorize_paid(
            "test-image", None, monthly_spend=Decimal(0), experiment_spend=Decimal(0)
        )


def test_metrics_separate_quota_tokens_and_money_and_cache_is_hash_compatible(tmp_path):
    from src.hybrid.autonomous import ArtifactCache, PersistentMetrics

    metrics = PersistentMetrics(tmp_path / "metrics.json")
    metrics.record(
        "request-1",
        model="gpt-test",
        tokens_input=10,
        tokens_cached=3,
        tokens_output=5,
        tokens_reasoning=7,
        latency_s=1.5,
        money_usd="0.25",
        quota_units=99,
    )
    assert metrics.load()["totals"] == {
        "money_usd": "0.25",
        "quota_units": 99,
        "tokens_cached": 3,
        "tokens_input": 10,
        "tokens_output": 5,
        "tokens_reasoning": 7,
    }

    cache = ArtifactCache(tmp_path / "cache")
    source = tmp_path / "audio.bin"
    source.write_bytes(b"audio")
    cache.put("tts", [source], {"voice": "test", "rate": "+0%"}, source)
    assert cache.get("tts", [source], {"voice": "test", "rate": "+0%"}) is not None
    assert cache.get("tts", [source], {"voice": "other", "rate": "+0%"}) is None


def test_operational_metrics_reconciles_once_by_request_or_event_and_keeps_unknown_billing_unspent(tmp_path):
    from datetime import UTC, datetime

    from src.hybrid.autonomous import OperationalMetricsLedger

    ledger = OperationalMetricsLedger(tmp_path / "operational-metrics.sqlite3")
    observation = {
        "event_id": "event-1",
        "request_id": "request-1",
        "episode_id": "TEST-EP",
        "worker": "worker-a",
        "experiment": True,
        "observed_at": datetime(2026, 10, 31, 23, 59, tzinfo=UTC),
        "model": "model https://signed.example/result?token=secret",
        "tokens_input": 10,
        "tokens_cached": 3,
        "tokens_output": 5,
        "tokens_reasoning": 7,
        "latency_s": 1.5,
        "quota_units": 99,
        "billing_status": "UNKNOWN",
    }
    assert ledger.reconcile(observation) is True
    assert ledger.reconcile(dict(observation)) is False
    replay = dict(observation, event_id="event-worker-replay", worker="worker-b")
    assert ledger.reconcile(replay) is False

    report = ledger.report()
    assert report["money_usd"] is None
    assert report["unknown_billing_requests"] == 1
    assert report["unauthenticated_billing_requests"] == 0
    assert report["tokens"] == {"input": 10, "cached": 3, "output": 5, "reasoning": 7}
    assert report["quota_units"] == 99
    assert report["latency_s"] == 1.5
    assert report["by_month_utc"]["2026-10"]["unknown_billing_requests"] == 1
    assert report["by_experiment"]["true"]["requests"] == 1
    assert report["by_worker"]["worker-a"]["requests"] == 1
    assert report["models"] == []


def test_operational_metrics_rejects_missing_measurements_and_unauthenticated_money(tmp_path):
    from src.hybrid.autonomous import OperationalMetricsLedger

    ledger = OperationalMetricsLedger(tmp_path / "operational-metrics.sqlite3")
    with pytest.raises(ValueError, match="missing metric"):
        ledger.reconcile({
            "event_id": "e1", "request_id": "r1", "episode_id": "TEST-EP",
            "worker": "worker", "experiment": False, "billing_status": "UNKNOWN",
        })
    with pytest.raises(ValueError, match="unauthenticated"):
        ledger.reconcile({
            "event_id": "e2", "request_id": "r2", "episode_id": "TEST-EP",
            "worker": "worker", "experiment": False, "billing_status": "UNAUTHENTICATED",
            "money_usd": "0.10",
        })


@pytest.mark.parametrize("latency_s", [float("nan"), float("inf"), float("-inf")])
def test_operational_metrics_rejects_nonfinite_latency_before_any_metrics_write(tmp_path, latency_s):
    import sqlite3

    from src.hybrid.autonomous import OperationalMetricsLedger

    path = tmp_path / "operational-metrics.sqlite3"
    ledger = OperationalMetricsLedger(path)
    snapshot_before = path.with_suffix(".snapshot.json").read_bytes()
    observation = {
        "event_id": "nonfinite-latency-event", "request_id": "nonfinite-latency-request",
        "episode_id": "TEST-EP", "worker": "writer", "experiment": False,
        "tokens_input": 1, "latency_s": latency_s, "billing_status": "UNKNOWN",
    }

    with pytest.raises(ValueError, match="latency_s must be nonnegative"):
        ledger.reconcile(observation)

    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM observation_conflicts").fetchone() == (0,)
    assert path.with_suffix(".snapshot.json").read_bytes() == snapshot_before


def test_operational_metrics_keeps_finite_or_absent_latency_rules(tmp_path):
    from src.hybrid.autonomous import OperationalMetricsLedger

    for index, (latency_s, expected_latency) in enumerate(((0, 0.0), (1.5, 1.5), (None, None))):
        ledger = OperationalMetricsLedger(tmp_path / f"operational-metrics-{index}.sqlite3")
        assert ledger.reconcile({
            "event_id": f"finite-latency-event-{index}",
            "request_id": f"finite-latency-request-{index}",
            "episode_id": "TEST-EP", "worker": "writer", "experiment": False,
            "tokens_input": 1, "latency_s": latency_s, "billing_status": "UNKNOWN",
        }) is True
        assert ledger.report()["latency_s"] == expected_latency


def test_operational_metrics_only_persists_catalog_models_and_preserves_event_time_across_restart(tmp_path):
    import sqlite3
    from datetime import UTC, datetime

    from src.hybrid.autonomous import OperationalMetricsLedger

    path = tmp_path / "operational-metrics.sqlite3"
    ledger = OperationalMetricsLedger(path, model_catalog={"gpt-5.6-terra"})
    known = {
        "event_id": "known-model", "request_id": "known-request", "episode_id": "TEST-EP",
        "worker": "first-worker", "experiment": False,
        "observed_at": datetime(2026, 10, 31, 23, 59, tzinfo=UTC),
        "model": "gpt-5.6-terra", "tokens_input": 1, "billing_status": "UNKNOWN",
    }
    unknown = {
        "event_id": "unknown-model", "request_id": "unknown-request", "episode_id": "TEST-EP",
        "worker": "first-worker", "experiment": False,
        "observed_at": datetime(2026, 11, 1, 0, 1, tzinfo=UTC),
        "model": "arbitrary-secret-value", "tokens_input": 1, "billing_status": "UNKNOWN",
    }
    assert ledger.reconcile(known) is True
    assert ledger.reconcile(unknown) is True
    restarted = OperationalMetricsLedger(path, model_catalog={"gpt-5.6-terra"})
    report = restarted.report()
    snapshot = OperationalMetricsLedger.read_snapshot(path)
    assert snapshot["status"] == "CURRENT"
    assert snapshot["report"]["requests"] == 2
    assert report["models"] == ["gpt-5.6-terra"]
    assert report["by_month_utc"]["2026-10"]["requests"] == 1
    assert report["by_month_utc"]["2026-11"]["requests"] == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT model FROM observations WHERE request_id='unknown-request'").fetchone() == (None,)
        assert db.execute("SELECT observed_at_utc FROM observations WHERE request_id='known-request'").fetchone() == ("2026-10-31T23:59:00+00:00",)


def test_operational_metrics_unknown_receipt_time_is_not_assigned_a_month(tmp_path):
    from src.hybrid.autonomous import OperationalMetricsLedger

    ledger = OperationalMetricsLedger(tmp_path / "operational-metrics.sqlite3")
    assert ledger.reconcile_executor_receipt(
        {"request_id": "receipt-without-time", "status": "COMPLETE"}, episode_id="TEST-EP"
    ) is True
    report = ledger.report()
    assert report["unknown_timestamp_requests"] == 1
    assert report["by_month_utc"] == {}


def test_operational_metrics_rejects_secret_bearing_identity_urls_before_observation_write(tmp_path):
    import sqlite3

    from src.hybrid.autonomous import (
        OperationalMetricsConflict,
        OperationalMetricsLedger,
    )

    path = tmp_path / "operational-metrics.sqlite3"
    ledger = OperationalMetricsLedger(path)
    base = {
        "event_id": "evt:literal-1", "request_id": "req:literal-1", "episode_id": "TEST-EP",
        "worker": "worker:one", "experiment": False, "tokens_input": 1,
        "billing_status": "UNKNOWN",
    }
    for request_id, event_id in (
        ("https://example/a?token=one", "evt:secret-a"),
        ("https://example/b?token=two", "evt:secret-b"),
    ):
        with pytest.raises(ValueError, match="opaque identifier"):
            ledger.reconcile({**base, "request_id": request_id, "event_id": event_id})
    for field in ("event_id", "episode_id", "worker"):
        with pytest.raises(ValueError, match="opaque identifier"):
            ledger.reconcile({**base, field: "https://example/a?token=one"})
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM observation_conflicts").fetchone() == (0,)

    assert ledger.reconcile(base) is True
    assert ledger.reconcile(dict(base)) is False
    assert ledger.reconcile({**base, "event_id": "evt:worker-replay", "worker": "worker:two"}) is False
    with pytest.raises(OperationalMetricsConflict):
        ledger.reconcile({**base, "event_id": "evt:divergent", "tokens_input": 2})
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT request_id, event_id, episode_id, worker FROM observations").fetchone() == (
            "req:literal-1", "evt:literal-1", "TEST-EP", "worker:one",
        )
    assert ledger.report()["conflicts"] == 1


def test_operational_metrics_rejects_persistent_divergent_identity_but_dedupes_worker_replay(tmp_path):
    from datetime import UTC, datetime

    from src.hybrid.autonomous import (
        OperationalMetricsConflict,
        OperationalMetricsLedger,
    )

    ledger = OperationalMetricsLedger(tmp_path / "operational-metrics.sqlite3")
    observation = {
        "event_id": "event-1", "request_id": "request-1", "episode_id": "TEST-EP",
        "worker": "worker-a", "experiment": True,
        "observed_at": datetime(2026, 10, 31, 23, 59, tzinfo=UTC),
        "tokens_input": 10, "billing_status": "UNKNOWN",
    }
    assert ledger.reconcile(observation) is True
    assert ledger.reconcile({**observation, "event_id": "worker-replay", "worker": "worker-b"}) is False
    with pytest.raises(OperationalMetricsConflict):
        ledger.reconcile({**observation, "tokens_input": 11})
    assert ledger.report()["requests"] == 1
    assert ledger.report()["conflicts"] == 1


def test_cli_status_reads_only_atomic_metrics_snapshot_with_existing_wal_and_writer(tmp_path):
    import json

    from src.hybrid.autonomous import OperationalMetricsLedger

    rid = "a" * 64
    root = tmp_path / "TEST" / rid
    root.mkdir(parents=True)
    (root / "prompt.txt").write_text("fixture", encoding="utf-8")
    (root / "harness.json").write_text('{"prompt_sha256":"' + __import__("hashlib").sha256(b"fixture").hexdigest() + '","request":{}}', encoding="utf-8")
    (root / "runner.json").write_text('{"state":"NEW","media":{}}', encoding="utf-8")
    db_path = root / "operational-metrics.sqlite3"
    ledger = OperationalMetricsLedger(db_path)
    assert ledger.reconcile({
        "event_id": "snapshot-event", "request_id": "snapshot-request", "episode_id": "TEST-EP",
        "worker": "writer", "experiment": False, "tokens_input": 1, "billing_status": "UNKNOWN",
    }) is True
    ready = tmp_path / "writer-ready"
    child = subprocess.Popen(
        [sys.executable, "-c", "from pathlib import Path; import sqlite3,sys,time; db=sqlite3.connect(sys.argv[1]); db.execute('PRAGMA journal_mode=WAL'); db.execute('BEGIN IMMEDIATE'); db.execute('PRAGMA user_version=7'); Path(sys.argv[2]).write_text('ready'); time.sleep(2); db.rollback(); db.close()", str(db_path), str(ready)],
        cwd=Path(__file__).parents[2],
    )
    try:
        for _ in range(40):
            if ready.exists():
                break
            time.sleep(0.05)
        assert ready.exists()
        assert db_path.with_name(db_path.name + "-wal").exists()
        before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file() and not path.name.endswith("-shm")}
        completed = subprocess.run(
            [sys.executable, "-c", "from src.hybrid.autonomous_cli import main; import sys; raise SystemExit(main(sys.argv[1:]))", "status", "--workspace", str(tmp_path), "--request-id", rid],
            cwd=Path(__file__).parents[2], text=True, capture_output=True, check=False,
        )
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        after = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file() and not path.name.endswith("-shm")}
    finally:
        child.wait(timeout=10)
    assert after == before
    assert result["operational_metrics"]["status"] == "CURRENT"
    assert result["operational_metrics"]["report"]["requests"] == 1
    assert result["operational_metrics"]["snapshot_sha256"]


def test_metrics_snapshot_missing_and_stale_are_never_current(tmp_path):
    from datetime import UTC, datetime, timedelta

    from src.hybrid.autonomous import OperationalMetricsLedger

    path = tmp_path / "operational-metrics.sqlite3"
    assert OperationalMetricsLedger.read_snapshot(path) == {"status": "MISSING"}
    ledger = OperationalMetricsLedger(path)
    ledger.reconcile({
        "event_id": "stale-event", "request_id": "stale-request", "episode_id": "TEST-EP",
        "worker": "writer", "experiment": False, "tokens_input": 1, "billing_status": "UNKNOWN",
    })
    stale = OperationalMetricsLedger.read_snapshot(
        path, now=datetime.now(UTC) + timedelta(seconds=301)
    )
    assert stale["status"] == "STALE"
    assert "report" not in stale


def test_metrics_snapshot_rejects_future_timestamp_under_explicit_clock_policy(tmp_path):
    import json
    from datetime import UTC, datetime, timedelta

    from src.hybrid.autonomous import OperationalMetricsLedger, _digest_json

    path = tmp_path / "operational-metrics.sqlite3"
    ledger = OperationalMetricsLedger(path)
    ledger.reconcile({
        "event_id": "future-event", "request_id": "future-request", "episode_id": "TEST-EP",
        "worker": "writer", "experiment": False, "tokens_input": 1, "billing_status": "UNKNOWN",
    })
    snapshot_path = path.with_suffix(".snapshot.json")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["generated_at_utc"] = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    snapshot["snapshot_sha256"] = _digest_json({key: value for key, value in snapshot.items() if key != "snapshot_sha256"})
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    result = OperationalMetricsLedger.read_snapshot(path)
    assert result == {"status": "STALE", "reason": "SNAPSHOT_CLOCK_POLICY"}


@pytest.mark.parametrize("mutate", [
    lambda report: report.update({"requests": False}),
    lambda report: report["tokens"].update({"input": float("nan")}),
    lambda report: report["by_worker"].update({"writer": {"requests": 0}}),
    lambda report: report.update({"unexpected": 1}),
])
def test_metrics_snapshot_requires_closed_typed_report_schema(tmp_path, mutate):
    import json

    from src.hybrid.autonomous import OperationalMetricsLedger, _digest_json

    path = tmp_path / "operational-metrics.sqlite3"
    ledger = OperationalMetricsLedger(path)
    ledger.reconcile({
        "event_id": "schema-event", "request_id": "schema-request", "episode_id": "TEST-EP",
        "worker": "writer", "experiment": False, "tokens_input": 1, "billing_status": "UNKNOWN",
    })
    snapshot_path = path.with_suffix(".snapshot.json")
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    mutate(snapshot["report"])
    snapshot["snapshot_sha256"] = _digest_json({key: value for key, value in snapshot.items() if key != "snapshot_sha256"})
    snapshot_path.write_text(json.dumps(snapshot, allow_nan=True), encoding="utf-8")

    assert OperationalMetricsLedger.read_snapshot(path) == {"status": "STALE", "reason": "SNAPSHOT_INVALID"}


def test_metrics_constructor_crash_leaves_durable_writer_marker_not_prior_current(tmp_path, monkeypatch):
    import json
    from datetime import UTC, datetime

    from src.hybrid.autonomous import OperationalMetricsLedger, _digest_json

    path = tmp_path / "operational-metrics.sqlite3"
    prior = {
        "schema": OperationalMetricsLedger._SNAPSHOT_SCHEMA,
        "status": "CURRENT",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "report": {"prior": "must-not-survive-reset"},
    }
    prior["snapshot_sha256"] = _digest_json(prior)
    path.with_suffix(".snapshot.json").write_text(json.dumps(prior), encoding="utf-8")
    monkeypatch.setattr(OperationalMetricsLedger, "_db", lambda self: (_ for _ in ()).throw(OSError("ctor crash")))

    with pytest.raises(OSError, match="ctor crash"):
        OperationalMetricsLedger(path)
    assert OperationalMetricsLedger.read_snapshot(path) == {"status": "STALE", "reason": "WRITER_IN_PROGRESS"}


def test_cli_status_missing_metrics_database_creates_nothing(tmp_path):
    import json

    rid = "b" * 64
    root = tmp_path / "TEST" / rid
    root.mkdir(parents=True)
    (root / "prompt.txt").write_text("fixture", encoding="utf-8")
    (root / "harness.json").write_text('{"prompt_sha256":"' + __import__("hashlib").sha256(b"fixture").hexdigest() + '","request":{}}', encoding="utf-8")
    (root / "runner.json").write_text('{"state":"NEW","media":{}}', encoding="utf-8")
    before = {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()}
    completed = subprocess.run(
        [sys.executable, "-c", "from src.hybrid.autonomous_cli import main; import sys; raise SystemExit(main(sys.argv[1:]))", "status", "--workspace", str(tmp_path), "--request-id", rid],
        cwd=Path(__file__).parents[2], text=True, capture_output=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)["operational_metrics"] == {"status": "MISSING"}
    assert {path.name: path.read_bytes() for path in root.iterdir() if path.is_file()} == before
    assert not (root / "operational-metrics.sqlite3").exists()
    assert not (root / "operational-metrics.sqlite3-wal").exists()


def test_profile_d_uses_terra_medium_and_luna_low_with_explicit_escalation():
    from src.providers.llm.core_model_router import CoreModelRouter

    router = CoreModelRouter.profile_d()
    assert router.route("script").model == "gpt-5.6-terra"
    assert router.route("script").reasoning_effort == "medium"
    assert router.route("metadata").model == "gpt-5.6-luna"
    assert router.escalate("script").model == "gpt-5.6-sol"


def test_director_factory_exposes_the_single_autonomous_runner(tmp_path):
    from src.agents.director import DirectorAgent

    runner = DirectorAgent().create_autonomous_two_gate_runner(
        "TEST-EP",
        tmp_path / "runner",
        policy(),
        tmp_path / "jobs.db",
        limit=Decimal("1.00"),
    )

    assert runner.episode_id == "TEST-EP"
    assert runner.ledger.binding["policy_sha256"] == runner.policy.digest
    assert runner.ledger.binding["mode"] == "TEST"


def test_runner_requires_a_compatible_test_ledger_before_dispatch(tmp_path):
    from src.hybrid.autonomous import (
        AutonomousPolicy,
        AutonomousTwoGateRunner,
        PolicyError,
    )
    from src.hybrid.standing_budget import StandingBudgetLedger

    parsed = AutonomousPolicy.from_dict(policy())
    with pytest.raises(PolicyError, match="ledger"):
        AutonomousTwoGateRunner(
            tmp_path / "missing",
            "TEST-EP",
            parsed,
            Executor(tmp_path / "jobs.db", Config(limit=Decimal("1.00"))),
        )
    incompatible = StandingBudgetLedger(
        tmp_path / "incompatible.sqlite3",
        policy_sha256="0" * 64,
        episode_limit_usd="1.00",
        month_limit_usd="3.00",
        experiment_limit_usd="0.20",
        mode="TEST",
    )
    with pytest.raises(PolicyError, match="ledger"):
        AutonomousTwoGateRunner(
            tmp_path / "wrong",
            "TEST-EP",
            parsed,
            Executor(tmp_path / "other-jobs.db", Config(limit=Decimal("1.00"))),
            incompatible,
        )


def test_two_runner_instances_serialize_start_and_crashed_lock_holder_releases(
    tmp_path,
):
    from src.hybrid.autonomous import AutonomousPolicy, AutonomousTwoGateRunner
    from src.hybrid.locks import try_lock

    parsed = AutonomousPolicy.from_dict(policy())
    root, db = tmp_path / "run", tmp_path / "jobs.db"
    first = AutonomousTwoGateRunner(
        root,
        "TEST-EP",
        parsed,
        Executor(db, Config(limit=Decimal("1.00"))),
        ledger(tmp_path, parsed),
    )
    second = AutonomousTwoGateRunner(
        root,
        "TEST-EP",
        parsed,
        Executor(db, Config(limit=Decimal("1.00"))),
        ledger(tmp_path, parsed),
    )
    candidate = job(tmp_path)
    first.start({"packet": "test"}, [candidate])
    assert second.start({"packet": "test"}, [candidate])["state"] == "READY"

    lock_path = tmp_path / "crash.lock"
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from src.hybrid.locks import try_lock; import sys,time; fd=try_lock(sys.argv[1]); print(bool(fd), flush=True); time.sleep(30)",
            str(lock_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        stdout=subprocess.PIPE,
    )
    assert holder.stdout is not None and holder.stdout.readline().strip() == "True"
    assert try_lock(lock_path) is None
    holder.kill()
    holder.wait(timeout=10)
    deadline = time.monotonic() + 3
    acquired = None
    while acquired is None and time.monotonic() < deadline:
        acquired = try_lock(lock_path)
        if acquired is None:
            time.sleep(0.02)
    assert acquired is not None
    os.close(acquired)


def test_production_can_run_when_publication_is_explicitly_disabled(tmp_path):
    from src.hybrid.autonomous import AutonomousPolicy

    document = policy()
    document["publication"] = {"enabled": False}
    parsed = AutonomousPolicy.from_dict(document)
    assert parsed.publication_enabled is False


def test_cache_rejects_tampered_cached_bytes(tmp_path):
    from src.hybrid.autonomous import ArtifactCache

    cache = ArtifactCache(tmp_path / "cache")
    source = tmp_path / "audio.bin"
    source.write_bytes(b"audio")
    key = cache.put("tts", [source], {"voice": "test"}, source)
    cached = tmp_path / "cache" / "tts" / key
    cached.write_bytes(b"attacker replacement")
    with pytest.raises(ValueError, match="cache.*hash"):
        cache.get("tts", [source], {"voice": "test"})


def test_start_binds_packet_and_resume_jobs_across_a_fresh_process(tmp_path):
    from src.hybrid.autonomous import AutonomousPolicy, AutonomousTwoGateRunner

    candidate = job(tmp_path)
    root = tmp_path / "run"
    db = tmp_path / "jobs.db"
    parsed = AutonomousPolicy.from_dict(policy())
    first = AutonomousTwoGateRunner(
        root,
        "TEST-EP",
        parsed,
        Executor(db, Config(limit=Decimal("1.00"))),
        ledger(tmp_path, parsed),
    )
    started = first.start({"prompt": "exact prompt", "packet": "test"}, [candidate])
    assert started["packet_sha256"]
    fresh = AutonomousTwoGateRunner(
        root,
        "TEST-EP",
        parsed,
        Executor(db, Config(limit=Decimal("1.00"))),
        ledger(tmp_path, parsed),
    )
    with pytest.raises(ValueError, match="packet"):
        fresh.assert_packet({"prompt": "changed", "packet": "test"})
    altered = Job(
        candidate.scene,
        candidate.category,
        candidate.mode,
        candidate.endpoint,
        {"prompt": "changed"},
        candidate.manifest,
        candidate.cost,
    )
    with pytest.raises(ValueError, match="hash-bound"):
        fresh.validate_resume_jobs([altered])
    assert (
        fresh.validate_resume_jobs([candidate])[candidate.request_id]["request_id"]
        == candidate.request_id
    )


def test_gate_refuses_artifact_replaced_after_approval_and_publish_intent_is_durable(
    tmp_path,
):
    from src.hybrid.autonomous import (
        AutonomousPolicy,
        AutonomousTwoGateRunner,
        PolicyError,
    )

    parsed = AutonomousPolicy.from_dict(policy())
    runner = AutonomousTwoGateRunner(
        tmp_path / "run",
        "TEST-EP",
        parsed,
        Executor(tmp_path / "jobs.db", Config(limit=Decimal("1.00"))),
        ledger(tmp_path, parsed),
    )
    runner.start({"packet": "test"}, [job(tmp_path)])
    request_id = runner._load()["jobs"][0]["request_id"]
    runner._save(
        {
            **runner._load(),
            "state": "MEDIA_PENDING",
            "qa": {request_id: {"result_sha256": "test"}},
        }
    )
    thumbnail, video = final_media(tmp_path)
    runner.stage_delivery(
        thumbnail,
        video,
        qa={
            "approved": True,
            "mode": "TEST",
            "subject": "technical-test",
            "issuer": "TEST",
        },
    )
    runner.record_human_gate("thumbnail", thumbnail, approved=True, reviewer="human")
    runner.record_human_gate("video", video, approved=True, reviewer="human")
    video.write_bytes(b"replaced")
    with pytest.raises(PolicyError, match="bytes"):
        runner.publish(lambda package: {"video_id": "x", "readback": {"video_id": "x"}})
    assert runner._load()["state"] == "READY_TO_PUBLISH"


def test_qa_fail_is_hash_bound_and_does_not_open_human_gate(tmp_path):
    from src.hybrid.autonomous import AutonomousPolicy, AutonomousTwoGateRunner

    parsed = AutonomousPolicy.from_dict(policy())
    runner = AutonomousTwoGateRunner(
        tmp_path / "run",
        "TEST-EP",
        parsed,
        Executor(tmp_path / "jobs.db", Config(limit=Decimal("1.00"))),
        ledger(tmp_path, parsed),
    )
    candidate = job(tmp_path)
    runner.start({"packet": "test"}, [candidate])
    receipt = {
        "status": "COMPLETE",
        "result": str(tmp_path / "not-used"),
        "result_sha256": "0" * 64,
    }
    with pytest.raises(ValueError, match="QA FAIL"):
        runner.record_qa(
            candidate.request_id,
            receipt,
            {"approved": False, "reviewer": "independent"},
        )
    assert runner._load()["state"] == "READY"


@pytest.mark.asyncio
async def test_cancelled_executor_run_marks_reservation_unknown_and_propagates(tmp_path):
    from src.hybrid.autonomous import AutonomousPolicy, AutonomousTwoGateRunner

    parsed = AutonomousPolicy.from_dict(policy())
    budget = ledger(tmp_path, parsed)
    runner = AutonomousTwoGateRunner(
        tmp_path / "run",
        "TEST-EP",
        parsed,
        Executor(tmp_path / "jobs.db", Config(limit=Decimal("1.00"))),
        budget,
    )
    candidate = job(tmp_path)
    runner.start({"packet": "test"}, [candidate])

    class CancellingProvider:
        mode = "TEST"

        def __init__(self):
            self.submits = 0

        async def submit(self, job, request_id, checkpoint):
            self.submits += 1
            raise asyncio.CancelledError

        async def recover(self, job, request_id, provider_id, partial, checkpoint):
            raise AssertionError("cancelled submit must not enter recovery")

    provider = CancellingProvider()
    with pytest.raises(asyncio.CancelledError):
        await runner.resume(provider, lambda receipt: receipt)

    assert provider.submits == 1
    assert (
        budget.reserve(
            candidate.request_id,
            "TEST-EP",
            candidate.endpoint,
            candidate.cost,
        )["state"]
        == "UNKNOWN"
    )
