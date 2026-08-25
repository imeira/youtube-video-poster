"""Base agent class — shared interface for all specialized agents (§11-12).

Each agent has:
  responsibility, input, output, schema, tools, constraints,
  success criteria, failure modes (per AGENTS.md).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AgentResult:
    """Result from an agent execution."""
    success: bool
    data: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    next_state: str = ""  # state to transition to on success


class BaseAgent(ABC):
    """Base class for all specialized agents (§11).

    Agents are stateless — all state lives in EpisodeStateStore (§14).
    They read input, do work, write output, and return an AgentResult.
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    async def run(self, episode_id: str, **kwargs) -> AgentResult:
        """Execute the agent's work for this episode.

        Args:
            episode_id: The episode being processed.

        Returns:
            AgentResult with success/failure and data.
        """
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name}>"
