"""Metadata Agent — YouTube title, description, tags, chapters, playlist (§92-93).

Responsibility: Generate publishable YouTube metadata.
Input: episode theme, research (biblical refs), scenes (for chapters), captions
Output: metadata/metadata.json
Constraints:
  §92: title, description, keywords, chapters, language, playlist, thumbnail, captions
  §93: auto-select from initial playlists
  §91/§97: no misleading clickbait; substantial editorial contribution
Model routing: uses cheap LLM (title/tags are simple tasks) with template fallback.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.agents.base import BaseAgent, AgentResult

logger = logging.getLogger(__name__)

# §93: Initial playlists
PLAYLISTS = [
    "Aventuras do Antigo Testamento",
    "Histórias de Jesus",
    "Heróis e Heroínas da Bíblia",
    "Milagres da Bíblia",
    "Lições de Fé e Coragem",
]

# Keyword → playlist routing for auto-selection (§93)
_OLD_TESTAMENT = {"gênesis", "êxodo", "1 samuel", "daniel", "jonas", "noé", "davi", "criação"}
_JESUS = {"jesus", "cristo", "evangelho", "mateus", "marcos", "lucas", "joão"}
_MIRACLE = {"milagre", "peixe", "leões", "tempestade", "cova", "dilúvio"}
_HEROES = {"davi", "golias", "daniel", "jonas", "noé", "moisés", "josé"}


class MetadataAgent(BaseAgent):
    """Generates YouTube metadata (§92-93). LLM-assisted with template fallback."""

    def __init__(self, llm_provider=None):
        super().__init__(name="YouTubeMetadata")
        self._llm = llm_provider

    async def run(
        self,
        episode_id: str,
        theme: str = "",
        research_data: dict | None = None,
        scenes: list[dict] | None = None,
        language: str = "pt-BR",
        metadata_dir: str = "",
        captions_files: dict | None = None,
        thumbnail_path: str = "",
        **kwargs,
    ) -> AgentResult:
        """Generate YouTube metadata (§92)."""
        research_data = research_data or {}
        scenes = scenes or []

        title = await self._make_title(theme, research_data)
        description = await self._make_description(theme, research_data)
        tags = self._make_tags(theme, research_data)
        chapters = self._make_chapters(scenes)
        playlist = self._select_playlist(theme, research_data)

        metadata = {
            "title": title,
            "description": description,
            "tags": tags,
            "keywords": tags,  # §92 keywords alias
            "chapters": chapters,
            "language": language,
            "playlist": playlist,
            "category": "Education",
            "made_for_kids": True,  # §24 audience 6-10
            "thumbnail": thumbnail_path,
            "captions": captions_files or {},
            "references": research_data.get("references", []),
        }

        md_dir = Path(metadata_dir) if metadata_dir else None
        out_path = ""
        if md_dir:
            md_dir.mkdir(parents=True, exist_ok=True)
            out_path = str(md_dir / "metadata.json")
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(metadata, f, indent=2, ensure_ascii=False)

        return AgentResult(
            success=True,
            data={"metadata": metadata, "metadata_path": out_path},
            next_state="",
        )

    async def _make_title(self, theme: str, research: dict) -> str:
        """Generate a compelling, non-clickbait title (§91/§97)."""
        if self._llm and getattr(self._llm, "available", lambda: False)():
            try:
                prompt = (
                    f"Crie um título de vídeo do YouTube para crianças de 6 a 10 anos "
                    f"sobre a história bíblica: '{theme}'. "
                    f"Máximo 60 caracteres, cativante mas SEM clickbait enganoso, em português do Brasil. "
                    f"Responda APENAS com o título, sem aspas."
                )
                title = await self._llm.complete(
                    prompt=prompt,
                    system="Você é um especialista em conteúdo infantil bíblico para YouTube.",
                    max_tokens=40,
                    temperature=0.8,
                )
                title = title.strip().strip('"').split("\n")[0]
                if 5 <= len(title) <= 80:
                    return title
            except Exception as e:
                logger.warning(f"LLM title generation failed, using template: {e}")

        # Template fallback
        story = research.get("story", theme).strip()
        return f"{story} | Histórias da Bíblia para Crianças 📖✨"

    async def _make_description(self, theme: str, research: dict) -> str:
        """Generate the video description with biblical references (§22, §92)."""
        summary = research.get("summary", "")
        refs = research.get("references", [])
        ref_lines = []
        for r in refs:
            book = r.get("book", "")
            ch = r.get("chapter", "")
            vs = r.get("verses", "")
            ref_lines.append(f"📖 {book} {ch}:{vs}".strip())

        llm_intro = ""
        if self._llm and getattr(self._llm, "available", lambda: False)():
            try:
                prompt = (
                    f"Escreva um parágrafo curto (2-3 frases) de descrição de vídeo do YouTube, "
                    f"para crianças de 6-10 anos, sobre a história bíblica '{theme}'. "
                    f"Resumo: {summary}. Em português do Brasil, tom acolhedor. "
                    f"Responda apenas com o parágrafo."
                )
                llm_intro = (await self._llm.complete(prompt=prompt, max_tokens=200, temperature=0.7)).strip()
            except Exception as e:
                logger.warning(f"LLM description failed, using template: {e}")

        intro = llm_intro or (
            f"Venha descobrir a emocionante história de {research.get('story', theme)}! "
            f"{summary} Uma aventura cheia de fé, coragem e amor de Deus, "
            f"perfeita para toda a família. 🌟"
        )

        parts = [
            intro,
            "",
            "📚 Referências bíblicas:",
            *ref_lines,
            "",
            "🔔 Inscreva-se no canal para mais histórias da Bíblia animadas!",
            "👍 Deixe seu like e compartilhe com a família.",
            "",
            "#HistóriasBíblicas #BíbliaParaCrianças #DesenhoBíblico",
        ]
        return "\n".join(parts)

    def _make_tags(self, theme: str, research: dict) -> list[str]:
        """Generate keyword tags (§92)."""
        base = [
            "bíblia para crianças", "histórias bíblicas", "desenho bíblico",
            "bíblia infantil", "histórias da bíblia", "escola dominical",
            "jesus para crianças", "deus", "fé", "valores cristãos",
        ]
        story = research.get("story", theme).lower()
        # Add story-specific tags
        for word in story.split():
            if len(word) > 3 and word not in ("história", "mundo", "grande"):
                base.append(word)
        # Add character tags
        chars = set()
        for r in research.get("narrative_classification", {}).get("BIBLICAL_FACT", []):
            for name in ["Davi", "Golias", "Jonas", "Daniel", "Noé", "Deus", "Jesus"]:
                if name.lower() in r.lower():
                    chars.add(name.lower())
        base.extend(chars)
        # Dedup preserving order, cap at 30 (YouTube ~500 char limit)
        seen = set()
        tags = []
        for t in base:
            if t not in seen:
                seen.add(t)
                tags.append(t)
        return tags[:30]

    def _make_chapters(self, scenes: list[dict]) -> list[dict]:
        """Generate YouTube chapters from scene timestamps (§92).

        YouTube requires the first chapter at 00:00 and >= 3 chapters, each >= 10s.
        Groups scenes into ~3-6 chapters.
        """
        if not scenes:
            return []

        total = scenes[-1].get("end", 0)
        if total < 30:
            # Too short for meaningful chapters
            return [{"start": 0.0, "title": "Início"}]

        # Group scenes into up to 5 chapters, each >= 10s
        n_chapters = min(5, max(3, len(scenes) // 3))
        per = max(1, len(scenes) // n_chapters)
        chapters = []
        for i in range(0, len(scenes), per):
            group = scenes[i:i + per]
            start = group[0].get("start", 0.0)
            # Chapter title from first scene's narration (short)
            narration = group[0].get("narration", "").strip()
            title = narration[:40].rstrip(".,;") or f"Parte {len(chapters) + 1}"
            # Ensure >= 10s gap from previous
            if chapters and start - chapters[-1]["start"] < 10:
                continue
            chapters.append({"start": round(start, 1), "title": title})

        # First chapter must be at 0
        if chapters:
            chapters[0]["start"] = 0.0
        return chapters

    def _select_playlist(self, theme: str, research: dict) -> str:
        """Auto-select the best playlist (§93)."""
        text = (theme + " " + research.get("summary", "")).lower()
        refs_text = " ".join(
            r.get("book", "").lower() for r in research.get("references", [])
        )
        full = text + " " + refs_text

        scores = {p: 0 for p in PLAYLISTS}
        if any(k in full for k in _JESUS):
            scores["Histórias de Jesus"] += 3
        if any(k in full for k in _OLD_TESTAMENT):
            scores["Aventuras do Antigo Testamento"] += 2
        if any(k in full for k in _MIRACLE):
            scores["Milagres da Bíblia"] += 2
        if any(k in full for k in _HEROES):
            scores["Heróis e Heroínas da Bíblia"] += 2

        best = max(scores, key=scores.get)
        # If nothing matched, default to faith/courage
        if scores[best] == 0:
            return "Lições de Fé e Coragem"
        return best
