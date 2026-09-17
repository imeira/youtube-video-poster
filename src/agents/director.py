"""Director Agent — central orchestrator (§12).

Responsibility: Coordinate all agents, manage state machine, enforce budget.
§12: No specialized agent may publish directly or bypass global rules.
§55-56: Cleanup orphaned RunPod pods on startup.
§8: Silence is NOT approval.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.agents.animation import AnimationAgent
from src.agents.assembly import AssemblyAgent
from src.agents.audio import AudioAgent
from src.agents.captions import CaptionsAgent
from src.agents.duration_planner import DurationPlannerAgent
from src.agents.image_gen import ImageGenAgent
from src.agents.metadata import MetadataAgent
from src.agents.research import ResearchAgent
from src.agents.script import ScriptAgent
from src.agents.script_qa import ScriptQAAgent
from src.agents.storyboard import StoryboardAgent
from src.agents.thumbnail import ThumbnailAgent
from src.budget.guard import BudgetGuard, CostLedger
from src.config.loader import StudioConfig, get_config
from src.providers.llm.core_model_router import CoreModelRouter
from src.state.machine import EpisodeState, EpisodeStateStore
from src.storage.episode_fs import EpisodeFS
from src.telegram.approval_gate import TelegramApprovalGate, format_decision_message

logger = logging.getLogger(__name__)

_BIBLICAL_BOOKS = tuple(sorted({
    "Gênesis", "Êxodo", "Levítico", "Números", "Deuteronômio", "Josué",
    "Juízes", "Rute", "1 Samuel", "2 Samuel", "1 Reis", "2 Reis",
    "1 Crônicas", "2 Crônicas", "Esdras", "Neemias", "Ester", "Jó",
    "Salmos", "Salmo", "Provérbios", "Eclesiastes", "Cântico dos Cânticos",
    "Cantares", "Isaías", "Jeremias", "Lamentações", "Ezequiel", "Daniel",
    "Oseias", "Joel", "Amós", "Obadias", "Jonas", "Miqueias", "Naum",
    "Habacuque", "Sofonias", "Ageu", "Zacarias", "Malaquias", "Mateus",
    "Marcos", "Lucas", "João", "Atos", "Romanos", "1 Coríntios",
    "2 Coríntios", "Gálatas", "Efésios", "Filipenses", "Colossenses",
    "1 Tessalonicenses", "2 Tessalonicenses", "1 Timóteo", "2 Timóteo",
    "Tito", "Filemom", "Hebreus", "Tiago", "1 Pedro", "2 Pedro",
    "1 João", "2 João", "3 João", "Judas", "Apocalipse",
}, key=len, reverse=True))
_BIBLICAL_LOCATOR = re.compile(
    r"^\d+(?::\d+)?"
    r"(?:(?:\s*[–—-]\s*|\s+(?:e|a)\s+|,\s*)\d+(?::\d+)?)*$",
    re.IGNORECASE,
)


def _is_valid_biblical_reference(passage: str) -> bool:
    """Validate one or more Portuguese Bible references separated by semicolons."""
    seen_book = False
    for raw_part in passage.split(";"):
        part = raw_part.strip()
        folded = part.casefold()
        book = next(
            (candidate for candidate in _BIBLICAL_BOOKS
             if folded.startswith(f"{candidate.casefold()} ")),
            None,
        )
        if book:
            numbers = part[len(book):].strip()
            seen_book = True
        elif seen_book and part[:1].isdigit():
            numbers = part
        else:
            return False
        if not _BIBLICAL_LOCATOR.fullmatch(numbers):
            return False
    return seen_book


def _read_json_file(path) -> dict:
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def _write_json_file(path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


class DirectorAgent:
    """Central orchestrator — coordinates the full pipeline (§12).

    §12: No agent publishes directly or bypasses global rules.
    §14: State persists in state.json — survives restarts.
    §8: Silence is NOT approval for HITL gates.
    """

    @staticmethod
    def thumbnail_copy(theme: str) -> tuple[str, str, str]:
        """Return mobile headline, story subtitle, and biblical book reference."""
        title, separator, passage = theme.partition(" — ")
        if not title.strip():
            raise ValueError("Every thumbnail requires a headline")
        if not separator or not passage.strip():
            raise ValueError("Every thumbnail requires a biblical book reference")
        if not _is_valid_biblical_reference(passage.strip()):
            raise ValueError("Every thumbnail requires a valid biblical reference")
        if "adão e eva" in title.lower():
            headline = "ADÃO E EVA"
            subtitle = "O JARDIM DO ÉDEN" if "jardim do éden" in title.lower() else ""
        else:
            headline = title.upper()
            subtitle = ""
        return headline, subtitle, passage.upper() if separator else ""

    def __init__(
        self,
        config: StudioConfig | None = None,
        model_router: CoreModelRouter | None = None,
        approval_gate: TelegramApprovalGate | None = None,
    ):
        self.config = config or get_config()
        self.model_router = model_router or CoreModelRouter.profile_d()
        self.approval_gate = approval_gate or TelegramApprovalGate()
        self._agents: dict[str, Any] = {}
        self._episodes: dict[str, dict] = {}  # in-memory cache

    def __getattr__(self, name: str):
        """Initialize legacy agents only on the path that actually needs them."""
        if name in self._agents:
            return self._agents[name]
        factories = {
            "research": ResearchAgent,
            "audio": AudioAgent,
            "image_gen": lambda: ImageGenAgent(mode="lcm"),
            "animation": AnimationAgent,
            "assembly": AssemblyAgent,
            "captions": CaptionsAgent,
            "thumbnail": ThumbnailAgent,
            "script": lambda: ScriptAgent(
                llm_provider=self.model_router.provider_for("script", timeout=180)
            ),
            "storyboard": lambda: StoryboardAgent(
                llm_provider=self.model_router.provider_for("storyboard", timeout=120)
            ),
            "metadata": lambda: MetadataAgent(
                llm_provider=self.model_router.provider_for("metadata", timeout=60)
            ),
        }
        factory = factories.get(name)
        if factory is None:
            raise AttributeError(name)
        agent = factory()
        self._agents[name] = agent
        return agent

    def create_operational_pipeline(
        self,
        episode_id: str,
        *,
        approved_audio,
        source_manifest,
        database: Path,
        endpoint: str,
        image_cost: Decimal,
        storyboard_path: Path | None = None,
        imported_assets=None,
        blocked_scenes=(),
        prior_spend: Decimal = Decimal(0),
    ):
        """Open the sole compiled writer after audio/reference approval.

        This façade deliberately requires frozen audio and references instead of
        accepting paths. It cannot silently turn a generated draft into an
        approved source or invoke the legacy per-scene production loop.
        """
        from src.hybrid.compiled import OperationalPipeline, compile_storyboard
        from src.hybrid.execution import Executor
        from src.hybrid.planner import Config

        fs = EpisodeFS(episode_id, self.config)
        if not fs.exists():
            raise FileNotFoundError(f"episode does not exist: {episode_id}")
        storyboard_file = Path(storyboard_path) if storyboard_path else fs.paths.storyboard_dir / "scenes.json"
        storyboard = _read_json_file(storyboard_file)
        scenes = storyboard.get("scenes", storyboard.get("frames", []))
        episode = compile_storyboard(episode_id, approved_audio, scenes)
        return OperationalPipeline(
            episode,
            Executor(database, Config.load(), prior_spend=Decimal(prior_spend)),
            source_manifest,
            workspace=fs.paths.compiled_dir,
            endpoint=endpoint,
            image_cost=Decimal(image_cost),
            imported_assets=imported_assets,
            blocked_scenes=blocked_scenes,
        )

    def activate_compiled_production(
        self,
        episode_id: str,
        *,
        approved_audio,
        source_manifest,
        database: Path,
        endpoint: str,
        image_cost: Decimal,
        storyboard_path: Path | None = None,
        imported_assets=None,
        blocked_scenes=(),
        prior_spend: Decimal = Decimal(0),
    ):
        """Activate the sole compiled writer only at the image-generation hand-off.

        The caller must still provide independently approved, frozen inputs. This
        method intentionally dispatches no provider request and performs no
        approval transition, so activation cannot turn a plan or draft into media.
        """
        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state != EpisodeState.GENERATING_IMAGES:
            raise ValueError("compiled production activates only from GENERATING_IMAGES")
        return self.create_operational_pipeline(
            episode_id,
            approved_audio=approved_audio,
            source_manifest=source_manifest,
            database=database,
            endpoint=endpoint,
            image_cost=image_cost,
            storyboard_path=storyboard_path,
            imported_assets=imported_assets,
            blocked_scenes=blocked_scenes,
            prior_spend=prior_spend,
        )

    def issue_compiled_live_preflight(self, episode_id: str, pipeline, price_resolver, *, reviewer: str):
        """Issue one short-lived, budget-bound LIVE authority per compiled baseline."""
        from src.hybrid.preflight import LivePreflightIssuer

        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state is not EpisodeState.GENERATING_IMAGES:
            raise ValueError("LIVE preflight requires GENERATING_IMAGES state")
        if pipeline.episode.audio.mode != "LIVE":
            raise ValueError("LIVE preflight requires a LIVE compiled pipeline")
        return LivePreflightIssuer(
            fs.paths.costs_json,
            hard_limit=Decimal(str(self.config.budget.hard_limit_usd)),
            price_resolver=price_resolver,
        ).issue(
            pipeline.baseline_jobs(),
            reviewer=reviewer,
            receipt_path=fs.paths.qa_dir / "live_preflight.json",
        )

    async def dispatch_compiled_baselines(
        self, episode_id: str, pipeline, provider, *, authorizations=None, prices=None
    ) -> dict[str, Any]:
        """Dispatch compiled work once, then stop at independent visual QA."""
        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state is not EpisodeState.GENERATING_IMAGES:
            raise ValueError("compiled baseline dispatch requires GENERATING_IMAGES state")
        if pipeline.episode.audio.mode == "LIVE":
            jobs = pipeline.baseline_jobs()
            request_ids = {job.request_id for job in jobs}
            if (
                not isinstance(authorizations, dict)
                or not isinstance(prices, dict)
                or set(authorizations) != request_ids
                or set(prices) != request_ids
            ):
                raise ValueError("every compiled LIVE job requires exact authority and fresh price")
        receipts = await pipeline.dispatch_baselines(
            provider, authorizations=authorizations, prices=prices
        )
        qa_packets = pipeline.prepare_qa_packets()
        if set(qa_packets) != set(receipts):
            raise ValueError("every completed compiled baseline requires a QA packet")
        state.transition_to(
            EpisodeState.VISUAL_QA,
            agent="CompiledProduction",
            note="compiled candidates await independent visual QA",
        )
        state.save(fs.paths.state_json)
        return {"receipts": receipts, "qa_packets": qa_packets, "state": state.current_state.value}

    def record_compiled_visual_qa(self, episode_id: str, pipeline, decisions) -> dict[str, Any]:
        """Persist independent hash-bound visual verdicts and open animation only on PASS."""
        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state is not EpisodeState.VISUAL_QA:
            raise ValueError("compiled visual QA requires VISUAL_QA state")
        packets = pipeline.prepare_qa_packets()
        by_scene = {}
        for decision in decisions:
            if not isinstance(decision, dict) or set(decision) != {
                "scene_id", "result_sha256", "approved", "reviewer"
            }:
                raise ValueError("visual QA decision schema is invalid")
            scene_id = decision["scene_id"]
            if scene_id in by_scene:
                raise ValueError("visual QA decision duplicated a scene")
            if not isinstance(decision["approved"], bool) or not isinstance(decision["reviewer"], str) or not decision["reviewer"].strip():
                raise ValueError("visual QA decision requires boolean verdict and reviewer")
            packet = packets.get(scene_id)
            if packet is None or packet["result_sha256"] != decision["result_sha256"]:
                raise ValueError("visual QA decision does not bind a current candidate")
            by_scene[scene_id] = decision
        if set(by_scene) != set(packets):
            raise ValueError("independent visual QA must decide every current candidate")
        approved = {}
        for scene_id, decision in by_scene.items():
            asset = pipeline.record_visual_qa(
                scene_id, decision["result_sha256"], decision["approved"], decision["reviewer"]
            )
            approved[scene_id] = str(asset.path) if asset else ""
        if all(decision["approved"] for decision in by_scene.values()) and pipeline.render_ready():
            state.transition_to(
                EpisodeState.PLANNING_ANIMATION,
                agent="CompiledProduction",
                note="all compiled visual QA passed",
            )
            state.save(fs.paths.state_json)
        return {"approved": approved, "state": state.current_state.value}

    def render_compiled_video(self, episode_id: str, pipeline, renderer, *, hold: int = 4) -> dict[str, Any]:
        """Compose a subtitle-free local delivery master from the approved manifest only."""
        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state is not EpisodeState.PLANNING_ANIMATION:
            raise ValueError("compiled rendering requires PLANNING_ANIMATION state")
        if hold not in (3, 4, 5):
            raise ValueError("compiled render closing hold must be 3 to 5 seconds")
        manifest = pipeline.approved_manifest()
        scenes = pipeline.render_scenes(manifest)
        state.transition_to(EpisodeState.LOCAL_ANIMATION, agent="CompiledProduction", note="local render started")
        state.save(fs.paths.state_json)
        state.transition_to(EpisodeState.ASSEMBLING, agent="CompiledProduction", note="assembling compiled master")
        state.save(fs.paths.state_json)
        receipt = renderer.render_compiled(
            pipeline,
            scenes,
            manifest,
            pipeline.episode.audio,
            None,
            fs.paths.final_video,
            hold=hold,
        )
        if receipt.get("subtitles_sha256") is not None:
            raise ValueError("compiled delivery must not burn subtitles")
        if not fs.paths.final_video.is_file():
            raise ValueError("compiled renderer did not create final video")
        _write_json_file(fs.paths.qa_dir / "compiled_render_receipt.json", receipt)
        state.transition_to(EpisodeState.FINAL_QA, agent="CompiledProduction", note="compiled master ready for final QA")
        state.save(fs.paths.state_json)
        return receipt

    async def finalize_compiled_delivery(self, episode_id: str, pipeline, renderer, *, hold: int = 4) -> dict[str, Any]:
        """Render a compiled master and materialize all evidence required by final QA."""
        receipt = self.render_compiled_video(episode_id, pipeline, renderer, hold=hold)
        sidecars = await self.prepare_delivery_sidecars(episode_id, pipeline)
        return {"render_receipt": receipt, "sidecars": sidecars}

    async def complete_compiled_final_qa(
        self,
        episode_id: str,
        pipeline,
        renderer,
        *,
        published_script_hashes: set[str] | frozenset[str],
        checker=None,
        hold: int = 4,
    ) -> dict[str, Any]:
        """Run the compiled render, evidence and independent final-media gates in order."""
        delivery = await self.finalize_compiled_delivery(episode_id, pipeline, renderer, hold=hold)
        evidence = self.record_production_evidence_qa(
            episode_id, published_script_hashes=published_script_hashes
        )
        if evidence.get("approved") is not True:
            return {"delivery": delivery, "production_evidence_qa": evidence, "post_production_narrative_qa": None, "final_render_qa": None}
        narrative = self.record_post_production_narrative_qa(episode_id)
        if narrative.get("approved") is not True:
            return {"delivery": delivery, "production_evidence_qa": evidence, "post_production_narrative_qa": narrative, "final_render_qa": None}
        fs = EpisodeFS(episode_id, self.config)
        final_qa = await self.record_final_render_qa(
            episode_id,
            video_path=fs.paths.final_video,
            render_receipt=delivery["render_receipt"],
            checker=checker,
        )
        return {
            "delivery": delivery,
            "production_evidence_qa": evidence,
            "post_production_narrative_qa": narrative,
            "final_render_qa": final_qa,
        }

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
        self.thumbnail_copy(theme)  # Fail before filesystem/model work if the passage is missing.

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
            max_generative_clips=self.config.generative_video.max_clips_per_episode,
            preferred_clip_duration_s=self.config.generative_video.preferred_clip_duration_seconds,
            max_generative_seconds=self.config.generative_video.max_seconds_per_episode,
            cost_per_generative_second=self.config.cost_estimates.generative_video_second_usd,
            cost_per_image=self.config.cost_estimates.image_usd,
        )
        duration_plan = duration_planner.plan(theme, research_result.data)
        budget_check = duration_planner.check_budget(duration_plan)

        plan["duration_plan"] = duration_plan.to_dict()
        plan["budget_check"] = budget_check

        # Transition to WAITING_PLAN_APPROVAL (§95)
        state.transition_to(EpisodeState.WAITING_PLAN_APPROVAL, agent=self.name, note="Pre-production plan ready")
        state.save(fs.paths.state_json)

        # Save costs
        guard.ledger.save(fs.paths.costs_json)

        # §4/§7/§8: If cost exceeds budget, this MUST be surfaced for human approval
        # before any paid generation begins. The report is always produced (§20);
        # whether we block here depends on require_telegram_approval (pilot runs
        # may auto-approve locally, but production runs must gate on Telegram).
        report_text = duration_plan.format_report(self.config.budget.hard_limit_usd)
        plan["report_text"] = report_text

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
                recommendation=f"Opção {next(iter(options))} recomendada.",
            )
            if require_telegram_approval:
                approval = await self.approval_gate.request_approval(
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
                    await asyncio.to_thread(_write_json_file, fs.paths.plan_json, plan)
                    return plan
            else:
                self.approval_gate.send_message(message)
        elif require_telegram_approval:
            # Even within budget, send the plan for visibility (§20) but
            # don't block unless explicitly configured to require sign-off.
            self.approval_gate.send_message(f"📋 Plano de produção pronto:\n\n{report_text}")

        await asyncio.to_thread(_write_json_file, fs.paths.plan_json, plan)

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
            plan = await asyncio.to_thread(_read_json_file, fs.paths.plan_json)
            budget_check = plan.get("budget_check", {})
            budget_approval = plan.get("budget_approval", {})
            explicit_over_budget_approval = (
                budget_approval.get("approved") is True
                and budget_approval.get("response") == "D"
            )
            if not budget_check.get("within_budget", False) and not explicit_over_budget_approval:
                state.transition_to(
                    EpisodeState.WAITING_BUDGET_APPROVAL,
                    agent=self.name,
                    note="Production blocked until explicit over-budget approval",
                )
                state.save(fs.paths.state_json)
                return {
                    "status": "waiting_budget_approval",
                    "state": state.current_state.value,
                    "alternatives": budget_check.get("alternatives", []),
                }
            return await self._run_production(episode_id, fs, state)
        elif approval_type == "budget":
            # Budget override approved — continue cloud generation
            state.transition_to(EpisodeState.CLOUD_VIDEO_GENERATION, agent=self.name, note="budget approved")
            state.save(fs.paths.state_json)
            return {"status": "budget approved", "state": state.current_state.value}
        elif approval_type == "final":
            # Approval grants delivery acceptance only. Upload/publication must be
            # initiated later by a separate explicit command and hash-bound receipts.
            return {
                "status": "awaiting_separate_publication_instruction",
                "state": state.current_state.value,
            }

        return {"error": f"Unknown approval type: {approval_type}"}

    async def publish_after_explicit_instruction(
        self,
        episode_id: str,
        *,
        command: str,
        expected_command: str,
        publisher,
        video_receipt_path: Path,
        thumbnail_receipt_path: Path,
        metadata_path: Path,
        captions_path: Path | None = None,
    ) -> dict[str, Any]:
        """Publish only through durable separate authorization and remote readback."""
        from src.approval.receipts import load_approval_receipt
        from src.publishing.controller import PublicationController

        fs = EpisodeFS(episode_id, self.config)
        return await PublicationController(publisher).publish(
            state_path=fs.paths.state_json,
            expected_command=expected_command,
            command=command,
            video=load_approval_receipt(video_receipt_path),
            thumbnail=load_approval_receipt(thumbnail_receipt_path),
            metadata_path=metadata_path,
            publication_receipt_path=fs.paths.qa_dir / "publication_receipt.json",
            captions_path=captions_path,
        )

    def record_production_evidence_qa(
        self, episode_id: str, *, published_script_hashes: set[str] | frozenset[str] = frozenset()
    ) -> dict[str, Any]:
        """Persist independent originality, caption, source and manifest evidence before final QA."""
        from src.qa.production_evidence import ProductionEvidenceQA
        from src.qa.published_inventory import PublishedInventory

        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state is not EpisodeState.FINAL_QA:
            raise ValueError("production evidence QA requires FINAL_QA state")
        inventory = PublishedInventory.scan(self.config.episodes_dir, exclude_episode_id=episode_id)
        result = ProductionEvidenceQA().review(
            script_path=fs.paths.script_dir / "script.json",
            manifest_path=fs.paths.compiled_dir / "manifest.json",
            captions_path=fs.paths.captions_vtt,
            metadata_path=fs.paths.metadata_dir / "metadata.json",
            published_script_hashes=set(published_script_hashes) | inventory.script_hashes,
            published_artifact_hashes=inventory.artifact_hashes,
        )
        report = {"approved": result.approved, "findings": list(result.findings), "report": result.report}
        _write_json_file(fs.paths.qa_dir / "production_evidence_qa.json", report)
        return report

    def record_post_production_narrative_qa(self, episode_id: str) -> dict[str, Any]:
        """Persist the independent biblical narrative and child-safety verdict."""
        from src.qa.post_production import PostProductionNarrativeQA

        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state is not EpisodeState.FINAL_QA:
            raise ValueError("post-production narrative QA requires FINAL_QA state")
        result = PostProductionNarrativeQA().review(
            script_path=fs.paths.script_dir / "script.json",
            captions_path=fs.paths.captions_vtt,
            metadata_path=fs.paths.metadata_dir / "metadata.json",
        )
        report = {"approved": result.approved, "findings": list(result.findings), "report": result.report}
        _write_json_file(fs.paths.qa_dir / "post_production_narrative_qa.json", report)
        return report

    async def prepare_delivery_sidecars(self, episode_id: str, pipeline) -> dict[str, Any]:
        """Create required sidecar captions, thumbnail and metadata from frozen compiled media."""
        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state is not EpisodeState.FINAL_QA:
            raise ValueError("delivery sidecars require FINAL_QA state")
        request = _read_json_file(fs.paths.request_json)
        research = _read_json_file(fs.paths.research_dir / "sources.json")
        script = _read_json_file(fs.paths.script_dir / "script.json")
        storyboard = _read_json_file(fs.paths.storyboard_dir / "scenes.json")
        scenes = storyboard.get("scenes")
        narration = script.get("narration")
        if not isinstance(scenes, list) or not scenes or not isinstance(narration, str) or not narration.strip():
            raise ValueError("compiled sidecars require persisted narration and storyboard")
        manifest = pipeline.approved_manifest()
        frames = tuple(pipeline.episode.frames)
        if len(frames) != len(manifest.assets) or [scene.get("scene_id") for scene in scenes] != [
            frame.scene_id for frame in frames
        ]:
            raise ValueError("sidecars require the active compiled scene order")
        images = []
        for frame, asset in zip(frames, manifest.assets, strict=True):
            if not asset.path.is_file():
                raise ValueError("compiled thumbnail asset is missing")
            images.append({"scene_id": frame.scene_id, "image_path": str(asset.path)})
        timestamps = [
            {"start": scene.get("start"), "end": scene.get("end"), "text": scene.get("narration", "")}
            for scene in scenes
        ]
        if any(
            not isinstance(item["start"], (int, float))
            or not isinstance(item["end"], (int, float))
            or item["end"] <= item["start"]
            or not isinstance(item["text"], str)
            or not item["text"].strip()
            for item in timestamps
        ):
            raise ValueError("captions require real semantic storyboard timestamps")
        captions = await self.captions.run(
            episode_id=episode_id,
            sentence_timestamps=timestamps,
            narration=narration,
            subtitles_dir=str(fs.paths.subtitles_dir),
        )
        if not captions.success or not fs.paths.captions_vtt.is_file():
            raise RuntimeError("caption sidecar generation failed")
        headline, subtitle, book_subtitle = self.thumbnail_copy(str(request.get("theme", "")))
        thumbnail = await self.thumbnail.run(
            episode_id=episode_id,
            images=images,
            scenes=scenes,
            headline=headline,
            subtitle=subtitle,
            book_subtitle=book_subtitle,
            thumbnails_dir=str(fs.paths.thumbnails_dir),
        )
        thumbnail_path = Path(thumbnail.data.get("thumbnail_path", "")) if thumbnail.success else None
        if thumbnail_path is None or not thumbnail_path.is_file():
            raise RuntimeError("thumbnail generation failed")
        metadata = await self.metadata.run(
            episode_id=episode_id,
            theme=str(request.get("theme", "")),
            research_data=research,
            scenes=scenes,
            language=str(request.get("language", "pt-BR")),
            metadata_dir=str(fs.paths.metadata_dir),
            captions_files=captions.data.get("files", {}),
            thumbnail_path=str(thumbnail_path),
        )
        if not metadata.success or not (fs.paths.metadata_dir / "metadata.json").is_file():
            raise RuntimeError("metadata generation failed")
        return {
            "captions": captions.data["files"],
            "thumbnail": str(thumbnail_path),
            "metadata": metadata.data.get("metadata_path", ""),
        }

    async def deliver_for_approval(
        self,
        episode_id: str,
        *,
        messenger,
        chat_id: str,
        thumbnail_path: Path,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Deliver final thumbnail and video once, as separate hash-bound review media."""
        from src.approval.receipts import ApprovalReceipt
        from src.delivery.controller import DeliveryController

        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state is not EpisodeState.WAITING_THUMBNAIL_APPROVAL:
            raise ValueError("approval delivery requires WAITING_THUMBNAIL_APPROVAL state")
        thumbnail = ApprovalReceipt.approve("thumbnail", thumbnail_path, "delivery-preflight")
        video = ApprovalReceipt.approve("video", fs.paths.final_video, "delivery-preflight")
        return await DeliveryController(messenger).deliver_for_approval(
            chat_id=chat_id,
            episode_id=episode_id,
            thumbnail=thumbnail,
            video=video,
            receipt_dir=fs.paths.qa_dir / "delivery",
        )

    async def record_final_render_qa(
        self,
        episode_id: str,
        *,
        video_path: Path,
        render_receipt: dict[str, Any],
        checker=None,
    ) -> dict[str, Any]:
        """Persist independent final-media QA before opening the video approval gate."""
        from src.qa.final_render import FinalRenderQA

        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state != EpisodeState.FINAL_QA:
            raise ValueError("final render QA requires FINAL_QA state")
        evidence_path = fs.paths.qa_dir / "production_evidence_qa.json"
        try:
            evidence = _read_json_file(evidence_path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("passing production evidence QA is required before final render QA") from error
        if evidence.get("approved") is not True:
            raise ValueError("passing production evidence QA is required before final render QA")
        narrative_path = fs.paths.qa_dir / "post_production_narrative_qa.json"
        try:
            narrative = _read_json_file(narrative_path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise ValueError("passing post-production narrative QA is required before final render QA") from error
        if narrative.get("approved") is not True:
            raise ValueError("passing post-production narrative QA is required before final render QA")
        result = (checker or FinalRenderQA()).review(video_path, render_receipt)
        report = {
            "approved": result.approved,
            "findings": list(result.findings),
            "report": result.report,
        }
        await asyncio.to_thread(_write_json_file, fs.paths.qa_dir / "final_render_qa.json", report)
        if result.approved:
            state.transition_to(
                EpisodeState.WAITING_THUMBNAIL_APPROVAL,
                agent="FinalRenderQA",
                note="independent final media QA passed; thumbnail delivery approval required",
            )
            state.save(fs.paths.state_json)
        return report

    def confirm_delivered_artifact(
        self,
        episode_id: str,
        *,
        artifact_kind: str,
        command: str,
        expected_command: str,
        approver: str,
        artifact_path: Path,
        delivery_receipt_path: Path,
    ):
        """Persist an explicit artifact approval bound to its delivered media bytes."""
        from src.approval.controller import ApprovalController

        fs = EpisodeFS(episode_id, self.config)
        expected_state = {
            "thumbnail": EpisodeState.WAITING_THUMBNAIL_APPROVAL,
            "video": EpisodeState.WAITING_VIDEO_APPROVAL,
        }.get(artifact_kind)
        next_state = {
            "thumbnail": EpisodeState.WAITING_VIDEO_APPROVAL,
            "video": EpisodeState.WAITING_FINAL_APPROVAL,
        }.get(artifact_kind)
        if expected_state is None or next_state is None:
            raise ValueError("artifact_kind must be thumbnail or video")
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state is not expected_state:
            raise ValueError(f"{artifact_kind} approval requires {expected_state.value} state")
        receipt = ApprovalController().confirm_delivery(
            episode_id=episode_id,
            artifact_kind=artifact_kind,
            command=command,
            expected_command=expected_command,
            approver=approver,
            artifact_path=artifact_path,
            delivery_receipt_path=delivery_receipt_path,
            approval_receipt_path=fs.paths.qa_dir / f"approval-{artifact_kind}.json",
        )
        state.transition_to(next_state, agent="ApprovalController", note=f"{artifact_kind} delivery approved")
        state.save(fs.paths.state_json)
        return receipt

    def reject_delivered_artifact(self, episode_id: str, *, artifact_id: str, reason: str) -> dict[str, Any]:
        """Supersede rejected approval media and every dependent artifact before rebuilding."""
        from src.pipeline.revision import RevisionRegistry

        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state not in {
            EpisodeState.WAITING_THUMBNAIL_APPROVAL,
            EpisodeState.WAITING_VIDEO_APPROVAL,
            EpisodeState.WAITING_FINAL_APPROVAL,
        }:
            raise ValueError("delivery rejection requires an active approval gate")
        registry = RevisionRegistry(fs.paths.qa_dir)
        registry.reject(artifact_id, reason=reason)
        receipt = registry.read(artifact_id)
        state.transition_to(
            EpisodeState.ASSEMBLING,
            agent="RevisionRegistry",
            note=f"rejected {artifact_id}; dependent delivery lineage superseded",
        )
        state.save(fs.paths.state_json)
        return receipt

    def _build_visual_strategy_engine(self, local_provider, cloud_provider):
        """Build the visual router from the central episode limits."""
        from src.providers.gpu.gpu_compute_provider import GenerativeVideoConfig
        from src.providers.gpu.visual_strategy import VisualStrategyEngine

        configured = self.config.generative_video
        engine_config = GenerativeVideoConfig(
            enabled=False,
            provider="transactional_live_only",
            max_clips_per_episode=configured.max_clips_per_episode,
            max_seconds_per_episode=configured.max_seconds_per_episode,
            preferred_clip_duration_seconds=configured.preferred_clip_duration_seconds,
            maximum_clip_duration_seconds=configured.maximum_clip_duration_seconds,
            cost_limit_per_clip_usd=configured.cost_limit_per_clip_usd,
        )
        return VisualStrategyEngine(engine_config, local_provider, cloud_provider)

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
        plan, research_data = await asyncio.gather(
            asyncio.to_thread(_read_json_file, fs.paths.plan_json),
            asyncio.to_thread(_read_json_file, fs.paths.research_dir / "sources.json"),
        )

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

        # Script QA is an independent gate. Persist its source-bound verdict
        # before creating immutable narration audio.
        state.transition_to(EpisodeState.SCRIPT_QA, agent="ScriptQA")
        state.save(fs.paths.state_json)
        packet_path = Path(script_result.data.get("script_packet_path", ""))
        if not packet_path.is_file():
            state.transition_to(EpisodeState.FAILED, agent="ScriptQA", note="script packet missing")
            state.save(fs.paths.state_json)
            return {"error": "script packet missing"}
        qa_result = ScriptQAAgent().review(await asyncio.to_thread(_read_json_file, packet_path))
        qa_record = {"approved": qa_result.approved, "findings": list(qa_result.findings)}
        await asyncio.to_thread(_write_json_file, fs.paths.script_dir / "script_qa.json", qa_record)
        if not qa_result.approved:
            state.transition_to(EpisodeState.FAILED, agent="ScriptQA", note="; ".join(qa_result.findings))
            state.save(fs.paths.state_json)
            return {"error": "script QA failed", "findings": list(qa_result.findings)}

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
            audio_duration_s=float(audio_result.data["duration_s"]),
            storyboard_dir=str(fs.paths.storyboard_dir),
        )

        if not storyboard_result.success:
            state.transition_to(EpisodeState.FAILED, note=storyboard_result.error)
            state.save(fs.paths.state_json)
            return {"error": storyboard_result.error}

        state.transition_to(EpisodeState.GENERATING_IMAGES, agent=self.name, note="production ready for image gen")
        state.save(fs.paths.state_json)

        # The Director stops at the compiled hand-off. A caller must provide the
        # independently approved audio and immutable image-to-image references
        # to create_operational_pipeline(); this prevents the legacy serial
        # ImageGen/Animation agents from becoming a second production writer.
        return {
            "episode_id": episode_id,
            "state": state.current_state.value,
            "narration_preview": narration[:200],
            "word_count": script_result.data["word_count"],
            "audio_duration_s": round(audio_result.data["duration_s"], 1),
            "scene_count": storyboard_result.data["scene_count"],
            "compiled_activation": {
                "storyboard": str(fs.paths.storyboard_dir / "scenes.json"),
                "audio": audio_result.data["audio_path"],
                "requires": ["approved_audio", "source_manifest", "executor", "provider"],
            },
        }

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

        # Step 11: Visual Strategy — decide local vs generative video (§63-67)
        from src.providers.gpu.gpu_compute_provider import (
            LocalGPUProvider,
            SceneImportance,
        )

        local_gpu = LocalGPUProvider()
        strategy_engine = self._build_visual_strategy_engine(local_gpu, None)

        # Classify each scene and mark strategy
        for scene in scenes:
            importance = SceneImportance(scene.get("importance", "NORMAL"))
            strategy = strategy_engine.decide(
                scene_importance=importance,
                scene_duration=scene.get("duration", 5.0),
                emotion=scene.get("emotion", ""),
                location=scene.get("location", ""),
                characters=scene.get("characters", []),
                narration=scene.get("narration", ""),
            )
            scene["visual_strategy"] = strategy.strategy
            scene["visual_strategy_reason"] = strategy.reason

        gen_summary = strategy_engine.get_usage_summary()
        logger.info(f"Visual strategy: {gen_summary['generative_clips_used']} generative clips, "
                     f"{gen_summary['generative_seconds_used']}s of {gen_summary['max_seconds']}s max")

        # Step 12: Animate locally (§49-52, §67: local animation is PRIMARY)
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
        # Captions and metadata are best-effort; the required thumbnail is fatal.
        _req = await asyncio.to_thread(_read_json_file, fs.paths.request_json)
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
        headline, subtitle, book_subtitle = self.thumbnail_copy(theme_str)
        thumb_result = await self.thumbnail.run(
            episode_id=episode_id,
            images=image_result.data["generated"],
            scenes=scenes,
            headline=headline,
            subtitle=subtitle,
            book_subtitle=book_subtitle,
            thumbnails_dir=str(fs.paths.thumbnails_dir),
        )
        if not thumb_result.success:
            state.transition_to(
                EpisodeState.FAILED,
                agent=self.thumbnail.name,
                note=thumb_result.error,
            )
            state.save(fs.paths.state_json)
            return {"error": thumb_result.error}
        thumbnail_path = thumb_result.data.get("thumbnail_path", "")

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
        """Create the pre-production packet; never bypass the human plan gate.

        This compatibility entry point intentionally returns at
        ``WAITING_PLAN_APPROVAL``. Continuing production requires a durable
        approval receipt through the approval workflow.
        """
        return await self.start_episode(theme=theme, episode_id=episode_id)

    def cleanup_orphans(self) -> list[str]:
        """Legacy pod cleanup is disabled; LIVE recovery owns remote operations."""
        logger.info("Legacy RunPod cleanup disabled; use transactional request recovery")
        return []

    @property
    def name(self) -> str:
        return "Director"
