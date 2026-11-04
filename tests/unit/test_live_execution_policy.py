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

REQUEST = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
REVISION = "818556d97edb648f"


def _manifest(tmp_path: Path, mode: str = "TEST") -> Manifest:
    reference = tmp_path / "reference.png"
    Image.new("RGB", (32, 18), "green").save(reference)
    asset = FrozenAsset.approve(reference, "independent-qa", mode)
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    return Manifest.freeze((asset,), FrozenAsset.approve(sheet, "independent-qa", mode), "independent-qa", mode)


def _job(tmp_path: Path) -> Job:
    return Job("S001", "first", "TEST", "seedance-v1-5-pro-i2v", {
        "prompt": "Jacó rests safely at Betel", "image_urls": [str(tmp_path / "reference.png")],
        "image_size": {"width": 32, "height": 18},
    }, _manifest(tmp_path), Decimal("0.04"))


class SealedTestTransport:
    """A local deterministic TEST transport; it cannot request LIVE admission."""

    mode = "TEST"

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


def test_live_admission_is_blocked_before_license_or_provider_use(tmp_path):
    from src.hybrid.live_execution_policy import (
        JacoExecutionPolicyResolver,
        LiveAdmissionError,
    )

    historical = tmp_path / "financial.json"
    historical.write_text('{"schema":"jaco-factory-financial-reconciliation/1","request_hash":"' + REQUEST + '","revision":"' + REVISION + '","currency":"USD","total_episode_cap":"10.00","status":"APPROVED_USD10","approval_scope":"FINANCIAL_ONLY","authorizes_generation":false}', encoding="utf-8")
    resolver = JacoExecutionPolicyResolver(historical)

    with pytest.raises(LiveAdmissionError, match="LIVE remains blocked"):
        resolver.admit(provider=SealedTestTransport(tmp_path), license_status="MISSING", provider_registry={})
    assert not (tmp_path / "candidate.png").exists()


@pytest.mark.asyncio
async def test_sealed_test_transport_exercises_test_executor_without_live_authority(tmp_path):
    provider = SealedTestTransport(tmp_path)
    job = _job(tmp_path)
    executor = Executor(tmp_path / "jobs.sqlite3", Config(limit=Decimal("10.00"), concurrency=1))

    receipt = await executor.run(job, provider)

    assert receipt["status"] == "COMPLETE"
    assert provider.submits == 1
    assert receipt["mode"] == "TEST"
    assert receipt["authorization"] is None


def test_test_mode_provider_cannot_be_registered_as_live(tmp_path):
    from src.hybrid.live_execution_policy import (
        LiveAdmissionError,
        trusted_provider_registry,
    )

    transport = SealedTestTransport(tmp_path)
    with pytest.raises(LiveAdmissionError, match="trusted composition"):
        trusted_provider_registry(transport)


def test_caller_controlled_transport_attributes_cannot_register_or_admit_live(tmp_path):
    """No Python attributes, local quote, or literal license can create LIVE authority."""
    from src.hybrid.live_execution_policy import (
        JacoExecutionPolicyResolver,
        LiveAdmissionError,
        trusted_provider_registry,
    )

    historical = tmp_path / "financial.json"
    historical.write_text(
        '{"schema":"jaco-factory-financial-reconciliation/1","request_hash":"' + REQUEST
        + '","revision":"' + REVISION
        + '","currency":"USD","total_episode_cap":"10.00","status":"APPROVED_USD10",'
        '"approval_scope":"FINANCIAL_ONLY","authorizes_generation":false}',
        encoding="utf-8",
    )
    provider = SealedTestTransport(tmp_path)
    provider.mode = "LIVE"
    provider.test_transport = True
    provider.trusted_endpoint = "seedance-v1-5-pro-i2v"
    with pytest.raises(LiveAdmissionError, match="trusted composition"):
        trusted_provider_registry(provider)
    test_job = _job(tmp_path)
    forged_live_job = Job(
        test_job.scene, test_job.category, "LIVE", test_job.endpoint,
        {"model": "caller-selected", "payload": "caller-selected"},
        test_job.manifest, Decimal("9.99"),
    )
    with pytest.raises(LiveAdmissionError, match="LIVE remains blocked"):
        JacoExecutionPolicyResolver(historical).admit(
            provider=provider,
            license_status="VERIFIED",
            provider_registry=object(),
            price_quote={
                "endpoint": forged_live_job.endpoint,
                "amount": "9.99",
                "evidence": "arbitrary-financial-json-string",
            },
            job=forged_live_job,
        )
    assert provider.submits == 0
    assert provider.recovers == 0
    assert not (tmp_path / "candidate.png").exists()


@pytest.mark.asyncio
async def test_director_cannot_bypass_blocked_live_service_with_raw_executor(tmp_path):
    """The Director facade must stop before price resolution or executor dispatch."""

    from src.hybrid.live_execution_policy import LiveAdmissionError

    historical = tmp_path / "financial.json"
    historical.write_text(
        '{"schema":"jaco-factory-financial-reconciliation/1","request_hash":"' + REQUEST
        + '","revision":"' + REVISION
        + '","currency":"USD","total_episode_cap":"10.00","status":"APPROVED_USD10",'
        '"approval_scope":"FINANCIAL_ONLY","authorizes_generation":false}',
        encoding="utf-8",
    )
    provider = SealedTestTransport(tmp_path)
    provider.mode = "LIVE"
    job = _job(tmp_path)
    live_job = Job(job.scene, job.category, "LIVE", job.endpoint, job.payload, job.manifest, job.cost)
    executor = Executor(tmp_path / "jobs.sqlite3", Config(limit=Decimal("10.00"), concurrency=1))
    price_calls = []

    class Pipeline:
        episode = SimpleNamespace(audio=SimpleNamespace(mode="LIVE"))
        run = SimpleNamespace(executor=executor)

        @staticmethod
        def baseline_jobs():
            return (live_job,)

        async def dispatch_baselines(self, *_args, **_kwargs):
            raise AssertionError("blocked LIVE policy must not dispatch")

    config = replace(get_config(), budget=BudgetConfig(
        target_usd=5, warning_usd=8, hard_limit_usd=10, require_approval_above_limit=True,
    ))
    with pytest.raises(LiveAdmissionError, match="trusted composition"):
        await DirectorAgent(config=config).dispatch_current_policy_baselines(
            "JACO_GENESIS28", Pipeline(), provider,
            historical_financial_record=historical,
            license_status="VERIFIED",
            price_resolver=lambda _job: price_calls.append(True),
        )

    assert price_calls == []
    assert provider.submits == 0
    assert provider.recovers == 0
    assert not (tmp_path / "candidate.png").exists()
