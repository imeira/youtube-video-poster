"""Image Generation Agent — generates consistent stills for each scene (§42).

Responsibility: Generate images using SD1.5+LCM (fast mode)
Input: storyboard/scenes.json with image_prompt per scene
Output: images/SC<id>.png
Constraints: 4GB VRAM (B2-B4); LCM mode = 7.1s/image; fp16 all-GPU
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from src.agents.base import BaseAgent, AgentResult
from src.providers.image.local_sd15_provider import LocalSD15Provider

logger = logging.getLogger(__name__)


class ImageGenAgent(BaseAgent):
    """Generates images for all storyboard scenes (§42).

    B3: LCM mode = 7.1s/image @ 512x512
    B2: Standard mode = 38.8s/image @ 512x512
    """

    def __init__(self, mode: str = "lcm"):
        super().__init__(name="ImageGen")
        self.mode = mode
        self._provider: LocalSD15Provider | None = None

    def _get_provider(self) -> LocalSD15Provider:
        if self._provider is None:
            self._provider = LocalSD15Provider(mode=self.mode)
        return self._provider

    async def run(
        self,
        episode_id: str,
        scenes: list[dict] | None = None,
        images_dir: str = "",
        seed_base: int = 42,
        **kwargs,
    ) -> AgentResult:
        """Generate one image per scene.

        Args:
            scenes: List of scene dicts with image_prompt.
            images_dir: Directory to save images.
            seed_base: Base seed for reproducibility.

        Returns:
            AgentResult with generated image paths.
        """
        if not scenes:
            return AgentResult(success=False, error="No scenes provided")

        provider = self._get_provider()
        images_dir_path = Path(images_dir) if images_dir else None
        if images_dir_path:
            images_dir_path.mkdir(parents=True, exist_ok=True)

        generated = []
        failed = []
        total_time = 0.0

        for scene in scenes:
            scene_id = scene["scene_id"]
            prompt = scene.get("image_prompt", "")
            negative = scene.get("negative_prompt", "")
            seed = seed_base + hash(scene_id) % 10000

            logger.info(f"Generating image for {scene_id} (seed={seed})...")
            result = await provider.generate(
                prompt=prompt,
                negative_prompt=negative,
                width=512,
                height=512,
                seed=seed,
            )

            if result.success:
                # Move to episode images dir if specified
                img_path = result.image_path
                if images_dir_path:
                    target = images_dir_path / f"{scene_id}.png"
                    import shutil
                    shutil.move(img_path, target)
                    img_path = str(target)

                generated.append({
                    "scene_id": scene_id,
                    "image_path": img_path,
                    "seed": result.seed,
                    "generation_time": result.generation_time,
                })
                total_time += result.generation_time
                logger.info(f"  {scene_id}: {result.generation_time:.1f}s")
            else:
                failed.append({"scene_id": scene_id, "error": result.error})
                logger.warning(f"  {scene_id}: FAILED - {result.error}")

        success = len(generated) > 0
        return AgentResult(
            success=success,
            data={
                "generated": generated,
                "failed": failed,
                "total_generated": len(generated),
                "total_failed": len(failed),
                "total_time_s": round(total_time, 1),
            },
            next_state="VISUAL_QA" if success else "FAILED",
        )
