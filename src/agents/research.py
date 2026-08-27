"""Research Agent — biblical source grounding (§22-23).

Responsibility: Research the theme, identify biblical passages, classify claims.
Input: theme string
Output: research/sources.json with references and narrative classification
Constraints: Must cite biblical passages (§22); classify facts vs inferences vs additions (§23)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

from src.agents.base import AgentResult, BaseAgent


class ResearchAgent(BaseAgent):
    """Researches biblical sources for a theme (§22).

    Uses LLM to identify passages and classify narrative elements.
    """

    # Known biblical stories and their references (pre-seeded for common stories)
    KNOWN_STORIES: ClassVar[dict[str, dict]] = {
        "criação do mundo": {
            "references": [{"book": "Gênesis", "chapter": 1, "verses": "1-31"}, {"book": "Gênesis", "chapter": 2, "verses": "1-25"}],
            "summary": "Deus cria os céus, a terra, a vida e o Jardim do Éden; depois forma Adão e Eva e os coloca no jardim.",
            "key_facts": [
                "No princípio, a terra estava sem forma, vazia e coberta de trevas e águas",
                "Deus criou a luz e separou o dia da noite",
                "Deus separou as águas e estabeleceu o céu",
                "Deus fez aparecer a terra seca, os mares, as plantas e as árvores",
                "Deus criou o sol, a lua e as estrelas para iluminar e marcar os tempos",
                "Deus criou os peixes e as aves e os abençoou",
                "Deus criou os animais terrestres",
                "Deus criou o ser humano, homem e mulher, à sua imagem",
                "Deus terminou sua obra e descansou no sétimo dia",
                "Deus formou Adão do pó e soprou nele o fôlego de vida",
                "Deus colocou Adão no Jardim do Éden para cuidar dele",
                "No jardim havia árvores agradáveis, a árvore da vida e a árvore do conhecimento do bem e do mal",
                "Deus disse a Adão que poderia comer das árvores, menos da árvore do conhecimento do bem e do mal",
                "Adão deu nomes aos animais e percebeu que estava sozinho",
                "Deus fez Eva e a apresentou a Adão",
                "Adão e Eva estavam nus e não sentiam vergonha",
            ],
            "visual_constraints": {
                "humans_allowed_after": "criação do ser humano em Gênesis 1:26-27; formação de Adão em Gênesis 2:7",
                "pre_human_rule": "Nenhum humano, rosto humano, criança ou silhueta humana antes de Gênesis 1:26",
                "adam_eve_rule": "Adão e Eva sem roupas; enquadramento infantil não sexualizado com folhas e distância cobrindo áreas íntimas; não desenhar roupas",
            },
        },
        "adão e eva no jardim do éden": {
            "references": [
                {"book": "Gênesis", "chapter": 2, "verses": "4-25"},
                {"book": "Gênesis", "chapter": 3, "verses": "1-24"},
            ],
            "summary": (
                "Deus forma Adão, cria o Jardim do Éden e depois Eva. Eles vivem em paz com a "
                "criação até desobedecerem à única orientação de Deus; consequências chegam com "
                "carinho (cuidado, promessa de redenção) e eles aprendem sobre escolhas, perdão "
                "e o amor constante de Deus."
            ),
            "key_facts": [
                "Deus formou Adão do pó da terra e soprou nele o fôlego de vida",
                "Deus plantou um jardim no Éden e colocou Adão lá para cuidar dele",
                "Deus orientou que Adão não comesse do fruto de uma árvore específica",
                "Adão deu nomes aos animais, mas nenhum era companhia como ele",
                "Deus fez Eva a partir de Adão e a apresentou a ele",
                "A serpente enganou Eva dizendo que nada de ruim aconteceria",
                "Eva comeu o fruto proibido e ofereceu a Adão, que também comeu",
                "Adão e Eva sentiram vergonha e esconderam-se de Deus",
                "Deus perguntou o que haviam feito e cada um explicou",
                "Deus anunciou consequências para a desobediência, mas também cuidado",
                "Deus fez roupas para Adão e Eva antes de enviá-los do jardim",
                "Anjos com espada flamejante guardaram o caminho da árvore da vida",
            ],
            "visual_constraints": {
                "adam_eve_rule": (
                    "Adão e Eva sem roupas apenas antes da queda; enquadramento infantil não "
                    "sexualizado com vegetação, cabelos longos, distância e objetos em primeiro "
                    "plano cobrindo áreas íntimas; após a queda usam as roupas feitas por Deus"
                ),
                "serpent_rule": (
                    "Serpente astuta mas não assustadora; estilo cartoon suave, sem horror"
                ),
                "god_visual_representation": (
                    "Presença divina por luz, vento e som suaves; nunca rosto ou corpo humano"
                ),
                "expulsion_tone": (
                    "Saída do jardim tratada com esperança e carinho; sem choro dramático ou medo intenso"
                ),
            },
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
        "torre de babel": {
            "references": [
                {"book": "Gênesis", "chapter": 11, "verses": "1-9"},
            ],
            "source_urls": [
                "https://www.bibliaonline.com.br/acf/gn/11/1-9",
                "https://www.bibliaonline.com.br/nvi/gn/11/1-9",
                "https://revista.abib.org.br/EB/article/view/262",
            ],
            "summary": (
                "Pessoas que falavam a mesma língua se estabeleceram em Sinar e decidiram "
                "construir uma cidade e uma torre para fazer um nome e evitar a dispersão. "
                "O Senhor confundiu a língua delas, espalhou-as pela terra e a construção parou."
            ),
            "key_facts": [
                "Toda a terra tinha uma só língua e as mesmas palavras",
                "As pessoas seguiram para o leste, encontraram uma planície em Sinar e se estabeleceram ali",
                "Elas fizeram tijolos queimados e usaram betume como argamassa",
                "Planejaram uma cidade e uma torre que alcançasse os céus para fazer um nome e não serem espalhadas",
                "O Senhor desceu para ver a cidade e a torre e observou a unidade daquele povo",
                "O Senhor confundiu a língua para que não se entendessem, e a construção da cidade parou",
                "O Senhor espalhou as pessoas por toda a terra, e o lugar recebeu o nome de Babel",
            ],
            "visual_constraints": {
                "god_visual_representation": (
                    "Presença divina somente por luz, vento, nuvens ou mudança ambiental; "
                    "nunca corpo, rosto, mãos ou silhueta humana"
                ),
                "tower_continuity": (
                    "Mesma cidade e mesma torre antiga de tijolos em construção em todas as cenas; "
                    "não mostrar altura exata, topo concluído, queda ou destruição"
                ),
                "language_confusion": (
                    "Mostrar desencontro de comunicação com gestos e expressões infantis seguras, "
                    "sem pânico, violência, humilhação ou caricatura de idiomas reais"
                ),
                "unsupported_details": (
                    "Não mostrar Ninrode, escravidão, chicotes, exército, ídolos, templo, raio, fogo, "
                    "colapso da torre, número específico de línguas ou altura específica"
                ),
            },
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
                "source_urls": matched.get("source_urls", []),
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
                "visual_constraints": matched.get("visual_constraints", {}),
            }

            # Save to research dir
            if research_dir:
                Path(research_dir).mkdir(parents=True, exist_ok=True)
                with open(Path(research_dir) / "sources.json", "w", encoding="utf-8") as f:  # noqa: ASYNC230 — small JSON write in stdlib-only agent
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
