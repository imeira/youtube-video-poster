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
from src.agents.captions import CaptionsAgent
from src.agents.thumbnail import ThumbnailAgent
from src.agents.metadata import MetadataAgent
from src.agents.duration_planner import DurationPlannerAgent
from src.telegram.approval_gate import TelegramApprovalGate, format_decision_message
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
        self.captions = CaptionsAgent()
        self.thumbnail = ThumbnailAgent()
        # LLM provider is optional — metadata falls back to templates if unavailable
        try:
            from src.providers.llm.openrouter_provider import OpenRouterLLMProvider
            llm = OpenRouterLLMProvider()
            if not llm.available():
                llm = None
        except Exception:
            llm = None
        self.metadata = MetadataAgent(llm_provider=llm)
        self._episodes: dict[str, dict] = {}  # in-memory cache

    async def start_episode(
        self,
        theme: str,
        language: str = "",
        channel: str = "",
        episode_id: str = "",
        require_telegram_approval: bool = False,
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
        result = await self._run_preproduction(
            episode_id, theme, fs, state, guard,
            require_telegram_approval=require_telegram_approval,
        )

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
        require_telegram_approval: bool = False,
    ) -> dict[str, Any]:
        """Run pre-production: research → duration plan → WAITING_PLAN_APPROVAL.

        §98: Steps 1-5 (analyze, research, identify, duration, budget).
        §18-21: Adaptive duration based on story complexity, NOT fixed.
        §4/§7/§8: Budget gate with human approval before expensive generation.
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

        # Step 4-5: Adaptive duration planning (§18-21) — NOT a fixed duration.
        # Analyzes story complexity (event count, reference span) to recommend
        # 3-15 min, following the categories in §18.
        state.transition_to(EpisodeState.PLANNING, agent=self.name)
        state.save(fs.paths.state_json)

        duration_planner = DurationPlannerAgent(
            budget_hard_limit=self.config.budget.hard_limit_usd,
            budget_target=self.config.budget.target_usd,
        )
        duration_plan = duration_planner.plan(theme, research_result.data)
        budget_check = duration_planner.check_budget(duration_plan)

        plan["duration_plan"] = duration_plan.to_dict()
        plan["budget_check"] = budget_check

        # Transition to WAITING_PLAN_APPROVAL (§95)
        state.transition_to(EpisodeState.WAITING_PLAN_APPROVAL, agent=self.name, note="Pre-production plan ready")
        state.save(fs.paths.state_json)

        # Save plan
        import json
        with open(fs.paths.plan_json, "w", encoding="utf-8") as f:
            json.dump(plan, f, indent=2, ensure_ascii=False)

        # Save costs
        guard.ledger.save(fs.paths.costs_json)

        # §4/§7/§8: If cost exceeds budget, this MUST be surfaced for human approval
        # before any paid generation begins. The report is always produced (§20);
        # whether we block here depends on require_telegram_approval (pilot runs
        # may auto-approve locally, but production runs must gate on Telegram).
        report_text = duration_plan.format_report(self.config.budget.hard_limit_usd)
        plan["report_text"] = report_text

        if require_telegram_approval:
            gate = TelegramApprovalGate()
            if not budget_check["within_budget"]:
                options = {
                    alt["option"]: f"{alt['title']} — {alt['description']} (~${alt['estimated_cost']:.2f})"
                    for alt in budget_check["alternatives"]
                }
                message = format_decision_message(
                    episode_title=theme,
                    stage="Pré-produção — orçamento",
                    situation=f"Custo máximo estimado (${duration_plan.cost_max_usd:.2f}) excede "
                              f"o limite configurado (${self.config.budget.hard_limit_usd:.2f}).",
                    analysis=duration_plan.justification,
                    options=options,
                    recommendation=f"Opção {list(options.keys())[0]} recomendada.",
                )
                approval = await gate.request_approval(
                    message, valid_responses=list(options.keys()) + ["CANCELAR"],
                )
                plan["budget_approval"] = {
                    "approved": approval.approved,
                    "response": approval.response,
                    "timed_out": approval.timed_out,
                }
                if not approval.approved:
                    state.transition_to(EpisodeState.PAUSED, agent=self.name,
                                         note=f"Budget approval not granted: {approval.reason}")
                    state.save(fs.paths.state_json)
                    return plan
            else:
                # Even within budget, send the plan for visibility (§20) but
                # don't block unless explicitly configured to require sign-off.
                gate.send_message(f"📋 Plano de produção pronto:\n\n{report_text}")

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
            target_duration_s=plan.get("duration_plan", {}).get("recommended_duration_s", 180),
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

        # Step 12b: Finishing — captions, thumbnail, metadata (§31, §91, §92-93)
        # These are non-fatal: a failure here logs but does not fail the episode.
        theme = fs.paths.request_json  # placeholder, real theme loaded below
        import json as _json
        with open(fs.paths.request_json, encoding="utf-8") as f:
            _req = _json.load(f)
        theme_str = _req.get("theme", "")
        language = _req.get("language", "pt-BR")

        # Captions (§31-32: from real narration timestamps)
        captions_result = await self.captions.run(
            episode_id=episode_id,
            sentence_timestamps=audio_result.data.get("sentence_timestamps"),
            word_timestamps=audio_result.data.get("word_timestamps"),
            narration=narration,
            subtitles_dir=str(fs.paths.subtitles_dir),
        )
        captions_files = captions_result.data.get("files", {}) if captions_result.success else {}

        # Thumbnail (§91)
        headline = research_data.get("story", theme_str)
        thumb_result = await self.thumbnail.run(
            episode_id=episode_id,
            images=image_result.data["generated"],
            scenes=scenes,
            headline=headline,
            thumbnails_dir=str(fs.paths.thumbnails_dir),
        )
        thumbnail_path = thumb_result.data.get("thumbnail_path", "") if thumb_result.success else ""

        # Metadata (§92-93)
        meta_result = await self.metadata.run(
            episode_id=episode_id,
            theme=theme_str,
            research_data=research_data,
            scenes=scenes,
            language=language,
            metadata_dir=str(fs.paths.metadata_dir),
            captions_files=captions_files,
            thumbnail_path=thumbnail_path,
        )
        metadata = meta_result.data.get("metadata", {}) if meta_result.success else {}

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
            "captions": captions_files,
            "thumbnail": thumbnail_path,
            "title": metadata.get("title", ""),
            "playlist": metadata.get("playlist", ""),
            "chapters": len(metadata.get("chapters", [])),
            "tags_count": len(metadata.get("tags", [])),
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
