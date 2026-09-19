"""Independent deterministic review of structured child-safe scripts."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from src.content.narrator import LORENA


@dataclass(frozen=True)
class ScriptQAResult:
    approved: bool
    findings: tuple[str, ...]


class ScriptQAAgent:
    """Blocks unsourced claims and child-unsafe language before TTS."""

    _BANNED = (
        "SEGREDO", "PROIBID", "CHOCANTE", "CHOQUE", "ASSUSTADOR", "TERROR", "SANGUE", "MUTILA",
        "INFERNO", "COMENTE", "COMENTARIO", "ENDERECO", "COMPRE", "COMPRA",
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
        closing_duration = packet.get("closing_duration_s")
        if not isinstance(closing_duration, int) or not 3 <= closing_duration <= 10:
            findings.append("CLOSING_MUST_BE_3_TO_10_SECONDS")
        legacy_revoice = packet.get("legacy_published_episode_revoice") is True
        if not legacy_revoice and packet.get("recurring_narrator") != LORENA:
            findings.append("CANONICAL_LORENA_NARRATOR_REQUIRED")
        segments = packet.get("segments")
        if not isinstance(segments, list) or not segments:
            findings.append("SEGMENTS_REQUIRED")
            return ScriptQAResult(False, tuple(findings))
        canonical = "\n\n".join(str(segment.get("narration", "")) for segment in segments)
        if packet.get("narration") != canonical:
            findings.append("NARRATION_MUST_EQUAL_APPROVED_SEGMENTS")
        closing = segments[-1]
        if not legacy_revoice and not (
            closing.get("kind") == "family_reflection"
            and closing.get("presenter") == LORENA["name"]
            and closing.get("visual_mode") == "generated_video"
            and isinstance(closing.get("duration_s"), int)
            and 3 <= closing.get("duration_s") <= 10
            and closing.get("duration_s") == packet.get("closing_duration_s")
            and closing.get("lesson_role") == "episode_central_message"
        ):
            findings.append("FINAL_LORENA_VIDEO_LESSON_REQUIRED")
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
            if re.search(r"\b(?:COMENTE|DIGA|FALE|ESCREVA)\s+(?:SEU|SUA)\s+(?:NOME|IDADE|ENDERECO)\b", normalized):
                findings.append(f"{segment_id}:PERSONAL_DATA_REQUEST")
            sentences = [sentence for sentence in re.split(r"[.!?]+", narration) if sentence.strip()]
            if any(len(re.findall(r"\b\w+\b", sentence)) > 30 for sentence in sentences):
                findings.append(f"{segment_id}:SENTENCE_TOO_LONG")
        return ScriptQAResult(not findings, tuple(dict.fromkeys(findings)))
