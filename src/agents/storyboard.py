"""Storyboard Agent — divides script into scenes with real timestamps (§33-34).

Responsibility: Align scenes to narration timestamps (not fixed intervals)
Input: narration.txt + TTS SentenceBoundary timestamps
Output: storyboard/scenes.json (scene schema §34)
Constraints: Semantic division (not 5s/sentence) (§33); real timestamps from audio (§32)
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from src.agents.base import AgentResult, BaseAgent

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
        audio_duration_s: float = 0.0,
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

        scenes = []
        humans_created = False

        sol_scenes = []
        sol_plan_path = os.environ.get("STUDIO_SOL_PLAN_PATH", "")
        if sol_plan_path and Path(sol_plan_path).exists():
            try:
                sol_scenes = json.loads(Path(sol_plan_path).read_text(encoding="utf-8")).get("scenes", [])
            except (OSError, json.JSONDecodeError, TypeError) as e:
                logger.warning("Could not load GPT-5.6-SOL storyboard plan: %s", e)
        if sol_scenes and len(sol_scenes) != len(sentence_timestamps):
            logger.warning("SOL scenes (%d) != TTS timestamps (%d); using proportional alignment", len(sol_scenes), len(sentence_timestamps))

        for i, ts in enumerate(sentence_timestamps):
            scene_id = f"SC{i+1:03d}"
            text = ts["text"]
            timeline_start = 0.0 if i == 0 and audio_duration_s > 0 else float(ts["start"])
            if audio_duration_s > 0:
                timeline_end = (
                    float(sentence_timestamps[i + 1]["start"])
                    if i + 1 < len(sentence_timestamps)
                    else float(audio_duration_s)
                )
            else:
                timeline_end = float(ts["end"])
            timeline_duration = timeline_end - timeline_start

            sol_scene = None
            if sol_scenes:
                # Normally one SOL scene ends in one TTS sentence. If the TTS
                # provider groups boundaries differently, choose the nearest
                # proportional SOL scene rather than losing visual alignment.
                sol_index = min(len(sol_scenes) - 1, round(i * len(sol_scenes) / max(1, len(sentence_timestamps))))
                sol_scene = sol_scenes[sol_index]

            # Hard biblical visual continuity rules for Genesis 1-2.
            if sol_scene:
                sol_forbidden = [str(x).lower() for x in sol_scene.get("forbidden_characters", [])]
                sol_text = str(sol_scene.get("narration_pt", text)).lower()
                pre_human_forbidden = {
                    "seres humanos", "crianças", "rostos humanos", "silhuetas humanas",
                    "corpos humanos",
                }
                human_terms_present = any(term in sol_text for term in (
                    "adão", "adao", "eva", "primeiro homem", "o homem", "homem e mulher",
                    "primeira mulher", "casal",
                )) and "não havia homem" not in sol_text and "nao havia homem" not in sol_text
                sol_humans_allowed = human_terms_present or not any(item in pre_human_forbidden for item in sol_forbidden)
                declared_humans = any(
                    str(name).lower() in {"adão", "adao", "adam", "eva", "eve"}
                    for name in sol_scene.get("characters", [])
                )
                human_event = declared_humans or (
                    sol_humans_allowed and self._is_human_creation_event(sol_text)
                )
                humans_allowed = humans_created or human_event
            else:
                human_event = self._is_human_creation_event(text)
                humans_allowed = humans_created or human_event
            if human_event:
                humans_created = True

            # Determine visual strategy (§46-48)
            # For the pilot, most scenes are LOCAL_ANIMATED_STILL
            # Only scenes with high movement would be RUNPOD (identified later)
            importance = self._assess_importance(text)

            sol_characters = [str(name).lower() for name in sol_scene.get("characters", [])] if sol_scene else []
            scene = {
                "scene_id": scene_id,
                "narration": text,
                "start": timeline_start,
                "end": timeline_end,
                "duration": timeline_duration,
                "characters": (
                    (sol_characters or self._extract_characters(text))
                    if humans_allowed else (["deus"] if "deus" in text.lower() else [])
                ),
                "forbidden_characters": [] if humans_allowed else ["human", "person", "child", "man", "woman", "Adam", "Eve"],
                "humans_allowed": humans_allowed,
                "creation_phase": "after_human_creation" if humans_allowed else "before_human_creation",
                "location": self._infer_location(text),
                "emotion": self._infer_emotion(text),
                "action": text[:80],
                "importance": importance,
                "visual_strategy": "LOCAL_ANIMATED_STILL",
                "references": list(sol_scene.get("references", [])) if sol_scene else [],
                "camera": "slow_push_in",
                "qa_status": "PENDING",
            }
            
            # GPT-5.6-SOL supplied the paired visual plan. Do not regenerate
            # this scene from generic keywords or an unrelated sentence.
            if sol_scene and sol_scene.get("visual_prompt_en"):
                scene["image_prompt"] = str(sol_scene["visual_prompt_en"]).strip()
                scene["visual_action"] = sol_scene.get("visual_action_pt", "")
                scene["continuity_anchor"] = sol_scene.get("continuity_anchor", "")
                scene["source_model"] = "gpt-5.6-sol"
            else:
                scene["image_prompt"] = await self._build_image_prompt_async(scene)
            scene["negative_prompt"] = self._build_negative_prompt(sol_scene)
            scene["animation_prompt"] = self._build_animation_prompt(scene)
            
            scenes.append(scene)

        # Save storyboard
        if storyboard_dir:
            Path(storyboard_dir).mkdir(parents=True, exist_ok=True)
            payload = json.dumps({"scenes": scenes}, indent=2, ensure_ascii=False)
            (Path(storyboard_dir) / "scenes.json").write_text(payload, encoding="utf-8")

        return AgentResult(
            success=True,
            data={"scenes": scenes, "scene_count": len(scenes)},
            next_state="GENERATING_IMAGES",
        )

    def _is_human_creation_event(self, text: str) -> bool:
        """Return true only when narration has reached human creation.

        Mentions such as 'não havia homem' are explicitly excluded, because
        Genesis 2 describes that state before Adam was formed.
        """
        t = text.lower()
        if "não havia homem" in t or "nao havia homem" in t:
            return False
        return any(term in t for term in (
            "criou o homem", "criou homem e mulher", "homem e mulher os criou",
            "primeiro homem", "primeira mulher", "formou o homem", "formou a primeira mulher",
            "formou adão", "formou adao", "soprou" , "criou adão", "criou adao",
            "apresentou eva", "fez eva", "adão e eva", "adao e eva",
        ))

    def _build_animation_prompt(self, scene: dict) -> str:
        """Describe only motion that is visible in this exact scene."""
        base = "gentle camera movement matching the described action"
        if not scene.get("humans_allowed"):
            return base + ", animate only light, water, sky, plants, stars, birds, or animals; no humans"
        if any(c in scene.get("characters", []) for c in ("adão", "eva")):
            return base + ", subtle leaf movement and gentle breathing; preserve established child-safe modest coverage or garments"
        return base

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
            if name.lower() in text.lower() and name.lower() not in characters:
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
        from src.style_guide import build_full_prompt
        
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
        visual_desc = await self._narration_to_visual(
            narration,
            characters,
            location,
            mood,
            humans_allowed=scene.get("humans_allowed", True),
            forbidden_characters=scene.get("forbidden_characters", []),
        )
        
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
    
    async def _narration_to_visual(
        self,
        narration: str,
        characters: list[str],
        location: str,
        mood: str,
        humans_allowed: bool = True,
        forbidden_characters: list[str] | None = None,
    ) -> str:
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
                
                rules = (
                    "ABSOLUTE RULE: no humans, people, human faces, children, Adam, or Eve may appear."
                    if not humans_allowed else
                    "If Adam or Eve appear, they must be unclothed with no garments; use a distant, child-safe, non-sexual composition with foliage covering intimate areas."
                )
                prompt = f"""Convert this Portuguese children's Bible narration into a concise English visual scene description for AI image generation.

Narration (Portuguese): "{narration}"
{char_hint}
{loc_hint}
Mood: {mood}
Visual safety rule: {rules}

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
            except (RuntimeError, ValueError, TimeoutError) as e:
                logger.warning(f"LLM visual description failed, using fallback: {e}")
        
        # Fallback: simple keyword-based visual description
        return self._fallback_visual(narration, characters, location, humans_allowed=humans_allowed)
    
    def _fallback_visual(
        self,
        narration: str,
        characters: list[str],
        location: str,
        humans_allowed: bool = True,
    ) -> str:
        """Fallback visual description from narration keywords (no LLM)."""
        text_lower = narration.lower()
        visuals = []
        
        if "não havia homem" in text_lower or "nao havia homem" in text_lower:
            visuals.append("mist rising from moist earth in the empty garden, no humans")
        elif any(term in text_lower for term in ("criou o homem", "homem e mulher", "ser humano", "imagem de deus")):
            visuals.append("two gentle unclothed human silhouettes appearing in divine golden light, distant child-safe framing, foliage covering intimate areas, no clothing")
        elif any(term in text_lower for term in ("formou adão", "formou adao", "soprou", "pó da terra", "po da terra")):
            visuals.append("God's radiant light shaping a human figure from clay-like dust and giving it life, distant child-safe framing, no clothing")
        elif any(term in text_lower for term in ("adão e eva", "adao e eva", "apresentou eva", "fez eva")):
            visuals.append("unclothed Adam and Eve together in the Garden of Eden, gentle faces, distant child-safe framing, large leaves covering intimate areas, no clothing")
        elif any(term in text_lower for term in ("sem forma", "vazia", "trevas", "abismo")):
            visuals.append("formless dark void above still deep waters, soft mist, no land, plants, animals, or humans")
        elif "luz" in text_lower or "primeiro dia" in text_lower:
            visuals.append("golden divine light emerging from darkness")
        elif "águas" in text_lower or "aguas" in text_lower or "céu" in text_lower or "ceu" in text_lower or "segundo dia" in text_lower:
            visuals.append("blue waters separating from sky, clouds forming above the waters")
        elif "plantas" in text_lower or "árvores" in text_lower or "arvores" in text_lower or "terceiro dia" in text_lower:
            visuals.append("green dry land rising from water, colorful plants, grass and fruit trees growing")
        elif "sol" in text_lower or "lua" in text_lower or "estrelas" in text_lower or "quarto dia" in text_lower:
            visuals.append("bright sun, glowing moon and stars appearing across a colorful sky")
        elif "peixes" in text_lower or "aves" in text_lower or "quinto dia" in text_lower:
            visuals.append("colorful fish swimming in blue ocean, friendly birds flying across the sky")
        elif "animais" in text_lower or "feras" in text_lower or "gado" in text_lower:
            visuals.append("friendly cartoon land animals gathering in a green paradise, no humans")
        elif "sétimo dia" in text_lower or "setimo dia" in text_lower or "descansou" in text_lower:
            visuals.append("peaceful completed creation beneath a warm sunset, calm resting scene, no humans")
        elif "deus" in text_lower or "criou" in text_lower:
            visuals.append("divine light shining over the specific creation described in the narration")
        else:
            visuals.append("the exact biblical creation action described, shown clearly in a beautiful child-safe cartoon scene")

        if not humans_allowed:
            visuals.append("strictly no humans or human-like figures")
        elif any(name in text_lower for name in ("adão", "adao", "eva")):
            visuals.append("Adam and Eve unclothed, child-safe distant framing, foliage covering intimate areas, no clothing")
        
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
        visual_desc = self._fallback_visual(
            narration,
            characters,
            location,
            humans_allowed=scene.get("humans_allowed", True),
        )

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
    
    def _build_negative_prompt(self, sol_scene: dict | None = None) -> str:
        """Get the negative prompt from style guide."""
        if sol_scene and "3d" in str(sol_scene.get("visual_prompt_en", "")).lower():
            return (
                "photorealistic, realistic skin, scary, violent, dark horror, adult themes, "
                "weapon violence, blood, gore, low quality, blurry, distorted, deformed faces, "
                "extra limbs, duplicate people, watermark, text, signature, ugly, creepy, "
                "explicit nudity, exposed intimate areas, sexualized pose, anatomical detail"
            )
        from src.style_guide import NEGATIVE_PROMPT
        return NEGATIVE_PROMPT
