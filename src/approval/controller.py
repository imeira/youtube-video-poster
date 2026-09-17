"""Hash-bound transition from delivered media to explicit human approval."""

from __future__ import annotations

import json
from pathlib import Path

from src.approval.receipts import ApprovalReceipt, save_approval_receipt


class ApprovalControllerError(ValueError):
    """Raised when a human decision cannot be bound to an exact delivery artifact."""


class ApprovalController:
    """Confirms one exact delivery artifact; it never infers approval from silence."""

    def confirm_delivery(
        self,
        *,
        episode_id: str,
        artifact_kind: str,
        command: str,
        expected_command: str,
        approver: str,
        artifact_path: Path | str,
        delivery_receipt_path: Path | str,
        approval_receipt_path: Path | str,
    ) -> ApprovalReceipt:
        """Persist a receipt only when the exact response approves delivered bytes."""
        if not command.strip() or command != expected_command:
            raise ApprovalControllerError("an exact separate human approval command is required")
        if artifact_kind not in {"thumbnail", "video"}:
            raise ApprovalControllerError("approval artifact kind is invalid")
        try:
            delivery = json.loads(Path(delivery_receipt_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ApprovalControllerError("delivery receipt is unreadable") from error
        expected_keys = {"episode_id", "artifact_kind", "approval", "message_id"}
        if set(delivery) != expected_keys or delivery["episode_id"] != episode_id:
            raise ApprovalControllerError("delivery receipt does not bind the episode")
        if delivery["artifact_kind"] != artifact_kind or type(delivery["message_id"]) is not int:
            raise ApprovalControllerError("delivery receipt does not bind the required media")
        try:
            delivered = ApprovalReceipt(**delivery["approval"])
        except TypeError as error:
            raise ApprovalControllerError("delivery approval schema mismatch") from error
        current = ApprovalReceipt.approve(artifact_kind, artifact_path, approver)
        if (
            delivered.artifact_kind != current.artifact_kind
            or Path(delivered.artifact_path).resolve() != Path(current.artifact_path).resolve()
            or delivered.artifact_sha256 != current.artifact_sha256
        ):
            raise ApprovalControllerError("approval artifact differs from delivered bytes")
        return save_approval_receipt(approval_receipt_path, current)
