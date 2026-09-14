"""Deterministic one-hour production scheduling without provider authority.

This module replaces per-frame orchestration chains with one compact run manifest:
independent baseline and QA work are dispatched in bounded waves, while paid
submission, recovery and promotion remain individually hash-bound elsewhere.
"""

from dataclasses import dataclass
from decimal import Decimal
from math import ceil

ONE_HOUR_SECONDS = 60 * 60


def _positive_int(name: str, value: int, *, allow_zero: bool = False) -> int:
    if type(value) is not int or value < (0 if allow_zero else 1):
        qualifier = "nonnegative" if allow_zero else "positive"
        raise ValueError(f"{name} must be {qualifier} integer")
    return value


@dataclass(frozen=True)
class OneHourSLA:
    """Bounded work envelope for a production run.

    Timing values are conservative stage budgets. A schedule is planning only:
    it never grants provider, recovery, promotion or publication authority.
    """

    scene_count: int
    provider_concurrency: int = 8
    qa_concurrency: int | None = None
    baseline_seconds: int = 45
    qa_seconds: int = 30
    remediation_slots: int = 4
    remediation_seconds: int = 90
    render_seconds: int = 600
    final_qa_seconds: int = 180
    buffer_seconds: int = 120
    freeze_seconds: int = 15

    def __post_init__(self):
        _positive_int("scene_count", self.scene_count)
        _positive_int("provider_concurrency", self.provider_concurrency)
        qa_concurrency = self.provider_concurrency if self.qa_concurrency is None else self.qa_concurrency
        _positive_int("qa_concurrency", qa_concurrency)
        if qa_concurrency < min(self.scene_count, self.provider_concurrency):
            raise ValueError("qa_concurrency must keep pace with provider concurrency")
        if type(self.remediation_slots) is not int or self.remediation_slots < 1:
            raise ValueError("remediation slots must be positive")
        for name in (
            "baseline_seconds",
            "qa_seconds",
            "remediation_seconds",
            "render_seconds",
            "final_qa_seconds",
            "buffer_seconds",
            "freeze_seconds",
        ):
            _positive_int(name, getattr(self, name), allow_zero=name == "buffer_seconds")

    @property
    def effective_qa_concurrency(self) -> int:
        return self.provider_concurrency if self.qa_concurrency is None else self.qa_concurrency


def _waves(count: int, parallelism: int) -> int:
    return ceil(count / parallelism)


def schedule(
    sla: OneHourSLA,
    *,
    estimated_paid_cost: Decimal = Decimal(0),
    hard_limit: Decimal = Decimal(0),
) -> dict:
    """Build a fail-closed, compact work schedule for a maximum one-hour run."""
    estimated_paid_cost = Decimal(str(estimated_paid_cost))
    hard_limit = Decimal(str(hard_limit))
    if not estimated_paid_cost.is_finite() or estimated_paid_cost < 0:
        raise ValueError("estimated_paid_cost must be finite and nonnegative")
    if not hard_limit.is_finite() or hard_limit < 0:
        raise ValueError("hard_limit must be finite and nonnegative")

    baseline_waves = _waves(sla.scene_count, sla.provider_concurrency)
    qa_waves = _waves(sla.scene_count, sla.effective_qa_concurrency)
    remediation_waves = _waves(sla.remediation_slots, sla.provider_concurrency)
    predicted_seconds = (
        baseline_waves * sla.baseline_seconds
        + qa_waves * sla.qa_seconds
        + remediation_waves * sla.remediation_seconds
        + sla.render_seconds
        + sla.final_qa_seconds
        + sla.buffer_seconds
        + sla.freeze_seconds
    )
    if predicted_seconds > ONE_HOUR_SECONDS:
        raise ValueError("one-hour SLA cannot be met with the supplied work envelope")

    return {
        "eligible": True,
        "deadline_seconds": ONE_HOUR_SECONDS,
        "predicted_seconds": predicted_seconds,
        "baseline": {
            "items": sla.scene_count,
            "waves": baseline_waves,
            "parallelism": sla.provider_concurrency,
            "stage_seconds": baseline_waves * sla.baseline_seconds,
        },
        "qa": {
            "items": sla.scene_count,
            "waves": qa_waves,
            "parallelism": sla.effective_qa_concurrency,
            "stage_seconds": qa_waves * sla.qa_seconds,
        },
        "remediation": {
            "slots": sla.remediation_slots,
            "waves": remediation_waves,
            "parallelism": sla.provider_concurrency,
            "stage_seconds": remediation_waves * sla.remediation_seconds,
        },
        "render": {"stage_seconds": sla.render_seconds},
        "final_qa": {"stage_seconds": sla.final_qa_seconds},
        "buffer": {"stage_seconds": sla.buffer_seconds},
        "control_plane": {
            "run_manifest": 1,
            "per_frame_events": sla.scene_count,
            "per_frame_authorities": 0,
            "per_frame_scripts": 0,
        },
        "budget": {
            "estimated_paid_cost": estimated_paid_cost,
            "hard_limit": hard_limit,
            "within_limit": estimated_paid_cost <= hard_limit,
        },
        "execution_authorized": False,
        "provider_calls_authorized": 0,
    }
