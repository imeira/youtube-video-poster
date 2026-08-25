"""Tests for the state machine (§13-17).

Tests:
- Valid transitions
- Invalid transitions raise error
- Pausing and resuming
- Failing and retrying
- Checkpoints and idempotency
- Persistence (save/load)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.state.machine import (
    EpisodeState,
    EpisodeStateStore,
    Checkpoint,
    InvalidTransitionError,
)


class TestStateTransitions:
    """§2.1: Valid state transitions."""

    def test_request_received_to_researching(self):
        """Normal flow: REQUEST_RECEIVED → RESEARCHING."""
        store = EpisodeStateStore(episode_id="TEST001")
        store.transition_to(EpisodeState.REQUEST_RECEIVED, agent="Director")
        store.transition_to(EpisodeState.RESEARCHING, agent="Research")
        assert store.current_state == EpisodeState.RESEARCHING
        assert store.previous_state == EpisodeState.REQUEST_RECEIVED

    def test_full_happy_path(self):
        """Walk the entire happy path from request to published."""
        store = EpisodeStateStore(episode_id="TEST002")
        store.transition_to(EpisodeState.REQUEST_RECEIVED, agent="Director")
        store.transition_to(EpisodeState.RESEARCHING)
        store.transition_to(EpisodeState.PLANNING)
        store.transition_to(EpisodeState.WAITING_PLAN_APPROVAL)
        store.transition_to(EpisodeState.SCRIPTING, note="approved by user")
        store.transition_to(EpisodeState.SCRIPT_QA)
        store.transition_to(EpisodeState.CHARACTER_DESIGN)
        store.transition_to(EpisodeState.STORYBOARDING)
        store.transition_to(EpisodeState.GENERATING_AUDIO)
        store.transition_to(EpisodeState.GENERATING_IMAGES)
        store.transition_to(EpisodeState.VISUAL_QA)
        store.transition_to(EpisodeState.PLANNING_ANIMATION)
        store.transition_to(EpisodeState.LOCAL_ANIMATION)
        store.transition_to(EpisodeState.ANIMATION_QA)
        store.transition_to(EpisodeState.ASSEMBLING)
        store.transition_to(EpisodeState.FINAL_QA)
        store.transition_to(EpisodeState.WAITING_FINAL_APPROVAL)
        store.transition_to(EpisodeState.UPLOADING)
        store.transition_to(EpisodeState.PUBLISHED)
        assert store.current_state == EpisodeState.PUBLISHED
        assert store.current_state.is_terminal

    def test_invalid_transition_raises(self):
        """Invalid transition should raise."""
        store = EpisodeStateStore(episode_id="TEST003")
        store.transition_to(EpisodeState.REQUEST_RECEIVED)
        # Can't skip from REQUEST_RECEIVED directly to SCRIPTING
        with pytest.raises(InvalidTransitionError):
            store.transition_to(EpisodeState.SCRIPTING)

    def test_can_transition_to_check(self):
        """can_transition_to should not mutate state."""
        store = EpisodeStateStore(episode_id="TEST004")
        store.transition_to(EpisodeState.REQUEST_RECEIVED)
        assert store.can_transition_to(EpisodeState.RESEARCHING)
        assert not store.can_transition_to(EpisodeState.SCRIPTING)
        # State should not have changed
        assert store.current_state == EpisodeState.REQUEST_RECEIVED

    def test_any_state_can_pause(self):
        """§6: User can pause at any state."""
        store = EpisodeStateStore(episode_id="TEST005")
        store.transition_to(EpisodeState.REQUEST_RECEIVED)
        store.transition_to(EpisodeState.RESEARCHING)
        store.transition_to(EpisodeState.PAUSED)
        assert store.current_state == EpisodeState.PAUSED

    def test_any_state_can_fail(self):
        """Any state can transition to FAILED."""
        store = EpisodeStateStore(episode_id="TEST006")
        store.transition_to(EpisodeState.REQUEST_RECEIVED)
        store.transition_to(EpisodeState.RESEARCHING)
        store.transition_to(EpisodeState.FAILED)
        assert store.current_state == EpisodeState.FAILED

    def test_any_state_can_cancel(self):
        """User can cancel at any state."""
        store = EpisodeStateStore(episode_id="TEST007")
        store.transition_to(EpisodeState.REQUEST_RECEIVED)
        store.transition_to(EpisodeState.RESEARCHING)
        store.transition_to(EpisodeState.CANCELLED)
        assert store.current_state == EpisodeState.CANCELLED
        assert store.current_state.is_terminal

    def test_failed_can_retry(self):
        """FAILED can retry to previous state."""
        store = EpisodeStateStore(episode_id="TEST008")
        store.transition_to(EpisodeState.REQUEST_RECEIVED)
        store.transition_to(EpisodeState.RESEARCHING)
        store.transition_to(EpisodeState.FAILED)
        store.transition_to(EpisodeState.RESEARCHING, note="retry")
        assert store.current_state == EpisodeState.RESEARCHING

    def test_published_is_terminal(self):
        """PUBLISHED is terminal — no further transitions possible."""
        store = EpisodeStateStore(episode_id="TEST009")
        # PUBLISHED is terminal by definition
        assert EpisodeState.PUBLISHED.is_terminal
        # Verify no transitions are valid from PUBLISHED
        store.current_state = EpisodeState.PUBLISHED
        store.previous_state = EpisodeState.UPLOADING
        assert not store.can_transition_to(EpisodeState.RESEARCHING)
        assert not store.can_transition_to(EpisodeState.REQUEST_RECEIVED)

    def test_approval_states_identified(self):
        """§8: Approval states should be identifiable."""
        assert EpisodeState.WAITING_PLAN_APPROVAL.is_approval_state
        assert EpisodeState.WAITING_BUDGET_APPROVAL.is_approval_state
        assert EpisodeState.WAITING_FINAL_APPROVAL.is_approval_state
        assert not EpisodeState.RESEARCHING.is_approval_state


class TestCheckpoints:
    """§16-17: Checkpoints and idempotency."""

    def test_mark_approved(self):
        """§16: Marking a scene as approved."""
        cp = Checkpoint()
        cp.mark_approved("SC001")
        assert cp.is_approved("SC001")
        assert not cp.is_approved("SC002")

    def test_idempotent_mark(self):
        """§17: Marking the same scene twice should not duplicate."""
        cp = Checkpoint()
        cp.mark_approved("SC001")
        cp.mark_approved("SC001")  # should not duplicate
        assert cp.approved_assets.count("SC001") == 1

    def test_request_regeneration(self):
        """§45: Request regeneration of a specific scene."""
        cp = Checkpoint()
        cp.request_regeneration("SC027")
        assert "SC027" in cp.pending_regeneration


class TestPersistence:
    """§14: State must survive restarts."""

    def test_save_and_load(self, tmp_path: Path):
        """State should survive save/load cycle."""
        state_path = tmp_path / "state.json"
        store = EpisodeStateStore(episode_id="TEST010")
        store.transition_to(EpisodeState.REQUEST_RECEIVED)
        store.transition_to(EpisodeState.RESEARCHING)
        store.save(state_path)

        # Load and verify
        loaded = EpisodeStateStore.load(state_path)
        assert loaded.episode_id == "TEST010"
        assert loaded.current_state == EpisodeState.RESEARCHING
        assert len(loaded.state_history) == 2

    def test_load_or_create_existing(self, tmp_path: Path):
        """§14: If state exists, load it (resume)."""
        state_path = tmp_path / "state.json"
        store = EpisodeStateStore(episode_id="TEST011")
        store.transition_to(EpisodeState.REQUEST_RECEIVED)
        store.transition_to(EpisodeState.RESEARCHING)
        store.save(state_path)

        # Simulate restart — load existing
        loaded = EpisodeStateStore.load_or_create(state_path, "TEST011")
        assert loaded.current_state == EpisodeState.RESEARCHING

    def test_load_or_create_new(self, tmp_path: Path):
        """§14: If no state, create new."""
        state_path = tmp_path / "state.json"
        store = EpisodeStateStore.load_or_create(state_path, "TEST012")
        assert store.current_state == EpisodeState.REQUEST_RECEIVED
        assert state_path.exists()

    def test_state_history_recorded(self, tmp_path: Path):
        """State history should record all transitions with timestamps."""
        state_path = tmp_path / "state.json"
        store = EpisodeStateStore(episode_id="TEST013")
        store.transition_to(EpisodeState.REQUEST_RECEIVED, agent="Director")
        store.transition_to(EpisodeState.RESEARCHING, agent="Research", note="started")
        store.save(state_path)

        loaded = EpisodeStateStore.load(state_path)
        assert len(loaded.state_history) == 2
        entry = loaded.state_history[1]
        assert entry["state"] == "RESEARCHING"
        assert entry["agent"] == "Research"
        assert entry["note"] == "started"
        assert "timestamp" in entry
