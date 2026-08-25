"""Tests for EP2 research: Adão e Eva no Jardim do Éden (Gênesis 2–3)."""

from __future__ import annotations

import pytest

from src.agents.research import ResearchAgent


@pytest.mark.asyncio
async def test_edem_theme_is_researched(tmp_path):
    agent = ResearchAgent()
    result = await agent.run(
        episode_id="EP2_EDEN",
        theme="Adão e Eva no Jardim do Éden — Gênesis 2–3",
        research_dir=str(tmp_path / "research"),
    )
    assert result.success is True
    refs = result.data["references"]
    books = {r["book"] for r in refs}
    chapters = {r["chapter"] for r in refs}
    assert books == {"Gênesis"}
    assert chapters == {2, 3}


@pytest.mark.asyncio
async def test_edem_has_enough_biblical_facts_for_adaptive_duration(tmp_path):
    agent = ResearchAgent()
    result = await agent.run(
        episode_id="EP2_EDEN",
        theme="Adão e Eva no Jardim do Éden — Gênesis 2–3",
        research_dir=str(tmp_path / "research"),
    )
    facts = result.data["narrative_classification"]["BIBLICAL_FACT"]
    # 12 events -> medium tier, 6-8 min per DurationPlannerAgent.
    assert len(facts) >= 10


@pytest.mark.asyncio
async def test_edem_carries_child_safe_visual_constraints(tmp_path):
    agent = ResearchAgent()
    result = await agent.run(
        episode_id="EP2_EDEN",
        theme="Adão e Eva no Jardim do Éden — Gênesis 2–3",
        research_dir=str(tmp_path / "research"),
    )
    constraints = result.data["visual_constraints"]
    assert "adam_eve_rule" in constraints
    assert "serpent_rule" in constraints
    assert "god_visual_representation" in constraints


@pytest.mark.asyncio
async def test_edem_sources_saved_to_disk(tmp_path):
    agent = ResearchAgent()
    await agent.run(
        episode_id="EP2_EDEN",
        theme="Adão e Eva no Jardim do Éden — Gênesis 2–3",
        research_dir=str(tmp_path / "research"),
    )
    sources = tmp_path / "research" / "sources.json"
    assert sources.exists()
