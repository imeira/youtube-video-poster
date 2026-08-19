"""Storyboard Agent — divides script into scenes with real timestamps (§33-34).

Responsibility: Align scenes to narration timestamps (not fixed intervals)
Input: narration.txt + TTS SentenceBoundary timestamps
Output: storyboard/scenes.json (scene schema §34)
Constraints: Semantic division (not 5s/sentence) (§33); real timestamps from audio (§32)
"""

from __future__ import annotations

import json
from pathlib import Path

from src.agents.base import BaseAgent, AgentResult


class StoryboardAgent(BaseAgent):
    """Creates semantic scenes aligned to real narration timestamps (§33-34)."""

    # §40-41: Visual style base
    STYLE_PREFIX = ("stylized 3d animation, warm cinematic lighting, "
                    "children's book illustration, family-friendly, high quality, ")

    def __init__(self):
        super().__init__(name="Storyboard")

    async def run(
        self,
        episode_id: str,
        narration: str = "",
        sentence_timestamps: list[dict] | None = None,
        storyboard_dir: str = "",
        visual_style: str = "",
        **kwargs,
    ) -> AgentResult:
        """Create storyboard from narration + timestamps.

        §27: SCRIPT → TTS → AUDIO → TIMESTAMPS → STORYBOARD → VISUAIS
        §33: Semantic division — each visual change aligned to narration
        §34: Scene schema with all required fields
        """
        if not sentence_timestamps:
            return AgentResult(success=False, error="No timestamps provided")

        style = visual_style or self.STYLE_PREFIX
        scenes = []

        for i, ts in enumerate(sentence_timestamps):
            scene_id = f"SC{i+1:03d}"
            text = ts["text"]

            # Determine visual strategy (§46-48)
            # For the pilot, most scenes are LOCAL_ANIMATED_STILL
            # Only scenes with high movement would be RUNPOD (identified later)
            importance = self._assess_importance(text)

            scene = {
                "scene_id": scene_id,
                "narration": text,
                "start": ts["start"],
                "end": ts["end"],
                "duration": ts["duration"],
                "characters": self._extract_characters(text),
                "location": self._infer_location(text),
                "emotion": self._infer_emotion(text),
                "action": text[:80],
                "importance": importance,
                "visual_strategy": "LOCAL_ANIMATED_STILL",
                "references": [],
                "camera": "slow_push_in",
                "qa_status": "PENDING",
            }
            
            # Build prompts with Character Bible + Style Guide (§56-60)
            scene["image_prompt"] = self._build_image_prompt(scene)
            scene["negative_prompt"] = self._build_negative_prompt()
            scene["animation_prompt"] = f"gentle movement, {text[:60]}"
            
            scenes.append(scene)

        # Save storyboard
        if storyboard_dir:
            Path(storyboard_dir).mkdir(parents=True, exist_ok=True)
            with open(Path(storyboard_dir) / "scenes.json", "w", encoding="utf-8") as f:
                json.dump({"scenes": scenes}, f, indent=2, ensure_ascii=False)

        return AgentResult(
            success=True,
            data={"scenes": scenes, "scene_count": len(scenes)},
            next_state="GENERATING_IMAGES",
        )

    def _assess_importance(self, text: str) -> str:
        """§68: Classify scene importance (LOW/NORMAL/HIGH/CRITICAL)."""
        text_lower = text.lower()
        critical_keywords = ["batalha", "luta", "vitória", "milagre", "criação", "derrotou"]
        high_keywords = ["deus", "anjo", "gigante", "tempestade", "arca", "leões", "peixe"]

        for kw in critical_keywords:
            if kw in text_lower:
                return "CRITICAL"
        for kw in high_keywords:
            if kw in text_lower:
                return "HIGH"
        return "NORMAL"

    def _extract_characters(self, text: str) -> list[str]:
        """Extract character names from narration."""
        characters = []
        known = ["Deus", "Davi", "Golias", "Jonas", "Daniel", "Noé", "Saul", "Adão", "Eva"]
        for name in known:
            if name.lower() in text.lower():
                if name not in characters:
                    characters.append(name.lower())
        return characters

    def _infer_location(self, text: str) -> str:
        """Infer the scene location from narration."""
        text_lower = text.lower()
        locations = {
            "campo": "campo verde", "mar": "mar", "navio": "navio",
            "cova": "cova dos leões", "arca": "arca", "céu": "céu",
            "terra": "terra", "praia": "praia", "nínive": "Nínive",
        }
        for keyword, location in locations.items():
            if keyword in text_lower:
                return location
        return "paisagem natural"

    def _infer_emotion(self, text: str) -> str:
        """Infer the emotional tone of a scene."""
        text_lower = text.lower()
        if any(w in text_lower for w in ["medo", "tempestade", "escuridão"]):
            return "suspense"
        if any(w in text_lower for w in ["alegria", "vitória", "salvou", "protegeu"]):
            return "joy"
        if any(w in text_lower for w in ["orde", "criou", "disse"]):
            return "awe"
        return "calm"

    def _build_image_prompt(self, scene: dict) -> str:
        """Build the image generation prompt with Character Bible + Style Guide (§40-41, §56-60).
        
        Uses character_bible.py and style_guide.py for consistent visual style.
        """
        from src.character_bible import build_character_prompt
        from src.style_guide import build_full_prompt
        
        narration = scene.get("narration", "")
        characters = scene.get("characters", [])
        location = scene.get("location", "")
        emotion = scene.get("emotion", "calm")
        
        # Map emotion to mood
        mood_map = {
            "calm": "peaceful",
            "joy": "joyful",
            "awe": "miraculous",
            "suspense": "dramatic",
        }
        mood = mood_map.get(emotion, "peaceful")
        
        # Get character visual descriptions
        char_desc = build_character_prompt(characters)
        
        # Build the full styled prompt
        prompts = build_full_prompt(
            scene_description=narration,
            characters=characters,
            location=location,
            mood=mood,
            lighting="divine" if "deus" in characters or "criou" in narration.lower() else "day",
            camera="medium",
            character_descriptions=char_desc,
        )
        
        return prompts["prompt"]
    
    def _build_negative_prompt(self) -> str:
        """Get the negative prompt from style guide."""
        from src.style_guide import NEGATIVE_PROMPT
        return NEGATIVE_PROMPT
