"""Tests for thumbnail/video approval gates with regeneration loop."""

from __future__ import annotations

import pytest

from src.approval.finishing_gate import FinishingGate, GateKind, apply_feedback


def make_gate(responses: list[str]) -> tuple[FinishingGate, list[str]]:
    sent: list[str] = []

    async def ask(message: str) -> str:
        sent.append(message)
        return responses.pop(0)

    return FinishingGate(request_approval=ask), sent


class TestApproval:
    @pytest.mark.asyncio
    async def test_first_round_approval(self):
        gate, sent = make_gate(["APROVAR"])

        decision = await gate.run(GateKind.THUMBNAIL, "/tmp/thumb.png", "EP2")

        assert decision.approved is True
        assert decision.rounds == 1
        assert "THUMBNAIL" in sent[0]
        assert "/tmp/thumb.png" in sent[0]
        assert "APROVAR" in sent[0] and "REJEITAR" in sent[0]

    @pytest.mark.asyncio
    async def test_rejection_then_approval_collects_feedback(self):
        gate, _ = make_gate([
            "REJEITAR: título muito pequeno e cores escuras",
            "APROVAR",
        ])

        decision = await gate.run(GateKind.VIDEO, "/tmp/final.mp4")

        assert decision.approved is True
        assert decision.rounds == 2
        assert decision.feedback_history == ["título muito pequeno e cores escuras"]

    @pytest.mark.asyncio
    async def test_cancel_stops_loop(self):
        gate, _ = make_gate(["CANCELAR"])

        decision = await gate.run(GateKind.VIDEO, "/tmp/final.mp4")

        assert decision.approved is False
        assert decision.cancelled is True

    @pytest.mark.asyncio
    async def test_unrecognized_reply_keeps_asking(self):
        gate, _ = make_gate(["ok", "APROVAR"])

        decision = await gate.run(GateKind.THUMBNAIL, "/tmp/t.png")

        assert decision.approved is True
        assert decision.rounds == 2
        assert decision.feedback_history == ["ok"]

    @pytest.mark.asyncio
    async def test_max_rounds_bounded(self):
        gate, _ = make_gate([f"REJEITAR: ajuste {i}" for i in range(10)])

        decision = await gate.run(GateKind.THUMBNAIL, "/tmp/t.png")

        assert decision.rounds == FinishingGate.MAX_ROUNDS
        assert len(decision.feedback_history) == FinishingGate.MAX_ROUNDS

    @pytest.mark.asyncio
    async def test_no_channel_raises(self):
        gate = FinishingGate()
        with pytest.raises(RuntimeError):
            await gate.run(GateKind.VIDEO, "/tmp/final.mp4")


class TestFeedbackMerge:
    def test_apply_feedback_appends_corrections(self):
        prompt = apply_feedback("Headline: A criação do mundo", ["cores mais vivas", "letra maior"])
        assert prompt.startswith("Headline: A criação do mundo")
        assert "Corrija: cores mais vivas" in prompt
        assert "Corrija: letra maior" in prompt

    def test_empty_history_returns_base(self):
        assert apply_feedback("base", []) == "base"
