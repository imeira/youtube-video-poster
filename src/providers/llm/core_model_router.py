"""Explicit Codex-first model routing for core studio tasks."""

from __future__ import annotations

from dataclasses import dataclass

from src.providers.llm.codex_provider import CodexLLMProvider


@dataclass(frozen=True)
class ModelRoute:
    provider: str
    model: str
    reasoning_effort: str

    def create_provider(self, timeout: int = 180) -> CodexLLMProvider:
        if self.provider != "openai-codex":
            raise ValueError(f"Unsupported core provider: {self.provider}")
        return CodexLLMProvider(
            model=self.model,
            reasoning_effort=self.reasoning_effort,
            timeout=timeout,
        )


class CoreModelRouter:
    """Route tasks by capability and risk instead of a single global model."""

    def __init__(self, routes: dict[str, ModelRoute]) -> None:
        self._routes = dict(routes)

    @classmethod
    def profile_d(cls) -> CoreModelRouter:
        sol_high = ModelRoute("openai-codex", "gpt-5.6-sol", "high")
        sol_xhigh = ModelRoute("openai-codex", "gpt-5.6-sol", "xhigh")
        terra_medium = ModelRoute("openai-codex", "gpt-5.6-terra", "medium")
        luna_low = ModelRoute("openai-codex", "gpt-5.6-luna", "low")
        return cls(
            {
                "research": sol_high,
                "planning": sol_high,
                "script": sol_high,
                "biblical_review": sol_xhigh,
                "child_review": terra_medium,
                "storyboard": sol_high,
                "character_bible": sol_high,
                "visual_strategy": sol_high,
                "coding": sol_high,
                "architecture": sol_xhigh,
                "metadata": luna_low,
            }
        )

    def route(self, task: str) -> ModelRoute:
        try:
            return self._routes[task]
        except KeyError:
            raise ValueError(f"Unknown model-routing task: {task}") from None

    def provider_for(self, task: str, timeout: int = 180) -> CodexLLMProvider:
        return self.route(task).create_provider(timeout=timeout)
