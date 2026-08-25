"""Profile D routing tests for the Codex-first studio policy."""

from __future__ import annotations

import json

import pytest

from src.providers.llm.codex_provider import CodexLLMProvider
from src.providers.llm.core_model_router import CoreModelRouter


def test_codex_provider_builds_ephemeral_read_only_command() -> None:
    provider = CodexLLMProvider(model="gpt-5.6-sol", reasoning_effort="high")

    command = provider.build_command()

    assert command[:2] == ["codex", "exec"]
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert ["-s", "read-only"] == command[command.index("-s"):command.index("-s") + 2]
    assert ["-m", "gpt-5.6-sol"] == command[command.index("-m"):command.index("-m") + 2]
    assert 'model_reasoning_effort="high"' in command
    assert "--json" in command


def test_codex_provider_extracts_last_agent_message() -> None:
    output = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "primeiro"},
                }
            ),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "resposta final"},
                }
            ),
        ]
    )

    assert CodexLLMProvider.extract_message(output) == "resposta final"


def test_codex_provider_rejects_output_without_agent_message() -> None:
    with pytest.raises(RuntimeError, match="agent message"):
        CodexLLMProvider.extract_message('{"type":"turn.completed"}')


def test_codex_provider_resolves_windows_cmd_wrapper(monkeypatch, tmp_path) -> None:
    cmd = tmp_path / "codex.CMD"
    cmd.write_text("@echo off", encoding="utf-8")
    node = tmp_path / "node.exe"
    node.write_bytes(b"")
    script = tmp_path / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")

    monkeypatch.setattr(
        "src.providers.llm.codex_provider.shutil.which",
        lambda name: str(cmd) if name == "codex" else None,
    )

    provider = CodexLLMProvider()

    assert provider.execution_command()[:3] == [str(node), str(script), "exec"]


def test_profile_d_routes_core_and_auxiliary_tasks() -> None:
    router = CoreModelRouter.profile_d()

    assert router.route("script").model == "gpt-5.6-sol"
    assert router.route("script").reasoning_effort == "high"
    assert router.route("storyboard").model == "gpt-5.6-sol"
    assert router.route("storyboard").reasoning_effort == "high"
    assert router.route("metadata").model == "gpt-5.6-luna"
    assert router.route("metadata").reasoning_effort == "low"


def test_profile_d_rejects_unknown_task() -> None:
    router = CoreModelRouter.profile_d()

    with pytest.raises(ValueError, match="Unknown model-routing task"):
        router.route("unclassified")


def test_director_uses_profile_d_codex_routes() -> None:
    from src.agents.director import DirectorAgent

    director = DirectorAgent(model_router=CoreModelRouter.profile_d())

    assert isinstance(director.script._llm, CodexLLMProvider)
    assert director.script._llm.model == "gpt-5.6-sol"
    assert director.script._llm.reasoning_effort == "high"
    assert isinstance(director.storyboard._llm, CodexLLMProvider)
    assert director.storyboard._llm.model == "gpt-5.6-sol"
    assert director.metadata._llm.model == "gpt-5.6-luna"
    assert director.metadata._llm.reasoning_effort == "low"
