"""Offline E2E for Telegram inbound composition into Director approval gates."""
from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import sqlite3
import threading
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.agents.director import DirectorAgent
from src.approval.receipts import ApprovalReceipt
from src.approval.telegram_service import (
    TelegramDirectorService,
    TelegramInboundSettings,
    create_telegram_inbound_app,
)
from src.state.machine import EpisodeState, EpisodeStateStore

SECRET = "test-only-external-secret"
CHAT_ID = "10001"
SENDER_ID = "20002"


def _hold_gate_lock(path: str, ready, release) -> None:
    from src.hybrid.locks import file_lock

    with file_lock(Path(path)):
        ready.set()
        assert release.wait(5)


def _config(episodes_dir: Path):
    return SimpleNamespace(
        episodes_dir=episodes_dir,
        approval_delivery_chat_id=CHAT_ID,
        approval_delivery_trusted_sender_ids=frozenset({SENDER_ID}),
    )


def _delivery(root: Path, *, episode_id: str = "EP8", message_id: int = 42):
    artifact = root / f"{episode_id}-thumbnail.png"
    artifact.write_bytes(b"frozen-thumbnail")
    delivered = ApprovalReceipt.approve("thumbnail", artifact, "delivery-audit")
    receipt = root / f"{episode_id}-delivery.json"
    receipt.write_text(json.dumps({
        "episode_id": episode_id, "artifact_kind": "thumbnail", "approval": asdict(delivered),
        "chat_id": CHAT_ID, "message_id": message_id,
    }), encoding="utf-8")
    return artifact, receipt


def _update(*, update_id: int = 1, message_id: int = 99, reply_to: int = 42, text: str = "APROVAR THUMBNAIL EP8"):
    return json.dumps({"update_id": update_id, "message": {
        "message_id": message_id, "from": {"id": int(SENDER_ID), "is_bot": False},
        "chat": {"id": int(CHAT_ID), "type": "private"},
        "reply_to_message": {"message_id": reply_to}, "text": text,
    }}, separators=(",", ":")).encode()


def _service(tmp_path: Path):
    episodes = tmp_path / "episodes"
    episode = episodes / "EP8"
    (episode / "qa").mkdir(parents=True)
    state = EpisodeStateStore.load_or_create(episode / "state.json", "EP8")
    # Isolated fixture materializes the exact already-open Director gate.
    state.current_state = EpisodeState.WAITING_THUMBNAIL_APPROVAL
    state.save(episode / "state.json")
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = _config(episodes)
    service = TelegramDirectorService(
        director=director,
        settings=TelegramInboundSettings(
            webhook_secret=SECRET, chat_id=CHAT_ID, trusted_sender_ids=frozenset({SENDER_ID}),
            instance_id="test-instance", journal_path=tmp_path / "external" / "journal.sqlite3",
            registry_path=tmp_path / "external" / "deliveries.sqlite3",
        ),
    )
    artifact, receipt = _delivery(tmp_path)
    service.register_delivery(receipt)
    return service, episode, artifact, receipt


@pytest.mark.asyncio
async def test_http_authenticated_request_journals_then_advances_only_registered_pending_gate(tmp_path: Path):
    service, episode, artifact, _ = _service(tmp_path)
    app = create_telegram_inbound_app(service)
    async with TestServer(app) as server, TestClient(server) as client:
        response = await client.post("/telegram/inbound", data=_update(), headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
        assert response.status == 200
        assert await response.json() == {"status": "approved", "episode_id": "EP8", "artifact_kind": "thumbnail"}

    assert EpisodeStateStore.load(episode / "state.json").current_state is EpisodeState.WAITING_VIDEO_APPROVAL
    approval = json.loads((episode / "qa" / "approval-thumbnail.json").read_text(encoding="utf-8"))
    assert approval["artifact_sha256"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert approval["inbound_receipt"]["text"] == "APROVAR THUMBNAIL EP8"


@pytest.mark.asyncio
async def test_http_concurrent_duplicate_is_one_transition_then_idempotent_ack(tmp_path: Path):
    service, episode, _, _ = _service(tmp_path)
    original = service._director.confirm_delivered_artifact
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    service._director.confirm_delivered_artifact = counted
    app = create_telegram_inbound_app(service)
    async with TestServer(app) as server, TestClient(server) as client:
        first, second = await asyncio.gather(
            client.post("/telegram/inbound", data=_update(), headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}),
            client.post("/telegram/inbound", data=_update(), headers={"X-Telegram-Bot-Api-Secret-Token": SECRET}),
        )
        replies = [await first.json(), await second.json()]

    assert sorted(reply["status"] for reply in replies) == ["acknowledged", "approved"]
    assert calls == 1
    assert EpisodeStateStore.load(episode / "state.json").current_state is EpisodeState.WAITING_VIDEO_APPROVAL


@pytest.mark.asyncio
async def test_cancelled_async_request_finishes_locked_transition_before_releasing_gate(tmp_path: Path):
    service, episode, _, _ = _service(tmp_path)
    original = service._director.confirm_delivered_artifact
    entered, release = threading.Event(), threading.Event()

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    service._director.confirm_delivered_artifact = blocked
    task = asyncio.create_task(service.handle_webhook_async(_update(), SECRET))
    assert await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    replay = await service.handle_webhook_async(_update(), SECRET)
    assert replay.body["status"] == "acknowledged"
    assert EpisodeStateStore.load(episode / "state.json").current_state is EpisodeState.WAITING_VIDEO_APPROVAL


@pytest.mark.asyncio
async def test_process_lock_serializes_the_same_authenticated_gate(tmp_path: Path):
    service, _, _, _ = _service(tmp_path)
    lock = service._gate_lock_path(chat_id=CHAT_ID, message_id=42)
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    holder = context.Process(target=_hold_gate_lock, args=(str(lock), ready, release))
    holder.start()
    try:
        assert await asyncio.to_thread(ready.wait, 5)
        pending = asyncio.create_task(asyncio.to_thread(service.handle_webhook, _update(), SECRET))
        await asyncio.sleep(0.1)
        assert not pending.done()
        release.set()
        result = await pending
        assert result.status == 200
    finally:
        release.set()
        holder.join(5)
        assert holder.exitcode == 0


def test_crash_after_receipt_before_state_recovers_without_new_approval(tmp_path: Path, monkeypatch):
    service, episode, _, _ = _service(tmp_path)
    original_save = EpisodeStateStore.save
    failed = False

    def crash_after_receipt(self, path):
        nonlocal failed
        if self.current_state is EpisodeState.WAITING_VIDEO_APPROVAL and not failed:
            failed = True
            raise RuntimeError("simulated crash after receipt")
        return original_save(self, path)

    monkeypatch.setattr(EpisodeStateStore, "save", crash_after_receipt)
    with pytest.raises(RuntimeError, match="simulated crash"):
        service.handle_webhook(_update(), SECRET)
    assert (episode / "qa" / "approval-thumbnail.json").is_file()
    assert EpisodeStateStore.load(episode / "state.json").current_state is EpisodeState.WAITING_THUMBNAIL_APPROVAL

    recovered = service.handle_webhook(_update(), SECRET)
    assert recovered.body["status"] == "recovered"
    assert recovered.body["result_status"] == "approved"
    assert EpisodeStateStore.load(episode / "state.json").current_state is EpisodeState.WAITING_VIDEO_APPROVAL


@pytest.mark.asyncio
async def test_http_rejects_missing_config_and_forged_cross_episode_replay_mutation_and_cancel(tmp_path: Path):
    settings = TelegramInboundSettings(
        webhook_secret=None, chat_id=CHAT_ID, trusted_sender_ids=frozenset({SENDER_ID}),
        instance_id="test-instance", journal_path=tmp_path / "journal.sqlite3", registry_path=tmp_path / "registry.sqlite3",
    )
    disabled = TelegramDirectorService(director=None, settings=settings)
    assert disabled.handle_webhook(_update(), SECRET).status == 503

    service, episode, _, _ = _service(tmp_path)
    app = create_telegram_inbound_app(service)
    async with TestServer(app) as server, TestClient(server) as client:
        forged = await client.post("/telegram/inbound", data=_update(), headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
        assert forged.status == 401
        cross = await client.post("/telegram/inbound", data=_update(reply_to=777), headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
        assert cross.status == 404
        cancelled = await client.post("/telegram/inbound", data=_update(update_id=3, message_id=101, text="CANCELAR"), headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
        assert cancelled.status == 422

    assert EpisodeStateStore.load(episode / "state.json").current_state is EpisodeState.WAITING_THUMBNAIL_APPROVAL
    approved_service, approved_episode, _, _ = _service(tmp_path / "replay")
    approved_app = create_telegram_inbound_app(approved_service)
    async with TestServer(approved_app) as server, TestClient(server) as client:
        accepted = await client.post("/telegram/inbound", data=_update(), headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
        assert accepted.status == 200
        replay = await client.post("/telegram/inbound", data=_update(), headers={"X-Telegram-Bot-Api-Secret-Token": SECRET})
        assert replay.status == 200
        assert await replay.json() == {
            "status": "acknowledged", "result_status": "approved", "episode_id": "EP8", "artifact_kind": "thumbnail",
        }
    assert EpisodeStateStore.load(approved_episode / "state.json").current_state is EpisodeState.WAITING_VIDEO_APPROVAL


def test_registry_mutation_cannot_redirect_an_authenticated_reply(tmp_path: Path):
    service, episode, _, _ = _service(tmp_path)

    with sqlite3.connect(tmp_path / "external" / "deliveries.sqlite3") as connection:
        connection.execute("UPDATE deliveries SET episode_id = ?", ("EP9",))

    result = service.handle_webhook(_update(), SECRET)
    assert result.status == 404
    assert EpisodeStateStore.load(episode / "state.json").current_state is EpisodeState.WAITING_THUMBNAIL_APPROVAL


def test_startup_reconciles_configured_episode_delivery_receipt_after_crash_before_registration(tmp_path: Path):
    episodes = tmp_path / "episodes"
    episode = episodes / "EP8"
    (episode / "qa" / "delivery").mkdir(parents=True)
    state = EpisodeStateStore.load_or_create(episode / "state.json", "EP8")
    state.current_state = EpisodeState.WAITING_THUMBNAIL_APPROVAL
    state.save(episode / "state.json")
    _, source = _delivery(tmp_path)
    destination = episode / "qa" / "delivery" / "thumbnail.json"
    destination.write_bytes(source.read_bytes())
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = _config(episodes)

    restarted = TelegramDirectorService(
        director=director,
        settings=TelegramInboundSettings(
            webhook_secret=SECRET, chat_id=CHAT_ID, trusted_sender_ids=frozenset({SENDER_ID}),
            instance_id="test-instance", journal_path=tmp_path / "external" / "journal.sqlite3",
            registry_path=tmp_path / "external" / "deliveries.sqlite3",
        ),
    )

    result = restarted.handle_webhook(_update(), SECRET)
    assert result.status == 200
    assert result.body["status"] == "approved"
    assert EpisodeStateStore.load(episode / "state.json").current_state is EpisodeState.WAITING_VIDEO_APPROVAL


def test_startup_reconciliation_rejects_tampered_delivery_receipt(tmp_path: Path):
    episodes = tmp_path / "episodes"
    episode = episodes / "EP8"
    (episode / "qa" / "delivery").mkdir(parents=True)
    _, source = _delivery(tmp_path)
    document = json.loads(source.read_text(encoding="utf-8"))
    document["chat_id"] = "10009"
    (episode / "qa" / "delivery" / "thumbnail.json").write_text(json.dumps(document), encoding="utf-8")
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = _config(episodes)

    with pytest.raises(ValueError, match="configured Telegram chat"):
        TelegramDirectorService(
            director=director,
            settings=TelegramInboundSettings(
                webhook_secret=SECRET, chat_id=CHAT_ID, trusted_sender_ids=frozenset({SENDER_ID}),
                instance_id="test-instance", journal_path=tmp_path / "external" / "journal.sqlite3",
                registry_path=tmp_path / "external" / "deliveries.sqlite3",
            ),
        )


def test_recovery_rejects_reparse_delivery_ancestry_before_receipt_read_or_lock(tmp_path: Path, monkeypatch):
    episodes = tmp_path / "episodes"
    episode = episodes / "EP8"
    delivery = episode / "qa" / "delivery"
    delivery.mkdir(parents=True)
    target = tmp_path / "outside"
    target.mkdir()
    _, source = _delivery(tmp_path)
    (target / "thumbnail.json").write_bytes(source.read_bytes())
    linked = episode / "qa" / "linked-delivery"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks or junctions unsupported: {error}")
    delivery.rmdir()
    linked.rename(delivery)
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = _config(episodes)

    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: pytest.fail("receipt read before boundary validation"))
    monkeypatch.setattr("src.approval.telegram_service.file_lock", lambda *_args, **_kwargs: pytest.fail("lock before boundary validation"))

    with pytest.raises(ValueError, match="reparse"):
        TelegramDirectorService(
            director=director,
            settings=TelegramInboundSettings(
                webhook_secret=SECRET, chat_id=CHAT_ID, trusted_sender_ids=frozenset({SENDER_ID}),
                instance_id="test-instance", journal_path=tmp_path / "external" / "journal.sqlite3",
                registry_path=tmp_path / "external" / "deliveries.sqlite3",
            ),
        )


@pytest.mark.asyncio
async def test_inbound_recovers_persisted_receipt_injected_before_registry_write(tmp_path: Path):
    episodes = tmp_path / "episodes"
    episode = episodes / "EP8"
    (episode / "qa" / "delivery").mkdir(parents=True)
    state = EpisodeStateStore.load_or_create(episode / "state.json", "EP8")
    state.current_state = EpisodeState.WAITING_THUMBNAIL_APPROVAL
    state.save(episode / "state.json")
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = _config(episodes)
    service = TelegramDirectorService(
        director=director,
        settings=TelegramInboundSettings(
            webhook_secret=SECRET, chat_id=CHAT_ID, trusted_sender_ids=frozenset({SENDER_ID}),
            instance_id="test-instance", journal_path=tmp_path / "external" / "journal.sqlite3",
            registry_path=tmp_path / "external" / "deliveries.sqlite3",
        ),
    )
    _, source = _delivery(tmp_path, message_id=43)
    (episode / "qa" / "delivery" / "thumbnail.json").write_bytes(source.read_bytes())

    result = await service.handle_webhook_async(_update(reply_to=43), SECRET)

    assert result.body["status"] == "approved"
    assert service._lookup(chat_id=CHAT_ID, message_id=43)["episode_id"] == "EP8"
