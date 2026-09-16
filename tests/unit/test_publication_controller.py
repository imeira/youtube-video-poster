"""Publication stays blocked until an exact later command and remote readback."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.approval.receipts import ApprovalReceipt
from src.providers.base import PublishResult
from src.publishing.controller import PublicationController, PublicationControllerError
from src.state.machine import EpisodeState, EpisodeStateStore


class ReadbackPublisher:
    async def upload(self, video_path, metadata, thumbnail="", captions=""):
        return PublishResult(True, video_id="video-123", video_url="https://example.invalid/video-123")

    async def readback(self, video_id):
        return {"video_id": video_id, "channel": "EraUmaVezBibliaAnimada", "hd_processed": True}


def approvals(tmp_path: Path):
    video, thumbnail = tmp_path / "video.mp4", tmp_path / "thumbnail.png"
    video.write_bytes(b"video")
    thumbnail.write_bytes(b"thumbnail")
    return video, thumbnail, ApprovalReceipt.approve("video", video, "human"), ApprovalReceipt.approve("thumbnail", thumbnail, "human")


@pytest.mark.asyncio
async def test_publication_controller_requires_exact_command_then_persists_remote_readback(tmp_path: Path):
    _video, _thumbnail, video_receipt, thumbnail_receipt = approvals(tmp_path)
    metadata = tmp_path / "metadata.json"
    metadata.write_text(json.dumps({"title": "Uma história fiel"}), encoding="utf-8")
    state_path = tmp_path / "state.json"
    EpisodeStateStore("EP8", current_state=EpisodeState.WAITING_FINAL_APPROVAL).save(state_path)

    receipt = await PublicationController(ReadbackPublisher()).publish(
        state_path=state_path,
        expected_command="PUBLICAR EP8",
        command="PUBLICAR EP8",
        video=video_receipt,
        thumbnail=thumbnail_receipt,
        metadata_path=metadata,
        publication_receipt_path=tmp_path / "qa" / "publication.json",
    )

    assert receipt["readback"]["video_id"] == "video-123"
    assert receipt["metadata_sha256"]
    assert EpisodeStateStore.load(state_path).current_state is EpisodeState.PUBLISHED
    assert (tmp_path / "qa" / "publication.json").is_file()


@pytest.mark.asyncio
async def test_publication_controller_does_not_upload_without_exact_separate_command(tmp_path: Path):
    _video, _thumbnail, video_receipt, thumbnail_receipt = approvals(tmp_path)
    metadata = tmp_path / "metadata.json"
    metadata.write_text("{}", encoding="utf-8")
    state_path = tmp_path / "state.json"
    EpisodeStateStore("EP8", current_state=EpisodeState.WAITING_FINAL_APPROVAL).save(state_path)

    with pytest.raises(PublicationControllerError, match="separate exact"):
        await PublicationController(ReadbackPublisher()).publish(
            state_path=state_path,
            expected_command="PUBLICAR EP8",
            command="APROVAR",
            video=video_receipt,
            thumbnail=thumbnail_receipt,
            metadata_path=metadata,
            publication_receipt_path=tmp_path / "qa" / "publication.json",
        )

    assert EpisodeStateStore.load(state_path).current_state is EpisodeState.WAITING_FINAL_APPROVAL
