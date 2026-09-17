"""Independent narrative and child-safety gate for final delivery sidecars."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PostProductionNarrativeQAResult:
    approved: bool
    findings: tuple[str, ...]
    report: dict[str, object]


class PostProductionNarrativeQA:
    """Verify structured biblical narrative and child-safe metadata independently."""

    def review(self, *, script_path: Path | str, captions_path: Path | str, metadata_path: Path | str) -> PostProductionNarrativeQAResult:
        paths = {"script": Path(script_path), "captions": Path(captions_path), "metadata": Path(metadata_path)}
        findings: list[str] = []
        report: dict[str, object] = {}
        for name, path in paths.items():
            if not path.is_file():
                findings.append(f"{name.upper()}_MISSING")
            else:
                report[f"{name}_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        if findings:
            return PostProductionNarrativeQAResult(False, tuple(findings), report)
        try:
            script = json.loads(paths["script"].read_text(encoding="utf-8"))
            metadata = json.loads(paths["metadata"].read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return PostProductionNarrativeQAResult(False, ("SIDECAR_JSON_INVALID",), report)
        audience = script.get("audience") if isinstance(script, dict) else None
        segments = script.get("segments") if isinstance(script, dict) else None
        if not isinstance(audience, dict) or audience.get("min_age") != 6 or audience.get("max_age") != 10:
            findings.append("AUDIENCE_INVALID")
        if not isinstance(segments, list) or not segments:
            findings.append("SCRIPT_SEGMENTS_INVALID")
        else:
            for segment in segments:
                if segment.get("kind") == "biblical_paraphrase" and not segment.get("source_refs"):
                    findings.append("BIBLICAL_SOURCE_MISSING")
                    break
        metadata_text = json.dumps(metadata, ensure_ascii=False).lower()
        if any(term in metadata_text for term in ("inscrev", "curta", "like", "compartilh")):
            findings.append("ENGAGEMENT_CTA")
        captions = paths["captions"].read_text(encoding="utf-8")
        if not captions.startswith("WEBVTT"):
            findings.append("CAPTIONS_INVALID")
        report["finding_count"] = len(findings)
        return PostProductionNarrativeQAResult(not findings, tuple(sorted(set(findings))), report)
