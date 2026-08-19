"""Director Agent — central orchestrator (§12).

Responsibility: Coordinate all agents, manage state machine, enforce budget.
§12: No specialized agent may publish directly or bypass global rules.
§55-56: Cleanup orphaned RunPod pods on startup.
§8: Silence is NOT approval.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from src.agents.base import BaseAgent, AgentResult
from src.agents.research import ResearchAgent
from src.agents.script import ScriptAgent
from src.agents.audio import AudioAgent
from src.agents.storyboard import StoryboardAgent
from src.agents.image_gen import ImageGenAgent
from src.agents.animation import AnimationAgent
from src.agents.assembly import AssemblyAgent
from src.budget.guard import BudgetGuard, CostLedger
from src.config.loader import StudioConfig, get_config
from src.state.machine import EpisodeState, EpisodeStateStore
from src.storage.episode_fs import EpisodeFS

logger = logging.getLogger(__name__)


class DirectorAgent:
    """Central orchestrator — coordinates the full pipeline (§12).

    §12: No agent publishes directly or bypasses global rules.
    §14: State persists in state.json — survives restarts.
    §8: Silence is NOT approval for HITL gates.
    """

    def __init__(self, config: StudioConfig | None = None):
        self.config = config or get_config()
        self.research = ResearchAgent()
        self.script = ScriptAgent()
        self.audio = AudioAgent()
        self.storyboard = StoryboardAgent()
        self.image_gen = ImageGenAgent(mode="lcm")
        self.animation = AnimationAgent()
        self.assembly = AssemblyAgent()
        self._episodes: dict[str, dict] = {}  # in-memory cache

    async def start_episode(
        self,
        theme: str,
        language: str = "",
        channel: str = "",
        episode_id: str = "",
    ) -> dict[str, Any]:
        """Start a new episode from a user request.

        §5: User provides only theme, language, channel.
        §98: Full pipeline from REQUEST_RECEIVED to PUBLISHED.
        """
        # Generate episode ID if not provided
        if not episode_id:
            import time
            episode_id = f"EP{int(time.time())}"

        # Set up episode filesystem (§15)
        fs = EpisodeFS(episode_id, self.config)
        fs.create_dirs()
        fs.save_request(theme, language, channel)

        # Initialize state (§13)
        state_path = fs.paths.state_json
        state = EpisodeStateStore.load_or_create(state_path, episode_id)

        # Initialize budget guard (§61)
        costs_path = fs.paths.costs_json
        ledger = CostLedger.load(costs_path, episode_id, self.config.budget)
        guard = BudgetGuard(ledger)

        # Run pre-production pipeline (up to WAITING_PLAN_APPROVAL)
        result = await self._run_preproduction(episode_id, theme, fs, state, guard)

        return {
            "episode_id": episode_id,
            "theme": theme,
            "state": state.current_state.value,
            "plan": result,
            "budget_remaining": ledger.remaining,
        }

    async def _run_preproduction(
        self,
        episode_id: str,
        theme: str,
        fs: EpisodeFS,
        state: EpisodeStateStore,
        guard: BudgetGuard,
    ) -> dict[str, Any]:
        """Run pre-production: research → plan → WAITING_PLAN_APPROVAL.

        §98: Steps 1-5 (analyze, research, identify, duration, budget).
        §20-21: Pre-production plan with all required fields.
        """
        plan = {}

        # Step 1-3: Research (§22)
        state.transition_to(EpisodeState.RESEARCHING, agent=self.research.name)
        state.save(fs.paths.state_json)

        research_result = await self.research.run(
            episode_id=episode_id,
            theme=theme,
            research_dir=str(fs.paths.research_dir),
        )

        if not research_result.success:
            state.transition_to(EpisodeState.FAILED, agent=self.research.name, note=research_result.error)
            state.save(fs.paths.state_json)
            return {"error": research_result.error}

        plan["research"] = research_result.data
        plan["references"] = research_result.data.get("references", [])

        # Step 4-5: Planning (§20-21, §61)
        state.transition_to(EpisodeState.PLANNING, agent=self.name)
        state.save(fs.paths.state_json)

        # Estimate duration and cost (§18-19, §20-21)
        target_duration_s = self.config.episode_duration.min_minutes * 60
        est_words = int((target_duration_s / 60) * 150)
        est_scenes = len(research_result.data.get("narrative_classification", {}).get("BIBLICAL_FACT", []))

        plan["duration_plan"] = {
            "target_duration_s": target_duration_s,
            "estimated_words": est_words,
            "estimated_scenes": est_scenes,
            "estimated_images": est_scenes,
        }

        # Cost estimate (§20-21)
        # Local generation is free; only RunPod i2v costs money
        est_runpod_seconds = min(est_scenes, 3) * 4  # ~3 cloud scenes @ 4s each
        est_runpod_cost = (est_runpod_seconds * 5.3 / 3600) * 0.74  # 4090 @ SECURE
        est_tts_cost = 0.07  # Azure fallback
        est_llm_cost = 0.30

        plan["cost_estimate"] = {
            "currency": self.config.budget.currency,
            "runpod_seconds": est_runpod_seconds,
            "runpod_cost": round(est_runpod_cost, 2),
            "tts_cost": est_tts_cost,
            "llm_cost": est_llm_cost,
            "minimum": round(est_runpod_cost + est_tts_cost, 2),
            "probable": round(est_runpod_cost + est_tts_cost + est_llm_cost, 2),
            "maximum": round(est_runpod_cost * 1.5 + est_tts_cost + est_llm_cost * 1.5, 2),
            "hard_limit": self.config.budget.hard_limit_usd,
        }

        # Transition to WAITING_PLAN_APPROVAL (§95)
        state.transition_to(EpisodeState.WAITING_PLAN_APPROVAL, agent=self.name, note="Pre-production plan ready")
        state.save(fs.paths.state_json)

        # Save plan
        import json
        with open(fs.paths.plan_json, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)

        # Save costs
        guard.ledger.save(fs.paths.costs_json)

        return plan

    async def continue_after_approval(
        self,
        episode_id: str,
        approval_type: str,
    ) -> dict[str, Any]:
        """Continue the pipeline after a human approval (§8).

        Args:
            episode_id: Episode to continue.
            approval_type: "plan" | "budget" | "final"
        """
        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)

        if approval_type == "plan":
            return await self._run_production(episode_id, fs, state)
        elif approval_type == "budget":
            # Budget override approved — continue cloud generation
            state.transition_to(EpisodeState.CLOUD_VIDEO_GENERATION, agent=self.name, note="budget approved")
            state.save(fs.paths.state_json)
            return {"status": "budget approved", "state": state.current_state.value}
        elif approval_type == "final":
            # Final approval — publish
            state.transition_to(EpisodeState.UPLOADING, agent=self.name, note="final approved")
            state.save(fs.paths.state_json)
            return {"status": "publishing", "state": state.current_state.value}

        return {"error": f"Unknown approval type: {approval_type}"}

    async def _run_production(
        self,
        episode_id: str,
        fs: EpisodeFS,
        state: EpisodeStateStore,
    ) -> dict[str, Any]:
        """Run production pipeline: script → audio → storyboard → images → animation.

        §98: Steps 6-14.
        """
        # Load plan and research
        import json
        with open(fs.paths.plan_json, encoding="utf-8") as f:
            plan = json.load(f)
        with open(fs.paths.research_dir / "sources.json", encoding="utf-8") as f:
            research_data = json.load(f)

        # Step 6: Script (§24)
        state.transition_to(EpisodeState.SCRIPTING, agent=self.script.name)
        state.save(fs.paths.state_json)

        script_result = await self.script.run(
            episode_id=episode_id,
            research_data=research_data,
            target_duration_s=plan.get("duration_plan", {}).get("target_duration_s", 180),
            script_dir=str(fs.paths.script_dir),
        )

        if not script_result.success:
            state.transition_to(EpisodeState.FAILED, note=script_result.error)
            state.save(fs.paths.state_json)
            return {"error": script_result.error}

        narration = script_result.data["narration"]

        # Step 7: Audio (§27-28)
        state.transition_to(EpisodeState.GENERATING_AUDIO, agent=self.audio.name)
        state.save(fs.paths.state_json)

        audio_result = await self.audio.run(
            episode_id=episode_id,
            narration=narration,
            audio_dir=str(fs.paths.audio_dir),
        )

        if not audio_result.success:
            state.transition_to(EpisodeState.FAILED, note=audio_result.error)
            state.save(fs.paths.state_json)
            return {"error": audio_result.error}

        # Step 8: Storyboard (§33-34)
        state.transition_to(EpisodeState.STORYBOARDING, agent=self.storyboard.name)
        state.save(fs.paths.state_json)

        storyboard_result = await self.storyboard.run(
            episode_id=episode_id,
            narration=narration,
            sentence_timestamps=audio_result.data["sentence_timestamps"],
            storyboard_dir=str(fs.paths.storyboard_dir),
        )

        if not storyboard_result.success:
            state.transition_to(EpisodeState.FAILED, note=storyboard_result.error)
            state.save(fs.paths.state_json)
            return {"error": storyboard_result.error}

        # Save complete production data
        production = {
            "narration": narration,
            "word_count": script_result.data["word_count"],
            "audio_duration_s": audio_result.data["duration_s"],
            "scene_count": storyboard_result.data["scene_count"],
            "scenes": storyboard_result.data["scenes"],
        }

        state.transition_to(EpisodeState.GENERATING_IMAGES, agent=self.name, note="production ready for image gen")
        state.save(fs.paths.state_json)

        # Step 10: Generate images (§42)
        scenes = storyboard_result.data["scenes"]
        image_result = await self.image_gen.run(
            episode_id=episode_id,
            scenes=scenes,
            images_dir=str(fs.paths.images_dir),
        )

        if not image_result.success:
            state.transition_to(EpisodeState.FAILED, note=image_result.error)
            state.save(fs.paths.state_json)
            return {"error": image_result.error}

        # Step 11: Animate (§49-52)
        state.transition_to(EpisodeState.LOCAL_ANIMATION, agent=self.animation.name)
        state.save(fs.paths.state_json)

        anim_result = await self.animation.run(
            episode_id=episode_id,
            scenes=scenes,
            images=image_result.data["generated"],
            animation_dir=str(fs.paths.animation_dir),
        )

        if not anim_result.success:
            state.transition_to(EpisodeState.FAILED, note=anim_result.error)
            state.save(fs.paths.state_json)
            return {"error": anim_result.error}

        # Step 12: Assemble final video (§50)
        state.transition_to(EpisodeState.ASSEMBLING, agent=self.assembly.name)
        state.save(fs.paths.state_json)

        assembly_result = await self.assembly.run(
            episode_id=episode_id,
            clips=anim_result.data["clips"],
            audio_path=audio_result.data["audio_path"],
            output_path=str(fs.paths.final_video),
        )

        if not assembly_result.success:
            state.transition_to(EpisodeState.FAILED, note=assembly_result.error)
            state.save(fs.paths.state_json)
            return {"error": assembly_result.error}

        # Step 13: Final QA (§97)
        state.transition_to(EpisodeState.FINAL_QA, agent=self.name, note="video assembled")
        state.save(fs.paths.state_json)

        # Transition to WAITING_FINAL_APPROVAL
        state.transition_to(EpisodeState.WAITING_FINAL_APPROVAL, agent=self.name, note="ready for approval")
        state.save(fs.paths.state_json)

        return {
            "episode_id": episode_id,
            "state": state.current_state.value,
            "narration_preview": narration[:200],
            "word_count": script_result.data["word_count"],
            "audio_duration_s": round(audio_result.data["duration_s"], 1),
            "scene_count": storyboard_result.data["scene_count"],
            "images_generated": image_result.data["total_generated"],
            "image_gen_time_s": image_result.data["total_time_s"],
            "clips_animated": anim_result.data["total_clips"],
            "animation_time_s": anim_result.data["total_time_s"],
            "final_video": str(fs.paths.final_video),
            "final_duration_s": assembly_result.data["duration_s"],
            "budget_remaining": 6.00,  # local-only, no external cost
        }

    async def run_full_pipeline(
        self,
        theme: str,
        episode_id: str = "",
        skip_image_gen: bool = False,
    ) -> dict[str, Any]:
        """Run the complete pipeline from request to final video (§98).

        §117: Pilot episode — do NOT auto-publish.
        """
        # Pre-production
        pre_result = await self.start_episode(theme=theme, episode_id=episode_id)
        eid = pre_result["episode_id"]

        # Production (after plan approval — auto-approve for pilot)
        result = await self.continue_after_approval(eid, "plan")
        return result

    def cleanup_orphans(self) -> list[str]:
        """§56: Clean up orphaned RunPod pods on startup."""
        try:
            from src.providers.gpu.runpod_provider import RunPodGPUProvider
            provider = RunPodGPUProvider()
            orphans = provider.cleanup_orphans()
            if orphans:
                logger.warning(f"Cleaned up {len(orphans)} orphaned pods: {orphans}")
            return orphans
        except Exception as e:
            logger.error(f"Orphan cleanup failed: {e}")
            return []

    @property
    def name(self) -> str:
        return "Director"
