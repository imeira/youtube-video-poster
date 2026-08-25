"""Thumbnail Agent — composes YouTube thumbnails from a hero image (§91).

Responsibility: Generate thumbnail with headline text overlay.
Input: scene images + episode theme, importance ranking
Output: thumbnails/thumbnail.png (1280x720) + variations
Constraints:
  §91: emotion, simplicity, mobile readability, main character, contrast,
       curiosity. Avoid misleading clickbait.
"""

from __future__ import annotations

import os
from pathlib import Path

from src.agents.base import BaseAgent, AgentResult

# YouTube recommended thumbnail size
THUMB_W, THUMB_H = 1280, 720

# Windows font candidates (bold/impactful for mobile readability §91)
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/ariblk.ttf",   # Arial Black
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


class ThumbnailAgent(BaseAgent):
    """Composes a YouTube thumbnail from the strongest scene image (§91)."""

    def __init__(self):
        super().__init__(name="Thumbnail")

    async def run(
        self,
        episode_id: str,
        images: list[dict] | None = None,
        scenes: list[dict] | None = None,
        headline: str = "",
        thumbnails_dir: str = "",
        **kwargs,
    ) -> AgentResult:
        """Generate the episode thumbnail (§91).

        Args:
            images: [{scene_id, image_path}] from ImageGenAgent.
            scenes: storyboard scenes (for importance-based hero selection).
            headline: Short punchy headline text (<= ~30 chars for mobile).
            thumbnails_dir: output directory.
        """
        if not images:
            return AgentResult(success=False, error="No images provided for thumbnail")

        hero_path = self._select_hero(images, scenes)
        if not hero_path or not os.path.exists(hero_path):
            return AgentResult(success=False, error=f"Hero image not found: {hero_path}")

        thumb_dir = Path(thumbnails_dir) if thumbnails_dir else None
        if thumb_dir:
            thumb_dir.mkdir(parents=True, exist_ok=True)

        try:
            out_path = self._compose(hero_path, headline, thumb_dir)
        except Exception as e:
            return AgentResult(success=False, error=f"Thumbnail composition failed: {e}")

        return AgentResult(
            success=True,
            data={
                "thumbnail_path": out_path,
                "hero_scene_image": hero_path,
                "headline": headline,
                "size": f"{THUMB_W}x{THUMB_H}",
            },
            next_state="",
        )

    def _select_hero(self, images: list[dict], scenes: list[dict] | None) -> str:
        """Pick the hero image — prefer highest-importance scene (§91 main character)."""
        if scenes:
            rank = {"CRITICAL": 3, "HIGH": 2, "NORMAL": 1, "LOW": 0}
            img_by_scene = {img["scene_id"]: img["image_path"] for img in images}
            best_scene = None
            best_rank = -1
            for scene in scenes:
                sid = scene.get("scene_id")
                r = rank.get(scene.get("importance", "NORMAL"), 1)
                # Prefer scenes with characters present
                if scene.get("characters"):
                    r += 1
                if sid in img_by_scene and r > best_rank:
                    best_rank = r
                    best_scene = sid
            if best_scene:
                return img_by_scene[best_scene]
        # Fallback: first image
        return images[0]["image_path"]

    def _load_font(self, size: int):
        from PIL import ImageFont
        for fp in _FONT_CANDIDATES:
            if os.path.exists(fp):
                return ImageFont.truetype(fp, size)
        return ImageFont.load_default()

    def _compose(self, hero_path: str, headline: str, thumb_dir: Path | None) -> str:
        from PIL import Image, ImageDraw, ImageFilter

        # Load and cover-crop hero to 1280x720
        hero = Image.open(hero_path).convert("RGB")
        hero = self._cover_resize(hero, THUMB_W, THUMB_H)

        draw = ImageDraw.Draw(hero, "RGBA")

        if headline:
            headline = headline.upper().strip()
            # Fit font size to width
            font_size = 96
            font = self._load_font(font_size)
            max_text_w = THUMB_W - 120
            while font_size > 40:
                bbox = draw.textbbox((0, 0), headline, font=font)
                if (bbox[2] - bbox[0]) <= max_text_w:
                    break
                font_size -= 6
                font = self._load_font(font_size)

            bbox = draw.textbbox((0, 0), headline, font=font)
            text_w = bbox[2] - bbox[0]
            text_h = bbox[3] - bbox[1]
            x = (THUMB_W - text_w) // 2
            y = THUMB_H - text_h - 90

            # Darkened band behind text for contrast (§91 mobile readability)
            band_pad = 30
            draw.rectangle(
                [0, y - band_pad, THUMB_W, y + text_h + band_pad],
                fill=(0, 0, 0, 140),
            )

            # Thick outline for readability
            outline = 6
            for dx in range(-outline, outline + 1, 2):
                for dy in range(-outline, outline + 1, 2):
                    draw.text((x + dx, y + dy), headline, font=font, fill=(0, 0, 0, 255))
            draw.text((x, y), headline, font=font, fill=(255, 221, 51, 255))  # warm yellow

        out_path = str((thumb_dir / "thumbnail.png") if thumb_dir else Path(hero_path).parent / "thumbnail.png")
        hero.save(out_path, "PNG")
        return out_path

    @staticmethod
    def _cover_resize(img, target_w: int, target_h: int):
        """Resize + center-crop to fully cover target dimensions."""
        from PIL import Image
        src_w, src_h = img.size
        scale = max(target_w / src_w, target_h / src_h)
        new_w, new_h = int(src_w * scale), int(src_h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - target_w) // 2
        top = (new_h - target_h) // 2
        return img.crop((left, top, left + target_w, top + target_h))
