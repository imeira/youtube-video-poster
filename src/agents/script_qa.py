"""Independent deterministic review of structured child-safe scripts."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata


@dataclass(frozen=True)
class ScriptQAResult:
    approved: bool
    findings: tuple[str, ...]


class ScriptQAAgent:
    """Blocks unsourced claims and child-unsafe language before TTS."""

    _BANNED = (
        "SEGREDO", "PROIBID", "CHOCANTE", "CHOQUE", "ASSUSTADOR", "TERROR", "SANGUE", "MUTILA",
        "INFERNO", "COMENTE", "COMENTARIO", "ENDERECO", "IDADE", "COMPRE", "COMPRA",
        "ANTES QUE SEJA TARDE", "URGENTE",
    )

    @staticmethod
    def _fold(text: str) -> str:
        return "".join(
            char for char in unicodedata.normalize("NFKD", text).upper()
            if not unicodedata.combining(char)
        )

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
        canonical = "\n\n".join(str(segment.get("narration", "")) for segment in segments)
        if packet.get("narration") != canonical:
            findings.append("NARRATION_MUST_EQUAL_APPROVED_SEGMENTS")
        for segment in segments:
            segment_id = str(segment.get("id", "UNKNOWN"))
            narration = str(segment.get("narration", ""))
            kind = segment.get("kind")
            if kind not in {"biblical_paraphrase", "family_reflection"}:
                findings.append(f"{segment_id}:UNKNOWN_KIND")
            if kind == "biblical_paraphrase" and not segment.get("source_refs"):
                findings.append(f"{segment_id}:SOURCE_REQUIRED")
            if not narration.strip():
                findings.append(f"{segment_id}:NARRATION_REQUIRED")
            normalized = self._fold(narration)
            findings.extend(term for term in self._BANNED if term in normalized)
            if re.search(r"\b(?:COMENTE|DIGA|FALE|ESCREVA)\s+(?:SEU|SUA)\s+NOME\b", normalized):
                findings.append(f"{segment_id}:PERSONAL_DATA_REQUEST")
            if len(re.findall(r"\b\w+\b", narration)) > 30:
                findings.append(f"{segment_id}:SENTENCE_TOO_LONG")
        return ScriptQAResult(not findings, tuple(dict.fromkeys(findings)))
