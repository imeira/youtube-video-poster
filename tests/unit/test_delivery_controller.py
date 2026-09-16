"""Telegram delivery must bind each media message to exact approved bytes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.approval.receipts import ApprovalReceipt
from src.delivery.controller import DeliveryController, DeliveryError


class Messenger:
    def __init__(self):
        self.photos = 0
        self.videos = 0

    async def send_photo(self, chat_id, photo_path, caption=""):
        self.photos += 1
        return 101

    async def send_video(self, chat_id, video_path, caption=""):
        self.videos += 1
        return 202


def approved(tmp_path: Path, kind: str, suffix: str):
    artifact = tmp_path / f"approved{suffix}"
    artifact.write_bytes(kind.encode())
    return ApprovalReceipt.approve(kind, artifact, "human")


@pytest.mark.asyncio
async def test_delivery_controller_sends_thumbnail_and_video_separately_with_hash_bound_receipts(tmp_path: Path):
    messenger = Messenger()
    thumbnail = approved(tmp_path, "thumbnail", ".png")
    video = approved(tmp_path, "video", ".mp4")

    receipts = await DeliveryController(messenger).deliver_for_approval(
        chat_id="test-chat",
        episode_id="EP8",
        thumbnail=thumbnail,
        video=video,
        receipt_dir=tmp_path / "delivery",
    )

    assert (messenger.photos, messenger.videos) == (1, 1)
    assert {receipt["artifact_kind"] for receipt in receipts} == {"thumbnail", "video"}
    assert json.loads((tmp_path / "delivery" / "thumbnail.json").read_text())["message_id"] == 101
    assert json.loads((tmp_path / "delivery" / "video.json").read_text())["message_id"] == 202


@pytest.mark.asyncio
async def test_delivery_controller_does_not_retry_or_record_success_when_media_send_fails(tmp_path: Path):
    class FailingMessenger(Messenger):
        async def send_video(self, chat_id, video_path, caption=""):
            self.videos += 1
            raise TimeoutError("transport timeout")

    messenger = FailingMessenger()
    with pytest.raises(DeliveryError, match="without retry"):
        await DeliveryController(messenger).deliver_for_approval(
            chat_id="test-chat",
            episode_id="EP8",
            thumbnail=approved(tmp_path, "thumbnail", ".png"),
            video=approved(tmp_path, "video", ".mp4"),
            receipt_dir=tmp_path / "delivery",
        )

    assert messenger.videos == 1
    assert not (tmp_path / "delivery" / "video.json").exists()
