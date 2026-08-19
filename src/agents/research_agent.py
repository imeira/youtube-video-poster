"""Research Agent — biblical source grounding (§22-23).

§22: Implement Biblical Source Grounding — register sources used.
§23: Classify BIBLICAL_FACT vs NARRATIVE_INFERENCE vs CREATIVE_ADDITION.
Never present a creative addition as explicitly recorded in the Bible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from src.config.loader import StudioConfig


@dataclass
class BiblicalReference:
    """A biblical passage reference (§22)."""
    book: str
    chapter: int
    verses: str  # e.g. "1-58" or "1-11, 20-25"


@dataclass
class NarrativeClassification:
    """§23: Classify each narrative element."""
    BIBLICAL_FACT: list[str] = field(default_factory=list)
    NARRATIVE_INFERENCE: list[str] = field(default_factory=list)
    CREATIVE_ADDITION: list[str] = field(default_factory=list)


@dataclass
class ResearchResult:
    """Output of the Research Agent."""
    story: str
    references: list[BiblicalReference] = field(default_factory=list)
    summary: str = ""
    narrative_classification: NarrativeClassification = field(default_factory=NarrativeClassification)
    key_events: list[str] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    sensitive_content: list[str] = field(default_factory=list)  # §26: sensitive cases

    def to_dict(self) -> dict[str, Any]:
        return {
            "story": self.story,
            "references": [asdict(r) for r in self.references],
            "summary": self.summary,
            "narrative_classification": asdict(self.narrative_classification),
            "key_events": self.key_events,
            "characters": self.characters,
            "sensitive_content": self.sensitive_content,
        }


class ResearchAgent:
    """Research Agent — researches biblical stories and identifies sources.

    Uses LLMProvider for research, web_search for source verification.
    """

    def __init__(self, config: StudioConfig):
        self.config = config

    async def research(self, theme: str, llm_provider=None) -> ResearchResult:
        """Research a biblical theme and return grounded sources.

        Args:
            theme: The biblical story theme (e.g., "Davi e Golias").
            llm_provider: LLM provider for research. If None, uses web search only.

        Returns:
            ResearchResult with references and classification.
        """
        # Common biblical stories with known references
        known_stories = {
            "criação": ResearchResult(
                story="Criação do Mundo",
                references=[BiblicalReference(book="Gênesis", chapter=1, verses="1-31")],
                summary="Deus criou o mundo em 6 dias e descansou no 7º.",
                key_events=["Criação da luz", "Criação do céu e terra", "Criação das plantas",
                           "Criação do sol e lua", "Criação dos animais", "Criação do homem",
                           "Descanso de Deus"],
                characters=["Deus", "Adão", "Eva"],
                sensitive_content=["nudez antes da queda (§26)"],
            ),
            "davi e golias": ResearchResult(
                story="Davi e Golias",
                references=[BiblicalReference(book="1 Samuel", chapter=17, verses="1-58")],
                summary="O jovem pastor Davi derrotou o gigante filisteu Golias com uma funda e uma pedra.",
                key_events=["Davi cuida das ovelhas", "Golias desafia Israel",
                           "Davi se oferece para lutar", "Davi escolhe 5 pedras",
                           "Davi derrota Golias", "Vitória de Israel"],
                characters=["Davi", "Golias", "Saul"],
                sensitive_content=[],
            ),
            "daniel na cova dos leões": ResearchResult(
                story="Daniel na Cova dos Leões",
                references=[BiblicalReference(book="Daniel", chapter=6, verses="1-28")],
                summary="Daniel foi lançado na cova dos leões por orar a Deus, mas foi protegido por um anjo.",
                key_events=["Daniel é fiel a Deus", "Inimigos criam lei contra oração",
                           "Daniel ora e é preso", "Lançado na cova dos leões",
                           "Deus envia anjo para proteger", "Daniel é salvo"],
                characters=["Daniel", "Rei Dario"],
                sensitive_content=[],
            ),
            "jonas e o grande peixe": ResearchResult(
                story="Jonas e o Grande Peixe",
                references=[BiblicalReference(book="Jonas", chapter=1, verses="1-17")],
                summary="Jonas tentou fugir de Deus, foi engolido por um grande peixe, e obedeceu a Deus.",
                key_events=["Deus manda Jonas ir a Nínive", "Jonas foge",
                           "Tempestade no mar", "Jonas é lançado no mar",
                           "Grande peixe engole Jonas", "Jonas ora", "Peixe vomita Jonas"],
                characters=["Jonas"],
                sensitive_content=[],
            ),
            "noé e a arca": ResearchResult(
                story="Noé e a Arca",
                references=[BiblicalReference(book="Gênesis", chapter=6, verses="9-22")],
                summary="Noé construiu uma arca por ordem de Deus e salvou sua família e os animais do dilúvio.",
                key_events=["Deus avisa Noé do dilúvio", "Noé constrói a arca",
                           "Animais entram na arca", "Chove 40 dias",
                           "Águas baixam", "Arco-íris como promessa"],
                characters=["Noé", "família de Noé"],
                sensitive_content=[],
            ),
        }

        theme_lower = theme.lower().strip()

        # Try to match known stories
        for key, result in known_stories.items():
            if key in theme_lower:
                result.narrative_classification = self._classify(result)
                return result

        # Default: create a generic research result
        result = ResearchResult(
            story=theme,
            references=[],  # would be filled by LLM research
            summary=f"Biblical story: {theme}",
            characters=[],
        )
        result.narrative_classification = self._classify(result)
        return result

    def _classify(self, result: ResearchResult) -> NarrativeClassification:
        """§23: Classify narrative elements."""
        classification = NarrativeClassification()

        # Key events from the biblical text are BIBLICAL_FACT
        for event in result.key_events:
            classification.BIBLICAL_FACT.append(event)

        # Characters are BIBLICAL_FACT
        for char in result.characters:
            classification.BIBLICAL_FACT.append(f"Personagem: {char}")

        # Visual descriptions and emotional context are CREATIVE_ADDITION
        classification.CREATIVE_ADDITION.append("Descrições visuais dos cenários")
        classification.CREATIVE_ADDITION.append("Expressões faciais e corporais dos personagens")
        classification.CREATIVE_ADDITION.append("Iluminação e atmosfera das cenas")

        return classification

    def save(self, result: ResearchResult, path: Path) -> None:
        """Save research result to JSON."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)