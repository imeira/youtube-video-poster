from __future__ import annotations

import asyncio
import hashlib
import json
import math
import multiprocessing
import os
import subprocess
import wave
from datetime import UTC, datetime, timedelta
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
from src.hybrid.execution import Executor, Job
from src.hybrid.planner import Config
from src.providers.tts.azure_speech_durable_executor import (
    AZURE_SPEECH_ENDPOINT,
    AzureSpeechDurableConsumer,
    AzureSpeechFixtureTransport,
    SubmissionUnknown,
)
from src.providers.tts.azure_speech_stock_tts import VOICE, OfflineAzureSpeechFixture

TEXT = "Olá, Jacó. Deus está conosco."
LICENSE = {"license_kind": "AZURE_STOCK_NEURAL_VOICE", "voice": VOICE,
           "license_status": "PROVIDER_TERMS_REQUIRED", "allowed_use": "production_narration"}


class _Outbound:
    """TEST-only authenticated-boundary fixture with durable readback semantics."""
    transport = "LOCAL_TEST"

    def deliver(self, *, chat_id: str, canonical_body: bytes) -> dict[str, object]:
        return {"transport": self.transport, "chat_id": chat_id, "message_id": 42,
                "body_sha256": hashlib.sha256(canonical_body).hexdigest()}

    def revalidate_delivery(self, document: object) -> dict[str, object] | None:
        return dict(document) if type(document) is dict else None


def _wav_bytes(root: Path) -> bytes:
    path = root / "fixture.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(8_000)
        audio.writeframes(b"".join(int(4_000 * math.sin(index / 8)).to_bytes(2, "little", signed=True) for index in range(8_000)))
    return path.read_bytes()


def _fixture(root: Path) -> OfflineAzureSpeechFixture:
    return OfflineAzureSpeechFixture(audio_bytes=_wav_bytes(root), transcript=TEXT,
        word_boundaries=[{"text": "Olá", "offset": 0, "duration": 2_000_000}], license_metadata=LICENSE)


def _authorized_bridge(root: Path):
    now = datetime(2026, 10, 29, tzinfo=UTC)
    adapter = TelegramInboundAdapter(journal_path=root / "inbound.sqlite3", expected_chat_id="10001",
        trusted_sender_ids=frozenset({"20002"}), webhook_secret="test-webhook-secret", instance_id="azure-durable-test")
    service = BudgetAuthorizationService(root=root / "authority", inbound_source=adapter, outbound_source=_Outbound(),
        settings=BudgetAuthorizationSettings(chat_id="10001", financial_sender_ids=frozenset({"20002"}), mode="TEST"), clock=lambda: now)
    request = service.create_request({"schema": 1, "kind": "BUDGET_AUTHORIZATION", "mode": "TEST", "request_id": "azure-budget-001",
        "episode_id": "EP-AZURE-001", "policy_sha256": "a" * 64, "expires_at": (now + timedelta(hours=1)).isoformat(),
        "allowed_providers": [AZURE_SPEECH_ENDPOINT], "currency": "USD",
        "caps_usd": {"episode": "1.00", "month": "1.00", "experiment": "1.00"}})
    delivery = service.deliver_request(request)
    body = json.dumps({"update_id": 1, "message": {"message_id": 2, "from": {"id": 20002, "is_bot": False},
        "chat": {"id": 10001, "type": "private"}, "reply_to_message": {"message_id": delivery.document["message_id"]},
        "text": "AUTORIZAR ORÇAMENTO azure-budget-001"}}).encode()
    assert adapter.ingest_webhook(body=body, secret_header="test-webhook-secret")
    return service.authorized_bridge(service.confirm(delivery).path)


def _job(root: Path, *, text: str = TEXT, voice: str = VOICE, price: str = "0.05") -> Job:
    image = root / "reference.png"
    Image.new("RGB", (8, 8), "white").save(image)
    asset = FrozenAsset.approve(image, "test", "TEST")
    sheet = root / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "test", "TEST"), "test", "TEST")
    return Job("azure-narration", "first", "TEST", AZURE_SPEECH_ENDPOINT,
        {"text": text, "voice": voice, "output_path": str(root / "narration.wav")}, manifest, Decimal(price))


def _consumer(root: Path, bridge, executor, factory):
    return AzureSpeechDurableConsumer(root=root / "azure-state", output_root=root,
        bridge=bridge, executor=executor, transport_factory=factory)


@pytest.mark.asyncio
async def test_constructor_requires_absolute_reparse_safe_output_root_before_intent_or_factory(tmp_path, monkeypatch):
    bridge = _authorized_bridge(tmp_path)
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(1)))
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        return AzureSpeechFixtureTransport(_fixture(tmp_path))

    with pytest.raises(ValueError, match="absolute"):
        AzureSpeechDurableConsumer(root=Path("relative"), output_root=tmp_path / "outputs",
            bridge=bridge, executor=executor, transport_factory=factory)
    with pytest.raises(ValueError, match="absolute"):
        AzureSpeechDurableConsumer(root=tmp_path / "azure-state", output_root=Path("relative"),
            bridge=bridge, executor=executor, transport_factory=factory)
    assert calls == 0
    assert not (tmp_path / "azure-state" / "azure-operations").exists()

    outside_root = tmp_path.parent / f"outside-{tmp_path.name}"
    outside_root.mkdir()
    outside = _job(outside_root)
    consumer = _consumer(tmp_path, bridge, executor, factory)
    with pytest.raises(ValueError, match="output root"):
        await consumer.synthesize(operation_id=outside.request_id, job=outside)
    assert calls == 0
    assert not (tmp_path / "azure-state" / "azure-operations").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions are native reparse points")
def test_constructor_rejects_native_junction_output_root_before_writes(tmp_path):
    real = tmp_path / "real-output"
    real.mkdir()
    junction = tmp_path / "junction-output"
    made = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(junction), str(real)], check=False, capture_output=True)
    if made.returncode:
        pytest.skip("native junction unavailable")
    bridge = _authorized_bridge(tmp_path)
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(1)))
    with pytest.raises(ValueError, match="reparse"):
        AzureSpeechDurableConsumer(root=tmp_path / "azure-state", output_root=junction,
            bridge=bridge, executor=executor, transport_factory=lambda: pytest.fail("factory must not run"))
    assert not (tmp_path / "azure-state").exists()
    assert list(real.iterdir()) == []


@pytest.mark.asyncio
async def test_authenticated_bridge_durable_intent_and_fixture_sdk_boundary_complete_one_tts(tmp_path):
    bridge = _authorized_bridge(tmp_path)
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(1)))
    transport = AzureSpeechFixtureTransport(_fixture(tmp_path))
    consumer = _consumer(tmp_path, bridge, executor, lambda: transport)
    job = _job(tmp_path)

    result = await consumer.synthesize(operation_id=job.request_id, job=job)

    assert result["status"] == "COMPLETE"
    assert transport.calls == 1
    assert Path(result["audio_path"]).read_bytes()
    assert bridge.claim(request_id=job.request_id)["state"] == "RESERVED"
    intent = json.loads((tmp_path / "azure-state" / "azure-operations" / f"{job.request_id}.json").read_text(encoding="utf-8"))
    assert intent["voice"] == VOICE
    assert intent["script_sha256"] == hashlib.sha256(TEXT.encode()).hexdigest()
    assert intent["price_usd"] == "0.05"


@pytest.mark.asyncio
async def test_operation_scope_rejects_changed_voice_script_or_price_before_fixture_sdk_call(tmp_path):
    bridge = _authorized_bridge(tmp_path)
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(1)))
    transport = AzureSpeechFixtureTransport(_fixture(tmp_path))
    consumer = _consumer(tmp_path, bridge, executor, lambda: transport)
    original = _job(tmp_path)
    await consumer.synthesize(operation_id=original.request_id, job=original)

    for changed in (_job(tmp_path, text="texto diferente"), _job(tmp_path, voice="pt-BR-AntonioNeural"), _job(tmp_path, price="0.06")):
        with pytest.raises(ValueError, match="operation id|immutable Azure operation scope"):
            await consumer.synthesize(operation_id=original.request_id, job=changed)
    assert transport.calls == 1


@pytest.mark.asyncio
async def test_cancellation_after_dispatch_marks_canonical_claim_unknown_and_fresh_consumer_never_builds_factory(tmp_path):
    class CancelAfterDispatch:
        calls = 0
        def synthesize(self, **_kwargs):
            self.calls += 1
            raise asyncio.CancelledError()

    bridge = _authorized_bridge(tmp_path)
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(1)))
    cancelled = CancelAfterDispatch()
    job = _job(tmp_path)
    with pytest.raises(asyncio.CancelledError):
        await _consumer(tmp_path, bridge, executor, lambda: cancelled).synthesize(operation_id=job.request_id, job=job)
    assert cancelled.calls == 1
    assert bridge.claim(request_id=job.request_id)["state"] == "UNKNOWN"

    factory_calls = 0
    def forbidden_factory():
        nonlocal factory_calls
        factory_calls += 1
        raise AssertionError("UNKNOWN claim must stop before factory construction")
    fresh = AzureSpeechDurableConsumer(root=tmp_path / "azure-state", output_root=tmp_path,
        bridge=_authorized_bridge(tmp_path), executor=Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(1))),
        transport_factory=forbidden_factory)
    with pytest.raises(SubmissionUnknown):
        await fresh.synthesize(operation_id=job.request_id, job=job)
    assert factory_calls == 0


def _timeout_worker(root_text: str, queue) -> None:
    root = Path(root_text)
    bridge = _authorized_bridge(root)
    executor = Executor(root / "executor.sqlite3", Config(limit=Decimal(1)))
    transport = AzureSpeechFixtureTransport(_fixture(root), timeout=True)
    consumer = _consumer(root, bridge, executor, lambda: transport)
    job = _job(root)
    try:
        asyncio.run(consumer.synthesize(operation_id=job.request_id, job=job))
    except SubmissionUnknown:
        queue.put({"calls": transport.calls, "state": bridge.claim(request_id=job.request_id)["state"]})
    else:
        queue.put({"error": "timeout did not become UNKNOWN"})


def _replay_worker(root_text: str, queue) -> None:
    root = Path(root_text)
    bridge = _authorized_bridge(root)
    executor = Executor(root / "executor.sqlite3", Config(limit=Decimal(1)))
    transport = AzureSpeechFixtureTransport(_fixture(root))
    factory_calls = 0
    def factory():
        nonlocal factory_calls
        factory_calls += 1
        return transport
    consumer = _consumer(root, bridge, executor, factory)
    job = _job(root)
    try:
        asyncio.run(consumer.synthesize(operation_id=job.request_id, job=job))
    except SubmissionUnknown:
        queue.put({"calls": transport.calls, "factory_calls": factory_calls, "state": bridge.claim(request_id=job.request_id)["state"]})
    else:
        queue.put({"error": "UNKNOWN was resubmitted"})


def test_fresh_process_timeout_marks_unknown_and_never_resubmits(tmp_path):
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    first = context.Process(target=_timeout_worker, args=(str(tmp_path), queue))
    first.start(); first.join(25)
    assert first.exitcode == 0
    assert queue.get(timeout=3) == {"calls": 1, "state": "UNKNOWN"}
    replay = context.Process(target=_replay_worker, args=(str(tmp_path), queue))
    replay.start(); replay.join(25)
    assert replay.exitcode == 0
    assert queue.get(timeout=3) == {"calls": 0, "factory_calls": 0, "state": "UNKNOWN"}


def _racing_worker(root_text: str, job: Job, queue) -> None:
    root = Path(root_text)
    bridge = _authorized_bridge(root)
    executor = Executor(root / "executor.sqlite3", Config(limit=Decimal(1)))
    def factory():
        queue.put({"factory": 1})
        return AzureSpeechFixtureTransport(_fixture(root))
    result = asyncio.run(_consumer(root, bridge, executor, factory).synthesize(operation_id=job.request_id, job=job))
    queue.put({"result": result["status"]})


def test_two_fresh_processes_share_one_canonical_admission_and_only_winner_constructs_factory(tmp_path):
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    job = _job(tmp_path)
    workers = [context.Process(target=_racing_worker, args=(str(tmp_path), job, queue)) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(30)
        assert worker.exitcode == 0
    events = [queue.get(timeout=3) for _ in range(3)]
    assert events.count({"factory": 1}) == 1
    assert events.count({"result": "COMPLETE"}) == 2
