"""Tests for mandatory pre-production budget approval gates."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from src.agents.director import DirectorAgent
from src.config.loader import BudgetConfig, get_config


class RecordingApprovalGate:
    """Approval gate test double that records outbound Telegram messages."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def send_message(self, text: str) -> int:
        self.messages.append(text)
        return len(self.messages)


@pytest.fixture
def over_budget_director(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    config = replace(
        get_config(),
        budget=BudgetConfig(
            target_usd=0.001,
            warning_usd=0.005,
            hard_limit_usd=0.01,
            require_approval_above_limit=True,
        ),
    )
    gate = RecordingApprovalGate()
    return DirectorAgent(config=config, approval_gate=gate), gate, tmp_path


@pytest.mark.asyncio
async def test_over_budget_plan_sends_telegram_alternatives(over_budget_director):
    director, gate, _ = over_budget_director

    result = await director.start_episode(
        theme="História de Davi e Golias — 1 Samuel 17",
        episode_id="OVER_BUDGET_NOTIFY",
    )

    assert result["plan"]["budget_check"]["within_budget"] is False
    assert result["plan"]["duration_plan"]["cost_min_usd"] > 0
    assert len(gate.messages) == 1
    assert "DECISÃO NECESSÁRIA" in gate.messages[0]
    assert "Reduzir clipes generativos" in gate.messages[0]
    assert "100% animação local" in gate.messages[0]
    assert "Aumentar orçamento" in gate.messages[0]


@pytest.mark.asyncio
async def test_plan_approval_cannot_bypass_over_budget_gate(over_budget_director):
    director, _, episodes_dir = over_budget_director
    await director.start_episode(
        theme="História de Davi e Golias — 1 Samuel 17",
        episode_id="OVER_BUDGET_BLOCK",
    )
    production_called = False

    async def fail_if_production_starts(*_args, **_kwargs):
        nonlocal production_called
        production_called = True
        raise AssertionError("production must not start before budget approval")

    director._run_production = fail_if_production_starts

    result = await director.continue_after_approval("OVER_BUDGET_BLOCK", "plan")

    assert production_called is False
    assert result["status"] == "waiting_budget_approval"
    state_text = await asyncio.to_thread(
        (episodes_dir / "OVER_BUDGET_BLOCK" / "state.json").read_text,
        encoding="utf-8",
    )
    state = json.loads(state_text)
    assert state["current_state"] == "WAITING_BUDGET_APPROVAL"
