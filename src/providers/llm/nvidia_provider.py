"""NVIDIA LLM Provider — uses NVIDIA integrate API for script generation.

Uses the NVIDIA API (integrate.api.nvidia.com) OpenAI-compatible endpoint.
Reads NVIDIA_API_KEY from ~/AppData/Local/hermes/.env.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)

_NVIDIA_URL = "https://integrate.api.nvidia.com/v1"


def _read_api_key() -> str | None:
    key = os.environ.get("NVIDIA_API_KEY")
    if key:
        return key.strip()
    env_path = os.path.expanduser("~/AppData/Local/hermes/.env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("NVIDIA_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return None


class NvidiaLLMProvider:
    """LLM text generation via NVIDIA integrate API.

    Default model: llama-3.1-nemotron-70b-instruct (good for Portuguese, free tier).
    """

    def __init__(self, model: str = "meta/llama-3.1-70b-instruct", api_key: str | None = None, timeout: int = 120):
        self.model = model
        self.api_key = api_key or _read_api_key()
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    async def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2000,
        temperature: float = 0.7,
        model: str = "",
    ) -> str:
        """Return the model's text completion, or raise on failure."""
        if not self.api_key:
            raise RuntimeError("NVIDIA_API_KEY not available")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = json.dumps({
            "model": model or self.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": 0.9,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{_NVIDIA_URL}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())
        return data["choices"][0]["message"]["content"].strip()
