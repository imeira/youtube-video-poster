"""Tests for Budget Guard (§4, §61-67).

Tests:
- Cost classification (§67)
- Budget check formula (§63)
- Job recording
- Override logging (§66)
- Cost ledger persistence
- Budget exceeded raises error
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.budget.guard import (
    BudgetAction,
    BudgetGuard,
    BudgetExceededError,
    BudgetOverride,
    CostEstimate,
    CostLedger,
    CostRating,
    CostRecord,
)
from src.config.loader import BudgetConfig


@pytest.fixture
def budget_config():
    return BudgetConfig(
        currency="USD",
        target_usd=4.00,
        warning_usd=5.00,
        hard_limit_usd=6.00,
    )


@pytest.fixture
def ledger(budget_config):
    return CostLedger(episode_id="TEST001", budget=budget_config)


@pytest.fixture
def guard(ledger):
    return BudgetGuard(ledger)


class TestCostClassification:
    """§67: Cost classification."""

    def test_excellent(self, ledger):
        assert ledger.classify_cost(2.50) == CostRating.EXCELLENT

    def test_good(self, ledger):
        assert ledger.classify_cost(3.50) == CostRating.GOOD

    def test_acceptable(self, ledger):
        assert ledger.classify_cost(4.50) == CostRating.ACCEPTABLE

    def test_attention(self, ledger):
        assert ledger.classify_cost(5.50) == CostRating.ATTENTION

    def test_blocked(self, ledger):
        assert ledger.classify_cost(6.50) == CostRating.BLOCKED


class TestBudgetCheck:
    """§63: projected_spend = current_spend + estimated_next_job."""

    def test_proceed_when_under_limit(self, guard, ledger):
        """Should proceed when projected < limit."""
        estimate = CostEstimate(provider="runpod", estimated_cost=0.50)
        result = guard.approve_job(estimate)
        assert result.action == BudgetAction.PROCEED
        assert result.projected_spend == 0.50
        assert result.remaining_budget == 5.50

    def test_warning_when_above_warning_threshold(self, guard, ledger):
        """Should warn when projected > warning."""
        ledger.spent = 4.60
        estimate = CostEstimate(provider="runpod", estimated_cost=0.50)
        result = guard.approve_job(estimate)
        assert result.action == BudgetAction.PROCEED_WITH_WARNING
        assert result.projected_spend == 5.10

    def test_block_when_above_hard_limit(self, guard, ledger):
        """Should block when projected > hard_limit."""
        ledger.spent = 5.60
        estimate = CostEstimate(provider="runpod", estimated_cost=0.50)
        result = guard.approve_job(estimate)
        assert result.action == BudgetAction.WAITING_BUDGET_APPROVAL
        assert result.projected_spend == 6.10

    def test_proceed_at_exact_limit(self, guard, ledger):
        """Should proceed when projected == limit (boundary)."""
        estimate = CostEstimate(provider="runpod", estimated_cost=6.00)
        result = guard.approve_job(estimate)
        assert result.action == BudgetAction.PROCEED_WITH_WARNING


class TestJobRecording:
    """§60: Cost records."""

    def test_record_job_updates_spent(self, guard, ledger):
        """Recording a job should update total spent."""
        estimate = CostEstimate(
            provider="runpod",
            gpu="NVIDIA GeForce RTX 4090",
            model="wan-2.2-i2v",
            hourly_price=0.74,
            estimated_duration_seconds=120,
        )
        record = guard.execute_job(estimate, actual_cost=0.03, job_id="JOB001", scene_id="SC001")
        assert ledger.spent == 0.03
        assert ledger.runpod == 0.03
        assert len(ledger.jobs) == 1
        assert record.actual_cost == 0.03

    def test_cost_auto_calculated_from_duration(self, guard):
        """CostEstimate should auto-calculate from duration and hourly price."""
        estimate = CostEstimate(
            provider="runpod",
            hourly_price=0.74,
            estimated_duration_seconds=180,  # 3 min
        )
        expected = (180 / 3600) * 0.74
        assert abs(estimate.estimated_cost - expected) < 0.001


class TestBudgetExceeded:
    """§63: Budget exceeded should raise."""

    def test_execute_raises_when_budget_exceeded(self, guard, ledger):
        """execute_job should raise BudgetExceededError."""
        ledger.spent = 5.90
        estimate = CostEstimate(provider="runpod", estimated_cost=0.50)
        with pytest.raises(BudgetExceededError):
            guard.execute_job(estimate, actual_cost=0.50, job_id="JOB002")


class TestOverride:
    """§66: Override logging."""

    def test_override_changes_limit(self, guard, ledger):
        """Override should change the hard limit."""
        assert ledger.hard_limit == 6.00
        override = guard.request_override(
            who="141718934",
            new_limit=7.00,
            reason="Critical scene",
            episode_id="TEST001",
        )
        assert ledger.hard_limit == 7.00
        assert len(ledger.overrides) == 1
        assert override.old_limit == 6.00
        assert override.new_limit == 7.00

    def test_override_logged_with_all_fields(self, guard, ledger):
        """§66: Override must log who, when, old, new, reason, episode."""
        guard.request_override(
            who="user123",
            new_limit=8.00,
            reason="Test override",
            episode_id="EP001",
        )
        ov = ledger.overrides[0]
        assert ov["who"] == "user123"
        assert ov["old_limit"] == 6.00
        assert ov["new_limit"] == 8.00
        assert ov["reason"] == "Test override"
        assert ov["episode"] == "EP001"
        assert "when" in ov


class TestPersistence:
    """Cost ledger persistence."""

    def test_save_and_load(self, tmp_path: Path, ledger, budget_config):
        """Ledger should survive save/load."""
        ledger.spent = 0.35
        ledger.runpod = 0.32
        costs_path = tmp_path / "costs.json"
        ledger.save(costs_path)

        loaded = CostLedger.load(costs_path, "TEST001", budget_config)
        assert loaded.spent == 0.35
        assert loaded.runpod == 0.32
