"""Tests for DurationPlannerAgent — adaptive episode duration (§18-21)."""

from __future__ import annotations

import pytest

from src.agents.duration_planner import DurationPlannerAgent, DurationPlan


@pytest.fixture
def short_story_research():
    """A simple story with few events (~5 key facts) -> should be short tier."""
    return {
        "references": [{"book": "Lucas", "chapter": 15, "verses": "8-10"}],
        "narrative_classification": {
            "BIBLICAL_FACT": [
                "Uma mulher tinha dez moedas de prata",
                "Ela perdeu uma moeda",
                "Ela acendeu uma lâmpada e varreu a casa",
                "Ela procurou cuidadosamente até encontrar",
                "Ela chamou amigas para se alegrar",
            ],
        },
    }


@pytest.fixture
def medium_story_research():
    """Davi e Golias — medium complexity (~7-9 events)."""
    return {
        "references": [{"book": "1 Samuel", "chapter": 17, "verses": "1-58"}],
        "narrative_classification": {
            "BIBLICAL_FACT": [
                "Davi era o mais novo de oito irmãos",
                "Davi era pastor de ovelhas",
                "Golias era um gigante filisteu",
                "Golias desafiou o exército de Israel por 40 dias",
                "Davi recusou a armadura do rei Saul",
                "Davi usou uma funda e cinco pedras lisas",
                "Davi derrotou Golias com uma única pedra",
                "O exército filisteu fugiu",
            ],
        },
    }


@pytest.fixture
def long_story_research():
    """Noé e a arca — long, multi-event narrative (~13+ events)."""
    return {
        "references": [
            {"book": "Gênesis", "chapter": 6, "verses": "9-22"},
            {"book": "Gênesis", "chapter": 7, "verses": "1-24"},
            {"book": "Gênesis", "chapter": 8, "verses": "1-19"},
        ],
        "narrative_classification": {
            "BIBLICAL_FACT": [f"Evento {i}" for i in range(13)],
        },
    }


@pytest.fixture
def special_story_research():
    """Nascimento/vida/ressurreição de Jesus — special, 16+ events."""
    return {
        "references": [
            {"book": "Mateus", "chapter": 1, "verses": "1-25"},
            {"book": "Lucas", "chapter": 2, "verses": "1-20"},
            {"book": "João", "chapter": 20, "verses": "1-18"},
        ],
        "narrative_classification": {
            "BIBLICAL_FACT": [f"Evento {i}" for i in range(18)],
        },
    }


class TestDurationClassification:
    def test_short_story_classified_correctly(self, short_story_research):
        planner = DurationPlannerAgent()
        plan = planner.plan("A moeda perdida", short_story_research)
        assert plan.complexity_tier == "short"
        assert 180.0 <= plan.recommended_duration_s <= 300.0

    def test_medium_story_classified_correctly(self, medium_story_research):
        planner = DurationPlannerAgent()
        plan = planner.plan("Davi e Golias", medium_story_research)
        assert plan.complexity_tier == "medium"
        assert 360.0 <= plan.recommended_duration_s <= 480.0

    def test_long_story_classified_correctly(self, long_story_research):
        planner = DurationPlannerAgent()
        plan = planner.plan("Noé e a Arca", long_story_research)
        assert plan.complexity_tier == "long"
        assert 480.0 <= plan.recommended_duration_s <= 720.0

    def test_special_story_classified_correctly(self, special_story_research):
        planner = DurationPlannerAgent()
        plan = planner.plan("Vida de Jesus", special_story_research)
        assert plan.complexity_tier == "special"
        assert 720.0 <= plan.recommended_duration_s <= 900.0


class TestDurationPlanContent:
    def test_word_count_scales_with_duration(self, short_story_research, long_story_research):
        planner = DurationPlannerAgent()
        short_plan = planner.plan("Curta", short_story_research)
        long_plan = planner.plan("Longa", long_story_research)
        assert long_plan.word_count_target > short_plan.word_count_target

    def test_scene_count_positive(self, medium_story_research):
        planner = DurationPlannerAgent()
        plan = planner.plan("Davi e Golias", medium_story_research)
        assert plan.scene_count > 0
        assert plan.image_count == plan.scene_count

    def test_local_plus_runpod_equals_total_scenes(self, medium_story_research):
        planner = DurationPlannerAgent()
        plan = planner.plan("Davi e Golias", medium_story_research)
        assert plan.local_animated_scenes + plan.runpod_candidate_scenes == plan.scene_count

    def test_runpod_scenes_capped_at_5(self, special_story_research):
        planner = DurationPlannerAgent()
        plan = planner.plan("Vida de Jesus", special_story_research)
        assert plan.runpod_candidate_scenes <= 5

    def test_cost_min_is_zero_when_all_local_possible(self, short_story_research):
        planner = DurationPlannerAgent()
        plan = planner.plan("Curta", short_story_research)
        assert plan.cost_min_usd == 0.0

    def test_cost_max_greater_than_likely(self, medium_story_research):
        planner = DurationPlannerAgent()
        plan = planner.plan("Davi e Golias", medium_story_research)
        assert plan.cost_max_usd >= plan.cost_likely_usd

    def test_never_pads_or_cuts_content(self, short_story_research):
        """§19: word count should reflect actual content complexity, not be forced to a fixed number."""
        planner = DurationPlannerAgent()
        plan = planner.plan("Curta", short_story_research)
        # 5 events -> short tier, word count should be in the 390-750 range mentioned in spec
        assert 300 <= plan.word_count_target <= 800

    def test_references_preserved(self, medium_story_research):
        planner = DurationPlannerAgent()
        plan = planner.plan("Davi e Golias", medium_story_research)
        assert plan.references == medium_story_research["references"]

    def test_key_events_preserved(self, medium_story_research):
        planner = DurationPlannerAgent()
        plan = planner.plan("Davi e Golias", medium_story_research)
        assert len(plan.key_events) == 8


class TestDurationPlanReport:
    def test_to_dict_has_required_fields(self, medium_story_research):
        """§21: pre-production report must include duration, words, scenes, images, cost."""
        planner = DurationPlannerAgent()
        plan = planner.plan("Davi e Golias", medium_story_research)
        d = plan.to_dict()
        required = [
            "recommended_duration_s", "word_count_target", "scene_count",
            "image_count", "cost_min_usd", "cost_likely_usd", "cost_max_usd",
            "references", "justification",
        ]
        for field in required:
            assert field in d

    def test_format_report_includes_key_sections(self, medium_story_research):
        planner = DurationPlannerAgent()
        plan = planner.plan("Davi e Golias", medium_story_research)
        report = plan.format_report()
        assert "Duração recomendada" in report
        assert "Palavras estimadas" in report
        assert "CUSTO" in report
        assert "1 Samuel" in report

    def test_format_report_flags_over_budget(self, special_story_research):
        planner = DurationPlannerAgent()
        plan = planner.plan("Vida de Jesus", special_story_research)
        # Force an artificially high cost to test the over-budget flag
        plan.cost_max_usd = 999.0
        report = plan.format_report(budget_hard_limit=6.00)
        assert "EXCEDE ORÇAMENTO" in report


class TestBudgetCheck:
    def test_within_budget_no_alternatives(self, short_story_research):
        planner = DurationPlannerAgent(budget_hard_limit=6.00)
        plan = planner.plan("Curta", short_story_research)
        result = planner.check_budget(plan)
        assert result["within_budget"] is True
        assert result["alternatives"] == []

    def test_over_budget_provides_alternatives(self, short_story_research):
        planner = DurationPlannerAgent(budget_hard_limit=0.01)  # artificially tiny
        plan = planner.plan("Curta", short_story_research)
        plan.cost_max_usd = 5.0  # force over budget
        result = planner.check_budget(plan)
        assert result["within_budget"] is False
        assert len(result["alternatives"]) >= 3
        option_letters = {alt["option"] for alt in result["alternatives"]}
        assert "A" in option_letters  # reduce clips
        assert "B" in option_letters  # all local
        assert "D" in option_letters  # increase budget

    def test_split_episode_alternative_for_long_stories(self, long_story_research):
        planner = DurationPlannerAgent(budget_hard_limit=0.01)
        plan = planner.plan("Longa", long_story_research)
        plan.cost_max_usd = 5.0
        result = planner.check_budget(plan)
        option_letters = {alt["option"] for alt in result["alternatives"]}
        # Long stories (>480s) should offer the split-episode alternative (C)
        assert "C" in option_letters
