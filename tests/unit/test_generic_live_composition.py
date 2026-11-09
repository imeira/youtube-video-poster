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
