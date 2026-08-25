"""Tests for the Codex availability fallback policy."""

from __future__ import annotations

from pathlib import Path

from src.providers.llm.openrouter_provider import (
    DEFAULT_FREE_FALLBACK_MODEL,
    OpenRouterLLMProvider,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TEXT_CONFIG_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".md", ".toml"}


def test_openrouter_provider_defaults_to_most_used_free_model() -> None:
    provider = OpenRouterLLMProvider(api_key="configured")

    assert DEFAULT_FREE_FALLBACK_MODEL == "stealth/ox-alpha"
    assert provider.model == DEFAULT_FREE_FALLBACK_MODEL
    assert provider.estimate_cost() == 0.0


def test_repository_has_no_gemini_model_references() -> None:
    violations: list[str] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_CONFIG_SUFFIXES:
            continue
        if path == Path(__file__).resolve():
            continue
        if any(part in {".git", "__pycache__"} for part in path.parts):
            continue
        text = path.read_text(encoding="utf-8-sig")
        lowered = text.lower()
        if "models/gemini-" in lowered or "gemini-2." in lowered or "gemini-3." in lowered:
            violations.append(str(path.relative_to(REPO_ROOT)))

    assert violations == []


def test_gemini_provider_module_was_removed() -> None:
    assert not (REPO_ROOT / "src/providers/llm/gemini_provider.py").exists()
