"""Image Generation Agent — generates consistent stills for each scene (§42).

Responsibility: Generate images using SD1.5+LCM (fast mode)
Input: storyboard/scenes.json with image_prompt per scene
Output: images/SC<id>.png
Constraints: 4GB VRAM (B2-B4); LCM mode = 7.1s/image; fp16 all-GPU
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from src.agents.base import AgentResult, BaseAgent
from src.providers.image.local_sd15_provider import LocalSD15Provider

logger = logging.getLogger(__name__)


class ImageGenAgent(BaseAgent):
    """Generates images for all storyboard scenes (§42).

    B3: LCM mode = 7.1s/image @ 512x512
    B2: Standard mode = 38.8s/image @ 512x512
    """

    def __init__(
        self,
        mode: str = "lcm",
        provider_factory: Callable[[str], LocalSD15Provider] | None = None,
    ):
        super().__init__(name="ImageGen")
        self.mode = mode
        self._provider_factory = provider_factory or (lambda selected_mode: LocalSD15Provider(mode=selected_mode))
        self._providers: dict[str, LocalSD15Provider] = {}

    def _get_provider(self, mode: str | None = None) -> LocalSD15Provider:
        selected_mode = mode or self.mode
        if selected_mode not in self._providers:
            self._providers[selected_mode] = self._provider_factory(selected_mode)
        return self._providers[selected_mode]

    @staticmethod
    def _character_assets_root() -> Path:
        configured = os.environ.get("STUDIO_CHARACTER_ASSETS_DIR", "")
        if configured:
            return Path(configured)
        return Path(__file__).resolve().parents[2] / "assets" / "characters" / "creation"

    def _canonical_references(
        self,
        characters: list[str],
        images_dir: Path | None,
    ) -> tuple[list[str] | None, str | None]:
        """Resolve immutable Adam/Eve identities; combine them for shared scenes."""
        normalized = {str(name).lower() for name in characters}
        recurring = [name for name in ("adam", "eve") if name in normalized]
        if "adão" in normalized or "adao" in normalized:
            recurring.insert(0, "adam") if "adam" not in recurring else None
        if "eva" in normalized and "eve" not in recurring:
            recurring.append("eve")
        recurring = list(dict.fromkeys(recurring))
        if not recurring:
            return None, None

        root = self._character_assets_root()
        paths = [root / name / "face_v1.png" for name in recurring]
        missing = [str(path) for path in paths if not path.is_file()]
        if missing:
            return None, f"Canonical reference missing: {', '.join(missing)}"
        return [str(path) for path in paths], None

    @staticmethod
    def _valid_existing_image(path: Path) -> bool:
        if not path.is_file():
            return False
        try:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                return image.width > 0 and image.height > 0
        except (OSError, ValueError):
            return False

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

        images_dir_path = Path(images_dir) if images_dir else None
        if images_dir_path:
            images_dir_path.mkdir(parents=True, exist_ok=True)

        generated = []
        failed = []
        total_time = 0.0

        for scene in scenes:
            scene_id = scene["scene_id"]
            existing = images_dir_path / f"{scene_id}.png" if images_dir_path else None
            if existing and self._valid_existing_image(existing):
                generated.append({
                    "scene_id": scene_id,
                    "image_path": str(existing),
                    "seed": 0,
                    "generation_time": 0.0,
                    "reused": True,
                })
                logger.info("  %s: reused valid existing image", scene_id)
                continue
            prompt = scene.get("image_prompt", "")
            negative = scene.get("negative_prompt", "")
            seed = seed_base + hash(scene_id) % 10000

            references, reference_error = self._canonical_references(
                scene.get("characters", []), images_dir_path,
            )
            if reference_error:
                failed.append({"scene_id": scene_id, "error": reference_error})
                logger.warning("  %s: FAILED - %s", scene_id, reference_error)
                continue
            if references:
                error = (
                    "Canonical character scene requires an external reference-grounded image; "
                    "local IP-Adapter is disabled after visual QA failure"
                )
                failed.append({"scene_id": scene_id, "error": error})
                logger.warning("  %s: FAILED - %s", scene_id, error)
                continue
            selected_mode = self.mode
            provider = self._get_provider(selected_mode)
            width, height = 1024, 576

            logger.info(f"Generating image for {scene_id} (seed={seed})...")
            result = await provider.generate(
                prompt=prompt,
                negative_prompt=negative,
                width=width,
                height=height,
                reference_images=references,
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

        success = not failed and len(generated) == len(scenes)
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
