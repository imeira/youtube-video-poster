"""Explicit Codex-first model routing for core studio tasks."""

from __future__ import annotations

from dataclasses import dataclass

from src.providers.llm.codex_provider import CodexLLMProvider
from src.providers.llm.usage import UsageStore


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

    def __init__(self, routes: dict[str, ModelRoute], usage_path=None) -> None:
        self._routes = dict(routes)
        self.usage_path = usage_path

    @classmethod
    def profile_d(cls, usage_path=None) -> CoreModelRouter:
        terra_medium = ModelRoute("openai-codex", "gpt-5.6-terra", "medium")
        luna_low = ModelRoute("openai-codex", "gpt-5.6-luna", "low")
        return cls(
            {
                "research": terra_medium,
                "planning": terra_medium,
                "script": terra_medium,
                "biblical_review": terra_medium,
                "child_review": terra_medium,
                "storyboard": terra_medium,
                "character_bible": terra_medium,
                "visual_strategy": terra_medium,
                "coding": terra_medium,
                "architecture": terra_medium,
                "metadata": luna_low,
            },
            usage_path=usage_path,
        )

    def route(self, task: str) -> ModelRoute:
        try:
            return self._routes[task]
        except KeyError:
            raise ValueError(f"Unknown model-routing task: {task}") from None

    def provider_for(self, task: str, timeout: int = 180) -> CodexLLMProvider:
        provider = self.route(task).create_provider(timeout=timeout)
        provider.task = task
        if self.usage_path is not None:
            provider.usage_store = UsageStore(self.usage_path)
        return provider

    def escalate(self, task: str) -> ModelRoute:
        """Explicit escalation only; routine routing never silently spends more."""
        self.route(task)  # retain the closed task vocabulary
        return ModelRoute("openai-codex", "gpt-5.6-sol", "high")
