"""Script Agent — writes narration script for children 6-10 (§24).

§24: Target audience children 6-10. Seek: clareza, emoção, curiosidade,
    aventura, suspense apropriado, linguagem simples, ritmo, retenção,
    fidelidade bíblica, valor educativo, conclusão significativa.
§19: Never pad with text to increase duration. Never truncate to lose comprehension.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from src.agents.research_agent import ResearchResult
from src.config.loader import StudioConfig


@dataclass
class Script:
    """Narration script output."""
    episode_id: str
    theme: str
    narration_text: str
    word_count: int = 0
    estimated_duration_minutes: float = 0.0
    scenes_preview: list[dict[str, Any]] = field(default_factory=list)  # rough scene breakdown

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ScriptAgent:
    """Script Agent — creates narration for children (§24).

    Writes in Brazilian Portuguese, targeting children 6-10.
    ~150 words per minute of narration.
    """

    WORDS_PER_MINUTE = 150  # pt-BR narration pace

    def __init__(self, config: StudioConfig):
        self.config = config

    async def create_script(
        self,
        episode_id: str,
        theme: str,
        research: ResearchResult,
        target_duration_minutes: float = 4.0,
        llm_provider=None,
    ) -> Script:
        """Create a narration script from research.

        Args:
            episode_id: Episode identifier.
            theme: Story theme.
            research: Research result with biblical grounding.
            target_duration_minutes: Target narration duration.
            llm_provider: LLM provider for script generation.

        Returns:
            Script with narration text and estimated duration.
        """
        target_words = int(target_duration_minutes * self.WORDS_PER_MINUTE)

        # For the pilot, use a pre-written script for "Criação do Mundo"
        if "criação" in theme.lower() or "criacao" in theme.lower():
            narration = self._pilot_creation_script()
        elif "davi" in theme.lower():
            narration = self._david_goliath_script()
        else:
            # Generic template — would use LLM in production
            narration = self._generic_script(theme, research, target_words)

        word_count = len(narration.split())
        actual_duration = word_count / self.WORDS_PER_MINUTE

        return Script(
            episode_id=episode_id,
            theme=theme,
            narration_text=narration,
            word_count=word_count,
            estimated_duration_minutes=round(actual_duration, 1),
        )

    def _pilot_creation_script(self) -> str:
        """Pre-written pilot script for 'Criação do Mundo' (~600 words, ~4 min)."""
        return """No princípio, não havia nada. Não havia luz, não havia céu, não havia terra. Havia apenas escuridão e silêncio. Mas Deus estava lá.

Então, Deus falou: "Haja luz!" E a luz apareceu. Deus viu que a luz era boa, e a separou das trevas. Ele chamou a luz de dia, e as trevas de noite. E foi a primeira coisa que Deus criou.

No segundo dia, Deus criou o céu. Ele separou as águas que estavam embaixo das águas que estavam em cima. E o céu ficou lindo, azul e brilhante.

No terceiro dia, Deus juntou as águas para que a terra seca aparecesse. Ele chamou a terra de continente, e as águas de mares. E Deus fez crescer nas plantas, as árvores, as flores coloridas, e a grama verde. A terra ficou cheia de vida e beleza.

No quarto dia, Deus criou o sol, a lua e as estrelas. O sol para brilhar de dia, a lua para iluminar a noite, e as estrelas para enfeitar o céu. E todas elas dançavam no espaço imenso.

No quinto dia, Deus encheu os mares de peixes de todas as cores e tamanhos. Pequenos e grandes, nadando felizes. E criou os pássaros para voarem no céu, cantando músicas lindas.

No sexto dia, Deus criou todos os animais da terra. Os leões, os elefantes, os cachorros, as borboletas, e tantos outros. Cada um especial, cada um único. E então, Deus fez a coisa mais especial de todas: Ele criou o ser humano. À Sua imagem e semelhança. E deu aos humanos a missão de cuidar de toda a criação.

No sétimo dia, Deus olhou para tudo o que havia criado e viu que era muito bom. Tudo era perfeito e bonito. E Deus descansou, feliz com a Sua criação.

E foi assim que tudo começou. Deus criou o mundo com amor, com palavras, e com poder. E tudo o que Ele fez era bom. Muito bom."""

    def _david_goliath_script(self) -> str:
        """Script for 'Davi e Golias' (~600 words)."""
        return """Era uma vez, em uma terra distante chamada Israel, um jovem pastor chamado Davi. Davi era o mais novo de oito irmãos. Ele cuidava das ovelhas de seu pai, levando-as a pastos verdes e águas tranquilas.

Enquanto seus irmãos mais velhos foram para a guerra, Davi ficou no campo, tocando sua harpa e cantando para Deus. Ele era corajoso e forte, mas ninguém sabia disso ainda.

Um dia, um gigante chamado Golias apareceu. Ele era tão alto quanto três homens! Com sua armadura brilhante e sua lança enorme, ele desafiou o exército de Israel. "Mandem alguém para lutar comigo!" ele rugia. Mas ninguém tinha coragem. Todos tremiam de medo.

Davi ouviu o desafio. Ele olhou para o gigante e disse: "Eu vou lutar contra você, em nome do Senhor!" Seus irmãos disseram que ele era louco. O rei Saul disse que ele era muito jovem. Mas Davi não tinha medo.

O rei deu a Davi uma armadura, mas era muito pesada. Davi tirou a armadura e pegou apenas sua funda e cinco pedras lisas do riacho.

Golias riu quando viu Davi. "Você é só um menino!" disse o gigante. Mas Davi respondeu: "Você vem com espada e lança, mas eu vou com o poder de Deus."

Davi colocou uma pedra na funda, girou e soltou. A pedra voou pelo ar e acertou Golias bem na testa. O gigante caiu! Todo o exército de Israel gritou de alegria.

E foi assim que Davi, com a força de Deus e a coragem de um jovem pastor, derrotou o gigante. Davi mostrou que não importa o tamanho do problema: com Deus, tudo é possível."""

    def _generic_script(self, theme: str, research: ResearchResult, target_words: int) -> str:
        """Generic script template (would use LLM in production)."""
        events = research.key_events if research.key_events else ["A história começa", "O conflito cresce", "A solução vem"]
        chars = research.characters if research.characters else ["o personagem principal"]

        lines = [f"Era uma vez, {theme.lower()}.", ""]
        for event in events:
            lines.append(f"{event}. {chars[0]} estava lá.")
        lines.append("E foi assim que tudo terminou bem.")

        return "\n\n".join(lines)

    def save(self, script: Script, path: Path) -> None:
        """Save script to files."""
        path.parent.mkdir(parents=True, exist_ok=True)
        # Save narration text
        narration_path = path.parent / "narration.txt"
        with open(narration_path, "w", encoding="utf-8") as f:
            f.write(script.narration_text)
        # Save script metadata
        with open(path, "w", encoding="utf-8") as f:
            json.dump(script.to_dict(), f, indent=2, ensure_ascii=False)