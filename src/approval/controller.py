"""Hash-bound transition from delivered media to authenticated human approval."""

from __future__ import annotations

import json
from pathlib import Path

from src.approval.inbound import AuthenticatedApprovalSource
from src.approval.receipts import (
    ApprovalReceipt,
    load_approval_receipt,
    save_approval_receipt,
)


class ApprovalControllerError(ValueError):
    """Raised when an authenticated decision cannot bind an exact delivery."""


def approval_command(*, episode_id: str, artifact_kind: str) -> str:
    """Derive the only accepted command internally from the gate identity."""
    if artifact_kind not in {"thumbnail", "video"} or not episode_id.strip():
        raise ApprovalControllerError("approval artifact kind is invalid")
    label = "THUMBNAIL" if artifact_kind == "thumbnail" else "VÍDEO"
    return f"APROVAR {label} {episode_id}"


class ApprovalController:
    """Confirms an exact delivery only through an authenticated inbound boundary."""

    def confirm_delivery(
        self,
        *,
        episode_id: str,
        artifact_kind: str,
        artifact_path: Path | str,
        delivery_receipt_path: Path | str,
        approval_receipt_path: Path | str,
        expected_chat_id: str,
        trusted_sender_ids: frozenset[str],
        inbound_source: AuthenticatedApprovalSource | None,
    ) -> ApprovalReceipt:
        """Persist a receipt when a verified reply binds the delivered bytes/message."""
        if artifact_kind not in {"thumbnail", "video"}:
            raise ApprovalControllerError("approval artifact kind is invalid")
        if not expected_chat_id.strip() or not trusted_sender_ids:
            raise ApprovalControllerError("trusted approval chat and sender configuration are required")
        try:
            delivery = json.loads(Path(delivery_receipt_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ApprovalControllerError("delivery receipt is unreadable") from error
        expected_keys = {"episode_id", "artifact_kind", "approval", "chat_id", "message_id"}
        if set(delivery) != expected_keys or delivery.get("episode_id") != episode_id:
            raise ApprovalControllerError("delivery receipt does not bind the episode")
        if (
            delivery.get("artifact_kind") != artifact_kind
            or type(delivery.get("message_id")) is not int
            or delivery["message_id"] <= 0
        ):
            raise ApprovalControllerError("delivery receipt does not bind the required media")
        if delivery.get("chat_id") != expected_chat_id:
            raise ApprovalControllerError("delivery receipt does not bind the configured destination")
        try:
            delivered = ApprovalReceipt(**delivery["approval"])
            delivered.verify()
        except Exception as error:
            raise ApprovalControllerError("delivery receipt no longer binds delivered bytes") from error
        current = ApprovalReceipt.approve(artifact_kind, artifact_path, "delivery-check")
        if (
            delivered.artifact_kind != current.artifact_kind
            or Path(delivered.artifact_path).resolve() != Path(current.artifact_path).resolve()
            or delivered.artifact_sha256 != current.artifact_sha256
        ):
            raise ApprovalControllerError("approval artifact differs from delivered bytes")
        if inbound_source is None:
            raise ApprovalControllerError("authenticated inbound approval transport is not configured")
        if Path(approval_receipt_path).is_file():
            try:
                receipt = load_approval_receipt(approval_receipt_path)
                receipt.verify()
            except Exception as error:
                raise ApprovalControllerError("persisted approval receipt is unreadable") from error
            if (
                receipt.artifact_kind != current.artifact_kind
                or Path(receipt.artifact_path).resolve() != Path(current.artifact_path).resolve()
                or receipt.artifact_sha256 != current.artifact_sha256
                or not isinstance(receipt.inbound_receipt, dict)
            ):
                raise ApprovalControllerError("persisted approval does not bind authenticated current artifact")
            reply = inbound_source.revalidate_reply(receipt.inbound_receipt)
            self._require_authority(
                reply, expected_chat_id=expected_chat_id,
                trusted_sender_ids=trusted_sender_ids, delivery_message_id=delivery["message_id"],
                episode_id=episode_id, artifact_kind=artifact_kind,
            )
            if reply.document() != receipt.inbound_receipt:
                raise ApprovalControllerError("persisted approval reply differs from authenticated journal")
            return receipt

        reply = inbound_source.claim_verified_reply(
            chat_id=expected_chat_id, message_id=delivery["message_id"]
        )
        if reply is None:
            reply = inbound_source.recover_verified_reply(
                chat_id=expected_chat_id, message_id=delivery["message_id"]
            )
        self._require_authority(
            reply, expected_chat_id=expected_chat_id,
            trusted_sender_ids=trusted_sender_ids, delivery_message_id=delivery["message_id"],
            episode_id=episode_id, artifact_kind=artifact_kind,
        )
        receipt = ApprovalReceipt.approve(
            artifact_kind, artifact_path, f"{reply.transport}:{reply.sender_id}",
            inbound_receipt=reply.document(),
        )
        return save_approval_receipt(approval_receipt_path, receipt)

    @staticmethod
    def _require_authority(
        reply,
        *,
        expected_chat_id: str,
        trusted_sender_ids: frozenset[str],
        delivery_message_id: int,
        episode_id: str,
        artifact_kind: str,
    ) -> None:
        if reply is None:
            raise ApprovalControllerError("authenticated inbound approval reply is required")
        if (
            reply.chat_id != expected_chat_id
            or reply.sender_id not in trusted_sender_ids
            or reply.reply_to_message_id != delivery_message_id
        ):
            raise ApprovalControllerError("authenticated reply does not bind trusted sender/chat/message")
        if reply.text != approval_command(episode_id=episode_id, artifact_kind=artifact_kind):
            raise ApprovalControllerError("an exact internally-derived approval command is required")