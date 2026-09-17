"""Immutable revision lineage for episode delivery artifacts.

A rejected artifact is never rewritten or eligible for a later approval.  This
registry records a hash-bound receipt per logical artifact and propagates a
supersession to every dependent artifact.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path

from src.pipeline.artifact_manifest import write_manifest_atomic


class ArtifactStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"


class SupersededArtifactError(ValueError):
    """Raised when a rejected or superseded artifact is used as active."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class RevisionRegistry:
    """Append-only artifact receipts stored beneath an episode workspace."""

    INDEX_NAME = "revision_registry.json"

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.path = self.root / self.INDEX_NAME

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {"schema_version": 1, "artifacts": {}}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("schema_version") != 1 or not isinstance(data.get("artifacts"), dict):
            raise ValueError("Invalid revision registry schema")
        return data

    def _save(self, data: dict[str, dict]) -> None:
        write_manifest_atomic(self.path, data)

    def read(self, artifact_id: str) -> dict:
        try:
            return self._load()["artifacts"][artifact_id]
        except KeyError as exc:
            raise KeyError(f"Unknown artifact: {artifact_id}") from exc

    def register(
        self,
        artifact_id: str,
        path: Path | str,
        *,
        revision: int,
        depends_on: Iterable[str] = (),
        supersedes: str | None = None,
    ) -> dict:
        file_path = Path(path)
        if revision < 1 or not artifact_id or not file_path.is_file():
            raise ValueError("Artifact id, positive revision, and existing file are required")
        data = self._load()
        artifacts = data["artifacts"]
        if artifact_id in artifacts:
            raise ValueError(f"Artifact already registered: {artifact_id}")
        dependencies = tuple(depends_on)
        if any(dependency not in artifacts for dependency in dependencies):
            raise ValueError("Every dependency must be registered first")
        if supersedes:
            prior = artifacts.get(supersedes)
            if prior is None or prior["status"] != ArtifactStatus.SUPERSEDED.value:
                raise ValueError("A successor must supersede a registered superseded artifact")
            prior["superseded_by"] = artifact_id
        receipt = {
            "artifact_id": artifact_id,
            "path": str(file_path.resolve()),
            "sha256": _sha256(file_path),
            "revision": revision,
            "depends_on": list(dependencies),
            "status": ArtifactStatus.ACTIVE.value,
            "supersedes": supersedes,
            "superseded_by": None,
            "rejection_reason": None,
        }
        artifacts[artifact_id] = receipt
        self._save(data)
        return receipt

    def reject(self, artifact_id: str, *, reason: str) -> None:
        if not reason.strip():
            raise ValueError("A rejection reason is required")
        data = self._load()
        artifacts = data["artifacts"]
        if artifact_id not in artifacts:
            raise KeyError(f"Unknown artifact: {artifact_id}")
        pending = [artifact_id]
        while pending:
            current = pending.pop()
            receipt = artifacts[current]
            if receipt["status"] == ArtifactStatus.SUPERSEDED.value:
                continue
            receipt["status"] = ArtifactStatus.SUPERSEDED.value
            receipt["rejection_reason"] = reason
            pending.extend(
                candidate_id
                for candidate_id, candidate in artifacts.items()
                if current in candidate["depends_on"]
            )
        self._save(data)

    def require_active(self, artifact_id: str) -> dict:
        receipt = self.read(artifact_id)
        file_path = Path(receipt["path"])
        if receipt["status"] != ArtifactStatus.ACTIVE.value:
            raise SupersededArtifactError(f"Artifact is superseded: {artifact_id}")
        if not file_path.is_file() or _sha256(file_path) != receipt["sha256"]:
            raise ValueError(f"Artifact bytes no longer match receipt: {artifact_id}")
        return receipt
