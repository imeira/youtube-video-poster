from decimal import Decimal

import pytest

from src.budget.guard import BudgetAction
from src.hybrid.planner import Config, Hero, plan


def _heroes():
    return [
        Hero(
            scene_id=f"S{i:02}",
            action="evento bíblico indispensável",
            impact=1.0,
            movement=1.0,
            start=float(i * 10),
        )
        for i in range(39)
    ]


def test_plan_exposes_dynamic_episode_delivery_with_child_safe_closing():
    result = plan(
        Config(),
        candidates=_heroes(),
        recommended_duration_seconds=480,
        essential_events=9,
        narration_words=1040,
        scene_count=39,
        closing_seconds=4,
    )

    delivery = result["delivery"]
    assert delivery == {
        "duration_seconds": 480,
        "essential_events": 9,
        "narration_words": 1040,
        "scene_count": 39,
        "hero_seconds": 60,
        "local_window_count": 27,
        "local_animation_seconds": 416,
        "closing_seconds": 4,
        "closing_required": True,
    }
    assert result["budget_action"] == BudgetAction.PROCEED
    assert result["conservative_budget_action"] == BudgetAction.PROCEED


def test_plan_does_not_impose_a_fixed_duration_and_rejects_invalid_closing():
    short = plan(Config(), recommended_duration_seconds=180, closing_seconds=3)
    long = plan(Config(), recommended_duration_seconds=900, closing_seconds=5)
    assert short["delivery"]["duration_seconds"] == 180
    assert long["delivery"]["duration_seconds"] == 900

    with pytest.raises(ValueError, match="3 to 5"):
        plan(Config(), recommended_duration_seconds=480, closing_seconds=2)

    with pytest.raises(ValueError, match="180 to 900"):
        plan(Config(), recommended_duration_seconds=179)


def test_plan_reserves_92_images_and_15_conditional_video_seconds():
    result = plan(Config(), candidates=_heroes(), images=Decimal("2.024"))
    assert sum(result["capacity"].values()) == 92
    assert result["hero_seconds"] == 60
    assert result["retry_capacity"] * 5 == 15
    assert result["conservative_api_cost"] == Decimal("5.924")
