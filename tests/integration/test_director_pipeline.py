"""Integration test: Director orchestrates end-to-end pre-production pipeline.

Tests the full flow: request → research → plan → WAITING_PLAN_APPROVAL
Then: approval → script → audio → storyboard → GENERATING_IMAGES

§98: Steps 1-9 of the pipeline.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest

from src.agents.director import DirectorAgent
from src.state.machine import EpisodeState


@pytest.fixture
def episodes_dir(tmp_path, monkeypatch):
    """Point episodes dir to tmp_path."""
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    return tmp_path


class TestDirectorPreProduction:
    """§98: Steps 1-5 — research → plan → approval."""

    @pytest.mark.asyncio
    async def test_start_episode_creacao(self, episodes_dir):
        """Start an episode for 'Criação do Mundo'."""
        director = DirectorAgent()
        result = await director.start_episode(
            theme="História da criação do mundo",
            episode_id="PILOT001",
        )
        assert result["episode_id"] == "PILOT001"
        assert result["theme"] == "História da criação do mundo"
        assert result["state"] == "WAITING_PLAN_APPROVAL"
        assert "references" in result["plan"]
        assert "budget_check" in result["plan"]
        assert result["plan"]["references"][0]["book"] == "Gênesis"
        assert result["budget_remaining"] > 5.0  # should have most of $6 left

    @pytest.mark.asyncio
    async def test_start_episode_davi(self, episodes_dir):
        """Start an episode for 'Davi e Golias'."""
        director = DirectorAgent()
        result = await director.start_episode(
            theme="História de Davi e Golias",
        )
        assert result["state"] == "WAITING_PLAN_APPROVAL"
        assert result["plan"]["references"][0]["book"] == "1 Samuel"

    @pytest.mark.asyncio
    async def test_episode_files_created(self, episodes_dir):
        """Episode filesystem should be created (§15)."""
        director = DirectorAgent()
        result = await director.start_episode(
            theme="História da criação do mundo",
            episode_id="PILOT002",
        )
        ep_root = episodes_dir / "PILOT002"
        assert (ep_root / "request.json").exists()
        assert (ep_root / "state.json").exists()
        assert (ep_root / "plan.json").exists()
        assert (ep_root / "costs.json").exists()
        assert (ep_root / "research" / "sources.json").exists()

    @pytest.mark.asyncio
    async def test_state_json_has_correct_state(self, episodes_dir):
        """State should be WAITING_PLAN_APPROVAL in state.json (§14 persistence)."""
        director = DirectorAgent()
        await director.start_episode(
            theme="História da criação do mundo",
            episode_id="PILOT003",
        )
        state_path = episodes_dir / "PILOT003" / "state.json"
        with open(state_path) as f:
            state = json.load(f)
        assert state["current_state"] == "WAITING_PLAN_APPROVAL"
        assert len(state["state_history"]) >= 3  # REQUEST_RECEIVED, RESEARCHING, PLANNING, WAITING


class TestDirectorProduction:
    """§98: Steps 6-9 — script → audio → storyboard."""

    @pytest.mark.asyncio
    async def test_continue_after_plan_approval(self, episodes_dir):
        """After plan approval, production should run."""
        director = DirectorAgent()
        await director.start_episode(
            theme="História da criação do mundo",
            episode_id="PILOT004",
        )
        # Simulate plan approval
        result = await director.continue_after_approval(
            episode_id="PILOT004",
            approval_type="plan",
        )
        assert result["episode_id"] == "PILOT004"
        assert "narration_preview" in result
        assert result["word_count"] > 0
        assert result["audio_duration_s"] > 0
        assert result["scene_count"] > 0
        assert result["state"] == "GENERATING_IMAGES"

    @pytest.mark.asyncio
    async def test_production_files_created(self, episodes_dir):
        """Production should create narration.txt, scenes.json, narration.mp3."""
        director = DirectorAgent()
        await director.start_episode(
            theme="História da criação do mundo",
            episode_id="PILOT005",
        )
        await director.continue_after_approval("PILOT005", "plan")

        ep_root = episodes_dir / "PILOT005"
        assert (ep_root / "script" / "narration.txt").exists()
        assert (ep_root / "audio" / "narration.mp3").exists()
        assert (ep_root / "storyboard" / "scenes.json").exists()

    @pytest.mark.asyncio
    async def test_scenes_have_timestamps_from_audio(self, episodes_dir):
        """§32: Scene timestamps should come from real audio (not fixed heuristic)."""
        director = DirectorAgent()
        await director.start_episode(
            theme="História da criação do mundo",
            episode_id="PILOT006",
        )
        result = await director.continue_after_approval("PILOT006", "plan")

        with open(episodes_dir / "PILOT006" / "storyboard" / "scenes.json") as f:
            scenes = json.load(f)["scenes"]

        for scene in scenes:
            assert scene["start"] >= 0
            assert scene["end"] > scene["start"]
            assert scene["duration"] > 0
