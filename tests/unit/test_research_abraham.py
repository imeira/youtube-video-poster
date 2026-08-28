"""Research contract for EP6 — O chamado de Abraão (Gênesis 12:1–9)."""

from __future__ import annotations

import json

import pytest

from src.agents.research import ResearchAgent


@pytest.mark.asyncio
async def test_abraham_call_research_is_grounded_scoped_and_child_safe(tmp_path):
    research_dir = tmp_path / "research"
    result = await ResearchAgent().run(
        episode_id="EP6_CALL_OF_ABRAHAM",
        theme="O chamado de Abraão — Gênesis 12",
        research_dir=str(research_dir),
    )

    assert result.success is True
    assert result.data["references"] == [
        {"book": "Gênesis", "chapter": 12, "verses": "1-9"}
    ]
    assert result.data["chapter_context"] == {
        "read_scope": "Gênesis 12:1-20",
        "episode_scope": "Gênesis 12:1-9",
        "excluded_from_episode": "Gênesis 12:10-20 — Abrão no Egito",
    }
    assert len(result.data["narrative_classification"]["BIBLICAL_FACT"]) == 9
    assert result.data["source_urls"] == [
        "https://www.bibliaonline.com.br/acf/gn/12",
        "https://www.bibliaonline.com.br/nvi/gn/12",
    ]

    constraints = result.data["visual_constraints"]
    assert "god_visual_representation" in constraints
    assert "name_continuity" in constraints
    assert "character_continuity" in constraints
    assert "anachronism_guard" in constraints
    assert "unsupported_details" in constraints

    saved = json.loads((research_dir / "sources.json").read_text(encoding="utf-8"))
    assert saved == result.data
