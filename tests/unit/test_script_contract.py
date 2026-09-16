"""Structured narration contracts for child-safe Bible production."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.script import ScriptAgent


@pytest.mark.asyncio
async def test_script_agent_persists_structured_segments_with_sources_and_closing(tmp_path: Path):
    research = {
        "story": "A promessa de um filho para Abraão e Sara",
        "summary": "Deus prometeu uma grande família a Abraão e Sara.",
        "references": [{"book": "Gênesis", "chapter": "15–18", "verses": "1–15"}],
        "narrative_classification": {
            "BIBLICAL_FACT": [
                "Deus prometeu a Abraão uma descendência numerosa.",
                "Sara ouviu a promessa de que teria um filho.",
            ]
        },
    }

    result = await ScriptAgent().run(
        episode_id="EP8",
        research_data=research,
        target_duration_s=240,
        script_dir=str(tmp_path),
    )

    assert result.success
    packet_path = tmp_path / "script.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    assert packet["audience"] == {"min_age": 6, "max_age": 10}
    assert all(segment["source_refs"] for segment in packet["segments"] if segment["kind"] == "biblical_paraphrase")
    assert packet["segments"][-1]["kind"] == "family_reflection"
    assert packet["closing_duration_s"] in (3, 4, 5)
    assert result.data["script_packet_path"] == str(packet_path)
