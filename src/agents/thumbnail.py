"""Child-safe, contract-driven YouTube thumbnail composition."""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from src.agents.base import AgentResult, BaseAgent

THUMB_W, THUMB_H = 1280, 720
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/ariblk.ttf",
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


class ThumbnailContractError(ValueError):
    """Raised when thumbnail copy violates a child-safe, hashable contract."""


@dataclass(frozen=True)
class ThumbnailContract:
    """Three exact layers required for a final child-safe thumbnail."""

    headline: str
    title: str
    book_subtitle: str
    required_book_subtitle: str | None = None

    _BANNED_TERMS = (
        "SEGREDO", "PROIBIDO", "CHOCANTE", "CHOQUE", "SÓ HOJE", "URGENTE",
        "ANTES QUE SEJA TARDE", "VOCÊ NÃO VAI ACREDITAR", "VOCE NAO VAI ACREDITAR",
    )

    def __post_init__(self) -> None:
        layers = self.layers
        if any(not layer.strip() for layer in layers):
            raise ThumbnailContractError("Exactly three non-empty text layers are required")
        if any(unicodedata.normalize("NFC", layer) != layer for layer in layers):
            raise ThumbnailContractError("Thumbnail copy must be normalized as NFC")
        if self.required_book_subtitle and self.book_subtitle != self.required_book_subtitle:
            raise ThumbnailContractError("Biblical book subtitle does not match the episode contract")
        if any(term in " ".join(layers).upper() for term in self._BANNED_TERMS):
            raise ThumbnailContractError("Child-unsafe clickbait is not allowed")

    @property
    def layers(self) -> tuple[str, str, str]:
        return (self.headline, self.title, self.book_subtitle)


class ThumbnailAgent(BaseAgent):
    """Composes a thumbnail while preserving exact approved copy."""

    def __init__(self):
        super().__init__(name="Thumbnail")

    async def run(
        self,
        episode_id: str,
        images: list[dict] | None = None,
        scenes: list[dict] | None = None,
        headline: str = "",
        subtitle: str = "",
        book_subtitle: str = "",
        thumbnails_dir: str = "",
        copy_contract: ThumbnailContract | None = None,
        **kwargs,
    ) -> AgentResult:
        if not images:
            return AgentResult(success=False, error="No images provided for thumbnail")
        if copy_contract:
            headline, subtitle, book_subtitle = copy_contract.layers
        if not headline.strip():
            return AgentResult(success=False, error="Headline is required to render thumbnail text")
        if not book_subtitle.strip():
            return AgentResult(success=False, error="Biblical book subtitle is required for every thumbnail")

        hero_path = self._select_hero(images, scenes)
        if not hero_path or not os.path.exists(hero_path):
            return AgentResult(success=False, error=f"Hero image not found: {hero_path}")
        thumb_dir = Path(thumbnails_dir) if thumbnails_dir else None
        if thumb_dir:
            thumb_dir.mkdir(parents=True, exist_ok=True)
        try:
            out_path = self._compose(hero_path, headline, subtitle, book_subtitle, thumb_dir)
        except (OSError, ValueError) as exc:
            return AgentResult(success=False, error=f"Thumbnail composition failed: {exc}")
        return AgentResult(
            success=True,
            data={
                "thumbnail_path": out_path,
                "hero_scene_image": hero_path,
                "headline": headline,
                "subtitle": subtitle,
                "book_subtitle": book_subtitle,
                "layers": [headline, subtitle, book_subtitle],
                "size": f"{THUMB_W}x{THUMB_H}",
            },
            next_state="",
        )

    def _select_hero(self, images: list[dict], scenes: list[dict] | None) -> str:
        if scenes:
            rank = {"CRITICAL": 3, "HIGH": 2, "NORMAL": 1, "LOW": 0}
            img_by_scene = {image["scene_id"]: image["image_path"] for image in images}
            best_scene, best_rank = None, -1
            for scene in scenes:
                scene_id = scene.get("scene_id")
                score = rank.get(scene.get("importance", "NORMAL"), 1) + bool(scene.get("characters"))
                if scene_id in img_by_scene and score > best_rank:
                    best_scene, best_rank = scene_id, score
            if best_scene:
                return img_by_scene[best_scene]
        return images[0]["image_path"]

    def _load_font(self, size: int):
        from PIL import ImageFont

        for path in _FONT_CANDIDATES:
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
        return ImageFont.load_default()

    def _compose(self, hero_path: str, headline: str, subtitle: str, book_subtitle: str, thumb_dir: Path | None) -> str:
        from PIL import Image, ImageDraw

        hero = self._cover_resize(Image.open(hero_path).convert("RGB"), THUMB_W, THUMB_H)
        draw = ImageDraw.Draw(hero, "RGBA")
        headline, subtitle, book_subtitle = (headline.strip(), subtitle.strip(), book_subtitle.strip())
        font = self._fit_font(draw, headline, 96, THUMB_W - 120)
        headline_box = draw.textbbox((0, 0), headline, font=font)
        headline_w, headline_h = headline_box[2] - headline_box[0], headline_box[3] - headline_box[1]
        subtitle_font = self._load_font(50) if subtitle else None
        book_font = self._load_font(38)
        subtitle_h = draw.textbbox((0, 0), subtitle, font=subtitle_font)[3] if subtitle_font else 0
        book_h = draw.textbbox((0, 0), book_subtitle, font=book_font)[3]
        y = THUMB_H - headline_h - subtitle_h - book_h - 135
        draw.rectangle([0, y - 30, THUMB_W, THUMB_H], fill=(0, 0, 0, 140))
        x = (THUMB_W - headline_w) // 2
        draw.text((x, y), headline, font=font, fill=(255, 221, 51, 255), stroke_width=6, stroke_fill=(0, 0, 0, 255))
        subtitle_y = y + headline_h + 22
        if subtitle_font:
            subtitle_w = draw.textbbox((0, 0), subtitle, font=subtitle_font)[2]
            draw.text(((THUMB_W - subtitle_w) // 2, subtitle_y), subtitle, font=subtitle_font, fill=(255, 255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0, 255))
        book_w = draw.textbbox((0, 0), book_subtitle, font=book_font)[2]
        draw.text(((THUMB_W - book_w) // 2, subtitle_y + subtitle_h + 14), book_subtitle, font=book_font, fill=(255, 221, 51, 255), stroke_width=3, stroke_fill=(0, 0, 0, 255))
        output = (thumb_dir / "thumbnail.png") if thumb_dir else Path(hero_path).parent / "thumbnail.png"
        hero.save(output, "PNG")
        return str(output)

    def _fit_font(self, draw, text: str, start: int, max_width: int):
        for size in range(start, 39, -6):
            font = self._load_font(size)
            box = draw.textbbox((0, 0), text, font=font)
            if box[2] - box[0] <= max_width:
                return font
        return self._load_font(40)

    @staticmethod
    def _cover_resize(image, target_w: int, target_h: int):
        from PIL import Image

        scale = max(target_w / image.width, target_h / image.height)
        resized = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
        left, top = (resized.width - target_w) // 2, (resized.height - target_h) // 2
        return resized.crop((left, top, left + target_w, top + target_h))
