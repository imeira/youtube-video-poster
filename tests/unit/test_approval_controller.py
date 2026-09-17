"""A Telegram media delivery becomes approval only after an exact human command."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from src.approval.controller import ApprovalController, ApprovalControllerError
from src.approval.receipts import ApprovalReceipt


def delivered_receipt(tmp_path: Path, kind: str):
    artifact = tmp_path / f"asset.{ 'png' if kind == 'thumbnail' else 'mp4'}"
    artifact.write_bytes(kind.encode())
    approval = ApprovalReceipt.approve(kind, artifact, "delivery-audit")
    delivery = {
        "episode_id": "EP8", "artifact_kind": kind, "approval": asdict(approval), "message_id": 42,
    }
    delivery_path = tmp_path / f"delivery-{kind}.json"
    delivery_path.write_text(json.dumps(delivery), encoding="utf-8")
    return artifact, delivery_path


def test_exact_human_decision_creates_receipt_bound_to_delivered_artifact(tmp_path: Path):
    artifact, delivery_path = delivered_receipt(tmp_path, "thumbnail")
    receipt_path = tmp_path / "approval-thumbnail.json"

    receipt = ApprovalController().confirm_delivery(
        episode_id="EP8",
        artifact_kind="thumbnail",
        command="APROVAR THUMBNAIL EP8",
        expected_command="APROVAR THUMBNAIL EP8",
        approver="human",
        artifact_path=artifact,
        delivery_receipt_path=delivery_path,
        approval_receipt_path=receipt_path,
    )

    assert receipt.artifact_sha256 == ApprovalReceipt.approve("thumbnail", artifact, "human").artifact_sha256
    assert receipt_path.is_file()


def test_approval_controller_rejects_ambiguous_command_or_mismatched_delivery(tmp_path: Path):
    artifact, delivery_path = delivered_receipt(tmp_path, "video")

    with pytest.raises(ApprovalControllerError, match="exact"):
        ApprovalController().confirm_delivery(
            episode_id="EP8", artifact_kind="video", command="APROVAR", expected_command="APROVAR VÍDEO EP8",
            approver="human", artifact_path=artifact, delivery_receipt_path=delivery_path,
            approval_receipt_path=tmp_path / "approval-video.json",
        )

    artifact.write_bytes(b"different")
    with pytest.raises(ApprovalControllerError, match="delivered"):
        ApprovalController().confirm_delivery(
            episode_id="EP8", artifact_kind="video", command="APROVAR VÍDEO EP8", expected_command="APROVAR VÍDEO EP8",
            approver="human", artifact_path=artifact, delivery_receipt_path=delivery_path,
            approval_receipt_path=tmp_path / "approval-video.json",
        )
