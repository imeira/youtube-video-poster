"""State Machine — episode lifecycle states and transitions.

§13: Each episode has persistent state.
§14: Production must survive restarts (resumability).
§17: Resuming must be idempotent (no double-publish, no duplicate charges).
§16: Checkpoints after expensive or approved stages.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any


class EpisodeState(str, Enum):
    """All possible episode states (§13)."""

    REQUEST_RECEIVED = "REQUEST_RECEIVED"
    RESEARCHING = "RESEARCHING"
    PLANNING = "PLANNING"
    WAITING_PLAN_APPROVAL = "WAITING_PLAN_APPROVAL"
    SCRIPTING = "SCRIPTING"
    SCRIPT_QA = "SCRIPT_QA"
    CHARACTER_DESIGN = "CHARACTER_DESIGN"
    STORYBOARDING = "STORYBOARDING"
    GENERATING_AUDIO = "GENERATING_AUDIO"
    GENERATING_IMAGES = "GENERATING_IMAGES"
    VISUAL_QA = "VISUAL_QA"
    PLANNING_ANIMATION = "PLANNING_ANIMATION"
    LOCAL_ANIMATION = "LOCAL_ANIMATION"
    CLOUD_VIDEO_GENERATION = "CLOUD_VIDEO_GENERATION"
    WAITING_BUDGET_APPROVAL = "WAITING_BUDGET_APPROVAL"
    ANIMATION_QA = "ANIMATION_QA"
    ASSEMBLING = "ASSEMBLING"
    FINAL_QA = "FINAL_QA"
    WAITING_FINAL_APPROVAL = "WAITING_FINAL_APPROVAL"
    UPLOADING = "UPLOADING"
    PUBLISHED = "PUBLISHED"
    PAUSED = "PAUSED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"

    @property
    def is_terminal(self) -> bool:
        """Terminal states: no further transitions possible."""
        return self in (EpisodeState.PUBLISHED, EpisodeState.CANCELLED)

    @property
    def is_approval_state(self) -> bool:
        """States that require human approval (silence ≠ approval, §8)."""
        return self in (
            EpisodeState.WAITING_PLAN_APPROVAL,
            EpisodeState.WAITING_BUDGET_APPROVAL,
            EpisodeState.WAITING_FINAL_APPROVAL,
        )


# ── Valid transitions (§2.1 of STATE_MACHINE.md) ─────────────────────────────

_TRANSITIONS: dict[EpisodeState, set[EpisodeState]] = {
    EpisodeState.REQUEST_RECEIVED: {EpisodeState.RESEARCHING, EpisodeState.CANCELLED},
    EpisodeState.RESEARCHING: {EpisodeState.PLANNING, EpisodeState.FAILED, EpisodeState.PAUSED, EpisodeState.CANCELLED},
    EpisodeState.PLANNING: {EpisodeState.WAITING_PLAN_APPROVAL, EpisodeState.FAILED, EpisodeState.PAUSED, EpisodeState.CANCELLED},
    EpisodeState.WAITING_PLAN_APPROVAL: {EpisodeState.SCRIPTING, EpisodeState.CANCELLED},
    EpisodeState.SCRIPTING: {EpisodeState.SCRIPT_QA, EpisodeState.GENERATING_AUDIO, EpisodeState.FAILED, EpisodeState.PAUSED, EpisodeState.CANCELLED},
    EpisodeState.SCRIPT_QA: {EpisodeState.CHARACTER_DESIGN, EpisodeState.SCRIPTING, EpisodeState.FAILED, EpisodeState.CANCELLED},
    EpisodeState.CHARACTER_DESIGN: {EpisodeState.STORYBOARDING, EpisodeState.FAILED, EpisodeState.PAUSED, EpisodeState.CANCELLED},
    EpisodeState.STORYBOARDING: {EpisodeState.GENERATING_AUDIO, EpisodeState.GENERATING_IMAGES, EpisodeState.FAILED, EpisodeState.PAUSED, EpisodeState.CANCELLED},
    EpisodeState.GENERATING_AUDIO: {EpisodeState.STORYBOARDING, EpisodeState.GENERATING_IMAGES, EpisodeState.FAILED, EpisodeState.PAUSED, EpisodeState.CANCELLED},
    EpisodeState.GENERATING_IMAGES: {EpisodeState.VISUAL_QA, EpisodeState.FAILED, EpisodeState.PAUSED, EpisodeState.CANCELLED},
    EpisodeState.VISUAL_QA: {EpisodeState.PLANNING_ANIMATION, EpisodeState.GENERATING_IMAGES, EpisodeState.FAILED, EpisodeState.CANCELLED},
    EpisodeState.PLANNING_ANIMATION: {EpisodeState.LOCAL_ANIMATION, EpisodeState.FAILED, EpisodeState.PAUSED, EpisodeState.CANCELLED},
    EpisodeState.LOCAL_ANIMATION: {EpisodeState.CLOUD_VIDEO_GENERATION, EpisodeState.ANIMATION_QA, EpisodeState.FAILED, EpisodeState.PAUSED, EpisodeState.CANCELLED},
    EpisodeState.CLOUD_VIDEO_GENERATION: {EpisodeState.WAITING_BUDGET_APPROVAL, EpisodeState.ANIMATION_QA, EpisodeState.LOCAL_ANIMATION, EpisodeState.FAILED, EpisodeState.PAUSED, EpisodeState.CANCELLED},
    EpisodeState.WAITING_BUDGET_APPROVAL: {EpisodeState.CLOUD_VIDEO_GENERATION, EpisodeState.LOCAL_ANIMATION, EpisodeState.CANCELLED},
    EpisodeState.ANIMATION_QA: {EpisodeState.ASSEMBLING, EpisodeState.FAILED, EpisodeState.CANCELLED},
    EpisodeState.ASSEMBLING: {EpisodeState.FINAL_QA, EpisodeState.FAILED, EpisodeState.PAUSED, EpisodeState.CANCELLED},
    EpisodeState.FINAL_QA: {EpisodeState.WAITING_FINAL_APPROVAL, EpisodeState.ASSEMBLING, EpisodeState.FAILED, EpisodeState.CANCELLED},
    EpisodeState.WAITING_FINAL_APPROVAL: {EpisodeState.UPLOADING, EpisodeState.ASSEMBLING, EpisodeState.CANCELLED},
    EpisodeState.UPLOADING: {EpisodeState.PUBLISHED, EpisodeState.FAILED, EpisodeState.PAUSED, EpisodeState.CANCELLED},
    EpisodeState.PUBLISHED: set(),  # terminal
    EpisodeState.PAUSED: set(),  # can resume to previous state (handled dynamically)
    EpisodeState.FAILED: set(),  # can retry to previous state (handled dynamically)
    EpisodeState.CANCELLED: set(),  # terminal
}


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""

    def __init__(self, from_state: EpisodeState, to_state: EpisodeState):
        self.from_state = from_state
        self.to_state = to_state
        valid = _TRANSITIONS.get(from_state, set())
        super().__init__(
            f"Invalid transition: {from_state.value} → {to_state.value}. "
            f"Valid transitions from {from_state.value}: {[s.value for s in valid]}"
        )


# ── State history entry ──────────────────────────────────────────────────────

@dataclass
class StateHistoryEntry:
    """A single entry in the state history (for audit trail)."""
    state: str
    timestamp: str  # ISO 8601 UTC
    agent: str
    note: str = ""

    @classmethod
    def now(cls, state: EpisodeState, agent: str, note: str = "") -> StateHistoryEntry:
        return cls(
            state=state.value,
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent=agent,
            note=note,
        )


# ── Checkpoint (§16) ──────────────────────────────────────────────────────────

@dataclass
class Checkpoint:
    """Checkpoint data — tracks completed work for idempotent resume (§17)."""
    last_completed_scene: str | None = None
    approved_assets: list[str] = field(default_factory=list)
    pending_regeneration: list[str] = field(default_factory=list)

    def mark_approved(self, scene_id: str) -> None:
        """Mark a scene's asset as approved (checkpoint, §16)."""
        if scene_id not in self.approved_assets:
            self.approved_assets.append(scene_id)

    def is_approved(self, scene_id: str) -> bool:
        """Check if a scene's asset was already approved (idempotency, §17)."""
        return scene_id in self.approved_assets

    def request_regeneration(self, scene_id: str) -> None:
        """Queue a scene for regeneration (§45)."""
        if scene_id not in self.pending_regeneration:
            self.pending_regeneration.append(scene_id)


# ── Episode state store ──────────────────────────────────────────────────────

@dataclass
class EpisodeStateStore:
    """Persistent episode state — survives restarts (§14).

    Stored as state.json in the episode directory.
    """

    episode_id: str
    current_state: EpisodeState = EpisodeState.REQUEST_RECEIVED
    previous_state: EpisodeState | None = None
    state_history: list[dict[str, Any]] = field(default_factory=list)
    checkpoint: dict[str, Any] = field(default_factory=Checkpoint().asdict if hasattr(Checkpoint, 'asdict') else dict)
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # in-memory only — not persisted
    _paused_from: EpisodeState | None = field(default=None, repr=False)

    def can_transition_to(self, to_state: EpisodeState) -> bool:
        """Check if a transition is valid without performing it."""
        if to_state == self.current_state:
            return True  # idempotent no-op (§17)
        if to_state == EpisodeState.PAUSED:
            return True  # can pause from any state (§6)
        if to_state == EpisodeState.FAILED:
            return True  # any state can fail
        if to_state == EpisodeState.CANCELLED:
            return True  # user can cancel anytime
        # PAUSED can resume to previous state
        if self.current_state == EpisodeState.PAUSED:
            return to_state == self._paused_from
        # FAILED can retry to previous state
        if self.current_state == EpisodeState.FAILED:
            return self.previous_state is not None and to_state == self.previous_state
        return to_state in _TRANSITIONS.get(self.current_state, set())

    def transition_to(self, to_state: EpisodeState, agent: str = "system", note: str = "") -> None:
        """Transition to a new state. Raises InvalidTransitionError if invalid.

        Records the transition in state_history and persists to disk.
        Same-state transition is a no-op (idempotent, §17).
        """
        if to_state == self.current_state:
            # Idempotent no-op — already in this state
            self.state_history.append(asdict(StateHistoryEntry.now(to_state, agent, note or "already in state")))
            self.updated_at = datetime.now(timezone.utc).isoformat()
            return

        if not self.can_transition_to(to_state):
            raise InvalidTransitionError(self.current_state, to_state)

        if to_state == EpisodeState.PAUSED:
            self._paused_from = self.current_state
        elif self.current_state == EpisodeState.PAUSED:
            # Resuming — clear paused_from
            self.previous_state = self.current_state
            self._paused_from = None
        elif self.current_state == EpisodeState.FAILED:
            # Retrying — don't update previous_state (keep original)
            pass
        else:
            self.previous_state = self.current_state

        self.current_state = to_state
        entry = StateHistoryEntry.now(to_state, agent, note)
        self.state_history.append(asdict(entry))
        self.updated_at = datetime.now(timezone.utc).isoformat()

    # ── Persistence ───────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict for JSON persistence."""
        return {
            "episode_id": self.episode_id,
            "current_state": self.current_state.value,
            "previous_state": self.previous_state.value if self.previous_state else None,
            "state_history": self.state_history,
            "checkpoint": asdict(Checkpoint(**self.checkpoint)) if isinstance(self.checkpoint, dict) else asdict(self.checkpoint),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EpisodeStateStore:
        """Deserialize from dict (JSON)."""
        store = cls(
            episode_id=data["episode_id"],
            current_state=EpisodeState(data.get("current_state", "REQUEST_RECEIVED")),
            previous_state=EpisodeState(data["previous_state"]) if data.get("previous_state") else None,
            state_history=data.get("state_history", []),
            checkpoint=data.get("checkpoint", {}),
            updated_at=data.get("updated_at", datetime.now(timezone.utc).isoformat()),
        )
        return store

    def save(self, path: Path) -> None:
        """Persist state to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def load(cls, path: Path) -> EpisodeStateStore:
        """Load state from JSON file. Raises FileNotFoundError if missing."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls.from_dict(data)

    @classmethod
    def load_or_create(cls, path: Path, episode_id: str) -> EpisodeStateStore:
        """Load existing state, or create new if not found (§14: resumability)."""
        if path.exists():
            return cls.load(path)
        store = cls(episode_id=episode_id)
        store.transition_to(EpisodeState.REQUEST_RECEIVED, agent="Director")
        store.save(path)
        return store
