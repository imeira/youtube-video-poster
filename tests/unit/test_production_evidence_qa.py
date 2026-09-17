from __future__ import annotations

import hashlib
import json
from pathlib import Path

from src.qa.production_evidence import ProductionEvidenceQA


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_production_evidence_qa_accepts_bound_original_episode_artifacts(tmp_path):
    script = _write(
        tmp_path / "script.json",
        json.dumps({
            "audience": {"min_age": 6, "max_age": 10},
            "segments": [
                {"id": "S001", "kind": "biblical_paraphrase", "narration": "Deus falou.", "source_refs": ["Gênesis 1:1"]},
                {"id": "S002", "kind": "family_reflection", "narration": "Vamos conversar.", "source_refs": []},
            ],
        }),
    )
    manifest = _write(tmp_path / "manifest.json", json.dumps({"assets": [{"sha256": "a" * 64}]}))
    captions = _write(tmp_path / "captions.vtt", "WEBVTT\n\n")
    metadata = _write(
        tmp_path / "metadata.json",
        json.dumps({"references": [{"book": "Gênesis"}], "licenses": {"visual_assets": "generated_or_canonical", "music": "none"}}),
    )

    result = ProductionEvidenceQA().review(
        script_path=script,
        manifest_path=manifest,
        captions_path=captions,
        metadata_path=metadata,
        published_script_hashes=set(),
    )

    assert result.approved is True
    assert result.report["script_sha256"] == hashlib.sha256(script.read_bytes()).hexdigest()


def test_production_evidence_qa_blocks_metadata_without_license_declarations(tmp_path):
    script = _write(
        tmp_path / "script.json",
        json.dumps({"audience": {"min_age": 6, "max_age": 10}, "segments": [{"id": "S001", "kind": "biblical_paraphrase", "narration": "Deus falou.", "source_refs": ["Gênesis 1:1"]}]}),
    )
    manifest = _write(tmp_path / "manifest.json", json.dumps({"assets": [{"sha256": "a" * 64}]}))
    captions = _write(tmp_path / "captions.vtt", "WEBVTT\n\n")
    metadata = _write(tmp_path / "metadata.json", json.dumps({"references": [{"book": "Gênesis"}]}))

    result = ProductionEvidenceQA().review(
        script_path=script, manifest_path=manifest, captions_path=captions, metadata_path=metadata, published_script_hashes=set()
    )

    assert "LICENSES_MISSING" in result.findings


def test_production_evidence_qa_blocks_reused_published_caption_artifact(tmp_path):
    script = _write(
        tmp_path / "script.json",
        json.dumps({"audience": {"min_age": 6, "max_age": 10}, "segments": [{"id": "S001", "kind": "biblical_paraphrase", "narration": "Deus falou.", "source_refs": ["Gênesis 1:1"]}]}),
    )
    manifest = _write(tmp_path / "manifest.json", json.dumps({"assets": [{"sha256": "a" * 64}]}))
    captions = _write(tmp_path / "captions.vtt", "WEBVTT\n\n")
    metadata = _write(tmp_path / "metadata.json", json.dumps({"references": [{"book": "Gênesis"}], "licenses": {"visual_assets": "generated_or_canonical", "music": "none"}}))

    result = ProductionEvidenceQA().review(
        script_path=script, manifest_path=manifest, captions_path=captions, metadata_path=metadata,
        published_script_hashes=set(), published_artifact_hashes={hashlib.sha256(captions.read_bytes()).hexdigest()},
    )

    assert "REUSED_ARTIFACT" in result.findings


def test_production_evidence_qa_blocks_missing_sidecars_and_reused_script(tmp_path):
    script = _write(tmp_path / "script.json", json.dumps({"narration_segments": [{"id": "S001"}]}))
    manifest = _write(tmp_path / "manifest.json", json.dumps({"assets": []}))

    result = ProductionEvidenceQA().review(
        script_path=script,
        manifest_path=manifest,
        captions_path=tmp_path / "missing.vtt",
        metadata_path=tmp_path / "missing.json",
        published_script_hashes={hashlib.sha256(script.read_bytes()).hexdigest()},
    )

    assert result.approved is False
    assert set(result.findings) == {
        "SCRIPT_PACKET_INVALID",
        "REUSED_SCRIPT",
        "CAPTIONS_MISSING",
        "METADATA_MISSING",
        "LICENSES_MISSING",
        "MANIFEST_ASSETS_MISSING",
    }
