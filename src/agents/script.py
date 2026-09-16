"""Script agent with a structured, child-safe narration contract."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.agents.base import AgentResult, BaseAgent

logger = logging.getLogger(__name__)


class ScriptAgent(BaseAgent):
    """Generates narration plus source-bound segments for children aged 6–10."""

    WORDS_PER_MINUTE = 130

    def __init__(self, llm_provider=None):
        super().__init__(name="Script")
        self._llm = llm_provider

    async def run(self, episode_id: str, research_data: dict | None = None, target_duration_s: int = 180, script_dir: str = "", **kwargs) -> AgentResult:
        if not research_data:
            return AgentResult(success=False, error="No research data provided")
        facts = research_data.get("narrative_classification", {}).get("BIBLICAL_FACT", [])
        references = research_data.get("references", [])
        if not facts:
            return AgentResult(success=False, error="Structured biblical facts are required")
        target_words = int((target_duration_s / 60) * self.WORDS_PER_MINUTE)
        narration = None
        if self._llm and getattr(self._llm, "available", lambda: False)():
            try:
                narration = await self._generate_llm_script(research_data.get("story", ""), research_data.get("summary", ""), facts, references, target_words)
            except Exception as exc:
                logger.warning("LLM script generation failed, using source-bound template: %s", exc)
        narration = narration or self._build_template_narration(research_data.get("summary", ""), facts)
        word_count = len(narration.split())
        packet_path = self._write_packet(episode_id, narration, facts, references, script_dir, target_duration_s)
        return AgentResult(success=True, data={
            "narration": narration,
            "word_count": word_count,
            "target_duration_s": target_duration_s,
            "estimated_duration_s": (word_count / self.WORDS_PER_MINUTE) * 60,
            "source": "llm" if self._llm and narration else "template",
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

    def _write_packet(self, episode_id: str, narration: str, facts: list[str], references: list[dict], script_dir: str, target_duration_s: int) -> Path | None:
        if not script_dir:
            return None
        directory = Path(script_dir)
        directory.mkdir(parents=True, exist_ok=True)
        labels = [self._reference_label(reference) for reference in references]
        segments = [{"id": f"S{index:03d}", "kind": "biblical_paraphrase", "narration": self._adapt_for_children(fact), "source_refs": labels, "editorial_risk": "LOW"} for index, fact in enumerate(facts, start=1)]
        segments.append({"id": f"S{len(segments)+1:03d}", "kind": "family_reflection", "narration": "Essa história nos lembra que podemos confiar em Deus e conversar sobre isso com nossa família.", "source_refs": [], "editorial_risk": "LOW"})
        packet = {"schema_version": 1, "episode_id": episode_id, "audience": {"min_age": 6, "max_age": 10}, "narration": narration, "references": labels, "segments": segments, "target_duration_s": target_duration_s, "closing_duration_s": 4, "burn_subtitles": False}
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
