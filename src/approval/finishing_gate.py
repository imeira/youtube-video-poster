"""Finishing gates — thumbnail and video approval with regeneration loop.

§95-96: Human approval required for thumbnail and final video.
Rejection triggers a new generation with the user's feedback until approved.
Works over Telegram (gateway sessions) or the local terminal (CLI sessions).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum


class GateKind(str, Enum):
    THUMBNAIL = "thumbnail"
    VIDEO = "video"


@dataclass
class GateDecision:
    """Result of one approval round."""

    kind: GateKind
    approved: bool
    rounds: int
    feedback_history: list[str] = field(default_factory=list)
    cancelled: bool = False


class FinishingGate:
    """Runs approve/regenerate loops for thumbnail and final video.

    `request_approval` is injected so Telegram sessions use the Telegram gate
    while terminal sessions can prompt locally. The message must always carry
    the artifact reference plus the numbered feedback instructions.
    """

    MAX_ROUNDS = 5

    def __init__(
        self,
        request_approval: Callable[[str], Awaitable[str]] | None = None,
    ):
        self._request_approval = request_approval

    async def _ask(self, message: str) -> str:
        if self._request_approval is None:
            raise RuntimeError("No approval channel configured")
        return await self._request_approval(message)

    async def run(self, kind: GateKind, artifact_path: str, summary: str = "") -> GateDecision:
        """Loop until the human approves, cancels, or MAX_ROUNDS is reached.

        Each rejection round returns the user's feedback so the caller can
        regenerate the artifact before asking again.
        """
        feedback_history: list[str] = []
        decision = GateDecision(kind=kind, approved=False, rounds=0)

        for _ in range(self.MAX_ROUNDS):
            decision.rounds += 1
            message = self._format_message(kind, artifact_path, summary, decision.rounds)
            response = (await self._ask(message)).strip()
            response_upper = response.upper()

            if response_upper.startswith("APROVAR"):
                decision.approved = True
                return decision
            if response_upper.startswith("CANCELAR"):
                decision.cancelled = True
                return decision
            if not response_upper.startswith("REJEITAR"):
                # Unrecognized reply — re-ask with explicit options next round.
                feedback_history.append(response or "(sem resposta)")
                decision.feedback_history = feedback_history
                continue

            feedback = response[len("REJEITAR"):].strip(" :;-—")
            if not feedback:
                feedback = "ajustar sem detalhes específicos"
            feedback_history.append(feedback)
            decision.feedback_history = feedback_history
            # Caller regenerates using this feedback, then we ask again.

        return decision

    @staticmethod
    def _format_message(kind: GateKind, artifact_path: str, summary: str, round_no: int) -> str:
        title = "THUMBNAIL" if kind is GateKind.THUMBNAIL else "VÍDEO FINAL"
        lines = [
            f"⚠️ APROVAÇÃO NECESSÁRIA — {title} (rodada {round_no})",
            "",
            f"Arquivo: {artifact_path}",
        ]
        if summary:
            lines.append(f"Resumo: {summary}")
        lines += [
            "",
            "Responda exatamente:",
            "• APROVAR — publicar com este arquivo",
            "• REJEITAR: <ajustes desejados> — gerar novo com os apontamentos",
            "• CANCELAR — encerrar o episódio sem publicar",
            "",
            "Silêncio não é aprovação.",
        ]
        return "\n".join(lines)


def apply_feedback(base_prompt: str, feedback_history: list[str]) -> str:
    """Merge accumulated rejection feedback into a regeneration prompt."""
    additions = [f"Corrija: {item}" for item in feedback_history if item]
    return "\n".join([base_prompt, *additions]) if additions else base_prompt
