"""Research Agent — biblical source grounding (§22-23).

Responsibility: Research the theme, identify biblical passages, classify claims.
Input: theme string
Output: research/sources.json with references and narrative classification
Constraints: Must cite biblical passages (§22); classify facts vs inferences vs additions (§23)
"""

from __future__ import annotations

import json
from pathlib import Path

from src.agents.base import BaseAgent, AgentResult


class ResearchAgent(BaseAgent):
    """Researches biblical sources for a theme (§22).

    Uses LLM to identify passages and classify narrative elements.
    """

    # Known biblical stories and their references (pre-seeded for common stories)
    KNOWN_STORIES = {
        "criação do mundo": {
            "references": [{"book": "Gênesis", "chapter": 1, "verses": "1-31"}, {"book": "Gênesis", "chapter": 2, "verses": "1-3"}],
            "summary": "Deus criou o mundo em seis dias e descansou no sétimo.",
            "key_facts": [
                "Deus criou a luz no primeiro dia",
                "Deus separou as águas e criou o céu no segundo dia",
                "Deus criou a terra, os mares e as plantas no terceiro dia",
                "Deus criou o sol, a lua e as estrelas no quarto dia",
                "Deus criou os peixes e as aves no quinto dia",
                "Deus criou os animais terrestres e os seres humanos no sexto dia",
                "Deus descansou no sétimo dia",
            ],
        },
        "davi e golias": {
            "references": [{"book": "1 Samuel", "chapter": 17, "verses": "1-58"}],
            "summary": "O jovem pastor Davi enfrentou e derrotou o gigante filisteu Golias.",
            "key_facts": [
                "Davi era o mais novo de oito irmãos",
                "Davi era pastor de ovelhas",
                "Golias era um gigante filisteu com mais de 2,7 metros",
                "Golias desafiou o exército de Israel por 40 dias",
                "Davi recusou a armadura do rei Saul",
                "Davi usou uma funda e cinco pedras lisas",
                "Davi derrotou Golias com uma única pedra na testa",
            ],
        },
        "jonas e o grande peixe": {
            "references": [{"book": "Jonas", "chapter": 1, "verses": "1-17"}, {"book": "Jonas", "chapter": 2, "verses": "1-10"}],
            "summary": "Jonas fugiu de Deus, foi engolido por um grande peixe, e se arrependeu.",
            "key_facts": [
                "Deus mandou Jonas ir a Nínive",
                "Jonas fugiu em um navio para Társis",
                "Deus enviou uma grande tempestade",
                "Jonas foi jogado no mar e a tempestade parou",
                "Um grande peixe engoliu Jonas",
                "Jonas orou dentro do peixe por três dias",
                "O peixe vomitou Jonas na praia",
            ],
        },
        "daniel na cova dos leões": {
            "references": [{"book": "Daniel", "chapter": 6, "verses": "1-28"}],
            "summary": "Daniel foi jogado na cova dos leões por orar a Deus, mas Deus o protegeu.",
            "key_facts": [
                "Daniel era um dos principais governadores do reino",
                "Inimigos armaram uma lei proibindo orações a outros deuses",
                "Daniel continuou orando a Deus três vezes ao dia",
                "Daniel foi jogado na cova dos leões",
                "Deus enviou um anjo que fechou a boca dos leões",
                "Daniel saiu ileso da cova",
            ],
        },
        "noé e a arca": {
            "references": [{"book": "Gênesis", "chapter": 6, "verses": "9-22"}, {"book": "Gênesis", "chapter": 7, "verses": "1-24"}, {"book": "Gênesis", "chapter": 8, "verses": "1-19"}],
            "summary": "Noé construiu uma arca por ordem de Deus e salvou sua família e os animais do dilúvio.",
            "key_facts": [
                "Deus viu que a terra estava cheia de violência",
                "Deus mandou Noé construir uma arca",
                "Noé era justo e andava com Deus",
                "A arca tinha três andares",
                "Noé levou um casal de cada animal",
                "Choveu por 40 dias e 40 noites",
                "As águas cobriram as montanhas mais altas",
                "Noé soltou um corvo e uma pomba",
                "A arca pousou no monte Ararate",
            ],
        },
    }

    def __init__(self):
        super().__init__(name="Research")

    async def run(self, episode_id: str, theme: str = "", research_dir: str = "", **kwargs) -> AgentResult:
        """Research the biblical theme and save sources.json (§22)."""
        theme_lower = theme.lower().strip()

        # Match against known stories
        matched = None
        for key, data in self.KNOWN_STORIES.items():
            if key in theme_lower or theme_lower in key:
                matched = data
                break

        if matched:
            # Classify narrative elements (§23)
            classified = {
                "story": theme,
                "references": matched["references"],
                "summary": matched["summary"],
                "narrative_classification": {
                    "BIBLICAL_FACT": matched["key_facts"],
                    "NARRATIVE_INFERENCE": [
                        "O tom de voz narrativo é acolhedor e infantil",
                        "As cenas são compostas para atrair a atenção de crianças",
                    ],
                    "CREATIVE_ADDITION": [
                        "Descrições visuais específicas (cores, expressões faciais)",
                        "Detalhes ambientais para atmosfera",
                    ],
                },
            }

            # Save to research dir
            if research_dir:
                Path(research_dir).mkdir(parents=True, exist_ok=True)
                with open(Path(research_dir) / "sources.json", "w", encoding="utf-8") as f:
                    json.dump(classified, f, indent=2, ensure_ascii=False)

            return AgentResult(
                success=True,
                data=classified,
                next_state="PLANNING",
            )
        else:
            return AgentResult(
                success=False,
                error=f"No biblical story found matching theme: '{theme}'. "
                      f"Known stories: {list(self.KNOWN_STORIES.keys())}",
                next_state="FAILED",
            )
