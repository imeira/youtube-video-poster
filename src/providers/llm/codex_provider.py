"""Codex Pro LLM provider backed by the authenticated Codex CLI."""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CodexUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0


class CodexLLMProvider:
    """Run isolated, read-only Codex completions through the Pro subscription."""

    def __init__(
        self,
        model: str = "gpt-5.6-sol",
        reasoning_effort: str = "high",
        timeout: int = 180,
        executable: str = "codex",
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout
        self.executable = executable
        self.last_usage = CodexUsage()

    def available(self) -> bool:
        return shutil.which(self.executable) is not None

    def build_command(self) -> list[str]:
        """Return a deterministic command that cannot mutate the workspace."""
        return [
            self.executable,
            "exec",
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-user-config",
            "--ignore-rules",
            "-s",
            "read-only",
            "-m",
            self.model,
            "-c",
            f'model_reasoning_effort="{self.reasoning_effort}"',
            "-c",
            'approval_policy="never"',
            "--disable",
            "plugins",
            "--json",
        ]

    def execution_command(self) -> list[str]:
        """Resolve npm's Windows ``.CMD`` shim to Node without shell quoting."""
        command = self.build_command()
        resolved = shutil.which(self.executable)
        if not resolved:
            return command

        path = Path(resolved)
        if path.suffix.lower() == ".cmd":
            node = path.parent / "node.exe"
            if not node.exists():
                node_path = shutil.which("node")
                node = Path(node_path) if node_path else node
            script = path.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
            if node.exists() and script.exists():
                return [str(node), str(script), *command[1:]]
        return [resolved, *command[1:]]

    async def complete(
        self,
        prompt: str,
        system: str = "",
        max_tokens: int = 2000,
        temperature: float = 0.7,
        model: str = "",
    ) -> str:
        """Return the final Codex agent message.

        ``max_tokens`` and ``temperature`` remain in the shared provider contract;
        Codex controls those internally. The prompt still carries the requested
        output budget so callers retain bounded-output intent.
        """
        if not self.available():
            raise RuntimeError("Codex CLI is not available")

        effective_model = model or self.model
        command = self.execution_command()
        command[command.index("-m") + 1] = effective_model
        combined = self._build_prompt(system, prompt, max_tokens, temperature)

        process = await asyncio.create_subprocess_exec(
            *command,
            combined,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), self.timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise RuntimeError(f"Codex completion timed out after {self.timeout}s") from None

        output = stdout.decode("utf-8", errors="replace")
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip().splitlines()
            summary = detail[-1] if detail else "no diagnostic"
            raise RuntimeError(f"Codex completion failed with exit {process.returncode}: {summary}")

        self.last_usage = self.extract_usage(output)
        return self.extract_message(output)

    @staticmethod
    def _build_prompt(system: str, prompt: str, max_tokens: int, temperature: float) -> str:
        sections = []
        if system.strip():
            sections.append(f"INSTRUÇÕES DO SISTEMA:\n{system.strip()}")
        sections.append(f"TAREFA:\n{prompt.strip()}")
        sections.append(
            "RESTRIÇÕES DE SAÍDA:\n"
            f"Seja objetivo e mantenha a resposta dentro de aproximadamente {max_tokens} tokens. "
            f"A criatividade solicitada pelo chamador é {temperature:.2f}."
        )
        return "\n\n".join(sections)

    @staticmethod
    def extract_message(output: str) -> str:
        messages: list[str] = []
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = event.get("item", {})
            if event.get("type") == "item.completed" and item.get("type") == "agent_message":
                text = str(item.get("text", "")).strip()
                if text:
                    messages.append(text)
        if not messages:
            raise RuntimeError("Codex output contained no agent message")
        return messages[-1]

    @staticmethod
    def extract_usage(output: str) -> CodexUsage:
        usage: dict = {}
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed":
                usage = event.get("usage", {})
        return CodexUsage(
            input_tokens=int(usage.get("input_tokens", 0)),
            cached_input_tokens=int(usage.get("cached_input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            reasoning_output_tokens=int(usage.get("reasoning_output_tokens", 0)),
        )
