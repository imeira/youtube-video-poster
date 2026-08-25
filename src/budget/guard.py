"""Budget Guard — financial gatekeeper for all paid operations.

§4: No component may exceed hard_limit_usd without human authorization.
§61: Budget Guard is consulted before every paid operation.
§63: projected_spend = current_spend + estimated_next_job; proceed if <= hard_limit.
§66: Override must be logged (who, when, old_limit, new_limit, reason, episode).
§8: Silence is NOT approval.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from src.config.loader import BudgetConfig


class CostRating(str, Enum):
    """§67: Cost classification."""
    EXCELLENT = "EXCELLENT"  # $0-3
    GOOD = "GOOD"  # $3-4
    ACCEPTABLE = "ACCEPTABLE"  # $4-5
    ATTENTION = "ATTENTION"  # $5-6
    BLOCKED = "BLOCKED"  # >$6


class BudgetAction(str, Enum):
    """Actions the Budget Guard can return."""
    PROCEED = "PROCEED"
    PROCEED_WITH_WARNING = "PROCEED_WITH_WARNING"
    WAITING_BUDGET_APPROVAL = "WAITING_BUDGET_APPROVAL"
    BLOCKED = "BLOCKED"


@dataclass
class CostEstimate:
    """Estimate for a single job."""
    provider: str
    gpu: str = ""
    model: str = ""
    hourly_price: float = 0.0
    estimated_duration_seconds: float = 0.0
    estimated_cost: float = 0.0

    def __post_init__(self):
        if self.estimated_cost == 0.0 and self.hourly_price > 0:
            self.estimated_cost = (self.estimated_duration_seconds / 3600) * self.hourly_price


@dataclass
class CostRecord:
    """Actual cost record for a completed job (§60)."""
    job_id: str
    provider: str
    gpu: str
    model: str
    hourly_price: float
    job_duration_seconds: float
    estimated_cost: float
    actual_cost: float
    scene_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    status: str = "completed"


@dataclass
class BudgetOverride:
    """§66: Override logging when operator authorizes limit change."""
    who: str
    when: str
    old_limit: float
    new_limit: float
    reason: str
    episode: str
    approval_method: str = "telegram_inline_keyboard"


@dataclass
class BudgetCheckResult:
    """Result of a budget check (§63)."""
    action: BudgetAction
    current_spend: float
    estimated_cost: float
    projected_spend: float
    hard_limit: float
    remaining_budget: float
    rating: CostRating
    message: str = ""


class BudgetExceededError(Exception):
    """Raised when a job would exceed the hard limit and no approval was given."""


# ── Cost Ledger (§62) ─────────────────────────────────────────────────────────

class CostLedger:
    """Per-episode cost ledger persisted as costs.json (§62)."""

    def __init__(self, episode_id: str, budget: BudgetConfig):
        self.episode_id = episode_id
        self.currency = budget.currency
        self.budget = budget.hard_limit_usd
        self.target = budget.target_usd
        self.warning = budget.warning_usd
        self.hard_limit = budget.hard_limit_usd
        self.spent: float = 0.0
        self.runpod: float = 0.0
        self.other_services: float = 0.0
        self.jobs: list[dict[str, Any]] = []
        self.overrides: list[dict[str, Any]] = []

    @property
    def projected(self) -> float:
        """Total spent so far (for budget checks, add estimate separately)."""
        return self.spent

    @property
    def remaining(self) -> float:
        """Remaining budget before hard limit."""
        return self.hard_limit - self.spent

    def classify_cost(self, cost: float) -> CostRating:
        """§67: Classify a cost amount."""
        if cost < 3.00:
            return CostRating.EXCELLENT
        elif cost < 4.00:
            return CostRating.GOOD
        elif cost < 5.00:
            return CostRating.ACCEPTABLE
        elif cost <= 6.00:
            return CostRating.ATTENTION
        else:
            return CostRating.BLOCKED

    def check(self, estimate: CostEstimate) -> BudgetCheckResult:
        """§63: Check if a job can proceed.

        projected_spend = current_spend + estimated_next_job
        If projected_spend <= hard_limit: PROCEED
        If projected_spend > hard_limit: WAITING_BUDGET_APPROVAL
        """
        projected = self.spent + estimate.estimated_cost
        remaining = self.hard_limit - projected
        rating = self.classify_cost(projected)

        if projected > self.hard_limit:
            action = BudgetAction.WAITING_BUDGET_APPROVAL
            message = (
                f"BUDGET ALERT: projected ${projected:.2f} > limit ${self.hard_limit:.2f}. "
                f"Need human approval (§63-65)."
            )
        elif projected > self.warning:
            action = BudgetAction.PROCEED_WITH_WARNING
            message = f"Warning: projected ${projected:.2f} exceeds warning ${self.warning:.2f}"
        else:
            action = BudgetAction.PROCEED
            message = f"OK: projected ${projected:.2f} within limit ${self.hard_limit:.2f}"

        return BudgetCheckResult(
            action=action,
            current_spend=self.spent,
            estimated_cost=estimate.estimated_cost,
            projected_spend=projected,
            hard_limit=self.hard_limit,
            remaining_budget=remaining,
            rating=rating,
            message=message,
        )

    def record_job(self, record: CostRecord) -> None:
        """Record an actual cost after job completion."""
        self.jobs.append(asdict(record))
        self.spent += record.actual_cost
        if record.provider == "runpod":
            self.runpod += record.actual_cost
        else:
            self.other_services += record.actual_cost

    def apply_override(self, override: BudgetOverride) -> None:
        """§66: Apply and log a budget override."""
        self.overrides.append(asdict(override))
        self.hard_limit = override.new_limit

    def to_dict(self) -> dict[str, Any]:
        return {
            "currency": self.currency,
            "budget": self.budget,
            "target": self.target,
            "warning": self.warning,
            "hard_limit": self.hard_limit,
            "spent": round(self.spent, 4),
            "projected": round(self.projected, 4),
            "runpod": round(self.runpod, 4),
            "other_services": round(self.other_services, 4),
            "jobs": self.jobs,
            "overrides": self.overrides,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: Path, episode_id: str, budget: BudgetConfig) -> CostLedger:
        """Load from costs.json, or create new if not found."""
        if path.exists():
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            ledger = cls(episode_id, budget)
            ledger.spent = data.get("spent", 0.0)
            ledger.runpod = data.get("runpod", 0.0)
            ledger.other_services = data.get("other_services", 0.0)
            ledger.jobs = data.get("jobs", [])
            ledger.overrides = data.get("overrides", [])
            # Apply overrides to current hard_limit
            if ledger.overrides:
                ledger.hard_limit = ledger.overrides[-1].get("new_limit", budget.hard_limit_usd) if isinstance(ledger.overrides[-1], dict) else budget.hard_limit_usd
            return ledger
        return cls(episode_id, budget)


# ── Budget Guard ──────────────────────────────────────────────────────────────

class BudgetGuard:
    """Central budget gatekeeper (§61).

    No paid operation may proceed without consulting Budget Guard.
    """

    def __init__(self, ledger: CostLedger):
        self.ledger = ledger

    def approve_job(self, estimate: CostEstimate) -> BudgetCheckResult:
        """Check if a job can proceed. Returns action to take."""
        return self.ledger.check(estimate)

    def execute_job(
        self,
        estimate: CostEstimate,
        actual_cost: float | None = None,
        job_id: str = "",
        scene_id: str = "",
    ) -> CostRecord:
        """Record a completed job. Raises if budget would be exceeded."""
        result = self.ledger.check(estimate)
        if result.action == BudgetAction.WAITING_BUDGET_APPROVAL:
            raise BudgetExceededError(result.message)

        actual = actual_cost if actual_cost is not None else estimate.estimated_cost
        record = CostRecord(
            job_id=job_id,
            provider=estimate.provider,
            gpu=estimate.gpu,
            model=estimate.model,
            hourly_price=estimate.hourly_price,
            job_duration_seconds=estimate.estimated_duration_seconds,
            estimated_cost=estimate.estimated_cost,
            actual_cost=actual,
            scene_id=scene_id,
        )
        self.ledger.record_job(record)
        return record

    def request_override(
        self,
        who: str,
        new_limit: float,
        reason: str,
        episode_id: str,
    ) -> BudgetOverride:
        """§66: Request and log a budget override."""
        override = BudgetOverride(
            who=who,
            when=datetime.now(timezone.utc).isoformat(),
            old_limit=self.ledger.hard_limit,
            new_limit=new_limit,
            reason=reason,
            episode=episode_id,
        )
        self.ledger.apply_override(override)
        return override
