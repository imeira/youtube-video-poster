"""The compiled executor uses the canonical episode hard limit."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import yaml

from src.hybrid.planner import Config


def test_hybrid_limit_matches_canonical_budget_config():
    root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))

    assert Config.load(root / "config.yaml").limit == Decimal(str(raw["budget"]["episode"]["hard_limit_usd"]))
