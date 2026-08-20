"""Animation Agent — animates still images with ffmpeg (§49-52).

Responsibility: Create Ken Burns / pan / zoom clips from approved images
Input: images/SC<id>.png + scene durations from storyboard
Output: animation/SC<id>.mp4
Constraints: libx264 CPU (B0); motion presets (§52); ~7 min for 4 min episode
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.agents.base import BaseAgent, AgentResult
from src.providers.video.local_ffmpeg_provider import LocalFFmpegVideoProvider

logger = logging.getLogger(__name__)


class AnimationAgent(BaseAgent):
    """Animates still images using ffmpeg motion presets (§49-52).

    Phase 0 benchmark: Ken Burns 3.56s/5s clip, parallax 15s/5s.
    """

    def __init__(self):
        super().__init__(name="Animation")
        self._provider = LocalFFmpegVideoProvider()

    async def run(
        self,
        episode_id: str,
        scenes: list[dict] | None = None,
        images: list[dict] | None = None,
        animation_dir: str = "",
        **kwargs,
    ) -> AgentResult:
        """Animate each image into a video clip.

        Args:
            scenes: Storyboard scenes with duration and camera (motion preset).
            images: List of {scene_id, image_path} from ImageGenAgent.
            animation_dir: Directory to save animation clips.

        Returns:
            AgentResult with clip paths.
        """
        if not scenes or not images:
            return AgentResult(success=False, error="Missing scenes or images")

        # Build a lookup: scene_id -> image_path
        image_map = {img["scene_id"]: img["image_path"] for img in images}

        anim_dir = Path(animation_dir) if animation_dir else None
        if anim_dir:
            anim_dir.mkdir(parents=True, exist_ok=True)

        clips = []
        failed = []
        total_time = 0.0

        for scene in scenes:
            scene_id = scene["scene_id"]
            image_path = image_map.get(scene_id)

            if not image_path:
                failed.append({"scene_id": scene_id, "error": "No image for scene"})
                continue

            duration = max(1, int(scene.get("duration", 3)))

            # §67-69: Use Visual Strategy Engine + Motion Presets for auto-selection
            from src.providers.video.motion_presets import select_motion_for_scene
            importance = scene.get("importance", "NORMAL")
            emotion = scene.get("emotion", "calm")
            location = scene.get("location", "")
            camera_hint = scene.get("camera", "")

            # If camera is already set to a valid preset, use it; otherwise auto-select
            motion = camera_hint if camera_hint in [
                "slow_push_in", "slow_pull_out", "pan_left", "pan_right",
                "vertical_reveal", "hero_reveal", "dramatic_zoom", "gentle_float",
                "parallax_walk", "storm_motion", "fire_glow", "water_motion",
            ] else select_motion_for_scene(importance, emotion, location)

            logger.info(f"Animating {scene_id} ({motion}, {duration}s, importance={importance})...")
            result = await self._provider.image_to_video(
                image_path=image_path,
                duration=duration,
                motion=motion,
            )

            if result.success:
                # Move to episode animation dir
                clip_path = result.video_path
                if anim_dir:
                    target = anim_dir / f"{scene_id}.mp4"
                    import shutil
                    shutil.move(clip_path, target)
                    clip_path = str(target)

                clips.append({
                    "scene_id": scene_id,
                    "clip_path": clip_path,
                    "duration_s": duration,
                    "generation_time": result.generation_time,
                })
                total_time += result.generation_time
                logger.info(f"  {scene_id}: {result.generation_time:.1f}s")
            else:
                failed.append({"scene_id": scene_id, "error": result.error})

        success = len(clips) > 0
        return AgentResult(
            success=success,
            data={
                "clips": clips,
                "failed": failed,
                "total_clips": len(clips),
                "total_failed": len(failed),
                "total_time_s": round(total_time, 1),
            },
            next_state="ASSEMBLING" if success else "FAILED",
        )

    def _map_camera_to_motion(self, camera: str) -> str:
        """Map storyboard camera directive to ffmpeg motion preset (§52)."""
        mapping = {
            "slow_push_in": "slow_push_in",
            "push_in": "slow_push_in",
            "zoom_in": "dramatic_zoom",
            "dramatic_zoom": "dramatic_zoom",
            "pull_out": "slow_pull_out",
            "slow_pull_out": "slow_pull_out",
            "pan_left": "pan_left",
            "pan_right": "pan_right",
            "float": "gentle_float",
            "gentle_float": "gentle_float",
        }
        return mapping.get(camera, "slow_push_in")
