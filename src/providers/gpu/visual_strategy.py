"""Visual Strategy Engine — decides local animation vs RunPod generative video (§63-65).

§63: Only HIGH/CRITICAL scenes are candidates for generative video.
§64: 5-15% of episode should use generative video (not rigid rule).
§65: Quality is measured by consistency, not % of generative scenes.
§67: Local animation is the PRIMARY strategy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.providers.gpu.gpu_compute_provider import (
    SceneImportance,
    GenerativeVideoConfig,
    GPUComputeProvider,
    GPUJobRequest,
    GPUJobResult,
    GPUJobStatus,
)

logger = logging.getLogger(__name__)


@dataclass
class VisualStrategy:
    """Decision result for a single scene."""
    strategy: str  # "LOCAL_ANIMATED_STILL" | "GENERATIVE_VIDEO"
    reason: str = ""
    motion_preset: str = "slow_push_in"
    estimated_cost: float = 0.0
    estimated_time: float = 0.0


class VisualStrategyEngine:
    """Decides whether to use local animation or generative video per scene (§63-67).

    §65: A well-edited episode with few generative scenes can be visually
    superior to one fully produced by inconsistent text-to-video.
    """

    def __init__(
        self,
        config: GenerativeVideoConfig,
        local_provider: GPUComputeProvider,
        cloud_provider: GPUComputeProvider | None = None,
    ):
        self.config = config
        self.local = local_provider
        self.cloud = cloud_provider
        self._generative_seconds_used: float = 0.0
        self._generative_clips_used: int = 0

    def decide(
        self,
        scene_importance: SceneImportance,
        scene_duration: float,
        emotion: str = "",
        location: str = "",
        characters: list[str] | None = None,
        narration: str = "",
    ) -> VisualStrategy:
        """Decide visual strategy for a scene.

        Returns LOCAL_ANIMATED_STILL or GENERATIVE_VIDEO with reasoning.
        """
        # §63: Only HIGH/CRITICAL scenes are candidates
        if not scene_importance.should_use_generative_video():
            return VisualStrategy(
                strategy="LOCAL_ANIMATED_STILL",
                reason=f"Scene importance {scene_importance.value} — local animation sufficient (§63)",
            )

        # Check if generative video is enabled
        if not self.config.enabled:
            return VisualStrategy(
                strategy="LOCAL_ANIMATED_STILL",
                reason="Generative video disabled in config",
            )

        # Check if cloud provider is available
        if self.cloud is None or not self.cloud.available():
            return VisualStrategy(
                strategy="LOCAL_ANIMATED_STILL",
                reason="Cloud GPU provider not available — using local animation",
            )

        # §64: Check episode-level limits (5-15% of episode)
        clip_duration = min(scene_duration, self.config.preferred_clip_duration_seconds)
        if not self.config.can_add_clip(self._generative_seconds_used, clip_duration):
            return VisualStrategy(
                strategy="LOCAL_ANIMATED_STILL",
                reason=f"Generative video limit reached ({self._generative_seconds_used:.0f}s used, "
                       f"max {self.config.max_seconds_per_episode}s per episode — §64)",
            )

        # §63: Check clip count limit
        if self._generative_clips_used >= self.config.max_clips_per_episode:
            return VisualStrategy(
                strategy="LOCAL_ANIMATED_STILL",
                reason=f"Max clips per episode reached ({self.config.max_clips_per_episode} — §63)",
            )

        # §65: Check if generative video really adds value
        # Heuristic: scenes with action, movement, or dramatic moments benefit
        narration_lower = narration.lower()
        action_keywords = [
            "correndo", "luta", "batalha", "vitória", "derrotou", "caindo",
            "tempestade", "milagre", "criou", "surgiu", "desapareceu",
        ]
        has_action = any(kw in narration_lower for kw in action_keywords)

        if not has_action and scene_importance == SceneImportance.HIGH:
            # HIGH importance but no action — local animation may be better
            return VisualStrategy(
                strategy="LOCAL_ANIMATED_STILL",
                reason="HIGH importance but no action keywords — local animation sufficient (§65)",
            )

        # Estimate cost and check budget
        request = GPUJobRequest(
            job_type="image_to_video",
            duration_seconds=clip_duration,
        )
        estimated_cost = self.cloud.estimate_cost(request)

        if estimated_cost > self.config.cost_limit_per_clip_usd:
            return VisualStrategy(
                strategy="LOCAL_ANIMATED_STILL",
                reason=f"Estimated cost ${estimated_cost:.2f} exceeds per-clip limit "
                       f"${self.config.cost_limit_per_clip_usd:.2f}",
            )

        # All checks passed — use generative video
        self._generative_seconds_used += clip_duration
        self._generative_clips_used += 1

        return VisualStrategy(
            strategy="GENERATIVE_VIDEO",
            reason=f"Scene {scene_importance.value} with action — generative video justified "
                   f"(clip {self._generative_clips_used}/{self.config.max_clips_per_episode}, "
                   f"{self._generative_seconds_used:.0f}s/{self.config.max_seconds_per_episode}s used)",
            estimated_cost=estimated_cost,
            estimated_time=self.cloud.estimate_time(request),
        )

    def get_usage_summary(self) -> dict:
        """Return summary of generative video usage for this episode."""
        return {
            "generative_clips_used": self._generative_clips_used,
            "generative_seconds_used": round(self._generative_seconds_used, 1),
            "max_clips": self.config.max_clips_per_episode,
            "max_seconds": self.config.max_seconds_per_episode,
            "enabled": self.config.enabled,
        }
