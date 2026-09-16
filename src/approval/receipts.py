"""Hash-bound human approval records and publication authorization."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


class PublicationAuthorizationError(ValueError):
    """Raised when publication is not authorized by exact approved artifacts."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class ApprovalReceipt:
    artifact_kind: str
    artifact_path: str
    artifact_sha256: str
    approver: str
    approved_at: str

    @classmethod
    def approve(cls, artifact_kind: str, artifact_path: Path | str, approver: str) -> "ApprovalReceipt":
        path = Path(artifact_path)
        if artifact_kind not in {"thumbnail", "video"} or not path.is_file() or not approver.strip():
            raise ValueError("A real video/thumbnail file and approver are required")
        return cls(artifact_kind, str(path.resolve()), _sha256(path), approver, datetime.now(UTC).isoformat())

    def verify(self) -> None:
        path = Path(self.artifact_path)
        if not path.is_file() or _sha256(path) != self.artifact_sha256:
            raise PublicationAuthorizationError(f"Approved {self.artifact_kind} bytes no longer match receipt")


@dataclass(frozen=True)
class PublicationAuthorization:
    command: str
    video_sha256: str
    thumbnail_sha256: str
    metadata_sha256: str
    authorized_at: str


def require_publication_authorization(*, command: str, expected_command: str, video: ApprovalReceipt, thumbnail: ApprovalReceipt, metadata_sha256: str) -> PublicationAuthorization:
    if command != expected_command or not command.strip():
        raise PublicationAuthorizationError("A separate exact publication command is required")
    if video.artifact_kind != "video" or thumbnail.artifact_kind != "thumbnail":
        raise PublicationAuthorizationError("Independent video and thumbnail approvals are required")
    if len(metadata_sha256) != 64 or any(char not in "0123456789abcdef" for char in metadata_sha256.lower()):
        raise PublicationAuthorizationError("Metadata hash is required")
    video.verify()
    thumbnail.verify()
    return PublicationAuthorization(command, video.artifact_sha256, thumbnail.artifact_sha256, metadata_sha256, datetime.now(UTC).isoformat())
