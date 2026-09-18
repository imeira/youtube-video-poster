from copy import deepcopy

import pytest

from src.hybrid.editorial import EditorialContractError
from src.hybrid.live_editorial import (
    BiblicalFactVerifier,
    DeterministicLiveScriptAuthor,
    PreSpendReconciler,
)


def _plan():
    return {
        "editorial_plan": {
            "estimated_word_count": 40,
            "estimated_duration_seconds": 6,
            "estimated_scene_count": 2,
            "estimated_costs_usd": {"total": "0.04"},
            "narration_words_per_minute": 120,
        },
        "budget_usd": "0.05",
        "successor_brief": {
            "feedback_identity": "f" * 64,
            "successor_identity": "s" * 64,
            "rejection_feedback": {"directives": ["usar frases mais simples", "nova composição"]},
            "thumbnail_constraints": {"requires_new_composition": True},
        },
    }


def test_deterministic_live_author_consumes_research_plan_and_successor_brief():
    research = {"claims": [{"source_ref": "Gênesis 15:5"}]}
    author = DeterministicLiveScriptAuthor()

    initial = author.author({**_plan(), "successor_brief": None}, research)
    successor = author.author(_plan(), research)

    assert successor["narration"] != initial["narration"]
    assert successor["script_identity"] != initial["script_identity"]
    assert successor["research_identity"]
    assert successor["feedback_identity"] == "f" * 64
    assert successor["thumbnail_concept"]["identity"] != initial["thumbnail_concept"]["identity"]
    assert successor["thumbnail_concept"]["composition"] != initial["thumbnail_concept"]["composition"]
    assert all("frases curtas" in segment["visual_action"] for segment in successor["segments"])


def test_reconciler_blocks_plan_deviation_before_spend():
    calls = []
    reconciler = PreSpendReconciler()
    plan = _plan()["editorial_plan"]
    actual = {"word_count": 30, "duration_seconds": 6, "scene_count": 2, "estimated_cost_usd": "0.04"}

    with pytest.raises(EditorialContractError, match="before spend"):
        reconciler.reserve(plan, actual, "0.05", lambda: calls.append("spent"))

    assert calls == []


def test_reconciler_allows_only_approved_tolerance_before_spend():
    calls = []
    plan = deepcopy(_plan()["editorial_plan"])
    plan["approved_tolerances"] = {"words": 1, "duration_seconds": 1, "scenes": 0, "cost_usd": "0.01"}

    receipt = PreSpendReconciler().reserve(
        plan,
        {"word_count": 41, "duration_seconds": 6.5, "scene_count": 2, "estimated_cost_usd": "0.045"},
        "0.05",
        lambda: calls.append("spent"),
    )

    assert calls == ["spent"]
    assert receipt["approved"] is True


def test_independent_biblical_verifier_blocks_invented_claim_with_allowed_ref():
    verifier = BiblicalFactVerifier()
    script = {
        "segments": [
            {"id": "S1", "narration": "Abrão olhou para as estrelas.", "source_refs": ["Gênesis 15:5"]},
            {"id": "S2", "narration": "Um dragão deu a Abraão um mapa mágico.", "source_refs": ["Gênesis 15:5"]},
        ]
    }

    report = verifier.verify(script)

    assert report["status"] == "BLOCKED"
    assert report["segments"][0]["classification"] in {"FACT", "PARAPHRASE"}
    assert report["segments"][1]["classification"] == "HUMAN_REVIEW"
    assert report["segments"][1]["block_reason"] == "invented_claim"


def test_independent_biblical_verifier_reports_all_required_editorial_classes():
    report = BiblicalFactVerifier().verify({"segments": [
        {"id": "F", "narration": "Deus mostrou as estrelas a Abrão.", "source_refs": ["Gênesis 15:5"]},
        {"id": "P", "narration": "Abrão olhou o céu cheio de estrelas.", "source_refs": ["Gênesis 15:5"]},
        {"id": "D", "narration": "O vento suave mexeu na tenda.", "source_refs": ["Gênesis 18:1"]},
        {"id": "O", "narration": "", "source_refs": ["Gênesis 18:1"]},
    ]})

    assert {item["classification"] for item in report["segments"]} >= {"FACT", "PARAPHRASE", "DRAMATIZATION", "OMISSION"}
