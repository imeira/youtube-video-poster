from decimal import Decimal

import pytest

from src.hybrid.revision import RevisionHarness, read
from src.hybrid.revision_fixtures import TestDependencies as Fixtures


def test_hero_clips_are_disabled_by_default_and_opt_in_is_hash_bound(tmp_path):
    with RevisionHarness(tmp_path / "default") as harness:
        harness.create_plan(mode="TEST", request="Fresh EP8")
        plan = read(harness.path)["plan"]
        assert plan["heroes"] is False
        assert plan["hero_plan"]["enabled"] is False
        assert plan["hero_plan"]["selected_scene_ids"] == []

    with RevisionHarness(tmp_path / "enabled") as harness:
        status = harness.create_plan(
            mode="TEST",
            request="Fresh EP8",
            heroes=True,
            hero_endpoint="runpod-test-endpoint",
            hero_cost="0.25",
        )
        plan = read(harness.path)["plan"]
        assert status["plan_hash"] == harness.control["plan_hash"]
        assert plan["heroes"] is True
        assert plan["hero_plan"]["endpoint"] == "runpod-test-endpoint"
        assert Decimal(plan["hero_plan"]["unit_cost_usd"]) == Decimal("0.25")
        assert plan["hero_plan"]["authorization_required"] is True
        selected = set(plan["hero_plan"]["selected_event_ids"])
        allowed = {
            event["id"] for event in plan["editorial_plan"]["indispensable_events"]
            if event["importance"] in {"HIGH", "CRITICAL"}
        }
        assert selected and selected <= allowed
        with pytest.raises(ValueError, match="exact plan hash"):
            harness.approve_plan("wrong", "human")


def test_hero_opt_in_requires_explicit_price_endpoint_and_budget(tmp_path):
    with RevisionHarness(tmp_path) as harness:
        with pytest.raises(ValueError, match="endpoint"):
            harness.create_plan(mode="TEST", request="Fresh EP8", heroes=True)
    with RevisionHarness(tmp_path / "price") as harness:
        with pytest.raises(ValueError, match="price"):
            harness.create_plan(mode="TEST", request="Fresh EP8", heroes=True,
                                hero_endpoint="runpod-test", hero_cost="0")
    with RevisionHarness(tmp_path / "budget") as harness:
        with pytest.raises(ValueError, match="budget"):
            harness.create_plan(mode="TEST", request="Fresh EP8", heroes=True,
                                hero_endpoint="runpod-test", hero_cost="6")


def test_live_hero_opt_in_fails_closed_before_provider_preflight(tmp_path):
    deps = Fixtures(tmp_path / "providers")
    deps.mode = "LIVE"
    with RevisionHarness(tmp_path / "run", dependencies=deps) as harness:
        with pytest.raises(ValueError, match="LIVE hero preflight"):
            harness.get_dependencies({"mode": "LIVE", "heroes": True}, tmp_path / "run")
