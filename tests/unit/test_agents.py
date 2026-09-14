"""Tests for agents (Research, Script, Storyboard, Audio, Director)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.research import ResearchAgent
from src.agents.script import ScriptAgent
from src.agents.storyboard import StoryboardAgent


class TestResearchAgent:
    """§22: Biblical source grounding."""

    @pytest.mark.asyncio
    async def test_known_story_criacao(self, tmp_path: Path):
        """Should find 'criação do mundo' story."""
        agent = ResearchAgent()
        result = await agent.run(
            episode_id="TEST001",
            theme="História da criação do mundo",
            research_dir=str(tmp_path),
        )
        assert result.success
        assert "references" in result.data
        assert result.data["references"][0]["book"] == "Gênesis"
        assert "narrative_classification" in result.data
        assert len(result.data["narrative_classification"]["BIBLICAL_FACT"]) > 0
        assert (tmp_path / "sources.json").exists()

    @pytest.mark.asyncio
    async def test_known_story_davi(self, tmp_path: Path):
        """Should find 'davi e golias' story."""
        agent = ResearchAgent()
        result = await agent.run(
            episode_id="TEST002",
            theme="História de Davi e Golias",
            research_dir=str(tmp_path),
        )
        assert result.success
        assert result.data["references"][0]["book"] == "1 Samuel"
        assert result.next_state == "PLANNING"

    @pytest.mark.asyncio
    async def test_unknown_story_fails(self):
        """Unknown theme should fail gracefully."""
        agent = ResearchAgent()
        result = await agent.run(
            episode_id="TEST003",
            theme="A história do tempo",
        )
        assert not result.success
        assert "No biblical story found" in result.error


class TestScriptAgent:
    """§24: Script for children 6-10."""

    @pytest.mark.asyncio
    async def test_generate_script(self, tmp_path: Path):
        """Should generate narration from research data."""
        agent = ScriptAgent()
        research = {
            "story": "Criação do Mundo",
            "summary": "Deus criou o mundo em seis dias.",
            "narrative_classification": {
                "BIBLICAL_FACT": ["Deus criou a luz", "Deus criou as plantas"],
            },
        }
        result = await agent.run(
            episode_id="TEST004",
            research_data=research,
            target_duration_s=180,
            script_dir=str(tmp_path),
        )
        assert result.success
        assert result.data["word_count"] > 0
        assert (tmp_path / "narration.txt").exists()
        assert result.next_state == "SCRIPT_QA"

    def test_adapt_for_children(self):
        """Children adaptation should add dramatic pauses."""
        agent = ScriptAgent()
        adapted = agent._adapt_for_children("Deus criou a luz.")
        assert "..." in adapted


class TestStoryboardAgent:
    """§33-34: Semantic scene division with timestamps."""

    @pytest.mark.asyncio
    async def test_scene_windows_cover_audio_gaps_and_trailing_silence(self, tmp_path: Path):
        agent = StoryboardAgent()
        timestamps = [
            {"start": 0.1, "end": 2.0, "duration": 1.9, "text": "Primeira frase."},
            {"start": 2.5, "end": 4.5, "duration": 2.0, "text": "Segunda frase."},
        ]
        result = await agent.run(
            episode_id="TEST_TIMELINE",
            narration="Primeira frase. Segunda frase.",
            sentence_timestamps=timestamps,
            audio_duration_s=5.0,
            storyboard_dir=str(tmp_path),
        )
        assert [(scene["start"], scene["end"], scene["duration"]) for scene in result.data["scenes"]] == [
            (0.0, 2.5, 2.5),
            (2.5, 5.0, 2.5),
        ]

    @pytest.mark.asyncio
    async def test_create_scenes(self, tmp_path: Path):
        """Should create scenes from timestamps."""
        agent = StoryboardAgent()
        timestamps = [
            {"start": 0.0, "end": 4.5, "duration": 4.5, "text": "Deus criou a luz no primeiro dia."},
            {"start": 4.5, "end": 8.0, "duration": 3.5, "text": "Deus viu que a luz era boa."},
        ]
        result = await agent.run(
            episode_id="TEST005",
            narration="Test narration",
            sentence_timestamps=timestamps,
            storyboard_dir=str(tmp_path),
        )
        assert result.success
        assert result.data["scene_count"] == 2
        assert (tmp_path / "scenes.json").exists()
        data = json.loads((tmp_path / "scenes.json").read_text(encoding="utf-8"))
        scene = data["scenes"][0]
        assert "scene_id" in scene
        assert "start" in scene
        assert "end" in scene
        assert "image_prompt" in scene
        assert "qa_status" in scene
        assert scene["qa_status"] == "PENDING"

    @pytest.mark.asyncio
    async def test_importance_classification(self, tmp_path: Path):
        """§68: Scenes should be classified by importance."""
        agent = StoryboardAgent()
        timestamps = [
            {"start": 0, "end": 3, "duration": 3, "text": "Deus criou o mundo."},
            {"start": 3, "end": 6, "duration": 3, "text": "E era tarde e manhã."},
        ]
        result = await agent.run(
            episode_id="TEST006",
            narration="test",
            sentence_timestamps=timestamps,
            storyboard_dir=str(tmp_path),
        )
        assert result.success
        scenes = result.data["scenes"]
        assert scenes[0]["importance"] in ("HIGH", "CRITICAL")

    @pytest.mark.asyncio
    async def test_no_timestamps_fails(self):
        """Should fail without timestamps (§32: real timestamps required)."""
        agent = StoryboardAgent()
        result = await agent.run(
            episode_id="T",
            narration="x",
            sentence_timestamps=None,
        )
        assert not result.success

    @pytest.mark.asyncio
    async def test_compiled_storyboard_never_calls_llm_once_per_scene(self):
        class ExplodingLLM:
            def available(self):
                return True

            async def complete(self, **_kwargs):
                raise AssertionError("compiled prompts must not invoke per-scene LLM")

        result = await StoryboardAgent(llm_provider=ExplodingLLM()).run(
            episode_id="COMPILED",
            narration="Abraão olha para o céu.",
            sentence_timestamps=[
                {"start": 0, "end": 2, "duration": 2, "text": "Abraão olha para o céu."}
            ],
        )

        assert result.success
        assert result.data["scenes"][0]["image_prompt"]
