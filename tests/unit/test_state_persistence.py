"""State persistence preserves pause target across restarts."""

from __future__ import annotations

from pathlib import Path

from src.state.machine import EpisodeState, EpisodeStateStore


def test_paused_state_resumes_after_reload(tmp_path: Path):
    path = tmp_path / "state.json"
    store = EpisodeStateStore(episode_id="EP8", current_state=EpisodeState.SCRIPTING)
    store.transition_to(EpisodeState.PAUSED, agent="test")
    store.save(path)

    restored = EpisodeStateStore.load(path)
    assert restored.can_transition_to(EpisodeState.SCRIPTING)
    restored.transition_to(EpisodeState.SCRIPTING, agent="test")
    assert restored.current_state is EpisodeState.SCRIPTING
