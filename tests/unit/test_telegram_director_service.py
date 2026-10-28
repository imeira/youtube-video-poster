"""Offline E2E for Telegram inbound composition into Director approval gates."""
from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import sqlite3
import threading
import time
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
_PROCESS_STARTUP_TIMEOUT_SECONDS = 20
_CRITICAL_SECTION_TIMEOUT_SECONDS = 5
_CHILD_CLEANUP_TIMEOUT_SECONDS = 5


def _hold_gate_lock(path: str, ready, release, readiness_delay: float = 0.0) -> None:
    from src.hybrid.locks import file_lock

    if readiness_delay:
        time.sleep(readiness_delay)
    with file_lock(Path(path)):
        ready.set()
        assert release.wait(5)


async def _cleanup_holder(holder: multiprocessing.Process, release) -> None:
    release.set()
    await asyncio.to_thread(holder.join, _CHILD_CLEANUP_TIMEOUT_SECONDS)
    if holder.is_alive():
        holder.terminate()
        await asyncio.to_thread(holder.join, _CHILD_CLEANUP_TIMEOUT_SECONDS)
    assert not holder.is_alive()
    assert holder.exitcode == 0


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


def test_thumbnail_approval_creates_hash_bound_video_outbox_without_delivery_callback(tmp_path: Path):
    service, _episode, artifact, _ = _service(tmp_path)

    result = service.handle_webhook(_update(), SECRET)

    assert result.body["status"] == "approved"
    with sqlite3.connect(tmp_path / "external" / "deliveries.sqlite3") as connection:
        row = connection.execute(
            "SELECT episode_id, thumbnail_path, thumbnail_sha256, status FROM video_delivery_outbox"
        ).fetchone()
    assert row == ("EP8", str(artifact.resolve()), hashlib.sha256(artifact.read_bytes()).hexdigest(), "PENDING")


@pytest.mark.asyncio
async def test_async_thumbnail_approval_notifies_outbox_only_after_durable_outcome(tmp_path: Path):
    service, _episode, _artifact, _ = _service(tmp_path)
    notifications: list[str] = []

    async def notify() -> None:
        with sqlite3.connect(tmp_path / "external" / "deliveries.sqlite3") as connection:
            assert connection.execute("SELECT COUNT(*) FROM video_delivery_outbox").fetchone() == (1,)
        notifications.append("after-commit")

    service.set_outbox_wakeup(notify)
    result = await service.handle_webhook_async(_update(), SECRET)
    await asyncio.sleep(0)

    assert result.body["status"] == "approved"
    assert notifications == ["after-commit"]


@pytest.mark.asyncio
async def test_async_thumbnail_ack_does_not_await_never_returning_notification(tmp_path: Path):
    service, _episode, _artifact, _ = _service(tmp_path)
    started = asyncio.Event()

    async def notify() -> None:
        started.set()
        await asyncio.Event().wait()

    service.set_outbox_wakeup(notify)
    result = await asyncio.wait_for(service.handle_webhook_async(_update(), SECRET), timeout=0.2)

    assert result.body["status"] == "approved"
    assert await asyncio.wait_for(started.wait(), timeout=0.2)
    with sqlite3.connect(tmp_path / "external" / "deliveries.sqlite3") as connection:
        assert connection.execute("SELECT status FROM video_delivery_outbox").fetchone() == ("PENDING",)


@pytest.mark.asyncio
async def test_sync_wakeup_cancelled_error_is_isolated_from_authenticated_ack(tmp_path: Path):
    service, _episode, _artifact, _ = _service(tmp_path)

    def notify() -> None:
        raise asyncio.CancelledError("detached wakeup failure")

    service.set_outbox_wakeup(notify)
    result = await service.handle_webhook_async(_update(), SECRET)

    assert result.body["status"] == "approved"


@pytest.mark.parametrize("corruption", ["bad-outcome-mac", "wrong-source", "mutated-binding"])
def test_legacy_outcome_repair_rejects_corruption_before_outbox_enqueue(tmp_path: Path, corruption: str):
    service, episode, artifact, _ = _service(tmp_path)
    assert service.handle_webhook(_update(), SECRET).body["status"] == "approved"
    registry = tmp_path / "external" / "deliveries.sqlite3"
    with sqlite3.connect(registry) as connection:
        connection.execute("DELETE FROM video_delivery_outbox")
        if corruption == "bad-outcome-mac":
            connection.execute("UPDATE outcomes SET mac = '0' * 64")
        elif corruption == "mutated-binding":
            record = service._lookup(chat_id=CHAT_ID, message_id=42)
            assert record is not None
            record["artifact_sha256"] = "f" * 64
            mac = service._registry_mac({"chat_id": CHAT_ID, "message_id": 42, **record})
            connection.execute(
                "UPDATE deliveries SET artifact_sha256 = ?, mac = ? WHERE chat_id = ? AND message_id = ?",
                (record["artifact_sha256"], mac, CHAT_ID, 42),
            )
    if corruption == "wrong-source":
        approval_path = episode / "qa" / "approval-thumbnail.json"
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        approval["inbound_receipt"]["sender_id"] = "99999"
        approval_path.write_text(json.dumps(approval), encoding="utf-8")

    TelegramDirectorService(director=service._director, settings=service._settings)

    with sqlite3.connect(registry) as connection:
        assert connection.execute("SELECT COUNT(*) FROM video_delivery_outbox").fetchone() == (0,)
    assert artifact.read_bytes() == b"frozen-thumbnail"


def test_legacy_outcome_repair_revalidates_then_enqueues_exactly_once_after_restart(tmp_path: Path):
    service, _episode, artifact, _ = _service(tmp_path)
    assert service.handle_webhook(_update(), SECRET).body["status"] == "approved"
    registry = tmp_path / "external" / "deliveries.sqlite3"
    with sqlite3.connect(registry) as connection:
        connection.execute("DELETE FROM video_delivery_outbox")

    TelegramDirectorService(director=service._director, settings=service._settings)
    TelegramDirectorService(director=service._director, settings=service._settings)

    with sqlite3.connect(registry) as connection:
        rows = connection.execute(
            "SELECT episode_id, thumbnail_path, thumbnail_sha256, status FROM video_delivery_outbox"
        ).fetchall()
    assert rows == [("EP8", str(artifact.resolve()), hashlib.sha256(artifact.read_bytes()).hexdigest(), "PENDING")]


def test_outcome_and_video_outbox_share_one_transaction_after_authenticated_approval(tmp_path: Path, monkeypatch):
    service, _episode, _artifact, _ = _service(tmp_path)

    monkeypatch.setattr(
        service, "_enqueue_video_delivery_in_transaction",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("crash after outcome before outbox")),
    )

    with pytest.raises(RuntimeError, match="crash after outcome"):
        service.handle_webhook(_update(), SECRET)
    with sqlite3.connect(tmp_path / "external" / "deliveries.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM outcomes").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM video_delivery_outbox").fetchone() == (0,)


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
        assert await asyncio.to_thread(ready.wait, _PROCESS_STARTUP_TIMEOUT_SECONDS)
        pending = asyncio.create_task(asyncio.to_thread(service.handle_webhook, _update(), SECRET))
        await asyncio.sleep(0.1)
        assert not pending.done()
        release.set()
        result = await asyncio.wait_for(pending, _CRITICAL_SECTION_TIMEOUT_SECONDS)
        assert result.status == 200
    finally:
        await _cleanup_holder(holder, release)


@pytest.mark.asyncio
async def test_process_lock_tolerates_slow_but_bounded_windows_spawn_readiness(tmp_path: Path):
    service, _, _, _ = _service(tmp_path)
    lock = service._gate_lock_path(chat_id=CHAT_ID, message_id=42)
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    holder = context.Process(target=_hold_gate_lock, args=(str(lock), ready, release, 5.1))
    holder.start()
    try:
        assert await asyncio.to_thread(ready.wait, _PROCESS_STARTUP_TIMEOUT_SECONDS)
        pending = asyncio.create_task(asyncio.to_thread(service.handle_webhook, _update(), SECRET))
        await asyncio.sleep(0.1)
        assert not pending.done()
        release.set()
        result = await asyncio.wait_for(pending, _CRITICAL_SECTION_TIMEOUT_SECONDS)
        assert result.status == 200
    finally:
        await _cleanup_holder(holder, release)


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
