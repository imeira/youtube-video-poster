"""Storyboard Agent — divides script into scenes with real timestamps (§33-34).

Responsibility: Align scenes to narration timestamps (not fixed intervals)
Input: narration.txt + TTS SentenceBoundary timestamps
Output: storyboard/scenes.json (scene schema §34)
Constraints: Semantic division (not 5s/sentence) (§33); real timestamps from audio (§32)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from src.agents.base import BaseAgent, AgentResult

logger = logging.getLogger(__name__)


class StoryboardAgent(BaseAgent):
    """Creates semantic scenes aligned to real narration timestamps (§33-34)."""

    # §40-41: Visual style base
    STYLE_PREFIX = ("stylized 3d animation, warm cinematic lighting, "
                    "children's book illustration, family-friendly, high quality, ")

    def __init__(self, llm_provider=None):
        super().__init__(name="Storyboard")
        self._llm = llm_provider

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
            # Use async LLM-powered prompt builder for English visual descriptions
            scene["image_prompt"] = await self._build_image_prompt_async(scene)
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

    async def _build_image_prompt_async(self, scene: dict) -> str:
        """Build image prompt using LLM to convert narration → visual description in English.
        
        CRITICAL: SD1.5 does NOT understand Portuguese. The narration text must be
        converted to a visual scene description in English before being used as
        an image generation prompt. Otherwise the model ignores the prompt and
        generates random images.
        """
        from src.character_bible import build_character_prompt
        from src.style_guide import build_full_prompt, BASE_STYLE
        
        narration = scene.get("narration", "")
        characters = scene.get("characters", [])
        location = scene.get("location", "")
        emotion = scene.get("emotion", "calm")
        
        mood_map = {
            "calm": "peaceful",
            "joy": "joyful",
            "awe": "miraculous",
            "suspense": "dramatic",
        }
        mood = mood_map.get(emotion, "peaceful")
        
        char_desc = build_character_prompt(characters)
        
        # Use LLM to convert Portuguese narration → English visual description
        visual_desc = await self._narration_to_visual(narration, characters, location, mood)
        
        # Build the full styled prompt with the ENGLISH visual description
        prompts = build_full_prompt(
            scene_description=visual_desc,
            characters=characters,
            location=location,
            mood=mood,
            lighting="divine" if "deus" in characters or "criou" in narration.lower() else "day",
            camera="medium",
            character_descriptions=char_desc,
        )
        
        return prompts["prompt"]
    
    async def _narration_to_visual(self, narration: str, characters: list[str], location: str, mood: str) -> str:
        """Convert Portuguese narration into an English visual scene description for SD1.5.

        Uses LLM with rate limiting (asyncio.sleep between calls to avoid 429).
        Falls back to keyword-based description if LLM is unavailable or rate-limited.
        """
        if self._llm and getattr(self._llm, "available", lambda: False)():
            try:
                # Rate limit: small delay between LLM calls to avoid 429
                import asyncio
                await asyncio.sleep(0.5)

                char_hint = f"Characters present: {', '.join(characters)}" if characters else "No specific characters"
                loc_hint = f"Location: {location}" if location else ""
                
                prompt = f"""Convert this Portuguese children's Bible narration into a concise English visual scene description for AI image generation.

Narration (Portuguese): "{narration}"
{char_hint}
{loc_hint}
Mood: {mood}

Write a SINGLE concise English sentence (max 30 words) describing what the IMAGE should show — the visual scene, characters' actions, and setting. Do NOT translate the narration. Describe only what you would SEE in the picture.

Example input: "Deus criou a luz no primeiro dia"
Example output: "Golden divine light emerging from darkness, brilliant rays piercing through void, heavenly creation scene"

Example input: "Deus criou os peixes e as aves no quinto dia"
Example output: "Colorful cartoon fish swimming in blue ocean, friendly birds flying above, vibrant underwater and sky scene"

Visual description:"""
                
                result = await self._llm.complete(
                    prompt=prompt,
                    system="You convert children's Bible narration into visual scene descriptions for AI image generation. Always respond in English with a single concise sentence.",
                    max_tokens=80,
                    temperature=0.6,
                )
                # Clean up — take only first sentence
                desc = result.strip().split('.')[0].strip()
                if desc:
                    return desc
            except Exception as e:
                logger.warning(f"LLM visual description failed, using fallback: {e}")
        
        # Fallback: simple keyword-based visual description
        return self._fallback_visual(narration, characters, location)
    
    def _fallback_visual(self, narration: str, characters: list[str], location: str) -> str:
        """Fallback visual description from narration keywords (no LLM)."""
        text_lower = narration.lower()
        visuals = []
        
        if "luz" in text_lower or "primeiro dia" in text_lower:
            visuals.append("golden divine light emerging from darkness")
        elif "águas" in text_lower or "céu" in text_lower or "segundo dia" in text_lower:
            visuals.append("blue waters separating from sky, clouds forming above ocean")
        elif "terra" in text_lower or "plantas" in text_lower or "terceiro dia" in text_lower:
            visuals.append("green earth rising from water, colorful plants and trees growing")
        elif "sol" in text_lower or "lua" in text_lower or "estrelas" in text_lower:
            visuals.append("bright sun, moon and stars in colorful sky")
        elif "peixes" in text_lower or "aves" in text_lower or "quinto dia" in text_lower:
            visuals.append("colorful cartoon fish in blue ocean, friendly birds flying in sky")
        elif "animais" in text_lower or "humanos" in text_lower or "sexto dia" in text_lower:
            visuals.append("friendly cartoon animals and happy children in green paradise")
        elif "descansou" in text_lower or "sétimo dia" in text_lower:
            visuals.append("peaceful sunset over beautiful creation, calm resting scene")
        elif "deus" in text_lower or "criou" in text_lower:
            visuals.append("divine light shining over beautiful creation landscape")
        else:
            visuals.append("beautiful biblical landscape with warm colors")
        
        return ", ".join(visuals)
    
    def _build_image_prompt(self, scene: dict) -> str:
        """Synchronous fallback — DO NOT use in async context (use _build_image_prompt_async)."""
        from src.character_bible import build_character_prompt
        from src.style_guide import build_full_prompt
        
        narration = scene.get("narration", "")
        characters = scene.get("characters", [])
        location = scene.get("location", "")
        emotion = scene.get("emotion", "calm")
        
        mood_map = {"calm": "peaceful", "joy": "joyful", "awe": "miraculous", "suspense": "dramatic"}
        mood = mood_map.get(emotion, "peaceful")
        char_desc = build_character_prompt(characters)
        
        # Use fallback visual (no LLM in sync mode)
        visual_desc = self._fallback_visual(narration, characters, location)
        
        prompts = build_full_prompt(
            scene_description=visual_desc,
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
