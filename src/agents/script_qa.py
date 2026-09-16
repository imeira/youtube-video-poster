"""Independent deterministic review of structured child-safe scripts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScriptQAResult:
    approved: bool
    findings: tuple[str, ...]


class ScriptQAAgent:
    """Blocks unsourced claims and child-unsafe language before TTS."""

    _BANNED = ("SEGREDO", "PROIBID", "CHOCANTE", "CHOQUE", "ASSUSTADOR", "TERROR", "SANGUE", "MUTILA")

    def review(self, packet: dict) -> ScriptQAResult:
        findings: list[str] = []
        if packet.get("audience") != {"min_age": 6, "max_age": 10}:
            findings.append("AUDIENCE_MUST_BE_6_TO_10")
        if packet.get("closing_duration_s") not in (3, 4, 5):
            findings.append("CLOSING_MUST_BE_3_TO_5_SECONDS")
        segments = packet.get("segments")
        if not isinstance(segments, list) or not segments:
            findings.append("SEGMENTS_REQUIRED")
            return ScriptQAResult(False, tuple(findings))
        for segment in segments:
            segment_id = str(segment.get("id", "UNKNOWN"))
            narration = str(segment.get("narration", ""))
            kind = segment.get("kind")
            if kind not in {"biblical_paraphrase", "family_reflection"}:
                findings.append(f"{segment_id}:UNKNOWN_KIND")
            if kind == "biblical_paraphrase" and not segment.get("source_refs"):
                findings.append(f"{segment_id}:SOURCE_REQUIRED")
            upper = narration.upper()
            findings.extend(term for term in self._BANNED if term in upper)
        return ScriptQAResult(not findings, tuple(dict.fromkeys(findings)))
