"""Generic protected composition admission is not a LIVE readiness claim."""
from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.execution import Job, ProviderResult
from src.hybrid.planner import Config
from src.hybrid.worker import DurableEpisodeWorker, EpisodeTaskQueue


class StaticTestTransport:
    """Immutable local transport fixture; its receipt is not provider evidence."""

    mode = "TEST"

    def __init__(self, root: Path):
        self.root = root
        self.submits = 0
        self.recovers = 0

    async def submit(self, job, request_id, checkpoint):
        self.submits += 1
        checkpoint(provider_id="STATIC-" + request_id[:12])
        output = self.root / "candidate.bin"
        output.write_bytes(b"static immutable TEST candidate")
        return ProviderResult(output, job.cost)

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        self.recovers += 1
        assert provider_id == "STATIC-" + request_id[:12]
        return ProviderResult(self.root / "candidate.bin", job.cost)


def test_empty_protected_registry_fails_before_factory_or_executor_access(tmp_path):
    from src.hybrid.generic_live_composition import (
        GenericLiveCompositionRegistry,
        LiveCompositionBlocked,
    )

    registry = GenericLiveCompositionRegistry.production()
    with pytest.raises(LiveCompositionBlocked, match="not qualified"):
        asyncio.run(registry.admit("GENERIC-NEXT-VIDEO"))
    assert not list(tmp_path.iterdir())


def test_caller_cannot_construct_a_live_registry_or_inject_a_callback():
    from src.hybrid.generic_live_composition import GenericLiveCompositionRegistry

    with pytest.raises(TypeError, match="production/static-test factories"):
        GenericLiveCompositionRegistry(operations={"generic-next-video": object()}, domain="LIVE")


def test_production_resolver_is_not_a_registry_and_rechecks_protected_loader(monkeypatch):
    from src.hybrid.generic_live_composition import (
        GenericLiveCompositionRegistry,
        LiveCompositionBlocked,
    )

    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("caller callback must never be accepted")

    registry = GenericLiveCompositionRegistry.production()
    assert not hasattr(registry, "_operations")
    with pytest.raises(LiveCompositionBlocked, match="protected composition"):
        asyncio.run(registry.admit("TEST-NEXT-VIDEO-001", callback=forbidden))
    assert calls == []


def test_operator_readiness_reports_protected_loader_failure_without_enablement():
    from src.hybrid.generic_live_composition import GenericLiveCompositionRegistry

    report = GenericLiveCompositionRegistry.production().readiness("GENERIC-NEXT-VIDEO")

    assert report["status"] == "BLOCKED"
    assert report["live_enabled"] is False
    assert "protected operation" in report["reason"]


def _job(tmp_path: Path) -> Job:
    reference = tmp_path / "reference.png"
    Image.new("RGB", (8, 8), "navy").save(reference)
    asset = FrozenAsset.approve(reference, "static TEST fixture", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "static TEST fixture", "TEST"), "static TEST fixture", "TEST")
    return Job("GENERIC-S001", "first", "TEST", "static-test-operation", {"prompt": "static fixture"}, manifest, Decimal("0.01"))


@pytest.mark.asyncio
async def test_worker_uses_registered_test_composition_and_fresh_resume_recovers_without_resubmit(tmp_path):
    from src.hybrid.generic_live_composition import GenericLiveCompositionRegistry

    job = _job(tmp_path)
    transport = StaticTestTransport(tmp_path)
    registry = GenericLiveCompositionRegistry.for_static_test(
        operation_id="generic-next-video",
        executor_database=tmp_path / "executor.sqlite3",
        executor_config=Config(limit=Decimal("1.00"), concurrency=1),
        job=job,
        transport=transport,
    )
    queue = EpisodeTaskQueue(tmp_path / "worker.sqlite3")
    queue.enqueue("GENERIC-NEXT-VIDEO", "ignored-caller-root", "fixture")

    async def handler(_task):
        receipt = await registry.admit("generic-next-video")
        return {"state": receipt["status"], "next_action": "TERMINAL"}

    await DurableEpisodeWorker(queue, "generic-worker", handler).run_once()
    assert queue.status("GENERIC-NEXT-VIDEO")["status"] == "DONE"
    assert transport.submits == 1

    reopened = GenericLiveCompositionRegistry.for_static_test(
        operation_id="generic-next-video",
        executor_database=tmp_path / "executor.sqlite3",
        executor_config=Config(limit=Decimal("1.00"), concurrency=1),
        job=job,
        transport=transport,
    )
    receipt = await reopened.admit("generic-next-video")
    assert receipt["status"] == "COMPLETE"
    assert transport.submits == 1
    assert transport.recovers == 0


def test_live_text_operation_is_unprovisioned_before_credentials_or_state(
    tmp_path, monkeypatch
):
    """A generic LIVE document cannot reach provider, executor, or ledger setup."""
    import json
    from hashlib import sha256

    import src.hybrid.qualified_episode_operation as qualified

    root = tmp_path / "protected"
    (root / "operations").mkdir(parents=True)
    (root / "payloads").mkdir()
    payload = {
        "prompt": "A child-safe biblical landscape rendered as an establishing image.",
        "aspect_ratio": "4:3",
        "resolution": "1k",
        "output_format": "png",
        "enable_prompt_expansion": False,
        "safety_tolerance": 2,
    }
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    (root / "payloads" / "generic-live.json").write_bytes(payload_bytes)
    policy = "a" * 64
    operation = {
        "schema": "qualified-episode-operation/v1",
        "operation_id": "GENERIC_LIVE_TEXT_001",
        "episode_id": "GENERIC_EPISODE_001",
        "mode": "LIVE",
        "executor_database": "state/executor.sqlite3",
        "ledger_database": "state/ledger.sqlite3",
        "ledger_policy_sha256": policy,
        "endpoint": "blackforestlabs/flux-3/text-to-image",
        "scene": "GENERIC_LIVE_TEXT_001",
        "category": "first",
        "input_kind": "TEXT_TO_IMAGE",
        "payload": {"path": "payloads/generic-live.json", "sha256": sha256(payload_bytes).hexdigest()},
        "maximum_usd": "0.048",
        "provider_kind": "FAL_FLUX_TEXT_TO_IMAGE",
    }
    (root / "operations" / "GENERIC_LIVE_TEXT_001.json").write_text(
        json.dumps(operation, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    monkeypatch.setattr(qualified, "_PROTECTED_ROOT", root)
    monkeypatch.delenv("FAL_KEY", raising=False)

    with pytest.raises(
        qualified.QualifiedEpisodeOperationError,
        match="generic LIVE qualification is unprovisioned",
    ):
        qualified.build_qualified_episode_operation("GENERIC_LIVE_TEXT_001")

    assert not (root / "state").exists()


def test_live_text_operation_tampered_payload_blocks_before_state_construction(tmp_path, monkeypatch):
    """A LIVE-shaped fixture is code coverage, not a provider authority claim."""
    import json
    from hashlib import sha256

    import src.hybrid.qualified_episode_operation as qualified

    root = tmp_path / "protected"
    (root / "operations").mkdir(parents=True)
    (root / "payloads").mkdir()
    payload_path = root / "payloads" / "payload.json"
    original = b'{"aspect_ratio":"4:3","enable_prompt_expansion":false,"output_format":"png","prompt":"A child-safe biblical establishing image.","resolution":"1k","safety_tolerance":2}'
    payload_path.write_bytes(original)
    (root / "operations" / "GENERIC_LIVE_TEXT_002.json").write_text(json.dumps({
        "schema": "qualified-episode-operation/v1", "operation_id": "GENERIC_LIVE_TEXT_002",
        "episode_id": "GENERIC_EPISODE_002", "mode": "LIVE",
        "executor_database": "state/executor.sqlite3", "ledger_database": "state/ledger.sqlite3",
        "ledger_policy_sha256": "b" * 64, "endpoint": "blackforestlabs/flux-3/text-to-image",
        "scene": "GENERIC_LIVE_TEXT_002", "category": "first", "input_kind": "TEXT_TO_IMAGE",
        "payload": {"path": "payloads/payload.json", "sha256": sha256(original).hexdigest()},
        "maximum_usd": "0.048", "provider_kind": "FAL_FLUX_TEXT_TO_IMAGE",
    }, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    payload_path.write_bytes(original.replace(b"child-safe", b"tampered"))
    monkeypatch.setattr(qualified, "_PROTECTED_ROOT", root)

    with pytest.raises(qualified.QualifiedEpisodeOperationError, match="payload hash binding"):
        qualified.build_qualified_episode_operation("GENERIC_LIVE_TEXT_002")
    assert not (root / "state").exists()


def test_forged_live_generic_job_blocks_before_prepare_or_reservation(tmp_path, monkeypatch):
    """Fixed storage alone cannot turn a caller-built job into LIVE authority."""
    import json
    from decimal import Decimal
    from hashlib import sha256

    import src.hybrid.qualified_episode_operation as qualified
    from src.hybrid.execution import Executor
    from src.hybrid.planner import Config
    from src.hybrid.standing_budget import StandingBudgetLedger

    root = tmp_path / "protected"
    (root / "operations").mkdir(parents=True)
    (root / "payloads").mkdir()
    payload = {"prompt": "approved", "aspect_ratio": "4:3", "resolution": "1k", "output_format": "png", "enable_prompt_expansion": False, "safety_tolerance": 2}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    (root / "payloads" / "payload.json").write_bytes(raw)
    operation = {"schema": "qualified-episode-operation/v1", "operation_id": "GENERIC_LIVE_TEXT_003", "episode_id": "GENERIC_EPISODE_003", "mode": "LIVE", "executor_database": "state/executor.sqlite3", "ledger_database": "state/ledger.sqlite3", "ledger_policy_sha256": "c" * 64, "endpoint": "blackforestlabs/flux-3/text-to-image", "scene": "GENERIC_LIVE_TEXT_003", "category": "first", "input_kind": "TEXT_TO_IMAGE", "payload": {"path": "payloads/payload.json", "sha256": sha256(raw).hexdigest()}, "maximum_usd": "0.048", "provider_kind": "FAL_FLUX_TEXT_TO_IMAGE"}
    (root / "operations" / "GENERIC_LIVE_TEXT_003.json").write_text(json.dumps(operation, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    monkeypatch.setattr(qualified, "_PROTECTED_ROOT", root)
    ledger = StandingBudgetLedger(root / "state" / "ledger.sqlite3", policy_sha256="c" * 64, episode_limit_usd="1", month_limit_usd="1", experiment_limit_usd="1", mode="LIVE")
    executor = Executor(root / "state" / "executor.sqlite3", Config(limit=Decimal(1)), standing_ledger=ledger)
    forged = qualified.QualifiedGenericJob("GENERIC_LIVE_TEXT_003", "first", "LIVE", operation["endpoint"], {**payload, "prompt": "forged"}, None, Decimal("0.048"), input_kind="TEXT_TO_IMAGE", operation_id=operation["operation_id"], episode_id=operation["episode_id"], ledger_policy_sha256="c" * 64)

    with pytest.raises(qualified.QualifiedEpisodeOperationError, match="does not match"):
        executor.prepare_intent(forged)
    assert executor.inspect(forged.request_id) is None
