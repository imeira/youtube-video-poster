from decimal import Decimal

import pytest

from src.hybrid.planner import Config, Hero, plan


def _heroes(count):
    return [
        Hero(
            scene_id=f"S{i:02}",
            action="ação bíblica",
            impact=1.0,
            movement=1.0,
            start=float(i),
        )
        for i in range(count)
    ]


def test_plan_releases_only_baseline_and_selected_heroes():
    result = plan(
        Config(),
        candidates=_heroes(4),
        images=Decimal("2.024"),
        recommended_duration_seconds=480,
        scene_count=39,
    )

    assert result["selected_work"] == {
        "baseline_images": 39,
        "hero_clips": 4,
        "hero_seconds": 20,
    }
    assert result["conditional_reserve"] == {
        "alternative_images": 39,
        "correction_images": 10,
        "thumbnail_images": 4,
        "hero_retries": 3,
        "hero_retry_seconds": 15,
    }
    assert result["phase_plan"]["baseline"]["gate"] == "IMAGE_QA"
    assert result["phase_plan"]["hero"]["requires"] == "APPROVED_IMAGE_QA"
    assert result["phase_plan"]["remediation"]["requires"] == "REJECTED_QA_BOUND_TO_RESULT_HASH"
    assert result["hero_cost"] == Decimal("1.040")
    assert result["delivery"]["hero_seconds"] == 20
    assert result["delivery"]["local_window_count"] == 35
    assert result["delivery"]["local_animation_seconds"] == 456


def test_plan_separates_immediate_probable_and_maximum_exposure():
    result = plan(
        Config(),
        candidates=_heroes(4),
        committed=Decimal("7.98"),
        images=Decimal("2.024"),
    )

    assert result["cost_envelope"] == {
        "immediate": Decimal("0.858"),
        "probable": Decimal("1.898"),
        "maximum": Decimal("5.924"),
    }
    assert result["api_cost"] == Decimal("1.898")
    assert result["conservative_api_cost"] == Decimal("5.924")
    assert result["immediate_projected"] == Decimal("8.838")
    assert result["probable_projected"] == Decimal("9.878")
    assert result["immediate_budget_action"].value == "PROCEED_WITH_WARNING"
    assert result["probable_budget_action"].value == "PROCEED_WITH_WARNING"


def test_plan_uses_real_scene_count_and_keeps_maximum_as_preflight_gate():
    result = plan(
        Config(),
        candidates=_heroes(1),
        images=Decimal("2.024"),
        committed=Decimal("7.98"),
        scene_count=4,
    )

    assert result["selected_work"]["baseline_images"] == 4
    assert result["phase_plan"]["baseline"]["selected"] == 4
    assert result["cost_envelope"]["immediate"] == Decimal("0.088")
    assert result["immediate_budget_action"].value == "PROCEED_WITH_WARNING"
    assert result["budget_action"].value == "WAITING_BUDGET_APPROVAL"

    with pytest.raises(ValueError, match="scene_count exceeds baseline capacity"):
        plan(Config(), scene_count=40)


def test_scene_count_is_nonnegative_even_without_duration():
    with pytest.raises(ValueError, match="scene_count must be a nonnegative integer"):
        plan(Config(), scene_count=-1)


def test_heroes_cannot_exceed_the_selected_baseline_count():
    result = plan(
        Config(),
        candidates=_heroes(12),
        images=Decimal("2.024"),
        scene_count=4,
    )

    assert result["selected_work"]["baseline_images"] == 4
    assert result["selected_work"]["hero_clips"] == 4
    assert result["delivery"] is None

