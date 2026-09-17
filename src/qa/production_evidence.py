"""Independent audit of the non-media evidence required for a YouTube delivery."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProductionEvidenceQAResult:
    approved: bool
    findings: tuple[str, ...]
    report: dict[str, Any]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


class ProductionEvidenceQA:
    """Rejects delivery when originality, sources, captions or manifest evidence is absent."""

    def review(
        self,
        *,
        script_path: Path | str,
        manifest_path: Path | str,
        captions_path: Path | str,
        metadata_path: Path | str,
        published_script_hashes: set[str] | frozenset[str],
    ) -> ProductionEvidenceQAResult:
        findings: list[str] = []
        script_path = Path(script_path)
        manifest_path = Path(manifest_path)
        captions_path = Path(captions_path)
        metadata_path = Path(metadata_path)
        report: dict[str, Any] = {}

        if not script_path.is_file():
            findings.append("SCRIPT_MISSING")
        else:
            script_hash = _sha256(script_path)
            report["script_sha256"] = script_hash
            script = _json_object(script_path)
            segments = script.get("segments") if script else None
            audience = script.get("audience") if script else None
            valid_segments = (
                isinstance(segments, list)
                and bool(segments)
                and all(
                    isinstance(segment, dict)
                    and isinstance(segment.get("id"), str)
                    and bool(segment["id"].strip())
                    and isinstance(segment.get("narration"), str)
                    and bool(segment["narration"].strip())
                    and segment.get("kind") in {"biblical_paraphrase", "family_reflection"}
                    and isinstance(segment.get("source_refs"), list)
                    and (
                        segment["kind"] != "biblical_paraphrase"
                        or bool(segment["source_refs"])
                    )
                    for segment in segments
                )
            )
            valid_audience = audience == {"min_age": 6, "max_age": 10}
            if script is None or not valid_segments or not valid_audience:
                findings.append("SCRIPT_PACKET_INVALID")
            if script_hash in published_script_hashes:
                findings.append("REUSED_SCRIPT")

        if not manifest_path.is_file():
            findings.append("MANIFEST_MISSING")
        else:
            manifest = _json_object(manifest_path)
            if manifest is None or not isinstance(manifest.get("assets"), list) or not manifest["assets"]:
                findings.append("MANIFEST_ASSETS_MISSING")
            else:
                report["manifest_sha256"] = _sha256(manifest_path)

        if not captions_path.is_file() or not captions_path.read_text(encoding="utf-8").startswith("WEBVTT"):
            findings.append("CAPTIONS_MISSING")
        else:
            report["captions_sha256"] = _sha256(captions_path)

        metadata = _json_object(metadata_path) if metadata_path.is_file() else None
        if metadata is None or not isinstance(metadata.get("references"), list) or not metadata["references"]:
            findings.append("METADATA_MISSING")
        else:
            report["metadata_sha256"] = _sha256(metadata_path)

        report["published_script_hashes_checked"] = len(published_script_hashes)
        return ProductionEvidenceQAResult(not findings, tuple(findings), report)
