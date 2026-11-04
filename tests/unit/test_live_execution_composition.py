"""Configured TEST-domain composition never mints LIVE capability."""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from src.approval.telegram_inbound import TelegramInboundAdapter
from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.budget_authorization import (
    BudgetAuthorizationService,
    BudgetAuthorizationSettings,
)
from src.hybrid.execution import Job, ProviderResult
from src.hybrid.planner import Config


class Outbound:
    transport = "LOCAL_TEST"

    def __init__(self):
        self.delivery = None

    def deliver(self, *, chat_id, canonical_body):
        self.delivery = {"transport": self.transport, "chat_id": chat_id, "message_id": 501,
                         "body_sha256": hashlib.sha256(canonical_body).hexdigest()}
        return dict(self.delivery)

    def revalidate_delivery(self, document):
        return dict(self.delivery) if document == self.delivery else None


class FakeProvider:
    mode = "TEST"

    def __init__(self, root: Path):
        self.root, self.submits, self.recovers = root, 0, 0

    async def submit(self, job, request_id, checkpoint):
        self.submits += 1
        checkpoint(provider_id="test-composition-remote-1")
        output = self.root / "candidate.bin"
        output.write_bytes(b"test-only-candidate")
        return ProviderResult(output, job.cost)

    async def recover(self, *args):
        self.recovers += 1
        raise AssertionError("fresh TEST execution must submit once")


def _job(tmp_path: Path) -> Job:
    image = tmp_path / "reference.png"
    Image.new("RGB", (8, 8), "white").save(image)
    asset = FrozenAsset.approve(image, "test", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "test", "TEST"), "test", "TEST")
    return Job("SC001", "first", "TEST", "test-image", {"model": "fixture-model", "prompt": "fixture"}, manifest, Decimal("0.05"))


def _composition(tmp_path: Path):
    from src.hybrid.live_execution_policy import ConfiguredTestExecutionComposition

    clock = lambda: datetime(2026, 10, 26, 12, tzinfo=UTC)
    inbound = TelegramInboundAdapter(journal_path=tmp_path / "inbound.sqlite3", expected_chat_id="100",
                                     trusted_sender_ids=frozenset({"200"}), webhook_secret="test-webhook-secret", instance_id="composition-test")
    service = BudgetAuthorizationService(
        root=tmp_path / "authority", inbound_source=inbound, outbound_source=Outbound(),
        settings=BudgetAuthorizationSettings(chat_id="100", financial_sender_ids=frozenset({"200"}), mode="TEST"), clock=clock,
    )
    request = service.create_request({
        "schema": 1, "kind": "BUDGET_AUTHORIZATION", "mode": "TEST", "request_id": "standing-budget-001",
        "episode_id": "TEST-EP", "policy_sha256": "a" * 64, "currency": "USD", "allowed_providers": ["test-image"],
        "caps_usd": {"episode": "1.00", "month": "1.00", "experiment": "1.00"}, "expires_at": "2026-10-27T12:00:00+00:00",
    })
    delivery = service.deliver_request(request)
    assert inbound.ingest_webhook(body=json.dumps({"update_id": 1, "message": {"message_id": 2, "from": {"id": 200, "is_bot": False}, "chat": {"id": 100, "type": "private"}, "reply_to_message": {"message_id": delivery.document["message_id"]}, "text": "AUTORIZAR ORÇAMENTO standing-budget-001"}}).encode(), secret_header="test-webhook-secret")
    receipt = service.confirm(delivery)
    provider = FakeProvider(tmp_path)
    composition = ConfiguredTestExecutionComposition.from_authenticated_budget_bridge(
        bridge=service.authorized_bridge(receipt.path), executor_database=tmp_path / "executor.sqlite3",
        executor_config=Config(limit=Decimal("1.00"), concurrency=1), provider_factory=lambda: provider,
        capability_root=tmp_path / "capabilities", clock=lambda: 4_102_444_000,
    )
    return composition, provider


@pytest.mark.asyncio
async def test_configured_test_composition_persists_capability_and_runs_existing_executor_gates(tmp_path):
    composition, provider = _composition(tmp_path)
    job = _job(tmp_path)

    receipt = await composition.execute(job, quote_expires_at=4_102_444_120, qa_evidence={"verdict": "PASS", "request": job.request_id})

    capability = json.loads((tmp_path / "capabilities" / f"{job.request_id}.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "COMPLETE"
    assert provider.submits == 1
    assert capability["mode"] == "TEST"
    assert capability["endpoint"] == job.endpoint
    assert capability["model"] == "fixture-model"
    assert capability["currency"] == "USD"
    assert capability["budget_scope"].startswith("AUTHENTICATED_TEST_REQUEST:")
    assert capability["qa_evidence_sha256"]
    assert composition.production_eligible == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("mutate", ["live", "expiry", "billing", "qa"])
async def test_test_composition_rejects_cross_domain_and_untrusted_inputs_before_provider_effect(tmp_path, mutate):
    composition, provider = _composition(tmp_path)
    job = _job(tmp_path)
    kwargs = {"quote_expires_at": 4_102_444_120, "qa_evidence": {"verdict": "PASS", "request": job.request_id}}
    if mutate == "live":
        job = Job(job.scene, job.category, "LIVE", job.endpoint, job.payload, job.manifest, job.cost)
    elif mutate == "expiry":
        kwargs["quote_expires_at"] = 4_102_443_999
    elif mutate == "billing":
        composition._bridge = object()
    else:
        kwargs["qa_evidence"] = {"signature": "forged"}

    with pytest.raises((PermissionError, ValueError), match="TEST|expiry|bridge|QA"):
        await composition.execute(job, **kwargs)
    assert provider.submits == 0
