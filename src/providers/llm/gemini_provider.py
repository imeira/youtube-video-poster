"""Gemini LLM Provider — uses Google AI Studio API for text generation.

Uses the Gemini API (generativelanguage.googleapis.com) OpenAI-compatible endpoint.
Reads GEMINI_API_KEY or GOOGLE_API_KEY from ~/AppData/Local/hermes/.env.

Models:
  - gemini-3.1-flash-lite: ultra-cheap ($0.08/$0.32), 1M context — T0 tasks
  - gemini-2.5-flash-lite: cheap + vision ($0.08/$0.32) — T1 tasks
  - gemini-2.5-flash: balanced ($0.24/$2.00) — T2 tasks
  - gemini-2.5-pro: advanced ($1.25/$5.00) — T3 tasks
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request

logger = logging.getLogger(__name__)

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"


def _read_api_key() -> str | None:
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if key:
        return key.strip()
    env_path = os.path.expanduser("~/AppData/Local/hermes/.env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip()
                if line.strip().startswith("GOOGLE_API_KEY="):
                    return line.split("=", 1)[1].strip()
    return None


class GeminiLLMProvider:
    """LLM text generation via Google AI Studio (Gemini API).

    Default model: gemini-2.5-flash (balanced, $0.24/$2.00 per M tokens).
    Supports 1M token context on all Gemini models.
    """

    def __init__(self, model: str = "gemini-2.5-flash", api_key: str | None = None, timeout: int = 120):
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
        """Return the model's text completion, or raise on failure.

        Uses the generateContent REST API (not OpenAI-compatible endpoint
        to avoid compatibility issues).
        """
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY not available")

        use_model = model or self.model

        # Build contents array for Gemini API
        contents = []
        if system:
            # Gemini uses system_instruction separately
            pass
        contents.append({"role": "user", "parts": [{"text": prompt}]})

        payload = json.dumps({
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": temperature,
                "topP": 0.9,
            },
            **({"systemInstruction": {"parts": [{"text": system}]}} if system else {}),
        }).encode("utf-8")

        url = f"{_GEMINI_URL}/models/{use_model}:generateContent?key={self.api_key}"
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read())

        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError(f"Gemini returned no candidates: {data.get('promptFeedback', {})}")

        parts = candidates[0].get("content", {}).get("parts", [])
        if not parts:
            raise RuntimeError("Gemini returned empty response")

        return parts[0].get("text", "").strip()
