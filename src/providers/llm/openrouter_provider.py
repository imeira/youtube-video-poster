"""OpenRouter LLM Provider — cheap-model-first text generation (§54).

Uses OpenRouter's OpenAI-compatible chat completions endpoint.
Model routing policy: cheapest model that reliably completes the task.
For metadata/titles (simple tasks), uses a flash/cheap model.

Reads OPENROUTER_API_KEY from ~/AppData/Local/hermes/.env.
Never blocks the pipeline: callers should fall back to templates on failure.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

from src.providers.base import LLMProvider

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


def _read_api_key() -> str | None:
    # Environment first
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key.strip()
    env_path = os.path.expanduser("~/AppData/Local/hermes/.env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("OPENROUTER_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return None


class OpenRouterLLMProvider(LLMProvider):
    """LLM text generation via OpenRouter (§54).

    Default model is a cheap flash model per the model-routing policy —
    metadata/title/tag generation are simple tasks and must not use premium models.
    """

    def __init__(self, model: str = "deepseek/deepseek-chat", api_key: str | None = None, timeout: int = 120):
        self.model = model
        self.api_key = api_key or _read_api_key()
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def estimate_cost(self, **params) -> float:
        # Flash models are ~$0.10-0.30 per 1M tokens; a metadata call is tiny.
        return 0.01

    async def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 800,
        temperature: float = 0.7,
        model: str = "",
    ) -> str:
        """Return the model's text completion, or raise on failure."""
        if not self.api_key:
            raise RuntimeError("OPENROUTER_API_KEY not available")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps({
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }).encode("utf-8")

        req = urllib.request.Request(
            _OPENROUTER_URL,
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/hermes-studio",
                "X-Title": "Hermes Animation Studio",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()

    async def execute(self, **params) -> str:
        return await self.complete(**params)
