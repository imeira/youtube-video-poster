"""Research contract for EP5 — A Torre de Babel (Gênesis 11:1–9)."""

from __future__ import annotations

import json

import pytest

from src.agents.research import ResearchAgent


@pytest.mark.asyncio
async def test_babel_research_is_grounded_and_child_safe(tmp_path):
    research_dir = tmp_path / "research"
    result = await ResearchAgent().run(
        episode_id="EP5_TOWER_OF_BABEL",
        theme="A Torre de Babel — Gênesis 11",
        research_dir=str(research_dir),
    )

    assert result.success is True
    assert result.data["references"] == [
        {"book": "Gênesis", "chapter": 11, "verses": "1-9"}
    ]
    assert len(result.data["narrative_classification"]["BIBLICAL_FACT"]) == 7
    assert result.data["source_urls"] == [
        "https://www.bibliaonline.com.br/acf/gn/11/1-9",
        "https://www.bibliaonline.com.br/nvi/gn/11/1-9",
        "https://revista.abib.org.br/EB/article/view/262",
    ]
    constraints = result.data["visual_constraints"]
    assert "god_visual_representation" in constraints
    assert "unsupported_details" in constraints

    saved = json.loads((research_dir / "sources.json").read_text(encoding="utf-8"))
    assert saved == result.data
