"""Scene schema and storyboard data structures.

§34: Scene schema — each scene has narration, timestamps, visual strategy, etc.
§33: Semantic storyboard division (not fixed 5s/sentence).
§27: Narration as timeline — timestamps from real audio.
§46-48: Visual Strategy Engine decides LOCAL vs RUNPOD per scene.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class SceneImportance(str, Enum):
    """§68: Scene importance classification."""
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class VisualStrategy(str, Enum):
    """§46: Visual strategy options."""
    STATIC_IMAGE = "STATIC_IMAGE"
    LOCAL_ANIMATED_STILL = "LOCAL_ANIMATED_STILL"
    LOCAL_IMAGE_TO_VIDEO = "LOCAL_IMAGE_TO_VIDEO"
    RUNPOD_GENERATIVE_VIDEO = "RUNPOD_GENERATIVE_VIDEO"


class QAStatus(str, Enum):
    """§34: QA status per scene."""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    REGENERATED = "REGENERATED"


@dataclass
class Scene:
    """A single scene in the storyboard (§34).

    All fields from the briefing §34 schema.
    """

    scene_id: str
    narration: str
    start: float  # seconds from episode start (from real audio timestamps, §32)
    end: float
    duration: float  # end - start

    # Visual content
    characters: list[str] = field(default_factory=list)
    location: str = ""
    emotion: str = ""
    action: str = ""

    # Strategy
    importance: SceneImportance = SceneImportance.NORMAL
    visual_strategy: VisualStrategy = VisualStrategy.LOCAL_ANIMATED_STILL

    # Prompts
    image_prompt: str = ""
    animation_prompt: str = ""
    negative_prompt: str = ""

    # References (character YAML files, reference images)
    references: list[str] = field(default_factory=list)

    # Camera (e.g. "slow push-in", "pan left")
    camera: str = ""

    # Asset paths (filled during pipeline)
    image_path: str = ""
    animation_clip_path: str = ""
    cloud_clip_path: str = ""

    # QA
    qa_status: QAStatus = QAStatus.PENDING
    qa_score: float = 0.0
    qa_problems: list[str] = field(default_factory=list)

    # Generation metadata (for audit trail, §89-90)
    seed: int = 0
    generation_attempts: int = 0
    generation_time: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["importance"] = self.importance.value
        d["visual_strategy"] = self.visual_strategy.value
        d["qa_status"] = self.qa_status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Scene:
        """Create Scene from dict (e.g., loading from JSON)."""
        data = data.copy()
        if "importance" in data and isinstance(data["importance"], str):
            data["importance"] = SceneImportance(data["importance"])
        if "visual_strategy" in data and isinstance(data["visual_strategy"], str):
            data["visual_strategy"] = VisualStrategy(data["visual_strategy"])
        if "qa_status" in data and isinstance(data["qa_status"], str):
            data["qa_status"] = QAStatus(data["qa_status"])
        return cls(**data)


@dataclass
class Storyboard:
    """Complete storyboard — list of scenes with total duration."""
    episode_id: str
    scenes: list[Scene] = field(default_factory=list)
    total_duration: float = 0.0  # seconds

    @property
    def scene_count(self) -> int:
        return len(self.scenes)

    @property
    def image_count(self) -> int:
        """Number of scenes that need an image (most of them)."""
        return sum(1 for s in self.scenes if s.visual_strategy != VisualStrategy.STATIC_IMAGE or True)

    @property
    def runpod_scene_count(self) -> int:
        """Scenes targeted for RunPod generative video (§68)."""
        return sum(1 for s in self.scenes if s.visual_strategy == VisualStrategy.RUNPOD_GENERATIVE_VIDEO)

    @property
    def local_scene_count(self) -> int:
        """Scenes for local animation."""
        return sum(1 for s in self.scenes if s.visual_strategy in (VisualStrategy.LOCAL_ANIMATED_STILL, VisualStrategy.LOCAL_IMAGE_TO_VIDEO))

    @property
    def runpod_seconds(self) -> float:
        """Total seconds of generative video (for budget estimation)."""
        return sum(s.duration for s in self.scenes if s.visual_strategy == VisualStrategy.RUNPOD_GENERATIVE_VIDEO)

    def to_dict(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id,
            "scenes": [s.to_dict() for s in self.scenes],
            "total_duration": self.total_duration,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Storyboard:
        return cls(
            episode_id=data["episode_id"],
            scenes=[Scene.from_dict(s) for s in data.get("scenes", [])],
            total_duration=data.get("total_duration", 0.0),
        )


def decide_visual_strategy(importance: SceneImportance, has_movement: bool, budget_allows_cloud: bool) -> VisualStrategy:
    """§46-48: Visual Strategy Engine decision logic.

    Only HIGH and CRITICAL scenes should normally be RunPod candidates (§68).
    """
    if importance in (SceneImportance.HIGH, SceneImportance.CRITICAL) and has_movement and budget_allows_cloud:
        return VisualStrategy.RUNPOD_GENERATIVE_VIDEO
    elif has_movement:
        return VisualStrategy.LOCAL_ANIMATED_STILL
    else:
        return VisualStrategy.LOCAL_ANIMATED_STILL  # default: animate locally