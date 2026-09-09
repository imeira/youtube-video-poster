from decimal import Decimal

import pytest

from src.budget.guard import BudgetAction, BudgetGuard, CostLedger
from src.config.loader import BudgetConfig
from src.hybrid.planner import Config, Geometry, Hero, image_cost, plan, route_image


def test_capacity_is_not_generation_and_local_only_is_zero():
    config = Config.load()
    assert config.capacity == {
        "first": 39,
        "alternative": 39,
        "correction": 10,
        "thumbnail": 4,
    }
    result = plan(config)
    assert result["requests"] == []
    assert result["hero_capacity"] == 12
    assert result["retry_capacity"] == 3
    assert result["hero_seconds"] == 60
    assert result["hero_cost"] == Decimal("3.120")
    assert result["retry_cost"] == Decimal("0.780")
    assert plan(config, local_only=True)["api_cost"] == 0


def test_geometry_and_references_are_priced_separately():
    config = Config.load()
    assert config.flux_endpoint == "fal-ai/flux-2/klein/9b/edit"
    assert image_cost(config, [Geometry(1000, 1000)], Geometry(1000, 1000)) == Decimal(
        ".022"
    )
    assert image_cost(
        config, [Geometry(2000, 1000), Geometry(1000, 1000)], Geometry(2000, 1000)
    ) == Decimal(".055")
    assert image_cost(config, [Geometry(64, 64)], Geometry(320, 180)) == Decimal(".022")
    with pytest.raises(ValueError):
        Geometry(0, 10)
    assert route_image(config)["endpoint"] == config.flux_endpoint
    with pytest.raises(ValueError, match="quality"):
        route_image(config, "gpt-image-2.5-flare")
    config.flare_prices["high"] = Decimal(".20")
    assert route_image(config, "gpt-image-2.5-flare", "high")["price"] == Decimal(".20")


def test_budget_guard_blocks_full_plan_and_semantic_selection():
    guard = BudgetGuard(CostLedger("offline", BudgetConfig(hard_limit_usd=10)))
    candidates = [Hero(str(i), "action", i / 20, i / 20, i) for i in range(20)]
    result = plan(
        Config.load(),
        candidates=candidates,
        guard=guard,
        committed=Decimal("7.98"),
        images=Decimal("1.36"),
    )
    assert result["budget_action"] == BudgetAction.WAITING_BUDGET_APPROVAL
    assert result["projected"] == Decimal("12.46")
    assert {h.scene_id for h in result["heroes"]} == {str(i) for i in range(8, 20)}
    assert [h.start for h in result["heroes"]] == sorted(
        h.start for h in result["heroes"]
    )
    assert plan(Config.load(), local_only=True, guard=guard)["api_cost"] == 0


def test_invalid_plans_fail_closed():
    with pytest.raises(ValueError):
        plan(Config.load(), committed=Decimal("NaN"))
    with pytest.raises(ValueError):
        plan(Config.load(), images=Decimal(-1))


def test_planner_always_consults_guard_even_without_injection():
    result = plan(Config(), committed=Decimal("7.98"), images=Decimal("1.36"))
    assert result["budget_action"] == BudgetAction.WAITING_BUDGET_APPROVAL
    assert result["conservative_projected"] == Decimal("13.24")
    assert result["conservative_budget_action"] == BudgetAction.WAITING_BUDGET_APPROVAL


def test_plan_cli_is_local_only_by_explicit_flag_and_never_executes():
    import json
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "src.hybrid", "--local-only"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["local_only"] and data["api_cost"] == "0"
    assert data["requests"] == [] and not data["execution_authorized"]
