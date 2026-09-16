"""Director does not convert final approval into publication."""

from __future__ import annotations

import pytest

from src.agents.director import DirectorAgent
from src.state.machine import EpisodeState, EpisodeStateStore
from src.storage.episode_fs import EpisodeFS


@pytest.mark.asyncio
async def test_final_approval_waits_for_separate_publication_command(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent()
    fs = EpisodeFS("EP8", director.config)
    fs.create_dirs()
    state = EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.WAITING_FINAL_APPROVAL)
    state.save(fs.paths.state_json)

    result = await director.continue_after_approval("EP8", "final")

    assert result["status"] == "awaiting_separate_publication_instruction"
    assert EpisodeStateStore.load(fs.paths.state_json).current_state is EpisodeState.WAITING_FINAL_APPROVAL
