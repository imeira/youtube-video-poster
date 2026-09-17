"""Hash-bound human approvals and an explicit separate publication command."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from src.approval.receipts import (
    ApprovalReceipt,
    PublicationAuthorizationError,
    load_approval_receipt,
    require_publication_authorization,
    save_approval_receipt,
)


def test_publication_needs_matching_thumbnail_video_and_separate_command(tmp_path: Path):
    video, thumbnail = tmp_path / "video.mp4", tmp_path / "thumbnail.png"
    video.write_bytes(b"video")
    thumbnail.write_bytes(b"thumbnail")
    video_receipt = ApprovalReceipt.approve("video", video, "operador")
    thumbnail_receipt = ApprovalReceipt.approve("thumbnail", thumbnail, "operador")

    authorization = require_publication_authorization(
        command="PUBLICAR EP8",
        expected_command="PUBLICAR EP8",
        video=video_receipt,
        thumbnail=thumbnail_receipt,
        metadata_sha256=hashlib.sha256(b"metadata").hexdigest(),
    )

    assert authorization.video_sha256 == video_receipt.artifact_sha256
    assert authorization.thumbnail_sha256 == thumbnail_receipt.artifact_sha256


def test_publication_rejects_changed_or_missing_approval(tmp_path: Path):
    video, thumbnail = tmp_path / "video.mp4", tmp_path / "thumbnail.png"
    video.write_bytes(b"video")
    thumbnail.write_bytes(b"thumbnail")
    video_receipt = ApprovalReceipt.approve("video", video, "operador")
    thumbnail_receipt = ApprovalReceipt.approve("thumbnail", thumbnail, "operador")
    video.write_bytes(b"different")

    with pytest.raises(PublicationAuthorizationError):
        require_publication_authorization(
            command="PUBLICAR EP8",
            expected_command="PUBLICAR EP8",
            video=video_receipt,
            thumbnail=thumbnail_receipt,
            metadata_sha256="a" * 64,
        )


def test_persisted_approval_receipt_round_trips_and_rechecks_artifact_bytes(tmp_path: Path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    receipt_path = tmp_path / "approval" / "video.json"

    saved = save_approval_receipt(
        receipt_path, ApprovalReceipt.approve("video", video, "operador")
    )
    loaded = load_approval_receipt(receipt_path)

    assert loaded == saved
    loaded.verify()
    video.write_bytes(b"changed")
    with pytest.raises(PublicationAuthorizationError, match="no longer match"):
        loaded.verify()
