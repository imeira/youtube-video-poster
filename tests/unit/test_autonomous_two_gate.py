"""Vertical acceptance tests for the durable autonomous two-gate runner."""
from __future__ import annotations

from decimal import Decimal

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
    return Manifest.freeze((frozen,), FrozenAsset.approve(sheet, "test-reference", "TEST"), "test-reference", "TEST")


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
    return Job("S001", "first", "TEST", "test-image", {"prompt": "test"}, manifest(tmp_path), Decimal("0.10"))


def policy():
    return {
        "schema": 1,
        "version": "test-v1",
        "episode_cap_usd": "1.00",
        "month_cap_usd": "3.00",
        "experiment_cap_usd": "0.20",
        "allowed_providers": ["test-image"],
        "publication": {"enabled": True, "channel": "test-channel", "visibility": "private"},
    }


@pytest.mark.asyncio
async def test_runner_resumes_executor_and_reaches_two_hash_bound_human_gates(tmp_path):
    from src.hybrid.autonomous import AutonomousPolicy, AutonomousTwoGateRunner

    runner = AutonomousTwoGateRunner(tmp_path / "run", "TEST-EP", AutonomousPolicy.from_dict(policy()), Executor(tmp_path / "jobs.db", Config(limit=Decimal("1.00"))))
    provider = FixtureProvider(tmp_path)
    candidate = job(tmp_path)

    started = runner.start({"packet": "test"}, [candidate])
    assert started["state"] == "READY"
    waiting_thumbnail = await runner.resume(provider, lambda receipt: {"approved": True, "reviewer": "independent-test-qa", "codes": []})
    assert waiting_thumbnail["state"] == "WAITING_THUMBNAIL_APPROVAL"
    assert provider.calls == 1

    resumed = await runner.resume(provider, lambda receipt: (_ for _ in ()).throw(AssertionError("QA must not rerun")))
    assert resumed["state"] == "WAITING_THUMBNAIL_APPROVAL"
    assert provider.calls == 1

    thumbnail = tmp_path / "thumbnail.png"
    thumbnail.write_bytes(b"thumbnail")
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    assert runner.record_human_gate("thumbnail", thumbnail, approved=True, reviewer="human-test")["state"] == "WAITING_VIDEO_APPROVAL"
    assert runner.record_human_gate("video", video, approved=True, reviewer="human-test")["state"] == "READY_TO_PUBLISH"

    published = runner.publish(lambda package: {"video_id": "TEST-video-id", "readback": package["video_sha256"]})
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
        valid.authorize_paid("test-image", None, monthly_spend=Decimal(0), experiment_spend=Decimal(0))


def test_metrics_separate_quota_tokens_and_money_and_cache_is_hash_compatible(tmp_path):
    from src.hybrid.autonomous import ArtifactCache, PersistentMetrics

    metrics = PersistentMetrics(tmp_path / "metrics.json")
    metrics.record("request-1", model="gpt-test", tokens_input=10, tokens_cached=3, tokens_output=5, tokens_reasoning=7, latency_s=1.5, money_usd="0.25", quota_units=99)
    assert metrics.load()["totals"] == {"money_usd": "0.25", "quota_units": 99, "tokens_cached": 3, "tokens_input": 10, "tokens_output": 5, "tokens_reasoning": 7}

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
        "TEST-EP", tmp_path / "runner", policy(), tmp_path / "jobs.db", limit=Decimal("1.00")
    )

    assert runner.episode_id == "TEST-EP"
