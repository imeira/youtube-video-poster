"""Research contract for EP9 — O nascimento de Isaque (Gênesis 21:1–8)."""

from __future__ import annotations

import json

import pytest

from src.agents.research import ResearchAgent


@pytest.mark.asyncio
async def test_isaac_birth_research_is_grounded_scoped_and_child_safe(tmp_path):
    research_dir = tmp_path / "research"
    result = await ResearchAgent().run(
        episode_id="EP9_BIRTH_OF_ISAAC",
        theme="O nascimento de Isaque — Gênesis 21",
        research_dir=str(research_dir),
    )

    assert result.success is True
    assert result.data["references"] == [
        {"book": "Gênesis", "chapter": 21, "verses": "1-8"}
    ]
    assert result.data["chapter_context"] == {
        "read_scope": "Gênesis 21:1-21",
        "episode_scope": "Gênesis 21:1-8",
        "excluded_from_episode": "Gênesis 21:9-21 — Hagar e Ismael deixam o acampamento",
    }

    facts = result.data["narrative_classification"]["BIBLICAL_FACT"]
    assert len(facts) == 8
    required_fragments = (
        "cumpriu o que havia prometido a Sara",
        "tempo determinado",
        "nome de Isaque",
        "oito dias",
        "cem anos",
        "riso e alegria",
        "amamentaria um filho",
        "grande banquete",
    )
    for fragment in required_fragments:
        assert any(fragment in fact for fact in facts), fragment

    narrative = " ".join([result.data["summary"], *facts]).casefold()
    for excluded in ("hagar", "ismael", "expuls", "deserto", "poço", "flecheiro"):
        assert excluded not in narrative

    assert result.data["source_urls"] == [
        "https://www.bibliaonline.com.br/acf/gn/21/1-8",
        "https://www.bibliaonline.com.br/nvi/gn/21/1-8",
    ]
    assert result.data["source_authority"]["language"] == "pt-BR"
    assert "Gênesis 21:9-21" in " ".join(result.data["accuracy_report"]["omissions"])

    constraints = result.data["visual_constraints"]
    assert set(constraints) == {
        "god_visual_representation",
        "parent_identity_continuity",
        "isaac_identity_continuity",
        "birth_safety",
        "covenant_sign_safety",
        "anachronism_guard",
        "unsupported_details",
    }
    assert "nunca rosto, corpo, mãos ou silhueta humana" in constraints["god_visual_representation"]
    assert "aecb6ef14f51a65f5f1d6c16118630b9643f20d26a42159618caac2cf92fdf2f" in constraints["parent_identity_continuity"]
    assert "406008921ec9978e6fec5ab7196e9c4bfb0d1fac5588e82fc31bcf58ffb5be61" in constraints["parent_identity_continuity"]
    assert "sem parto explícito" in constraints["birth_safety"]
    assert "não representar circuncisão" in constraints["covenant_sign_safety"]
    assert "Hagar, Ismael" in constraints["unsupported_details"]

    saved = json.loads((research_dir / "sources.json").read_text(encoding="utf-8"))
    assert saved == result.data
