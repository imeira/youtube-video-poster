"""LIVE policy admission is independent of TEST fixtures and publication."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src.agents.director import DirectorAgent
from src.config.loader import BudgetConfig, get_config
from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.execution import Executor, Job, ProviderResult
from src.hybrid.planner import Config
from src.state.machine import EpisodeState, EpisodeStateStore
from src.storage.episode_fs import EpisodeFS

REQUEST = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
REVISION = "818556d97edb648f"


def _manifest(tmp_path: Path) -> Manifest:
    reference = tmp_path / "reference.png"
    Image.new("RGB", (32, 18), "green").save(reference)
    asset = FrozenAsset.approve(reference, "independent-qa", "LIVE")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    return Manifest.freeze((asset,), FrozenAsset.approve(sheet, "independent-qa", "LIVE"), "independent-qa", "LIVE")


def _job(tmp_path: Path) -> Job:
    return Job("S001", "first", "LIVE", "seedance-v1-5-pro-i2v", {
        "prompt": "Jacó rests safely at Betel", "image_urls": [str(tmp_path / "reference.png")],
        "image_size": {"width": 32, "height": 18},
    }, _manifest(tmp_path), Decimal("0.04"))


class SealedTestTransport:
    """A local deterministic transport: it can prove mechanics, never authority."""

    mode = "LIVE"
    test_transport = True
    trusted_endpoint = "seedance-v1-5-pro-i2v"

    def __init__(self, root: Path):
        self.root = root
        self.submits = 0
        self.recovers = 0

    async def submit(self, job, request_id, checkpoint):
        self.submits += 1
        checkpoint(provider_id="sealed-live-001")
        output = self.root / "candidate.png"
        Image.new("RGB", (32, 18), "orange").save(output)
        return ProviderResult(output, job.cost)

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        self.recovers += 1
        return ProviderResult(self.root / "candidate.png", job.cost)


def test_current_jaco_policy_preserves_historical_generation_false_and_disables_publication(tmp_path):
    from src.hybrid.live_execution_policy import JacoExecutionPolicyResolver

    historical = tmp_path / "financial.json"
    historical.write_text(
        '{"schema":"jaco-factory-financial-reconciliation/1","request_hash":"' + REQUEST + '","revision":"' + REVISION + '","currency":"USD","total_episode_cap":"10.00","status":"APPROVED_USD10","approval_scope":"FINANCIAL_ONLY","authorizes_generation":false}',
        encoding="utf-8",
    )

    policy = JacoExecutionPolicyResolver(historical).resolve()

    assert policy.request_id == REQUEST
    assert policy.revision == REVISION
    assert policy.episode_cap_usd == Decimal("10.00")
    assert policy.publication_enabled is False
    assert policy.routine_human_gates == ("thumbnail", "final_video")
    assert policy.historical_generation_authorized is False


def test_missing_license_or_unregistered_provider_fails_closed_before_admission(tmp_path):
    from src.hybrid.live_execution_policy import (
        JacoExecutionPolicyResolver,
        LiveAdmissionError,
    )

    historical = tmp_path / "financial.json"
    historical.write_text('{"schema":"jaco-factory-financial-reconciliation/1","request_hash":"' + REQUEST + '","revision":"' + REVISION + '","currency":"USD","total_episode_cap":"10.00","status":"APPROVED_USD10","approval_scope":"FINANCIAL_ONLY","authorizes_generation":false}', encoding="utf-8")
    resolver = JacoExecutionPolicyResolver(historical)

    with pytest.raises(LiveAdmissionError, match="license"):
        resolver.admit(provider=SealedTestTransport(tmp_path), license_status="MISSING", provider_registry={})
    assert not (tmp_path / "candidate.png").exists()


@pytest.mark.asyncio
async def test_sealed_test_transport_exercises_live_executor_admission_without_test_authority(tmp_path):
    from src.hybrid.live_execution_policy import (
        JacoExecutionPolicyResolver,
        trusted_provider_registry,
    )

    historical = tmp_path / "financial.json"
    historical.write_text('{"schema":"jaco-factory-financial-reconciliation/1","request_hash":"' + REQUEST + '","revision":"' + REVISION + '","currency":"USD","total_episode_cap":"10.00","status":"APPROVED_USD10","approval_scope":"FINANCIAL_ONLY","authorizes_generation":false}', encoding="utf-8")
    provider = SealedTestTransport(tmp_path)
    job = _job(tmp_path)
    resolver = JacoExecutionPolicyResolver(historical)
    admission = resolver.admit(
        provider=provider, license_status="VERIFIED", provider_registry=trusted_provider_registry(provider),
        price_quote={"endpoint": job.endpoint, "amount": "0.04", "evidence": "sealed-test-quote"}, job=job,
    )
    executor = Executor(tmp_path / "jobs.sqlite3", Config(limit=Decimal("10.00"), concurrency=1))

    receipt = await executor.run(job, provider, authorization=admission.authorization, price=admission.price)

    assert receipt["status"] == "COMPLETE"
    assert provider.submits == 1
    assert receipt["authorization"]["reviewer"] == "CURRENT_POLICY_MACHINE_ADMISSION"
    assert admission.policy.publication_enabled is False


def test_test_mode_provider_cannot_be_registered_as_live(tmp_path):
    from src.hybrid.live_execution_policy import (
        LiveAdmissionError,
        trusted_provider_registry,
    )

    transport = SealedTestTransport(tmp_path)
    transport.mode = "TEST"
    with pytest.raises(LiveAdmissionError, match="LIVE"):
        trusted_provider_registry(transport)


@pytest.mark.asyncio
async def test_director_handoff_uses_current_policy_then_existing_live_kernel(tmp_path, monkeypatch):
    """Policy -> Director -> Executor reserve/submit -> QA packet is one slice."""
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path / "episodes"))
    historical = tmp_path / "financial.json"
    historical.write_text('{"schema":"jaco-factory-financial-reconciliation/1","request_hash":"' + REQUEST + '","revision":"' + REVISION + '","currency":"USD","total_episode_cap":"10.00","status":"APPROVED_USD10","approval_scope":"FINANCIAL_ONLY","authorizes_generation":false}', encoding="utf-8")
    config = replace(get_config(), budget=BudgetConfig(target_usd=5, warning_usd=8, hard_limit_usd=10, require_approval_above_limit=True))
    director = DirectorAgent(config=config)
    fs = EpisodeFS("JACO_GENESIS28", config)
    fs.create_dirs()
    EpisodeStateStore(episode_id="JACO_GENESIS28", current_state=EpisodeState.GENERATING_IMAGES).save(fs.paths.state_json)
    job = _job(tmp_path)
    provider = SealedTestTransport(tmp_path)
    executor = Executor(tmp_path / "jobs.sqlite3", Config(limit=Decimal("10.00"), concurrency=1))

    class Pipeline:
        episode = SimpleNamespace(audio=SimpleNamespace(mode="LIVE"))
        run = SimpleNamespace(executor=executor)
        def baseline_jobs(self): return (job,)
        async def dispatch_baselines(self, actual_provider, *, authorizations, prices):
            assert actual_provider is provider
            return {job.request_id: await executor.run(job, provider, authorization=authorizations[job.request_id], price=prices[job.request_id])}
        def prepare_qa_packets(self):
            receipt = executor.inspect(job.request_id)
            return {job.request_id: {"result_sha256": receipt["result_sha256"]}}

    result = await director.dispatch_current_policy_baselines(
        "JACO_GENESIS28", Pipeline(), provider, historical_financial_record=historical,
        license_status="VERIFIED", price_resolver=lambda _: {"endpoint": job.endpoint, "amount": "0.04", "evidence": "sealed-test-quote"},
    )

    assert result["state"] == "VISUAL_QA"
    assert provider.submits == 1
    assert executor.inspect(job.request_id)["status"] == "COMPLETE"
