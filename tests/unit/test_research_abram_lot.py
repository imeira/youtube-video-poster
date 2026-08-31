"""Research contract for EP7 — Abrão e Ló se separam (Gênesis 13:1–18)."""

from __future__ import annotations

import json

import pytest

from src.agents.research import ResearchAgent


@pytest.mark.asyncio
async def test_abram_and_lot_separation_research_is_grounded_scoped_and_child_safe(tmp_path):
    research_dir = tmp_path / "research"
    result = await ResearchAgent().run(
        episode_id="EP7_ABRAM_AND_LOT_SEPARATE",
        theme="Abraão e Ló escolhem caminhos diferentes — Gênesis 13",
        research_dir=str(research_dir),
    )

    assert result.success is True
    assert result.data["references"] == [
        {"book": "Gênesis", "chapter": 13, "verses": "1-18"}
    ]
    assert result.data["chapter_context"] == {
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
    }

    facts = result.data["narrative_classification"]["BIBLICAL_FACT"]
    assert len(facts) == 10
    required_fact_fragments = {
        "Egito para o Neguebe",
        "gado, prata e ouro",
        "entre Betel e Ai",
        "invocou o nome do Senhor",
        "rebanhos, gado e tendas",
        "cananeus e ferezeus",
        "parentes próximos",
        "planície do Jordão",
        "em direção a Zoar",
        "partiu para o leste",
        "Abrão morou em Canaã",
        "perto de Sodoma",
        "norte, sul, leste e oeste",
        "para sempre",
        "pó da terra",
        "Manre, em Hebrom",
        "construiu ali um altar",
    }
    for fragment in required_fact_fragments:
        assert any(fragment in fact for fact in facts), fragment

    narrative_body = " ".join([result.data["summary"], *facts])
    biblical_narrative = narrative_body.casefold()
    assert result.data["story"].startswith("Abraão e Ló")
    assert "Abraão" not in narrative_body
    assert "Abrão" in narrative_body
    for excluded in (
        "destruição de sodoma",
        "gomorra",
        "isaque",
        "resgate de ló",
        "camelos",
        "estrelas",
        "sacrifício",
        "fogo",
        "violência",
        "agressão física",
        "armas",
        "figura divina",
        "corpo de Deus",
        "madeira",
        "tijolos",
        "metal",
    ):
        assert excluded not in biblical_narrative
    assert "pedras" not in biblical_narrative
    assert result.data["source_urls"] == [
        "https://www.bibliaonline.com.br/nvi/gn/13",
        "https://www.bibliaonline.com.br/acf/gn/13",
    ]

    constraints = result.data["visual_constraints"]
    assert set(constraints) == {
        "god_visual_representation",
        "name_continuity",
        "ep6_character_continuity",
        "herd_and_household_safety",
        "conflict_rule",
        "lot_choice_and_sodom_rule",
        "promise_and_altar_rule",
        "anachronism_and_scope_guard",
    }
    required_constraint_fragments = {
        "god_visual_representation": (
            "apenas por mudança ambiental abstrata, luz, vento ou som não localizado",
            "nunca por corpo, rosto, mãos, silhueta humana",
        ),
        "name_continuity": ("Abrão, Sarai e Ló", "Abraão fica restrito ao título editorial", "Gênesis 17"),
        "ep6_character_continuity": ("identidades canônicas", "EP6", "sem gerar substituições"),
        "herd_and_household_safety": ("apenas adultos", "não incluir camelos", "Não mostrar crianças"),
        "conflict_rule": ("não violenta", "sem agressão física", "Não inventar falas"),
        "lot_choice_and_sodom_rule": ("bem irrigada", "Não dramatizar Sodoma", "destruição futura"),
        "promise_and_altar_rule": (
            "pó da terra",
            "sem transformar poeira em pessoas, estrelas ou milagre visual",
            "sem identificar ou detalhar material",
            "não há sacrifício, sangue, fogo ou fumaça",
        ),
        "anachronism_and_scope_guard": (
            "Gênesis 14+",
            "mudança de nomes",
            "Isaque",
            "resgate de Ló",
            "destruição de Sodoma",
        ),
    }
    for constraint, fragments in required_constraint_fragments.items():
        for fragment in fragments:
            assert fragment in constraints[constraint], (constraint, fragment)

    saved = json.loads((research_dir / "sources.json").read_text(encoding="utf-8"))
    assert saved == result.data
