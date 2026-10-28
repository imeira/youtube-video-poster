"""Script agent with a structured, child-safe narration contract."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.agents.base import AgentResult, BaseAgent
from src.content.narrator import LORENA, closing_fields

logger = logging.getLogger(__name__)


class ScriptAgent(BaseAgent):
    """Generates narration plus source-bound segments for children aged 6–10."""

    WORDS_PER_MINUTE = 130

    def __init__(self, llm_provider=None):
        super().__init__(name="Script")
        self._llm = llm_provider

    async def run(self, episode_id: str, research_data: dict | None = None, target_duration_s: int | None = 180, script_dir: str = "", **kwargs) -> AgentResult:
        if not research_data:
            return AgentResult(success=False, error="No research data provided")
        facts = research_data.get("narrative_classification", {}).get("BIBLICAL_FACT", [])
        references = research_data.get("references", [])
        if not facts:
            return AgentResult(success=False, error="Structured biblical facts are required")
        # Segments are the authority.  The top-level narration is derived from
        # them so QA, TTS and storyboard cannot silently review different text.
        source_segments = research_data.get("narrative_segments")
        segments = self._build_source_bound_segments(source_segments) if source_segments else self._build_segments(facts, references)
        narration = "\n\n".join(segment["narration"] for segment in segments)
        word_count = len(narration.split())
        packet_path = self._write_packet(
            episode_id, narration, segments, references, script_dir, target_duration_s,
            source_bound=bool(source_segments),
        )
        return AgentResult(success=True, data={
            "narration": narration,
            "word_count": word_count,
            "target_duration_s": target_duration_s,
            "estimated_duration_s": (word_count / self.WORDS_PER_MINUTE) * 60,
            "source": "source_bound_template",
            "script_packet_path": str(packet_path) if packet_path else "",
        }, next_state="SCRIPT_QA")

    async def _generate_llm_script(self, story: str, summary: str, facts: list[str], references: list[dict], target_words: int) -> str:
        facts_text = "\n".join(f"- {fact}" for fact in facts)
        refs_text = ", ".join(self._reference_label(reference) for reference in references)
        result = await self._llm.complete(
            prompt=(f"Escreva narração pt-BR infantil 6–10 para {story}. Resumo: {summary}.\n"
                    f"Fatos autorizados:\n{facts_text}\nReferências: {refs_text}\n"
                    "Use frases simples; não invente fatos nem use medo, culpa, violência gráfica, sexualização, segredo, proibido ou clickbait. Termine com uma lição familiar."),
            system="Você é roteirista infantil bíblico fiel às fontes fornecidas.",
            max_tokens=min(4000, target_words * 4), temperature=0.7)
        narration = result.strip()
        if not narration:
            raise ValueError("LLM returned empty narration")
        return narration

    def _build_template_narration(self, summary: str, facts: list[str]) -> str:
        lines = [f"Vamos conhecer uma história da Bíblia. {summary}".strip()]
        lines.extend(self._adapt_for_children(fact) for fact in facts)
        lines.append("Essa história nos lembra que podemos confiar em Deus e conversar sobre isso com nossa família.")
        return "\n\n".join(lines)

    def _build_segments(self, facts: list[str], references: list[dict]) -> list[dict]:
        labels = [self._reference_label(reference) for reference in references]
        segments = [
            {
                "id": f"S{index:03d}",
                "kind": "biblical_paraphrase",
                "narration": self._adapt_for_children(fact),
                "source_refs": labels,
                "editorial_risk": "LOW",
            }
            for index, fact in enumerate(facts, start=1)
        ]
        segments.append({
            "id": f"S{len(segments)+1:03d}",
            "kind": "family_reflection",
            "narration": "Essa história nos lembra que podemos confiar em Deus e conversar sobre isso com nossa família.",
            "source_refs": [],
            "editorial_risk": "LOW",
            **closing_fields(),
        })
        return segments

    def _build_source_bound_segments(self, source_segments: object) -> list[dict]:
        """Project authored source prose without rewriting, padding, or truncating it."""
        if not isinstance(source_segments, list) or not source_segments:
            raise ValueError("source-bound narrative segments are required")
        reflection_indexes = [
            index for index, segment in enumerate(source_segments)
            if isinstance(segment, dict) and segment.get("kind") == "family_reflection"
        ]
        if reflection_indexes != [len(source_segments) - 1]:
            raise ValueError("EXACTLY_ONE_FINAL_FAMILY_REFLECTION_REQUIRED")
        segments: list[dict] = []
        for index, source in enumerate(source_segments, start=1):
            if not isinstance(source, dict):
                raise TypeError("source-bound narrative segment must be an object")
            segment_id = source.get("id")
            kind = source.get("kind")
            narration = source.get("narration")
            facts = source.get("facts")
            if segment_id != f"S{index:03d}" or kind not in {"biblical_paraphrase", "family_reflection"}:
                raise ValueError("source-bound segment id or kind is invalid")
            if (
                not isinstance(narration, str)
                or not narration.strip()
                or narration.rstrip().endswith(("...", "…"))
            ):
                raise ValueError("source-bound narration must be complete prose")
            if not isinstance(facts, list) or not all(isinstance(fact, str) and fact.strip() for fact in facts):
                raise ValueError("source-bound segment facts are required")
            segment = {
                "id": segment_id,
                "kind": kind,
                "narration": narration,
                "source_refs": [str(source["verses"])] if kind == "biblical_paraphrase" and source.get("verses") else [],
                "editorial_risk": "LOW",
                "source_facts": list(facts),
            }
            if kind == "biblical_paraphrase" and not segment["source_refs"]:
                raise ValueError("source-bound biblical segment verses are required")
            segments.append(segment)
        return segments

    def _write_packet(self, episode_id: str, narration: str, segments: list[dict], references: list[dict], script_dir: str, target_duration_s: int | None, *, source_bound: bool = False) -> Path | None:
        if not script_dir:
            return None
        directory = Path(script_dir)
        directory.mkdir(parents=True, exist_ok=True)
        labels = [self._reference_label(reference) for reference in references]
        packet = {"schema_version": 1, "episode_id": episode_id, "audience": {"min_age": 6, "max_age": 10}, "narration": narration, "references": labels, "segments": segments, "burn_subtitles": False}
        if target_duration_s is None:
            packet["estimated_narration_seconds"] = (len(narration.split()) / self.WORDS_PER_MINUTE) * 60
            packet["duration_status"] = "ESTIMATE_PENDING_MEASURED_AUDIO"
            packet["duration_measurement"] = "PENDING_MEASURED_AUDIO"
            if source_bound:
                closing = segments[-1]
                packet["closing_estimated_duration_s"] = (
                    len(closing["narration"].split()) / self.WORDS_PER_MINUTE
                ) * 60
                packet["source_bound_preproduction"] = True
            else:
                packet["closing_duration_s"] = 4
                packet["recurring_narrator"] = dict(LORENA)
        else:
            packet["target_duration_s"] = target_duration_s
            packet["closing_duration_s"] = 4
            packet["recurring_narrator"] = dict(LORENA)
        (directory / "narration.txt").write_text(narration, encoding="utf-8")
        packet_path = directory / "script.json"
        packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2), encoding="utf-8")
        return packet_path

    @staticmethod
    def _reference_label(reference: dict) -> str:
        if reference.get("reference"):
            return str(reference["reference"])
        book, chapter, verses = (str(reference.get(key, "")).strip() for key in ("book", "chapter", "verses"))
        return f"{book} {chapter}:{verses}".strip(":")

    @staticmethod
    def _adapt_for_children(fact: str) -> str:
        fact = fact.replace(".", "...").strip()
        return fact[:1].upper() + fact[1:] if fact else fact
