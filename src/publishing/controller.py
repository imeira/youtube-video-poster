"""Publication control plane with explicit authorization and durable readback."""

from __future__ import annotations

import hashlib
import inspect
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from src.approval.receipts import (
    ApprovalReceipt,
    PublicationAuthorizationError,
    require_publication_authorization,
)
from src.hybrid.artifacts import atomic_json
from src.state.machine import EpisodeState, EpisodeStateStore


class PublicationControllerError(ValueError):
    """Raised when a publish attempt lacks exact authorization or readback."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PublicationController:
    """The only publication path; final-video approval alone cannot call upload."""

    def __init__(self, publisher: Any):
        self.publisher = publisher

    async def publish(
        self,
        *,
        state_path: Path | str,
        expected_command: str,
        command: str,
        video: ApprovalReceipt,
        thumbnail: ApprovalReceipt,
        metadata_path: Path | str,
        publication_receipt_path: Path | str,
        captions_path: Path | str | None = None,
    ) -> dict[str, Any]:
        """Upload once, then persist authoritative remote readback before PUBLISHED."""
        state_path = Path(state_path)
        receipt_path = Path(publication_receipt_path)
        metadata_path = Path(metadata_path)
        state = EpisodeStateStore.load(state_path)
        if state.current_state != EpisodeState.WAITING_FINAL_APPROVAL:
            raise PublicationControllerError("publication requires WAITING_FINAL_APPROVAL")
        if not metadata_path.is_file():
            raise PublicationControllerError("metadata file is required")
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise PublicationControllerError("metadata must be valid JSON") from error
        if not isinstance(metadata, dict):
            raise PublicationControllerError("metadata must be a JSON object")
        if receipt_path.exists():
            raise PublicationControllerError("publication receipt already exists; read recovery instead")
        metadata_sha256 = _sha256(metadata_path)
        try:
            authorization = require_publication_authorization(
                command=command,
                expected_command=expected_command,
                video=video,
                thumbnail=thumbnail,
                metadata_sha256=metadata_sha256,
            )
        except PublicationAuthorizationError as error:
            raise PublicationControllerError(str(error)) from error
        captions = ""
        if captions_path is not None:
            captions_file = Path(captions_path)
            if not captions_file.is_file():
                raise PublicationControllerError("caption upload artifact is missing")
            captions = str(captions_file.resolve())
        state.transition_to(EpisodeState.UPLOADING, agent="PublicationController", note="authorized upload")
        state.save(state_path)
        result = await self.publisher.upload(
            video.artifact_path,
            metadata,
            thumbnail=thumbnail.artifact_path,
            captions=captions,
        )
        if not result.success or not result.video_id or not result.video_url:
            raise PublicationControllerError("publisher did not return a durable video identifier and URL")
        readback_method = getattr(self.publisher, "readback", None)
        if not callable(readback_method):
            raise PublicationControllerError("publisher readback is required before publication")
        readback = readback_method(result.video_id)
        if inspect.isawaitable(readback):
            readback = await readback
        if not isinstance(readback, dict) or readback.get("video_id") != result.video_id:
            raise PublicationControllerError("publisher readback does not bind the uploaded video")
        receipt = {
            "authorization": asdict(authorization),
            "video_path": video.artifact_path,
            "thumbnail_path": thumbnail.artifact_path,
            "metadata_path": str(metadata_path.resolve()),
            "metadata_sha256": metadata_sha256,
            "video_id": result.video_id,
            "video_url": result.video_url,
            "readback": readback,
        }
        atomic_json(receipt_path, receipt)
        persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
        if persisted != receipt:
            raise PublicationControllerError("publication receipt readback mismatch")
        state.transition_to(EpisodeState.PUBLISHED, agent="PublicationController", note="remote readback verified")
        state.save(state_path)
        return receipt
