"""Script Agent — writes narration for children 6-10 (§24).

Responsibility: Create narration script from research
Input: research/sources.json, duration plan
Output: script/narration.txt
Constraints: Clarity, emotion, curiosity, adventure, appropriate suspense,
            simple language, rhythm, retention, biblical fidelity (§24)
            Never pad with text to increase duration (§19)
            Target 390-750 words for 3-5 min episodes (§18)
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.agents.base import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


class ScriptAgent(BaseAgent):
    """Generates a children's Bible narration script (§24).

    Uses LLM (NVIDIA API) for rich, engaging narration.
    Falls back to template if LLM unavailable.
    """

    # Children's narration pace: ~130 words/minute (slower than adult)
    WORDS_PER_MINUTE = 130

    def __init__(self, llm_provider=None):
        super().__init__(name="Script")
        self._llm = llm_provider

    async def run(
        self,
        episode_id: str,
        research_data: dict | None = None,
        target_duration_s: int = 180,
        script_dir: str = "",
        **kwargs,
    ) -> AgentResult:
        """Generate narration text from research data.

        Args:
            research_data: Output from ResearchAgent (sources.json).
            target_duration_s: Target narration duration in seconds (from DurationPlan).
            script_dir: Directory to save narration.txt.

        Returns:
            AgentResult with narration text and word count.
        """
        if not research_data:
            return AgentResult(success=False, error="No research data provided")

        target_words = int((target_duration_s / 60) * self.WORDS_PER_MINUTE)
        summary = research_data.get("summary", "")
        facts = research_data.get("narrative_classification", {}).get("BIBLICAL_FACT", [])
        story = research_data.get("story", "")
        references = research_data.get("references", [])

        # Try LLM first for rich narration
        narration = None
        if self._llm and getattr(self._llm, "available", lambda: False)():
            try:
                narration = await self._generate_llm_script(
                    story=story,
                    summary=summary,
                    facts=facts,
                    references=references,
                    target_words=target_words,
                )
            except Exception as e:
                logger.warning(f"LLM script generation failed, using template: {e}")

        # Fallback to template
        if not narration:
            narration = self._build_template_narration(
                story=story, summary=summary, facts=facts, target_words=target_words,
            )

        word_count = len(narration.split())

        # Save to script dir
        if script_dir:
            Path(script_dir).mkdir(parents=True, exist_ok=True)
            with open(Path(script_dir) / "narration.txt", "w", encoding="utf-8") as f:
                f.write(narration)

        return AgentResult(
            success=True,
            data={
                "narration": narration,
                "word_count": word_count,
                "target_duration_s": target_duration_s,
                "estimated_duration_s": (word_count / self.WORDS_PER_MINUTE) * 60,
                "source": "llm" if narration and self._llm else "template",
            },
            next_state="SCRIPT_QA",
        )

    async def _generate_llm_script(
        self,
        story: str,
        summary: str,
        facts: list[str],
        references: list[dict],
        target_words: int,
    ) -> str:
        """Generate rich narration using LLM (NVIDIA API)."""
        facts_text = "\n".join(f"- {f}" for f in facts)
        refs_text = ", ".join(
            f"{r.get('book', '')} {r.get('chapter', '')}:{r.get('verses', '')}"
            for r in references
        )

        system = (
            "Você é um roteirista especialista em conteúdo bíblico infantil para YouTube. "
            "Escreve narrações para crianças de 6 a 10 anos em português do Brasil. "
            "Suas narrações são envolventes, emocionantes, fiéis à Bíblia, com linguagem simples "
            "e ritmo que mantém a atenção das crianças. Nunca inventa fatos bíblicos — apenas "
            "adapta os fatos fornecidos em linguagem infantil."
        )

        prompt = f"""Escreva a narração completa para um vídeo do YouTube infantil sobre: {story}

Resumo da história: {summary}

Fatos bíblicos a incluir (NÃO invente fatos adicionais):
{facts_text}

Referências bíblicas: {refs_text}

REGRAS OBRIGATÓRIAS:
- Público: crianças de 6 a 10 anos
- Idioma: português do Brasil
- Tom: acolhedor, emocionante, educativo, fiel à Bíblia
- Linguagem simples e clara
- Comece com um gancho que prenda a atenção ("Era uma vez..." ou pergunta curiosa)
- Divida a história em momentos claros, cada fato bíblico vira uma cena narrada
- Use pausas naturais (pontos finais, não pontos de exclamação excessivos)
- Termine com uma lição ou mensagem de amor e fé
- TAMANHO OBRIGATÓRIO: mínimo {target_words} palavras, máximo {int(target_words * 1.3)} palavras
- NÃO escreva menos de {target_words} palavras — cada fato bíblico deve ser detalhado com descrições ricas
- Para cada um dos {len(facts)} fatos bíblicos, escreva pelo menos {max(50, target_words // max(1, len(facts)))} palavras descrevendo a cena
- NÃO adicione texto de preenchimento para aumentar a duração — detalhe a história com imagens vívidas
- NÃO corte fatos importantes para encurtar
- Escreva APENAS a narração, sem marcadores de cena, sem números, sem cabeçalhos
- A narração deve ser contínua, como se fosse lida por um narrador

Narração:"""

        result = await self._llm.complete(
            prompt=prompt,
            system=system,
            max_tokens=min(4000, target_words * 4),  # tokens ~= words * 2.5, generous
            temperature=0.7,
        )

        # Clean up — remove any markdown headers, scene markers, etc.
        lines = result.strip().split("\n")
        cleaned = []
        for line in lines:
            line = line.strip()
            if not line:
                if cleaned and cleaned[-1]:
                    cleaned.append("")  # preserve paragraph breaks
                continue
            # Skip markdown headers, scene markers, numbers
            if line.startswith("#") or line.startswith("**") or line.startswith("Cena"):
                continue
            if line.startswith("-") or line.startswith("1.") or line.startswith("2."):
                continue
            cleaned.append(line)

        narration = "\n".join(cleaned).strip()
        if not narration:
            raise ValueError("LLM returned empty narration after cleanup")

        return narration

    def _build_template_narration(self, story: str, summary: str, facts: list[str], target_words: int) -> str:
        """Build child-friendly narration from biblical facts (template fallback).

        §24: Clarity, emotion, curiosity, adventure, simple language, rhythm.
        §25: Child safety — no graphic violence or trauma.
        §23: Never present creative addition as biblical fact.
        """
        lines = []

        # Opening — hook for children
        lines.append(f"Era uma vez... {summary}")
        lines.append("")

        # Story beats from biblical facts, adapted for children
        for fact in facts:
            line = self._adapt_for_children(fact)
            lines.append(line)

        # Closing — meaningful conclusion (§24)
        lines.append("")
        lines.append("E foi assim que Deus mostrou o seu grande amor e cuidado.")

        narration = "\n".join(lines)

        # If too long, trim (§19: never pad, but also never truncate to lose comprehension)
        words = narration.split()
        if len(words) > target_words * 1.3:
            words = words[:int(target_words * 1.1)]
            narration = " ".join(words)

        return narration

    def _adapt_for_children(self, fact: str) -> str:
        """Adapt a biblical fact into child-friendly language (§24)."""
        fact = fact.replace(".", "...")
        if fact:
            fact = fact[0].upper() + fact[1:]
        return fact
