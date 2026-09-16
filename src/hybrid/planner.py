"""Capacity planning only. The adaptive duration/roadmap remain authoritative."""

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import yaml

from src.budget.guard import BudgetGuard, CostEstimate, CostLedger
from src.config.loader import BudgetConfig


def money(value) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("cost must be finite and nonnegative")
    return result


@dataclass(frozen=True)
class Config:
    capacity: dict = field(
        default_factory=lambda: {
            "first": 39,
            "alternative": 39,
            "correction": 10,
            "thumbnail": 4,
        }
    )
    flux_endpoint: str = "fal-ai/flux-2/klein/9b/edit"
    flux_per_mp: Decimal = Decimal(".011")
    flare_endpoint: str = ""  # Must be supplied by a verified deployment.
    flare_prices: dict = field(default_factory=dict)
    video_endpoint: str = "seedance-v1-5-pro-i2v"
    video_per_second: Decimal = Decimal(".052")
    hero_slots: int = 12
    retry_slots: int = 3
    clip_seconds: int = 5
    resolution: str = "720p"
    concurrency: int = 3
    limit: Decimal = Decimal(6)

    def __post_init__(self):
        if self.capacity != {
            "first": 39,
            "alternative": 39,
            "correction": 10,
            "thumbnail": 4,
        }:
            raise ValueError("capacity must preserve approved reserve contract")
        if (self.hero_slots, self.retry_slots, self.clip_seconds, self.resolution) != (
            12,
            3,
            5,
            "720p",
        ):
            raise ValueError(
                "hero contract must be 12 + 3 conditional, 5 seconds, 720p"
            )
        if type(self.concurrency) is not int or self.concurrency < 1:
            raise ValueError("positive concurrency required")
        for value in (self.flux_per_mp, self.video_per_second, self.limit):
            money(value)

    @classmethod
    def load(cls, path=None):
        path = (
            Path(path) if path else Path(__file__).resolve().parents[2] / "config.yaml"
        )
        raw = yaml.safe_load(path.read_text(encoding="utf-8")).get("hybrid_fast", {})
        for key in ("flux_per_mp", "video_per_second", "limit"):
            if key in raw:
                raw[key] = money(raw[key])
        if "flare_prices" in raw:
            raw["flare_prices"] = {
                key: money(value) for key, value in raw["flare_prices"].items()
            }
        return cls(**raw)


@dataclass(frozen=True)
class Geometry:
    width: int
    height: int

    def __post_init__(self):
        if any(type(n) is not int or n <= 0 for n in (self.width, self.height)):
            raise ValueError("positive integer geometry required")

    @property
    def mp(self):
        return Decimal(self.width * self.height) / 1_000_000


def image_cost(config, inputs, output):
    if not inputs:
        raise ValueError("edit requires at least one input")
    one_mp = Decimal(1)
    billable = sum(max(g.mp, one_mp) for g in inputs) + max(output.mp, one_mp)
    return billable * config.flux_per_mp


def route_image(config, model="flux", quality=None):
    if model == "flux":
        return {"endpoint": config.flux_endpoint, "price_per_mp": config.flux_per_mp}
    if model != "gpt-image-2.5-flare" or quality not in config.flare_prices:
        raise ValueError("explicit model and verified quality price required")
    return {
        "endpoint": config.flare_endpoint,
        "quality": quality,
        "price": money(config.flare_prices[quality]),
    }


@dataclass(frozen=True)
class Hero:
    scene_id: str
    action: str
    impact: float
    movement: float
    start: float

    def __post_init__(self):
        if not self.scene_id or not self.action.strip():
            raise ValueError("semantic scene identity and action required")
        if not (0 <= self.impact <= 1 and 0 <= self.movement <= 1):
            raise ValueError("semantic scores must be in [0, 1]")
        money(self.start)


def plan(
    config,
    *,
    candidates=(),
    local_only=False,
    guard: BudgetGuard | None = None,
    committed=Decimal(0),
    images=Decimal(0),
    recommended_duration_seconds: int | None = None,
    essential_events: int | None = None,
    narration_words: int | None = None,
    scene_count: int | None = None,
    closing_seconds: int = 4,
):
    """Build a costed hybrid plan without fixing the episode duration.

    The caller supplies the duration determined by biblical/story analysis. When
    present it must stay within the approved 3--15 minute range and reserves a
    3--5 second final teaching/comfort beat instead of ending on an action cut.
    No provider call is made here.
    """
    committed, images = money(committed), money(images)
    if closing_seconds not in (3, 4, 5):
        raise ValueError("closing_seconds must be 3 to 5 seconds")
    for name, value in {
        "essential_events": essential_events,
        "narration_words": narration_words,
        "scene_count": scene_count,
    }.items():
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"{name} must be a nonnegative integer")
    if recommended_duration_seconds is not None:
        if (
            type(recommended_duration_seconds) is not int
            or not 180 <= recommended_duration_seconds <= 900
        ):
            raise ValueError("recommended_duration_seconds must be 180 to 900")
        if recommended_duration_seconds <= closing_seconds:
            raise ValueError("closing duration must fit inside episode duration")
    if scene_count is not None and scene_count > config.capacity["first"]:
        raise ValueError("scene_count exceeds baseline capacity")
    if guard is None:
        guard = BudgetGuard(
            CostLedger("hybrid-plan", BudgetConfig(hard_limit_usd=float(config.limit)))
        )
    if len({h.scene_id for h in candidates}) != len(candidates):
        raise ValueError("duplicate scene IDs")
    selected_baseline_count = scene_count if scene_count is not None else config.capacity["first"]
    heroes = sorted(
        (h for h in candidates if h.movement > 0),
        key=lambda h: (-(h.impact * h.movement), h.start, h.scene_id),
    )[: min(config.hero_slots, selected_baseline_count)]
    selected_hero_count = 0 if local_only else len(heroes)
    selected_hero_seconds = selected_hero_count * config.clip_seconds
    hero_cost = selected_hero_seconds * config.video_per_second
    reserved_hero_cost = (
        config.hero_slots * config.clip_seconds * config.video_per_second
    )
    retry_cost = config.retry_slots * config.clip_seconds * config.video_per_second
    total_image_capacity = sum(config.capacity.values())
    baseline_image_cost = (
        Decimal(0)
        if local_only
        else images * Decimal(selected_baseline_count) / Decimal(total_image_capacity)
    )
    api = Decimal(0) if local_only else baseline_image_cost + hero_cost
    immediate = committed + baseline_image_cost
    immediate_check = guard.approve_job(
        CostEstimate(provider="hybrid", estimated_cost=float(immediate))
    )
    probable = committed + api
    probable_check = guard.approve_job(
        CostEstimate(provider="hybrid", estimated_cost=float(probable))
    )
    conservative_api = (
        Decimal(0) if local_only else images + reserved_hero_cost + retry_cost
    )
    conservative = committed + conservative_api
    conservative_check = guard.approve_job(
        CostEstimate(provider="hybrid", estimated_cost=float(conservative))
    )
    delivery = None
    if recommended_duration_seconds is not None:
        active_hero_seconds = selected_hero_seconds
        effective_scene_count = scene_count if scene_count is not None else len(candidates)
        local_window_count = max(0, effective_scene_count - selected_hero_count)
        local_animation_seconds = (
            recommended_duration_seconds - active_hero_seconds - closing_seconds
        )
        if local_animation_seconds < 0:
            raise ValueError("duration is shorter than hero and closing contract")
        delivery = {
            "duration_seconds": recommended_duration_seconds,
            "essential_events": essential_events,
            "narration_words": narration_words,
            "scene_count": effective_scene_count,
            "hero_seconds": active_hero_seconds,
            "local_window_count": local_window_count,
            "local_animation_seconds": local_animation_seconds,
            "closing_seconds": closing_seconds,
            "closing_required": True,
        }

    return {
        "requests": [],
        "delivery": delivery,
        "capacity": dict(config.capacity),
        "selected_work": {
            "baseline_images": selected_baseline_count,
            "hero_clips": selected_hero_count,
            "hero_seconds": selected_hero_seconds,
        },
        "conditional_reserve": {
            "alternative_images": config.capacity["alternative"],
            "correction_images": config.capacity["correction"],
            "thumbnail_images": config.capacity["thumbnail"],
            "hero_retries": config.retry_slots,
            "hero_retry_seconds": config.retry_slots * config.clip_seconds,
        },
        "phase_plan": {
            "baseline": {"gate": "IMAGE_QA", "selected": selected_baseline_count},
            "hero": {
                "gate": "HERO_QA",
                "requires": "APPROVED_IMAGE_QA",
                "selected": selected_hero_count,
            },
            "remediation": {
                "gate": "IMAGE_QA",
                "requires": "REJECTED_QA_BOUND_TO_RESULT_HASH",
                "selected": 0,
            },
            "render": {"gate": "FINAL_QA", "requires": "ALL_ACTIVE_ASSETS_APPROVED"},
        },
        "hero_capacity": config.hero_slots,
        "retry_capacity": config.retry_slots,
        "hero_seconds": selected_hero_seconds,
        "reserved_hero_seconds": config.hero_slots * config.clip_seconds,
        "hero_cost": hero_cost,
        "reserved_hero_cost": reserved_hero_cost,
        "retry_cost": retry_cost,
        "api_cost": api,
        "cost_envelope": {
            "immediate": baseline_image_cost,
            "probable": api,
            "maximum": conservative_api,
        },
        "immediate_projected": immediate + money(guard.ledger.spent),
        "probable_projected": probable + money(guard.ledger.spent),
        "projected": probable + money(guard.ledger.spent),
        "conservative_projected": conservative + money(guard.ledger.spent),
        "immediate_budget_action": immediate_check.action,
        "probable_budget_action": probable_check.action,
        "conservative_budget_action": conservative_check.action,
        "conservative_api_cost": conservative_api,
        "budget_action": conservative_check.action,
        "heroes": [] if local_only else sorted(heroes, key=lambda h: h.start),
        "local_only": local_only,
        "execution_authorized": False,
        "price_status": "CONFIGURED_ESTIMATE_REQUIRES_FRESH_LIVE_EVIDENCE",
    }
