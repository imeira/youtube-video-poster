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
    facts = result.data["narrative_classification"]["BIBLICAL_FACT"]
    assert len(facts) == 9
    required_fact_fragments = {
        "deixar sua terra",
        "grande povo",
        "todos os povos da terra",
        "setenta e cinco anos",
        "Sarai, Ló, os bens acumulados e as pessoas de sua casa",
        "Siquém",
        "cananeus",
        "descendência de Abrão",
        "Betel e Ai",
        "Neguebe",
    }
    for fragment in required_fact_fragments:
        assert any(fragment in fact for fact in facts), fragment

    altar_facts = [fact for fact in facts if "altar" in fact]
    assert len(altar_facts) == 2
    assert any("construiu ali um altar" in fact for fact in altar_facts)
    assert any("construiu outro altar" in fact for fact in altar_facts)
    assert any("árvore de Moré" in fact for fact in facts)
    assert all("carvalho de Moré" not in fact for fact in facts)

    biblical_narrative = " ".join([result.data["summary"], *facts]).casefold()
    for excluded in ("fome", "egito", "faraó", "isaque", "estrelas", "camelos", "sacrifício"):
        assert excluded not in biblical_narrative
    assert result.data["source_urls"] == [
        "https://www.bibliaonline.com.br/acf/gn/12",
        "https://www.bibliaonline.com.br/nvi/gn/12",
    ]

    constraints = result.data["visual_constraints"]
    assert set(constraints) == {
        "god_visual_representation",
        "name_continuity",
        "character_continuity",
        "journey_continuity",
        "anachronism_guard",
        "altar_rule",
        "unsupported_details",
    }
    required_constraint_fragments = {
        "god_visual_representation": (
            "somente por luz, vento, som",
            "nunca por corpo, rosto, mãos ou silhueta humana",
        ),
        "name_continuity": ("usar Abrão e Sarai", "Gênesis 17", "título canônico"),
        "character_continuity": ("Abrão, Sarai e Ló", "setenta e cinco anos", "não inventar idades"),
        "journey_continuity": ("Harã, Canaã, Siquém, Betel/Ai e Neguebe",),
        "anachronism_guard": ("mapas impressos", "veículos", "animais específicos não citados"),
        "altar_rule": ("Altares simples de pedras", "não descreve sacrifício"),
        "unsupported_details": (
            "fome, Egito, faraó",
            "nascimento de Isaque",
            "estrelas",
            "camelos",
            "figura divina humana",
        ),
    }
    for constraint, fragments in required_constraint_fragments.items():
        for fragment in fragments:
            assert fragment in constraints[constraint], (constraint, fragment)

    saved = json.loads((research_dir / "sources.json").read_text(encoding="utf-8"))
    assert saved == result.data
