"""Delivery is resumable, hash-bound, and sequential by default."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from src.approval.receipts import ApprovalReceipt
from src.delivery.controller import DeliveryController, DeliveryError
from src.hybrid.artifacts import atomic_json


class Messenger:
    def __init__(self):
        self.calls: list[str] = []

    async def send_photo(self, chat_id, photo_path, caption=""):
        self.calls.append("thumbnail")
        return 101

    async def send_video(self, chat_id, video_path, caption=""):
        self.calls.append("video")
        return 202


def approved(tmp_path: Path, kind: str, suffix: str) -> ApprovalReceipt:
    artifact = tmp_path / f"approved{suffix}"
    artifact.write_bytes(kind.encode())
    return ApprovalReceipt.approve(kind, artifact, "human")


@pytest.mark.asyncio
async def test_delivery_sends_only_thumbnail_until_hash_bound_thumbnail_approval(tmp_path: Path):
    messenger = Messenger()
    thumbnail = approved(tmp_path, "thumbnail", ".png")
    video = approved(tmp_path, "video", ".mp4")
    controller = DeliveryController(messenger)

    thumbnail_delivery = await controller.deliver_for_approval(
        chat_id="test-chat", episode_id="EP8", thumbnail=thumbnail, receipt_dir=tmp_path / "delivery"
    )

    assert thumbnail_delivery["artifact_kind"] == "thumbnail"
    assert messenger.calls == ["thumbnail"]
    with pytest.raises(DeliveryError, match="approved thumbnail"):
        await controller.deliver_for_approval(
            chat_id="test-chat", episode_id="EP8", thumbnail=thumbnail, video=video, receipt_dir=tmp_path / "delivery"
        )
    assert messenger.calls == ["thumbnail"]

    video_delivery = await controller.deliver_for_approval(
        chat_id="test-chat",
        episode_id="EP8",
        thumbnail=thumbnail,
        video=video,
        thumbnail_approval=ApprovalReceipt.approve("thumbnail", thumbnail.artifact_path, "human"),
        receipt_dir=tmp_path / "delivery",
    )

    assert video_delivery["artifact_kind"] == "video"
    assert messenger.calls == ["thumbnail", "video"]
    assert json.loads((tmp_path / "delivery" / "thumbnail.json").read_text())["message_id"] == 101
    assert json.loads((tmp_path / "delivery" / "video.json").read_text())["message_id"] == 202


@pytest.mark.asyncio
async def test_delivery_recovery_does_not_resend_after_valid_readback(tmp_path: Path):
    messenger = Messenger()
    thumbnail = approved(tmp_path, "thumbnail", ".png")
    root = tmp_path / "delivery"
    atomic_json(root / "thumbnail.json", {
        "episode_id": "EP8", "artifact_kind": "thumbnail", "approval": thumbnail.__dict__, "chat_id": "test-chat", "message_id": 101,
    })

    recovered = await DeliveryController(messenger).deliver_for_approval(
        chat_id="test-chat", episode_id="EP8", thumbnail=thumbnail, receipt_dir=root
    )

    assert recovered["message_id"] == 101
    assert messenger.calls == []


@pytest.mark.asyncio
async def test_ambiguous_delivery_never_reposts_on_replay(tmp_path: Path):
    class AmbiguousMessenger(Messenger):
        async def send_photo(self, chat_id, photo_path, caption=""):
            self.calls.append("thumbnail")
            raise TimeoutError("transport timeout")

    messenger = AmbiguousMessenger()
    thumbnail = approved(tmp_path, "thumbnail", ".png")
    controller = DeliveryController(messenger)
    for _ in range(2):
        with pytest.raises(DeliveryError, match="ambiguous"):
            await controller.deliver_for_approval(
                chat_id="test-chat", episode_id="EP8", thumbnail=thumbnail, receipt_dir=tmp_path / "delivery"
            )

    assert messenger.calls == ["thumbnail"]
    assert (tmp_path / "delivery" / "thumbnail.attempt.json").is_file()


@pytest.mark.asyncio
async def test_mutated_thumbnail_blocks_video_delivery(tmp_path: Path):
    messenger = Messenger()
    thumbnail = approved(tmp_path, "thumbnail", ".png")
    video = approved(tmp_path, "video", ".mp4")
    controller = DeliveryController(messenger)
    await controller.deliver_for_approval(
        chat_id="test-chat", episode_id="EP8", thumbnail=thumbnail, receipt_dir=tmp_path / "delivery"
    )
    Path(thumbnail.artifact_path).write_bytes(b"mutated")

    with pytest.raises(Exception, match="no longer match|approved thumbnail"):
        await controller.deliver_for_approval(
            chat_id="test-chat", episode_id="EP8", thumbnail=thumbnail, video=video,
            thumbnail_approval=ApprovalReceipt.approve("thumbnail", thumbnail.artifact_path, "human"),
            receipt_dir=tmp_path / "delivery",
        )
    assert messenger.calls == ["thumbnail"]


@pytest.mark.asyncio
async def test_delivery_replay_rejects_missing_or_swapped_destination_before_messenger(tmp_path: Path):
    messenger = Messenger()
    thumbnail = approved(tmp_path, "thumbnail", ".png")
    root = tmp_path / "delivery"
    atomic_json(root / "thumbnail.json", {
        "episode_id": "EP8", "artifact_kind": "thumbnail", "approval": thumbnail.__dict__, "chat_id": "authorized-chat", "message_id": 101,
    })

    with pytest.raises(DeliveryError, match="bind"):
        await DeliveryController(messenger).deliver_for_approval(
            chat_id="swapped-chat", episode_id="EP8", thumbnail=thumbnail, receipt_dir=root
        )

    assert messenger.calls == []


@pytest.mark.asyncio
async def test_concurrent_delivery_attempts_send_media_once(tmp_path: Path):
    class SlowMessenger(Messenger):
        async def send_photo(self, chat_id, photo_path, caption=""):
            await asyncio.sleep(0.05)
            return await super().send_photo(chat_id, photo_path, caption)

    messenger = SlowMessenger()
    thumbnail = approved(tmp_path, "thumbnail", ".png")
    controller = DeliveryController(messenger)

    first, second = await asyncio.gather(
        controller.deliver_for_approval(
            chat_id="authorized-chat", episode_id="EP8", thumbnail=thumbnail, receipt_dir=tmp_path / "delivery"
        ),
        controller.deliver_for_approval(
            chat_id="authorized-chat", episode_id="EP8", thumbnail=thumbnail, receipt_dir=tmp_path / "delivery"
        ),
    )

    assert first["message_id"] == second["message_id"] == 101
    assert messenger.calls == ["thumbnail"]


@pytest.mark.asyncio
async def test_cancel_during_blocked_lock_acquisition_releases_after_worker_acquires(monkeypatch, tmp_path: Path):
    """Cancellation waits for the background enter then balances it with exit."""
    import threading

    import src.delivery.controller as delivery_module

    entered = threading.Event()
    allow_enter = threading.Event()
    exited = threading.Event()

    class BlockingLock:
        def __enter__(self):
            entered.set()
            assert allow_enter.wait(timeout=2)
            return self

        def __exit__(self, *_):
            exited.set()

    monkeypatch.setattr(delivery_module, "file_lock", lambda _: BlockingLock())
    task = asyncio.create_task(DeliveryController._gate_lock(tmp_path / "receipt.json").__aenter__())
    await asyncio.to_thread(entered.wait, 1)
    task.cancel()
    allow_enter.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await asyncio.to_thread(exited.wait, 1)


@pytest.mark.asyncio
async def test_cancelled_lock_owner_does_not_block_second_delivery(monkeypatch, tmp_path: Path):
    """The next caller acquires after cancellation even when enter completed in a thread."""
    import threading

    import src.delivery.controller as delivery_module

    entered = threading.Event()
    allow_enter = threading.Event()
    released = threading.Event()

    class BlockingLock:
        def __enter__(self):
            entered.set()
            assert allow_enter.wait(timeout=2)
            return self

        def __exit__(self, *_):
            released.set()

    monkeypatch.setattr(delivery_module, "file_lock", lambda _: BlockingLock())
    first = asyncio.create_task(DeliveryController._gate_lock(tmp_path / "receipt.json").__aenter__())
    await asyncio.to_thread(entered.wait, 1)
    first.cancel()
    allow_enter.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert await asyncio.to_thread(released.wait, 1)
    async with DeliveryController._gate_lock(tmp_path / "receipt.json"):
        pass
