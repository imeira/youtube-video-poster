"""Revision lineage protects rejected delivery artifacts from reuse."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.pipeline.revision import (
    ArtifactStatus,
    RevisionRegistry,
    SupersededArtifactError,
)


def test_rejection_supersedes_descendants_without_mutating_prior_receipts(tmp_path: Path):
    registry = RevisionRegistry(tmp_path)
    video = tmp_path / "renders" / "final_v1.mp4"
    gate = tmp_path / "approval" / "video_gate_v1.json"
    video.parent.mkdir(parents=True)
    gate.parent.mkdir(parents=True)
    video.write_bytes(b"video-v1")
    gate.write_text("gate-v1", encoding="utf-8")

    registry.register("video-v1", video, revision=1)
    registry.register("video-gate-v1", gate, revision=1, depends_on=("video-v1",))
    receipt_before = registry.read("video-v1")

    registry.reject("video-v1", reason="roteiro infantil precisa ser refeito")

    assert registry.read("video-v1")["status"] == ArtifactStatus.SUPERSEDED.value
    assert registry.read("video-gate-v1")["status"] == ArtifactStatus.SUPERSEDED.value
    assert registry.read("video-v1")["sha256"] == receipt_before["sha256"]
    with pytest.raises(SupersededArtifactError):
        registry.require_active("video-gate-v1")


def test_active_successor_is_required_for_approval(tmp_path: Path):
    registry = RevisionRegistry(tmp_path)
    old = tmp_path / "thumbnail_v1.png"
    new = tmp_path / "thumbnail_v2.png"
    old.write_bytes(b"old")
    new.write_bytes(b"new")

    registry.register("thumbnail-v1", old, revision=1)
    registry.reject("thumbnail-v1", reason="imagem rejeitada")
    registry.register("thumbnail-v2", new, revision=2, supersedes="thumbnail-v1")

    assert registry.require_active("thumbnail-v2")["revision"] == 2
    assert registry.read("thumbnail-v1")["superseded_by"] == "thumbnail-v2"
