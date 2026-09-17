"""Fail-closed delivery of exact approval media through a notification provider."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.approval.receipts import ApprovalReceipt
from src.hybrid.artifacts import atomic_json


class DeliveryError(RuntimeError):
    """Raised after one failed media delivery attempt; callers must recover explicitly."""


class DeliveryController:
    """Sends thumbnail and video independently; neither send authorizes publication."""

    def __init__(self, messenger: Any):
        self.messenger = messenger

    async def deliver_for_approval(
        self,
        *,
        chat_id: str,
        episode_id: str,
        thumbnail: ApprovalReceipt,
        video: ApprovalReceipt,
        receipt_dir: Path | str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Create one hash-bound success receipt per media artifact, with no retry loop."""
        if not episode_id.strip() or not chat_id.strip():
            raise DeliveryError("episode and delivery destination are required")
        if thumbnail.artifact_kind != "thumbnail" or video.artifact_kind != "video":
            raise DeliveryError("separate thumbnail and video approvals are required")
        thumbnail.verify()
        video.verify()
        root = Path(receipt_dir)
        thumbnail_receipt = await self._send(
            chat_id, episode_id, thumbnail, root / "thumbnail.json", "send_photo"
        )
        video_receipt = await self._send(
            chat_id, episode_id, video, root / "video.json", "send_video"
        )
        return thumbnail_receipt, video_receipt

    @staticmethod
    def _recover_receipt(receipt_path: Path, *, episode_id: str, approval: ApprovalReceipt) -> dict[str, Any]:
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DeliveryError("delivery recovery receipt is unreadable") from error
        if (
            not isinstance(receipt, dict)
            or set(receipt) != {"episode_id", "artifact_kind", "approval", "message_id"}
            or receipt.get("episode_id") != episode_id
            or receipt.get("artifact_kind") != approval.artifact_kind
            or receipt.get("approval") != asdict(approval)
            or type(receipt.get("message_id")) is not int
            or receipt["message_id"] <= 0
        ):
            raise DeliveryError("delivery recovery receipt does not bind current approved media")
        return receipt

    async def _send(
        self,
        chat_id: str,
        episode_id: str,
        approval: ApprovalReceipt,
        receipt_path: Path,
        method_name: str,
    ) -> dict[str, Any]:
        if receipt_path.exists():
            return self._recover_receipt(receipt_path, episode_id=episode_id, approval=approval)
        sender = getattr(self.messenger, method_name, None)
        if not callable(sender):
            raise DeliveryError("notification provider does not support required media")
        try:
            message_id = await sender(chat_id, approval.artifact_path, caption=f"{episode_id} — revisão")
        except Exception as error:
            raise DeliveryError(
                f"{approval.artifact_kind} delivery failed without retry; explicit recovery required"
            ) from error
        if type(message_id) is not int or message_id <= 0:
            raise DeliveryError("notification provider did not return a media message identifier")
        receipt = {
            "episode_id": episode_id,
            "artifact_kind": approval.artifact_kind,
            "approval": asdict(approval),
            "message_id": message_id,
        }
        atomic_json(receipt_path, receipt)
        return receipt
