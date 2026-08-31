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
        "chamado de abraão": {
            "references": [
                {"book": "Gênesis", "chapter": 12, "verses": "1-9"},
            ],
            "source_urls": [
                "https://www.bibliaonline.com.br/acf/gn/12",
                "https://www.bibliaonline.com.br/nvi/gn/12",
            ],
            "chapter_context": {
                "read_scope": "Gênesis 12:1-20",
                "episode_scope": "Gênesis 12:1-9",
                "excluded_from_episode": "Gênesis 12:10-20 — Abrão no Egito",
            },
            "summary": (
                "O Senhor chamou Abrão para deixar Harã e seguir até a terra que lhe mostraria, "
                "prometendo abençoá-lo e, por meio dele, alcançar todos os povos. Abrão obedeceu, "
                "partiu com Sarai e Ló, chegou a Canaã e construiu altares ao Senhor durante a jornada."
            ),
            "key_facts": [
                "O Senhor mandou Abrão deixar sua terra, seus parentes e a casa de seu pai para ir à terra que lhe mostraria",
                "O Senhor prometeu fazer de Abrão um grande povo, abençoá-lo, engrandecer seu nome e torná-lo uma bênção",
                "O Senhor prometeu abençoar quem abençoasse Abrão e que, por meio dele, todos os povos da terra seriam abençoados",
                "Abrão partiu como o Senhor havia ordenado; Ló foi com ele, e Abrão tinha setenta e cinco anos ao sair de Harã",
                "Abrão levou Sarai, Ló, os bens acumulados e as pessoas de sua casa; eles partiram e chegaram a Canaã",
                "Abrão atravessou a terra até Siquém, junto à árvore de Moré, quando os cananeus habitavam a região",
                "O Senhor prometeu dar aquela terra à descendência de Abrão, e Abrão construiu ali um altar ao Senhor",
                "Abrão armou suas tendas entre Betel e Ai, construiu outro altar e invocou o nome do Senhor",
                "Abrão continuou sua jornada em direção ao Neguebe",
            ],
            "visual_constraints": {
                "god_visual_representation": (
                    "A aparição e a fala do Senhor são representadas somente por luz, vento, som e "
                    "mudança ambiental abstrata; nunca por corpo, rosto, mãos ou silhueta humana"
                ),
                "name_continuity": (
                    "Em Gênesis 12, usar Abrão e Sarai na narração e nas fichas da época; os nomes "
                    "Abraão e Sara pertencem à mudança posterior de Gênesis 17. O título canônico "
                    "do episódio pode manter Abraão para reconhecimento do público"
                ),
                "character_continuity": (
                    "Criar e congelar fichas canônicas novas para Abrão, Sarai e Ló. Somente Abrão "
                    "tem idade informada no escopo: setenta e cinco anos; não inventar idades para Sarai ou Ló"
                ),
                "journey_continuity": (
                    "Manter as mesmas tendas, bagagens, roupas, paleta da caravana e direção geral da "
                    "viagem entre Harã, Canaã, Siquém, Betel/Ai e Neguebe"
                ),
                "anachronism_guard": (
                    "Sem mapas impressos, bússola, placas modernas, veículos, arquitetura clássica tardia, "
                    "armas medievais ou animais específicos não citados em Gênesis 12:1-9"
                ),
                "altar_rule": (
                    "Altares simples de pedras; o texto registra sua construção, mas não descreve sacrifício no escopo"
                ),
                "unsupported_details": (
                    "Não incluir fome, Egito, faraó ou pragas de Gênesis 12:10-20; não antecipar a mudança "
                    "de nomes, o nascimento de Isaque, estrelas como sinal da promessa ou os acontecimentos "
                    "de Gênesis 13 em diante; não incluir camelos; não inventar diálogos de Sarai ou Ló, "
                    "mapa sobrenatural, descendentes já presentes ou figura divina humana"
                ),
            },
        },
        "abraão e ló escolhem caminhos diferentes": {
            "references": [
                {"book": "Gênesis", "chapter": 13, "verses": "1-18"},
            ],
            "source_urls": [
                "https://www.bibliaonline.com.br/nvi/gn/13",
                "https://www.bibliaonline.com.br/acf/gn/13",
            ],
            "chapter_context": {
                "read_scope": "Gênesis 13:1-18",
                "episode_scope": "Gênesis 13:1-18",
                "previous_context_not_retold": (
                    "Gênesis 12:10-20 explica a menção inicial ao Egito, mas não será "
                    "reencenado ou narrado além do que Gênesis 13 registra."
                ),
                "excluded_from_episode": (
                    "Não antecipar Gênesis 14 em diante, a mudança de nomes de Gênesis 17, "
                    "o nascimento de Isaque, a destruição de Sodoma e Gomorra de Gênesis 19 "
                    "ou outros eventos posteriores."
                ),
            },
            "summary": (
                "Abrão, Sarai e Ló seguiram para o Neguebe. Como Abrão e Ló tinham muitos bens, "
                "rebanhos e tendas, a terra não comportava os dois grupos juntos e houve "
                "desavença entre seus pastores. Abrão propôs uma separação pacífica e deixou Ló "
                "escolher primeiro. Ló escolheu a planície do Jordão e partiu para o leste; Abrão "
                "permaneceu em Canaã. Depois, o Senhor prometeu a Abrão a terra para sempre e uma "
                "descendência comparada ao pó da terra. Abrão se estabeleceu perto de Manre, em Hebrom, e "
                "construiu um altar ao Senhor."
            ),
            "key_facts": [
                "Abrão subiu do Egito para o Neguebe com sua mulher, tudo o que possuía e Ló; Abrão tinha muitos bens, gado, prata e ouro",
                "Abrão voltou do Neguebe em direção a Betel, ao lugar entre Betel e Ai onde sua tenda e um altar já haviam estado; ali invocou o nome do Senhor",
                "Ló também possuía rebanhos, gado e tendas",
                "A terra não comportava os dois grupos juntos porque seus bens eram muitos; houve desavença entre os pastores de Abrão e os de Ló, quando cananeus e ferezeus habitavam a terra",
                "Abrão pediu que não houvesse contenda entre eles e seus pastores porque eram parentes próximos; disse que toda a terra estava diante de Ló e propôs direções opostas: se Ló fosse para a esquerda, Abrão iria para a direita, e vice-versa",
                "Ló viu toda a planície do Jordão bem irrigada em direção a Zoar; o texto compara sua aparência ao jardim do Senhor e à terra do Egito. Ló escolheu a planície e partiu para o leste; os dois se separaram",
                "Abrão morou em Canaã; Ló morou entre as cidades da planície e mudou seu acampamento para perto de Sodoma. O texto registra que os habitantes de Sodoma eram perversos e pecadores contra o Senhor, sem descrever atos específicos",
                "Depois que Ló se separou, o Senhor disse a Abrão que olhasse para norte, sul, leste e oeste e prometeu dar a terra para sempre a Abrão e à sua descendência",
                "O Senhor comparou a descendência de Abrão ao pó da terra e ordenou que ele percorresse o comprimento e a largura da terra, porque a daria a ele",
                "Abrão mudou seu acampamento para perto de Manre, em Hebrom, e construiu ali um altar dedicado ao Senhor",
            ],
            "visual_constraints": {
                "god_visual_representation": (
                    "Toda fala ou promessa do Senhor será representada apenas por mudança ambiental "
                    "abstrata, luz, vento ou som não localizado; nunca por corpo, rosto, mãos, "
                    "silhueta humana, anjo humanoide ou figura nas nuvens"
                ),
                "name_continuity": (
                    "Usar Abrão, Sarai e Ló na narração e nos ativos; Abraão fica restrito ao título "
                    "editorial. As mudanças de nome pertencem a Gênesis 17 e são excluídas"
                ),
                "ep6_character_continuity": (
                    "Reutilizar as identidades canônicas aprovadas de Abrão, Sarai e Ló do EP6 sem "
                    "gerar substituições de personagem. Sarai não recebe idade, fala ou papel adicional"
                ),
                "herd_and_household_safety": (
                    "Mostrar apenas adultos dignos como membros da casa e pastores; rebanhos e gado "
                    "são citados, mas não incluir camelos, arreios específicos ou contagens não bíblicas. "
                    "Não mostrar crianças como filhos de Abrão, Sarai ou Ló"
                ),
                "conflict_rule": (
                    "A desavença entre pastores deve ser compreensível e não violenta: sem agressão "
                    "física, armas, gritos, ameaças, perseguição ou disputa teatralizada. Não inventar "
                    "falas para os pastores"
                ),
                "lot_choice_and_sodom_rule": (
                    "A planície do Jordão pode ser mostrada como bem irrigada. As comparações são apenas "
                    "narradas, sem cutaway ou reencenação do jardim do Senhor ou do Egito. Não dramatizar "
                    "Sodoma, não mostrar atos perversos, destruição, fogo, sofrimento ou a destruição futura"
                ),
                "promise_and_altar_rule": (
                    "A comparação ao pó da terra pertence a Gênesis 13 e pode ser narrada sem "
                    "transformar poeira em pessoas, estrelas ou milagre visual. Mostrar o altar sem "
                    "identificar ou detalhar material; não há sacrifício, sangue, fogo ou fumaça "
                    "descritos no escopo"
                ),
                "anachronism_and_scope_guard": (
                    "Sem mapas impressos, bússola, placas, veículos, arquitetura moderna ou clássica "
                    "tardia, armas medievais, eventos de Gênesis 14+, mudança de nomes, Isaque, resgate "
                    "de Ló, destruição de Sodoma, nem detalhes de Gênesis 12:10-20 além da menção inicial"
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
                "chapter_context": matched.get("chapter_context", {}),
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
