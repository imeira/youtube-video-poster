"""Fail-closed, resumable sequential delivery of exact approval media."""

from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.approval.receipts import ApprovalReceipt, PublicationAuthorizationError
from src.hybrid.artifacts import atomic_json
from src.hybrid.locks import file_lock


class DeliveryError(RuntimeError):
    """Raised when a delivery cannot safely advance or recover."""


class DeliveryController:
    """Deliver thumbnail first; deliver video only after its exact thumbnail approval."""

    def __init__(self, messenger: Any):
        self.messenger = messenger

    async def deliver_for_approval(
        self,
        *,
        chat_id: str,
        episode_id: str,
        thumbnail: ApprovalReceipt,
        receipt_dir: Path | str,
        video: ApprovalReceipt | None = None,
        thumbnail_approval: ApprovalReceipt | None = None,
        after_persist=None,
    ) -> dict[str, Any]:
        """Deliver one gate artifact, never both in the same unapproved call.

        Omitting ``video`` opens/resumes only the thumbnail gate.  Supplying a
        video requires a verified, hash-bound human thumbnail approval and a
        valid prior thumbnail delivery receipt.  There is deliberately no
        compatibility path that sends both artifacts together.
        """
        if not episode_id.strip() or not chat_id.strip():
            raise DeliveryError("episode and delivery destination are required")
        if thumbnail.artifact_kind != "thumbnail":
            raise DeliveryError("a thumbnail approval artifact is required")
        thumbnail.verify()
        root = Path(receipt_dir)
        if video is None:
            return await self._send(
                chat_id, episode_id, thumbnail, root / "thumbnail.json", "send_photo", after_persist
            )
        if video.artifact_kind != "video":
            raise DeliveryError("a video approval artifact is required")
        video.verify()
        self._require_approved_delivered_thumbnail(
            chat_id=chat_id,
            episode_id=episode_id,
            thumbnail=thumbnail,
            thumbnail_approval=thumbnail_approval,
            receipt_path=root / "thumbnail.json",
        )
        return await self._send(
            chat_id, episode_id, video, root / "video.json", "send_video", after_persist
        )

    @staticmethod
    def _recover_receipt(
        receipt_path: Path, *, chat_id: str, episode_id: str, approval: ApprovalReceipt
    ) -> dict[str, Any]:
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DeliveryError("delivery recovery receipt is unreadable") from error
        if (
            not isinstance(receipt, dict)
            or set(receipt) != {"episode_id", "artifact_kind", "approval", "chat_id", "message_id"}
            or receipt.get("episode_id") != episode_id
            or receipt.get("artifact_kind") != approval.artifact_kind
            or receipt.get("approval") != asdict(approval)
            or receipt.get("chat_id") != chat_id
            or type(receipt.get("message_id")) is not int
            or receipt["message_id"] <= 0
        ):
            raise DeliveryError("delivery recovery receipt does not bind current approved media")
        try:
            ApprovalReceipt(**receipt["approval"]).verify()
        except (TypeError, PublicationAuthorizationError) as error:
            raise DeliveryError("delivery recovery receipt media no longer matches") from error
        return receipt

    def _require_approved_delivered_thumbnail(
        self,
        *,
        chat_id: str,
        episode_id: str,
        thumbnail: ApprovalReceipt,
        thumbnail_approval: ApprovalReceipt | None,
        receipt_path: Path,
    ) -> None:
        if thumbnail_approval is None or thumbnail_approval.artifact_kind != "thumbnail":
            raise DeliveryError("video delivery requires an approved thumbnail")
        try:
            thumbnail_approval.verify()
        except PublicationAuthorizationError as error:
            raise DeliveryError("video delivery requires an approved thumbnail") from error
        if (
            thumbnail_approval.artifact_path != thumbnail.artifact_path
            or thumbnail_approval.artifact_sha256 != thumbnail.artifact_sha256
        ):
            raise DeliveryError("video delivery requires approval of the delivered thumbnail")
        self._recover_receipt(
            receipt_path, chat_id=chat_id, episode_id=episode_id, approval=thumbnail
        )

    @staticmethod
    @asynccontextmanager
    async def _gate_lock(receipt_path: Path):
        """Hold a lock without leaking it if cancellation races thread acquisition."""
        lock = file_lock(receipt_path.with_suffix(".lock"))
        acquire = asyncio.create_task(asyncio.to_thread(lock.__enter__))
        cancelled_while_acquiring = False
        while not acquire.done():
            try:
                await asyncio.shield(acquire)
            except asyncio.CancelledError:
                # The worker can acquire after this task is cancelled.  Keep
                # waiting, then balance that acquisition before propagating.
                cancelled_while_acquiring = True
        acquire.result()
        if cancelled_while_acquiring:
            release = asyncio.create_task(asyncio.to_thread(lock.__exit__, None, None, None))
            while not release.done():
                try:
                    await asyncio.shield(release)
                except asyncio.CancelledError:
                    pass
            release.result()
            raise asyncio.CancelledError
        try:
            yield
        finally:
            release = asyncio.create_task(asyncio.to_thread(lock.__exit__, None, None, None))
            cancelled_while_releasing = False
            while not release.done():
                try:
                    await asyncio.shield(release)
                except asyncio.CancelledError:
                    cancelled_while_releasing = True
            release.result()
            if cancelled_while_releasing:
                raise asyncio.CancelledError

    async def _send(
        self,
        chat_id: str,
        episode_id: str,
        approval: ApprovalReceipt,
        receipt_path: Path,
        method_name: str,
        after_persist=None,
    ) -> dict[str, Any]:
        async with self._gate_lock(receipt_path):
            if receipt_path.exists():
                return self._recover_receipt(
                    receipt_path, chat_id=chat_id, episode_id=episode_id, approval=approval
                )
            sender = getattr(self.messenger, method_name, None)
            if not callable(sender):
                raise DeliveryError("notification provider does not support required media")
            attempt_path = receipt_path.with_suffix(".attempt.json")
            if attempt_path.exists():
                try:
                    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    raise DeliveryError("delivery intent is unreadable") from error
                expected_intent = {
                    "episode_id": episode_id,
                    "artifact_kind": approval.artifact_kind,
                    "approval": asdict(approval),
                    "chat_id": chat_id,
                }
                if attempt != expected_intent:
                    raise DeliveryError("delivery intent does not bind the current destination")
                raise DeliveryError(
                    "delivery outcome is ambiguous; recover provider evidence before any retry"
                )
            attempt = {
                "episode_id": episode_id,
                "artifact_kind": approval.artifact_kind,
                "approval": asdict(approval),
                "chat_id": chat_id,
            }
            atomic_json(attempt_path, attempt)
            try:
                message_id = await sender(
                    chat_id, approval.artifact_path, caption=f"{episode_id} — revisão"
                )
            except Exception as error:
                raise DeliveryError(
                    f"{approval.artifact_kind} delivery outcome is ambiguous; explicit recovery required"
                ) from error
            if type(message_id) is not int or message_id <= 0:
                raise DeliveryError("notification provider did not return a media message identifier")
            receipt = {**attempt, "message_id": message_id}
            atomic_json(receipt_path, receipt)
            persisted = self._recover_receipt(
                receipt_path, chat_id=chat_id, episode_id=episode_id, approval=approval
            )
            if after_persist is not None:
                callback_result = after_persist(receipt_path)
                if inspect.isawaitable(callback_result):
                    await callback_result
            return persisted
