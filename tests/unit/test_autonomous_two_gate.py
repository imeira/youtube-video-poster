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
