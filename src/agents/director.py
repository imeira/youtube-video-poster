"""Director Agent — central orchestrator (§12).

Responsibility: Coordinate all agents, manage state machine, enforce budget.
§12: No specialized agent may publish directly or bypass global rules.
§55-56: Cleanup orphaned RunPod pods on startup.
§8: Silence is NOT approval.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import stat
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
from src.providers.llm.usage import track_episode_usage
from src.state.machine import EpisodeState, EpisodeStateStore
from src.storage.episode_fs import EpisodeFS
from src.storage.episode_id import validate_episode_id
from src.telegram.approval_gate import TelegramApprovalGate, format_decision_message

logger = logging.getLogger(__name__)

_BIBLICAL_BOOKS = tuple(
    sorted(
        {
            "Gênesis",
            "Êxodo",
            "Levítico",
            "Números",
            "Deuteronômio",
            "Josué",
            "Juízes",
            "Rute",
            "1 Samuel",
            "2 Samuel",
            "1 Reis",
            "2 Reis",
            "1 Crônicas",
            "2 Crônicas",
            "Esdras",
            "Neemias",
            "Ester",
            "Jó",
            "Salmos",
            "Salmo",
            "Provérbios",
            "Eclesiastes",
            "Cântico dos Cânticos",
            "Cantares",
            "Isaías",
            "Jeremias",
            "Lamentações",
            "Ezequiel",
            "Daniel",
            "Oseias",
            "Joel",
            "Amós",
            "Obadias",
            "Jonas",
            "Miqueias",
            "Naum",
            "Habacuque",
            "Sofonias",
            "Ageu",
            "Zacarias",
            "Malaquias",
            "Mateus",
            "Marcos",
            "Lucas",
            "João",
            "Atos",
            "Romanos",
            "1 Coríntios",
            "2 Coríntios",
            "Gálatas",
            "Efésios",
            "Filipenses",
            "Colossenses",
            "1 Tessalonicenses",
            "2 Tessalonicenses",
            "1 Timóteo",
            "2 Timóteo",
            "Tito",
            "Filemom",
            "Hebreus",
            "Tiago",
            "1 Pedro",
            "2 Pedro",
            "1 João",
            "2 João",
            "3 João",
            "Judas",
            "Apocalipse",
        },
        key=len,
        reverse=True,
    )
)
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
            (
                candidate
                for candidate in _BIBLICAL_BOOKS
                if folded.startswith(f"{candidate.casefold()} ")
            ),
            None,
        )
        if book:
            numbers = part[len(book) :].strip()
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
        inbound_approval_source=None,
    ):
        self.config = config or get_config()
        self.model_router = model_router or CoreModelRouter.profile_d(
            usage_path=self.config.episodes_dir.parent / "usage.sqlite3"
        )
        self.approval_gate = approval_gate or TelegramApprovalGate()
        self.inbound_approval_source = inbound_approval_source
        self._agents: dict[str, Any] = {}
        self._episodes: dict[str, dict] = {}  # in-memory cache
        # One flight owns an episode's non-idempotent local encode until its
        # synchronous renderer has returned, even if its original caller is
        # cancelled.  This is intentionally not a new executor: LocalRenderer
        # retains ownership of its bounded scene/encoder worker pool.
        self._compiled_render_flights: dict[str, tuple[str, asyncio.Task]] = {}
        # This is deliberately a process-local, fixed one-render pool.  It does
        # not claim a host-wide CPU limit; LocalRenderer retains its bounded
        # internal scene parallelism and FFmpeg thread settings.
        self._compiled_render_pool_size = 1
        self._compiled_render_admission: asyncio.Semaphore | None = None

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
        executor_config=None,
        motion_routes=None,
        motion_policy=None,
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
        storyboard_file = (
            Path(storyboard_path)
            if storyboard_path
            else fs.paths.storyboard_dir / "scenes.json"
        )
        storyboard = _read_json_file(storyboard_file)
        scenes = storyboard.get("scenes", storyboard.get("frames", []))
        episode = compile_storyboard(
            episode_id,
            approved_audio,
            scenes,
            motion_routes=motion_routes,
            motion_policy=motion_policy,
        )
        return OperationalPipeline(
            episode,
            Executor(
                database,
                executor_config or Config.load(),
                prior_spend=Decimal(prior_spend),
            ),
            source_manifest,
            workspace=fs.paths.compiled_dir,
            endpoint=endpoint,
            image_cost=Decimal(image_cost),
            imported_assets=imported_assets,
            blocked_scenes=blocked_scenes,
        )

    def create_autonomous_two_gate_runner(
        self,
        episode_id: str,
        workspace: Path,
        policy_document: dict,
        database: Path,
        *,
        limit: Decimal,
        concurrency: int = 1,
    ):
        """Create the durable two-human-gate control plane.

        This factory does not dispatch anything.  Callers must provide the
        transactional executor jobs at start/resume, preserving the executor as
        the only provider submission path.
        """
        from src.hybrid.autonomous import AutonomousPolicy, AutonomousTwoGateRunner
        from src.hybrid.execution import Executor
        from src.hybrid.planner import Config
        from src.hybrid.standing_budget import StandingBudgetLedger

        policy = AutonomousPolicy.from_dict(policy_document)
        if policy.episode_cap_usd != Decimal(limit):
            raise ValueError(
                "executor limit must equal the permanent episode policy ceiling"
            )
        ledger = StandingBudgetLedger(
            Path(database).with_name("standing-budget.sqlite3"),
            policy_sha256=policy.digest,
            episode_limit_usd=policy.episode_cap_usd,
            month_limit_usd=policy.month_cap_usd,
            experiment_limit_usd=policy.experiment_cap_usd,
            mode="TEST",
        )
        return AutonomousTwoGateRunner(
            workspace,
            episode_id,
            policy,
            Executor(database, Config(limit=Decimal(limit), concurrency=concurrency)),
            ledger,
        )

    def create_autonomous_compiled_test_adapter(
        self, pipeline, workspace: Path, policy_document: dict, *, ownership_assertion=None
    ):
        """Attach an existing TEST compiled pipeline to the durable runner.

        The compiled pipeline keeps its original Executor; creating a second
        executor would split the idempotency and cost ledgers.  This factory is
        intentionally unavailable to LIVE pipelines and dispatches nothing.
        """
        from src.hybrid.autonomous import (
            AutonomousCompiledTestAdapter,
            AutonomousPolicy,
            AutonomousTwoGateRunner,
        )
        from src.hybrid.standing_budget import StandingBudgetLedger

        if pipeline.episode.audio.mode != "TEST":
            raise ValueError("autonomous compiled adapter is TEST-only")
        validate_episode_id(pipeline.episode.episode_id)
        policy = AutonomousPolicy.from_dict(policy_document)
        executor = pipeline.run.executor
        if executor.config.limit != policy.episode_cap_usd:
            raise ValueError(
                "compiled executor limit must equal the permanent episode policy ceiling"
            )
        ledger = StandingBudgetLedger(
            executor.database.with_name("standing-budget.sqlite3"),
            policy_sha256=policy.digest,
            episode_limit_usd=policy.episode_cap_usd,
            month_limit_usd=policy.month_cap_usd,
            experiment_limit_usd=policy.experiment_cap_usd,
            mode="TEST",
        )
        runner = AutonomousTwoGateRunner(
            workspace,
            pipeline.episode.episode_id,
            policy,
            executor,
            ledger,
            ownership_assertion=ownership_assertion,
        )
        config = self.__dict__.get("config")
        return AutonomousCompiledTestAdapter(
            runner,
            pipeline,
            canonical_episode_root=(
                EpisodeFS(pipeline.episode.episode_id, config).paths.root.absolute()
                if config is not None else None
            ),
        )

    def import_preproduction_handoff(self, workspace: Path, request_id: str, manifest_path: Path):
        """Verify one local PREPRODUCTION import without activating production.

        The importer deliberately stops before state transition, pipeline,
        provider, rendering, messaging, or publication construction.
        """
        from src.hybrid.preproduction import import_real_preproduction_handoff

        return import_real_preproduction_handoff(workspace, request_id, manifest_path)

    def import_preproduction_audio_candidate(
        self, workspace: Path, request_id: str, audio_path: Path, timing_path: Path,
        license_path: Path, *, alignment_attestation_path: Path | None = None, app_config: object, clock,
    ):
        """Materialize a verified local candidate while retaining blocked TTS state."""
        from src.hybrid.audio_preproduction import import_real_audio_candidate

        return import_real_audio_candidate(
            workspace, request_id, audio_path, timing_path, license_path,
            alignment_attestation_path=alignment_attestation_path,
            app_config=app_config, clock=clock,
        )

    def import_audio_qualification_evidence(
        self,
        workspace: Path,
        request_id: str,
        *,
        origin_declaration: Path | None = None,
        editorial_review: Path | None = None,
        acoustic_review: Path | None = None,
        app_config: object,
        clock,
    ):
        """Materialize declared evidence only; this cannot qualify or promote audio."""
        from src.hybrid.audio_qualification_evidence import (
            import_audio_qualification_evidence,
        )

        return import_audio_qualification_evidence(
            workspace,
            request_id,
            origin_declaration=origin_declaration,
            editorial_review=editorial_review,
            acoustic_review=acoustic_review,
            app_config=app_config,
            clock=clock,
        )

    async def readback_elevenlabs_origin_from_submission(
        self,
        *,
        candidate_receipt_path: Path,
        submission_receipt_path: Path,
        app_config: object,
        readback_service,
    ) -> dict[str, object]:
        """Read one already-submitted TEST item without activating ElevenLabs.

        A caller cannot provide a history ID. It comes only from a durable receipt
        below the configured evidence root and must bind the immutable candidate.
        No real submission-receipt producer exists, so production fails before a
        factory, credential, or GET can be reached.
        """
        from src.hybrid.elevenlabs_origin_readback import ElevenLabsHistoryBinding
        from src.hybrid.external_trust_root import (
            TrustRootVerificationError,
            load_strict_json_snapshot,
        )
        from src.hybrid.locks import reject_reparse_path

        raw = getattr(app_config, "raw", None)
        configured = raw.get("external_trust_root") if isinstance(raw, dict) else None
        evidence_root = configured.get("evidence_root") if isinstance(configured, dict) else None
        if not isinstance(evidence_root, str) or not evidence_root:
            raise ValueError("configured evidence root is required before readback service")
        root = Path(evidence_root)
        if not root.is_absolute():
            raise ValueError("configured evidence root is invalid before readback service")
        try:
            candidate, _candidate_file_sha, _ = load_strict_json_snapshot(
                candidate_receipt_path, "immutable audio candidate receipt", contained_by=root, include_bytes=True,
            )
            submission, _submission_file_sha, _ = load_strict_json_snapshot(
                submission_receipt_path, "durable submission receipt", contained_by=root, include_bytes=True,
            )
        except TrustRootVerificationError as error:
            raise ValueError("missing or invalid durable submission receipt") from error

        candidate_fields = {
            "schema", "mode", "candidate_class", "request_id", "source_sha256",
            "script_sha256", "audio_sha256", "provider_id", "voice_id", "receipt_sha256",
        }
        submission_fields = {
            "schema", "mode", "request_id", "provider", "history_item_id",
            "source_sha256", "script_sha256", "audio_sha256", "voice_id",
            "candidate_receipt_sha256", "receipt_sha256",
        }

        def receipt_digest(receipt: dict[str, object]) -> str:
            projection = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
            return hashlib.sha256(
                json.dumps(projection, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()

        if set(candidate) != candidate_fields or candidate.get("schema") != "elevenlabs-origin-readback-candidate-fixture-v1":
            raise ValueError("immutable candidate receipt contract is unavailable")
        if candidate.get("receipt_sha256") != receipt_digest(candidate):
            raise ValueError("immutable candidate receipt_sha256 is invalid")
        if "history_item_id" not in submission:
            raise ValueError("history_item_id is required in durable submission receipt")
        if set(submission) != submission_fields:
            raise ValueError("durable submission receipt contract is invalid")
        if submission.get("receipt_sha256") != receipt_digest(submission):
            raise ValueError("durable submission receipt_sha256 is invalid")
        if candidate.get("mode") != "TEST" or candidate.get("candidate_class") != "TEST":
            raise ValueError("real immutable ElevenLabs candidate producer is unavailable")
        if submission.get("mode") != "TEST":
            raise ValueError("real submission receipt producer is unavailable")
        if submission.get("schema") != "elevenlabs-history-submission-receipt-fixture-v1":
            raise ValueError("durable submission receipt contract is invalid")
        bindings = {"request_id", "source_sha256", "script_sha256", "audio_sha256", "voice_id"}
        if any(submission.get(key) != candidate.get(key) for key in bindings) or submission.get(
            "candidate_receipt_sha256"
        ) != candidate.get("receipt_sha256"):
            raise ValueError("submission receipt does not bind immutable candidate")
        if submission.get("provider") != "elevenlabs" or candidate.get("provider_id") != "elevenlabs":
            raise ValueError("submission receipt provider binding is invalid")
        if not all(
            isinstance(candidate.get(key), str) and len(candidate[key]) == 64
            and all(char in "0123456789abcdef" for char in candidate[key])
            for key in ("source_sha256", "script_sha256", "audio_sha256")
        ):
            raise ValueError("immutable candidate hash binding is invalid")
        if not callable(readback_service):
            raise TypeError("TEST readback service is required")
        binding = ElevenLabsHistoryBinding(
            request_id=candidate["request_id"],
            history_item_id=submission.get("history_item_id"),
            expected_script_sha256=candidate["script_sha256"],
            expected_voice_id=candidate["voice_id"],
            expected_provider="elevenlabs",
            expected_source="TTS",
            expected_audio_sha256=candidate["audio_sha256"],
        )
        origin = await readback_service(binding, root / "elevenlabs-origin-readback")
        quarantine = root / "elevenlabs-origin-readback"
        expected_audio_path = quarantine / f"{submission['history_item_id']}.audio"
        expected_receipt_path = quarantine / f"{submission['history_item_id']}.receipt.json"
        if (
            Path(getattr(origin, "audio_path", "")) != expected_audio_path
            or Path(getattr(origin, "receipt_path", "")) != expected_receipt_path
        ):
            raise ValueError("readback service returned paths outside the configured quarantine")
        try:
            receipt, _receipt_file_sha, receipt_bytes = load_strict_json_snapshot(
                expected_receipt_path,
                "materialized origin readback receipt",
                contained_by=quarantine,
                include_bytes=True,
            )
            audio_path = reject_reparse_path(expected_audio_path)
            before = os.stat(audio_path, follow_symlinks=False)
            descriptor = os.open(
                audio_path,
                os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode) or not os.path.samestat(before, opened):
                    raise ValueError("materialized origin audio is missing or not a regular file")
                with os.fdopen(descriptor, "rb") as stream:
                    descriptor = None
                    audio = stream.read()
                after = os.stat(audio_path, follow_symlinks=False)
                if not os.path.samestat(opened, after):
                    raise ValueError("materialized origin audio changed during read")
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        except (TrustRootVerificationError, ValueError, OSError) as error:
            raise ValueError("materialized origin readback is unavailable") from error
        if receipt_bytes != json.dumps(
            receipt, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") or getattr(origin, "receipt", None) != receipt:
            raise ValueError("materialized origin receipt differs from callback result")
        receipt_fields = {
            "schema", "request_id", "provider", "history_item_id", "provider_request_id", "source",
            "voice_id", "model_id", "date_unix", "output_format", "returned_text_sha256",
            "history_metadata_sha256", "voice_metadata_sha256", "audio_sha256", "audio_bytes",
            "provider_output_hash", "authenticated_readback", "origin_provenance",
            "voice_access_observation", "commercial_license", "acoustic_alignment",
            "qualification_status", "status", "operations", "receipt_sha256",
        }
        if (
            set(receipt) != receipt_fields
            or receipt.get("schema") != "elevenlabs-history-origin-readback-v1"
            or receipt.get("receipt_sha256") != receipt_digest(receipt)
        ):
            raise ValueError("materialized origin receipt schema or canonical hash is invalid")
        if hashlib.sha256(audio).hexdigest() != receipt.get("audio_sha256") or len(audio) != receipt.get("audio_bytes"):
            raise ValueError("materialized origin audio does not match its receipt")
        origin_bindings = {
            "request_id": candidate["request_id"],
            "history_item_id": submission["history_item_id"],
            "provider": "elevenlabs",
            "source": "TTS",
            "returned_text_sha256": candidate["script_sha256"],
            "voice_id": candidate["voice_id"],
            "audio_sha256": candidate["audio_sha256"],
        }
        if any(receipt.get(key) != value for key, value in origin_bindings.items()):
            raise ValueError("materialized origin receipt does not close candidate and submission bindings")
        if (
            receipt.get("authenticated_readback") != "TEST_NOT_AUTHORITY"
            or receipt.get("qualification_status") != "NOT_QUALIFIED"
            or receipt.get("status") != "TTS_REQUIRED"
            or receipt.get("operations") != {
                "provider_dispatch": "BLOCKED", "render": "BLOCKED",
                "telegram": "BLOCKED", "publication": "BLOCKED",
            }
        ):
            raise ValueError("injected readback service returned an unsafe authority claim")
        return {
            "candidate_receipt_sha256": candidate["receipt_sha256"],
            "submission_receipt_sha256": submission["receipt_sha256"],
            "origin_receipt_sha256": receipt["receipt_sha256"],
            "audio_path": str(origin.audio_path),
            "receipt_path": str(origin.receipt_path),
            "authenticated_readback": "TEST_NOT_AUTHORITY",
            "qualification_status": "NOT_QUALIFIED",
            "status": "TTS_REQUIRED",
            "operations": dict(receipt["operations"]),
        }

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
        executor_config=None,
        motion_routes=None,
        motion_policy=None,
    ):
        """Activate the sole compiled writer only at the image-generation hand-off.

        The caller must still provide independently approved, frozen inputs. This
        method intentionally dispatches no provider request and performs no
        approval transition, so activation cannot turn a plan or draft into media.
        """
        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state != EpisodeState.GENERATING_IMAGES:
            raise ValueError(
                "compiled production activates only from GENERATING_IMAGES"
            )
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
            executor_config=executor_config,
            motion_routes=motion_routes,
            motion_policy=motion_policy,
        )

    def issue_compiled_live_preflight(
        self, episode_id: str, pipeline, price_resolver, *, reviewer: str
    ):
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
        self,
        episode_id: str,
        pipeline,
        provider,
        *,
        authorizations=None,
        prices=None,
        on_completed=None,
    ) -> dict[str, Any]:
        """Dispatch compiled work once, then stop at independent visual QA."""
        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state is not EpisodeState.GENERATING_IMAGES:
            raise ValueError(
                "compiled baseline dispatch requires GENERATING_IMAGES state"
            )
        if pipeline.episode.audio.mode == "LIVE":
            jobs = pipeline.baseline_jobs()
            request_ids = {job.request_id for job in jobs}
            if (
                not isinstance(authorizations, dict)
                or not isinstance(prices, dict)
                or set(authorizations) != request_ids
                or set(prices) != request_ids
            ):
                raise ValueError(
                    "every compiled LIVE job requires exact authority and fresh price"
                )
        dispatch_options = {"authorizations": authorizations, "prices": prices}
        if on_completed is not None:
            dispatch_options["on_completed"] = on_completed
        receipts = await pipeline.dispatch_baselines(provider, **dispatch_options)
        pipeline.run.executor.sync_cost_ledger(
            fs.paths.costs_json, episode_id=episode_id, budget=self.config.budget
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
        return {
            "receipts": receipts,
            "qa_packets": qa_packets,
            "state": state.current_state.value,
        }

    def record_compiled_visual_qa(
        self, episode_id: str, pipeline, decisions
    ) -> dict[str, Any]:
        """Persist independent hash-bound visual verdicts and open animation only on PASS."""
        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state is not EpisodeState.VISUAL_QA:
            raise ValueError("compiled visual QA requires VISUAL_QA state")
        packets = pipeline.prepare_qa_packets()
        by_scene = {}
        for decision in decisions:
            if not isinstance(decision, dict) or set(decision) != {
                "scene_id",
                "result_sha256",
                "approved",
                "reviewer",
            }:
                raise ValueError("visual QA decision schema is invalid")
            scene_id = decision["scene_id"]
            if scene_id in by_scene:
                raise ValueError("visual QA decision duplicated a scene")
            if (
                not isinstance(decision["approved"], bool)
                or not isinstance(decision["reviewer"], str)
                or not decision["reviewer"].strip()
            ):
                raise ValueError(
                    "visual QA decision requires boolean verdict and reviewer"
                )
            packet = packets.get(scene_id)
            if packet is None or packet["result_sha256"] != decision["result_sha256"]:
                raise ValueError("visual QA decision does not bind a current candidate")
            by_scene[scene_id] = decision
        if set(by_scene) != set(packets):
            raise ValueError(
                "independent visual QA must decide every current candidate"
            )
        approved = {}
        for scene_id, decision in by_scene.items():
            asset = pipeline.record_visual_qa(
                scene_id,
                decision["result_sha256"],
                decision["approved"],
                decision["reviewer"],
            )
            approved[scene_id] = str(asset.path) if asset else ""
        if (
            all(decision["approved"] for decision in by_scene.values())
            and pipeline.render_ready()
        ):
            state.transition_to(
                EpisodeState.PLANNING_ANIMATION,
                agent="CompiledProduction",
                note="all compiled visual QA passed",
            )
            state.save(fs.paths.state_json)
        return {"approved": approved, "state": state.current_state.value}

    def _compiled_render_contract(self, episode_id: str, pipeline, renderer, hold: int) -> dict[str, Any]:
        """Close every mutable render input before ownership or recovery reuse."""
        if hold not in (3, 4, 5):
            raise ValueError("compiled render closing hold must be 3 to 5 seconds")
        manifest = pipeline.approved_manifest()
        audio = pipeline.episode.audio
        scenes = pipeline.render_scenes(manifest)
        scene_hashes = []
        for scene in scenes:
            image_sha256 = getattr(getattr(scene, "image", None), "sha256", None)
            clip = getattr(scene, "clip", None)
            clip_sha256 = getattr(clip, "sha256", None)
            if not isinstance(image_sha256, str) or (clip is not None and not isinstance(clip_sha256, str)):
                raise TypeError("compiled render scene hash is unavailable")
            scene_hashes.append({"image_sha256": image_sha256, "clip_sha256": clip_sha256})
        config = self.__dict__.get("config")
        canonical_root = None
        if config is not None:
            # Do not resolve: receipt reads reject reparse points before opening.
            canonical_root = str(EpisodeFS(episode_id, config).paths.root.absolute())
        renderer_config = {
            name: getattr(renderer, name, None)
            for name in (
                "width", "height", "fps", "filter_complex_threads",
                "scene_workers", "encoder_threads",
            )
        }
        if not isinstance(getattr(manifest, "checksum", None), str) or not isinstance(
            getattr(audio, "sha256", None), str
        ):
            raise TypeError("compiled render immutable input hash is unavailable")
        return {
            "schema": "compiled-render-contract-v1",
            "canonical_episode_root": canonical_root,
            "manifest_sha256": manifest.checksum,
            "audio_sha256": audio.sha256,
            "scene_hashes": scene_hashes,
            "renderer_config": renderer_config,
            "hold_seconds": hold,
        }

    @staticmethod
    def _compiled_render_contract_sha256(contract: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _canonical_render_json(value: dict[str, Any]) -> bytes:
        from decimal import Decimal

        def json_value(item):
            if isinstance(item, Decimal):
                return float(item)
            raise TypeError(f"render evidence cannot encode {type(item).__name__}")

        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=json_value,
        ).encode("utf-8")

    @classmethod
    def _read_canonical_render_json(
        cls, path: Path, required_keys: set[str], label: str, *, canonical_episode_root: Path,
    ) -> tuple[dict[str, Any], str]:
        """Read canonical evidence from one reparse-safe immutable byte snapshot."""
        from src.hybrid.external_trust_root import (
            TrustRootVerificationError,
            load_strict_json_snapshot,
        )

        try:
            value, file_sha256, raw = load_strict_json_snapshot(
                path, label, contained_by=canonical_episode_root, include_bytes=True,
            )
        except TrustRootVerificationError as error:
            raise ValueError(f"{label} is unavailable") from error
        if not isinstance(value, dict) or set(value) != required_keys or raw != cls._canonical_render_json(value):
            raise ValueError(f"{label} is not strict canonical evidence")
        return value, file_sha256

    @classmethod
    def _write_exclusive_render_json(
        cls, path: Path, value: dict[str, Any], label: str, *, canonical_episode_root: Path,
    ) -> None:
        """Write only below the guarded lexical episode root."""
        from src.hybrid.external_trust_root import (
            TrustRootVerificationError,
            load_strict_json_snapshot,
        )
        from src.hybrid.locks import reject_reparse_path

        encoded = cls._canonical_render_json(value)
        try:
            root = reject_reparse_path(Path(canonical_episode_root))
            candidate = reject_reparse_path(Path(path))
            candidate.relative_to(root)
        except ValueError as error:
            raise ValueError(f"{label} rejects symlink or path outside canonical episode") from error
        try:
            descriptor = os.open(
                candidate,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
        except FileExistsError as error:
            try:
                _existing, _existing_sha256, raw = load_strict_json_snapshot(
                    candidate, label, contained_by=root, include_bytes=True,
                )
            except TrustRootVerificationError as snapshot_error:
                raise ValueError(f"{label} is unavailable") from snapshot_error
            if raw == encoded:
                return
            raise ValueError(f"{label} already exists") from error
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())

    def _compiled_render_recovery_evidence(
        self, fs, render_contract, contract_sha256, intent_path, completion_path,
    ) -> tuple[dict[str, Any], str, dict[str, Any], str]:
        """Authenticate the prepared intent, renderer completion and final bytes."""
        from src.hybrid.artifacts import sha256

        canonical_episode_root = Path(render_contract["canonical_episode_root"])
        intent_keys = {"schema", "render_contract", "render_contract_sha256"}
        intent, intent_sha256 = self._read_canonical_render_json(
            intent_path, intent_keys, "compiled render intent",
            canonical_episode_root=canonical_episode_root,
        )
        if (
            intent["schema"] != "compiled-render-intent-v1"
            or intent["render_contract"] != render_contract
            or intent["render_contract_sha256"] != contract_sha256
        ):
            raise ValueError("compiled render intent does not bind current inputs; successor required")
        completion_keys = {
            "schema", "intent_sha256", "render_contract_sha256", "output_sha256",
            "renderer_receipt", "renderer_receipt_sha256",
        }
        completion, completion_sha256 = self._read_canonical_render_json(
            completion_path, completion_keys, "renderer completion receipt",
            canonical_episode_root=canonical_episode_root,
        )
        renderer_receipt = completion["renderer_receipt"]
        if (
            completion["schema"] != "compiled-renderer-completion-v1"
            or completion["intent_sha256"] != intent_sha256
            or completion["render_contract_sha256"] != contract_sha256
            or not isinstance(renderer_receipt, dict)
            or completion["renderer_receipt_sha256"]
            != hashlib.sha256(self._canonical_render_json(renderer_receipt)).hexdigest()
            or renderer_receipt.get("output_sha256") != completion["output_sha256"]
            or not isinstance(completion["output_sha256"], str)
            or len(completion["output_sha256"]) != 64
            or not fs.paths.final_video.is_file()
            or sha256(fs.paths.final_video) != completion["output_sha256"]
        ):
            raise ValueError("renderer completion receipt is not bound to current final bytes")
        return intent, intent_sha256, renderer_receipt, completion_sha256


    @staticmethod
    def _advance_compiled_render_state(state: EpisodeStateStore) -> None:
        if state.current_state is EpisodeState.PLANNING_ANIMATION:
            state.transition_to(EpisodeState.LOCAL_ANIMATION, agent="CompiledRecovery")
        if state.current_state is EpisodeState.LOCAL_ANIMATION:
            state.transition_to(EpisodeState.ASSEMBLING, agent="CompiledRecovery")
        if state.current_state is EpisodeState.ASSEMBLING:
            state.transition_to(EpisodeState.FINAL_QA, agent="CompiledRecovery")
        if state.current_state is not EpisodeState.FINAL_QA:
            raise ValueError("compiled rendering requires PLANNING_ANIMATION state")

    def render_compiled_video(
        self, episode_id: str, pipeline, renderer, *, hold: int = 4,
        _expected_render_contract: dict[str, Any] | None = None,
        _ownership_assertion=None,
    ) -> dict[str, Any]:
        """Compose a subtitle-free local delivery master from the approved manifest only."""
        from src.hybrid.locks import file_lock

        fs = EpisodeFS(episode_id, self.config)
        # The crash-released OS lock covers recovery validation, rendering,
        # receipt publication and state persistence, across processes.
        with file_lock(fs.paths.qa_dir / ".compiled-render.lock"):
            return self._render_compiled_video_locked(
                episode_id, pipeline, renderer, hold=hold,
                expected_render_contract=_expected_render_contract,
                ownership_assertion=_ownership_assertion,
            )

    def _render_compiled_video_locked(
        self, episode_id: str, pipeline, renderer, *, hold: int,
        expected_render_contract: dict[str, Any] | None, ownership_assertion=None,
    ) -> dict[str, Any]:
        def assert_owner() -> None:
            if ownership_assertion is not None:
                ownership_assertion()

        assert_owner()
        fs = EpisodeFS(episode_id, self.config)
        render_contract = self._compiled_render_contract(episode_id, pipeline, renderer, hold)
        if expected_render_contract is not None and render_contract != expected_render_contract:
            raise ValueError("compiled render contract changed while waiting for admission")
        contract_sha256 = self._compiled_render_contract_sha256(render_contract)
        state = EpisodeStateStore.load(fs.paths.state_json)
        receipt_path = fs.paths.qa_dir / "compiled_render_receipt.json"
        intent_path = fs.paths.qa_dir / "compiled_render_intent.json"
        completion_path = fs.paths.qa_dir / "renderer_completion_receipt.json"
        director_receipt_keys = {
            "schema", "intent_sha256", "completion_sha256", "render_contract",
            "render_contract_sha256", "renderer_receipt",
        }
        if intent_path.is_file() and completion_path.is_file():
            intent, intent_sha256, renderer_receipt, completion_sha256 = self._compiled_render_recovery_evidence(
                fs, render_contract, contract_sha256, intent_path, completion_path
            )
            director_receipt = {
                "schema": "compiled-director-render-receipt-v1",
                "intent_sha256": intent_sha256,
                "completion_sha256": completion_sha256,
                "render_contract": render_contract,
                "render_contract_sha256": contract_sha256,
                "renderer_receipt": renderer_receipt,
            }
            if receipt_path.is_file():
                persisted, _persisted_sha256 = self._read_canonical_render_json(
                    receipt_path, director_receipt_keys, "compiled Director receipt",
                    canonical_episode_root=Path(render_contract["canonical_episode_root"]),
                )
                if persisted != director_receipt:
                    raise ValueError("compiled Director receipt does not bind renderer completion")
            else:
                assert_owner()
                self._write_exclusive_render_json(
                    receipt_path, director_receipt, "compiled Director receipt",
                    canonical_episode_root=Path(render_contract["canonical_episode_root"]),
                )
            assert_owner()
            self._advance_compiled_render_state(state)
            state.save(fs.paths.state_json)
            return director_receipt
        if intent_path.exists() or completion_path.exists() or fs.paths.final_video.exists():
            raise ValueError("unbound render artifacts are quarantined; successor required")
        if state.current_state is not EpisodeState.PLANNING_ANIMATION:
            raise ValueError("compiled rendering requires PLANNING_ANIMATION state")
        if hold not in (3, 4, 5):
            raise ValueError("compiled render closing hold must be 3 to 5 seconds")
        manifest = pipeline.approved_manifest()
        scenes = pipeline.render_scenes(manifest)
        intent = {
            "schema": "compiled-render-intent-v1",
            "render_contract": render_contract,
            "render_contract_sha256": contract_sha256,
        }
        assert_owner()
        self._write_exclusive_render_json(
            intent_path, intent, "compiled render intent",
            canonical_episode_root=Path(render_contract["canonical_episode_root"]),
        )
        intent_sha256 = hashlib.sha256(self._canonical_render_json(intent)).hexdigest()
        assert_owner()
        state.transition_to(
            EpisodeState.LOCAL_ANIMATION,
            agent="CompiledProduction",
            note="local render started",
        )
        state.save(fs.paths.state_json)
        assert_owner()
        state.transition_to(
            EpisodeState.ASSEMBLING,
            agent="CompiledProduction",
            note="assembling compiled master",
        )
        assert_owner()
        state.save(fs.paths.state_json)
        assert_owner()
        receipt = renderer.render_compiled(
            pipeline,
            scenes,
            manifest,
            pipeline.episode.audio,
            None,
            fs.paths.final_video,
            hold=hold,
            completion_receipt_path=completion_path,
            render_intent_sha256=intent_sha256,
            render_contract_sha256=contract_sha256,
            canonical_episode_root=Path(render_contract["canonical_episode_root"]),
        )
        # The renderer must have committed the completion before returning;
        # return values and present media alone are intentionally unauthenticated.
        _intent, _intent_sha256, renderer_receipt, _completion_sha256 = self._compiled_render_recovery_evidence(
            fs, render_contract, contract_sha256, intent_path, completion_path
        )
        if receipt != renderer_receipt or receipt.get("subtitles_sha256") is not None:
            raise ValueError("compiled renderer return does not match durable completion")
        return self._render_compiled_video_locked(
            episode_id, pipeline, renderer, hold=hold, expected_render_contract=render_contract,
            ownership_assertion=ownership_assertion,
        )

    def _compiled_render_state(
        self,
    ) -> tuple[dict[str, tuple[str, asyncio.Task]], asyncio.Semaphore]:
        """Support lightweight Director test instances created without __init__."""
        flights = self.__dict__.get("_compiled_render_flights")
        if flights is None:
            flights = self.__dict__["_compiled_render_flights"] = {}
        semaphore = self.__dict__.get("_compiled_render_admission")
        if semaphore is None:
            capacity = self.__dict__.get("_compiled_render_pool_size", 1)
            if type(capacity) is not int or capacity < 1:
                raise ValueError("compiled render pool size must be a positive integer")
            semaphore = self.__dict__["_compiled_render_admission"] = asyncio.Semaphore(capacity)
        return flights, semaphore

    async def _admit_compiled_render(
        self, episode_id: str, pipeline, renderer, hold: int, render_contract: dict[str, Any],
        ownership_assertion=None,
    ) -> dict[str, Any]:
        """Run exactly one synchronous render while holding its full thread budget."""
        _flights, semaphore = self._compiled_render_state()
        acquired = False
        try:
            # One atomic permit avoids partial weighted-acquisition deadlocks.
            await semaphore.acquire()
            acquired = True
            # Keep all FFmpeg setup, scene encoding, muxing, and receipt writes
            # together in the renderer's synchronous unit. Shielding happens at
            # the caller boundary so cancellation never frees episode ownership
            # while this worker thread still runs.
            return await asyncio.to_thread(
                self.render_compiled_video, episode_id, pipeline, renderer, hold=hold,
                _expected_render_contract=render_contract,
                _ownership_assertion=ownership_assertion,
            )
        finally:
            if acquired:
                semaphore.release()

    async def render_compiled_video_async(
        self, episode_id: str, pipeline, renderer, *, hold: int = 4, ownership_assertion=None,
    ) -> dict[str, Any]:
        """Await a single-writer, cancellation-safe off-thread compiled render."""
        flights, _admission = self._compiled_render_state()
        render_contract = self._compiled_render_contract(episode_id, pipeline, renderer, hold)
        contract_sha256 = self._compiled_render_contract_sha256(render_contract)
        existing = flights.get(episode_id)
        if existing is None:
            flight = asyncio.create_task(
                self._admit_compiled_render(
                    episode_id, pipeline, renderer, hold, render_contract, ownership_assertion,
                ),
                name=f"compiled-render:{episode_id}",
            )
            flights[episode_id] = (contract_sha256, flight)

            def release_owner(completed: asyncio.Task) -> None:
                if flights.get(episode_id) == (contract_sha256, completed):
                    flights.pop(episode_id, None)

            flight.add_done_callback(release_owner)
        else:
            active_contract_sha256, flight = existing
            if active_contract_sha256 != contract_sha256:
                raise ValueError("compiled render contract conflict for in-flight episode")
        # A cancelled caller must not cancel the owned encode. The retained
        # flight remains the retry target until its thread has fully unwound.
        return await asyncio.shield(flight)

    async def finalize_compiled_delivery(
        self, episode_id: str, pipeline, renderer, *, hold: int = 4
    ) -> dict[str, Any]:
        """Render a compiled master and materialize all evidence required by final QA."""
        receipt = await self.render_compiled_video_async(
            episode_id, pipeline, renderer, hold=hold
        )
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
        delivery = await self.finalize_compiled_delivery(
            episode_id, pipeline, renderer, hold=hold
        )
        evidence = self.record_production_evidence_qa(
            episode_id, published_script_hashes=published_script_hashes
        )
        if evidence.get("approved") is not True:
            return {
                "delivery": delivery,
                "production_evidence_qa": evidence,
                "post_production_narrative_qa": None,
                "final_render_qa": None,
            }
        narrative = self.record_post_production_narrative_qa(episode_id)
        if narrative.get("approved") is not True:
            return {
                "delivery": delivery,
                "production_evidence_qa": evidence,
                "post_production_narrative_qa": narrative,
                "final_render_qa": None,
            }
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
        self.thumbnail_copy(
            theme
        )  # Fail before filesystem/model work if the passage is missing.

        # Generate episode ID if not provided
        if not episode_id:
            import time

            episode_id = f"EP{int(time.time())}"

        # Set up episode filesystem (§15)
        fs = EpisodeFS(episode_id, self.config)
        if fs.exists():
            raise FileExistsError(
                f"episode already exists: {episode_id}; use the explicit resume workflow so request.json is never overwritten"
            )
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
            episode_id,
            theme,
            fs,
            state,
            guard,
            require_telegram_approval=require_telegram_approval,
        )

        return {
            "episode_id": episode_id,
            "theme": theme,
            "state": state.current_state.value,
            "plan": result,
            "budget_remaining": ledger.remaining,
        }

    @track_episode_usage
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
            state.transition_to(
                EpisodeState.FAILED,
                agent=self.research.name,
                note=research_result.error,
            )
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
        state.transition_to(
            EpisodeState.WAITING_PLAN_APPROVAL,
            agent=self.name,
            note="Pre-production plan ready",
        )
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
                alt[
                    "option"
                ]: f"{alt['title']} — {alt['description']} (~${alt['estimated_cost']:.2f})"
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
                    message,
                    valid_responses=list(options.keys()) + ["CANCELAR"],
                )
                plan["budget_approval"] = {
                    "approved": approval.approved,
                    "response": approval.response,
                    "timed_out": approval.timed_out,
                }
                if not approval.approved:
                    state.transition_to(
                        EpisodeState.PAUSED,
                        agent=self.name,
                        note=f"Budget approval not granted: {approval.reason}",
                    )
                    state.save(fs.paths.state_json)
                    await asyncio.to_thread(_write_json_file, fs.paths.plan_json, plan)
                    return plan
            else:
                self.approval_gate.send_message(message)
        elif require_telegram_approval:
            # Even within budget, send the plan for visibility (§20) but
            # don't block unless explicitly configured to require sign-off.
            self.approval_gate.send_message(
                f"📋 Plano de produção pronto:\n\n{report_text}"
            )

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
            if (
                not budget_check.get("within_budget", False)
                and not explicit_over_budget_approval
            ):
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
            from src.approval.receipts import require_plan_approval

            require_plan_approval(fs.paths.plan_json)
            return await self._run_production(episode_id, fs, state)
        elif approval_type == "budget":
            # Budget override approved — continue cloud generation
            state.transition_to(
                EpisodeState.CLOUD_VIDEO_GENERATION,
                agent=self.name,
                note="budget approved",
            )
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
        self,
        episode_id: str,
        *,
        published_script_hashes: set[str] | frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """Persist independent originality, caption, source and manifest evidence before final QA."""
        from src.qa.production_evidence import ProductionEvidenceQA
        from src.qa.published_inventory import PublishedInventory

        fs = EpisodeFS(episode_id, self.config)
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state is not EpisodeState.FINAL_QA:
            raise ValueError("production evidence QA requires FINAL_QA state")
        inventory = PublishedInventory.scan(
            self.config.episodes_dir, exclude_episode_id=episode_id
        )
        result = ProductionEvidenceQA().review(
            script_path=fs.paths.script_dir / "script.json",
            manifest_path=fs.paths.compiled_dir / "manifest.json",
            captions_path=fs.paths.captions_vtt,
            metadata_path=fs.paths.metadata_dir / "metadata.json",
            published_script_hashes=set(published_script_hashes)
            | inventory.script_hashes,
            published_artifact_hashes=inventory.artifact_hashes,
        )
        report = {
            "approved": result.approved,
            "findings": list(result.findings),
            "report": result.report,
        }
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
        report = {
            "approved": result.approved,
            "findings": list(result.findings),
            "report": result.report,
        }
        _write_json_file(fs.paths.qa_dir / "post_production_narrative_qa.json", report)
        return report

    async def prepare_delivery_sidecars(
        self, episode_id: str, pipeline
    ) -> dict[str, Any]:
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
        if (
            not isinstance(scenes, list)
            or not scenes
            or not isinstance(narration, str)
            or not narration.strip()
        ):
            raise ValueError(
                "compiled sidecars require persisted narration and storyboard"
            )
        manifest = pipeline.approved_manifest()
        frames = tuple(pipeline.episode.frames)
        if len(frames) != len(manifest.assets) or [
            scene.get("scene_id") for scene in scenes
        ] != [frame.scene_id for frame in frames]:
            raise ValueError("sidecars require the active compiled scene order")
        images = []
        for frame, asset in zip(frames, manifest.assets, strict=True):
            if not asset.path.is_file():
                raise ValueError("compiled thumbnail asset is missing")
            images.append({"scene_id": frame.scene_id, "image_path": str(asset.path)})
        timestamps = [
            {
                "start": scene.get("start"),
                "end": scene.get("end"),
                "text": scene.get("narration", ""),
            }
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
        headline, subtitle, book_subtitle = self.thumbnail_copy(
            str(request.get("theme", ""))
        )
        thumbnail = await self.thumbnail.run(
            episode_id=episode_id,
            images=images,
            scenes=scenes,
            headline=headline,
            subtitle=subtitle,
            book_subtitle=book_subtitle,
            thumbnails_dir=str(fs.paths.thumbnails_dir),
        )
        thumbnail_path = (
            Path(thumbnail.data.get("thumbnail_path", ""))
            if thumbnail.success
            else None
        )
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
        if (
            not metadata.success
            or not (fs.paths.metadata_dir / "metadata.json").is_file()
        ):
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
        after_persist=None,
    ) -> dict[str, Any]:
        """Send one frozen review artifact for the currently-open human gate.

        The thumbnail is always sent first. A later call in the video state
        requires the persisted exact thumbnail approval; no path sends both.
        """
        from src.approval.receipts import ApprovalReceipt, load_approval_receipt
        from src.delivery.controller import DeliveryController

        fs = EpisodeFS(episode_id, self.config)
        if (
            not self.config.approval_delivery_chat_id.strip()
            or chat_id != self.config.approval_delivery_chat_id
        ):
            raise ValueError("approval delivery requires the configured destination")
        state = EpisodeStateStore.load(fs.paths.state_json)
        if state.current_state not in {
            EpisodeState.WAITING_THUMBNAIL_APPROVAL,
            EpisodeState.WAITING_VIDEO_APPROVAL,
        }:
            raise ValueError("approval delivery requires an active thumbnail or video gate")

        def delivery_identity(kind, path):
            receipt_path = fs.paths.qa_dir / "delivery" / f"{kind}.json"
            if receipt_path.is_file():
                try:
                    receipt = ApprovalReceipt(**_read_json_file(receipt_path)["approval"])
                    receipt.verify()
                except (KeyError, TypeError, ValueError) as error:
                    raise ValueError("delivery receipt no longer binds current media") from error
                if Path(receipt.artifact_path).resolve() != Path(path).resolve():
                    raise ValueError("delivery path changed")
                return receipt
            return ApprovalReceipt.approve(kind, path, "delivery-preflight")

        thumbnail = delivery_identity("thumbnail", thumbnail_path)
        controller = DeliveryController(messenger)
        if state.current_state is EpisodeState.WAITING_THUMBNAIL_APPROVAL:
            return await controller.deliver_for_approval(
                chat_id=chat_id,
                episode_id=episode_id,
                thumbnail=thumbnail,
                receipt_dir=fs.paths.qa_dir / "delivery",
                after_persist=after_persist,
            )
        thumbnail_approval = load_approval_receipt(fs.paths.qa_dir / "approval-thumbnail.json")
        thumbnail_approval.verify()
        if (
            thumbnail_approval.artifact_kind != "thumbnail"
            or thumbnail_approval.artifact_path != thumbnail.artifact_path
            or thumbnail_approval.artifact_sha256 != thumbnail.artifact_sha256
        ):
            raise ValueError("persisted thumbnail approval does not bind delivered thumbnail")
        video = delivery_identity("video", fs.paths.final_video)
        return await controller.deliver_for_approval(
            chat_id=chat_id,
            episode_id=episode_id,
            thumbnail=thumbnail,
            video=video,
            thumbnail_approval=thumbnail_approval,
            receipt_dir=fs.paths.qa_dir / "delivery",
            after_persist=after_persist,
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
            raise ValueError(
                "passing production evidence QA is required before final render QA"
            ) from error
        if evidence.get("approved") is not True:
            raise ValueError(
                "passing production evidence QA is required before final render QA"
            )
        narrative_path = fs.paths.qa_dir / "post_production_narrative_qa.json"
        try:
            narrative = _read_json_file(narrative_path)
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise ValueError(
                "passing post-production narrative QA is required before final render QA"
            ) from error
        if narrative.get("approved") is not True:
            raise ValueError(
                "passing post-production narrative QA is required before final render QA"
            )
        result = (checker or FinalRenderQA()).review(video_path, render_receipt)
        report = {
            "approved": result.approved,
            "findings": list(result.findings),
            "report": result.report,
        }
        await asyncio.to_thread(
            _write_json_file, fs.paths.qa_dir / "final_render_qa.json", report
        )
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
            raise ValueError(
                f"{artifact_kind} approval requires {expected_state.value} state"
            )
        approval_path = fs.paths.qa_dir / f"approval-{artifact_kind}.json"
        receipt = ApprovalController().confirm_delivery(
            episode_id=episode_id,
            artifact_kind=artifact_kind,
            artifact_path=artifact_path,
            delivery_receipt_path=delivery_receipt_path,
            approval_receipt_path=approval_path,
            expected_chat_id=self.config.approval_delivery_chat_id,
            trusted_sender_ids=self.config.approval_delivery_trusted_sender_ids,
            inbound_source=self.inbound_approval_source,
        )
        state.transition_to(
            next_state,
            agent="ApprovalController",
            note=f"{artifact_kind} delivery approved",
        )
        state.save(fs.paths.state_json)
        return receipt

    def reject_delivered_artifact(
        self, episode_id: str, *, artifact_id: str, reason: str
    ) -> dict[str, Any]:
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

    @track_episode_usage
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
            target_duration_s=plan.get("duration_plan", {}).get(
                "recommended_duration_s", 180
            ),
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
            state.transition_to(
                EpisodeState.FAILED, agent="ScriptQA", note="script packet missing"
            )
            state.save(fs.paths.state_json)
            return {"error": "script packet missing"}
        qa_result = ScriptQAAgent().review(
            await asyncio.to_thread(_read_json_file, packet_path)
        )
        qa_record = {
            "approved": qa_result.approved,
            "findings": list(qa_result.findings),
        }
        await asyncio.to_thread(
            _write_json_file, fs.paths.script_dir / "script_qa.json", qa_record
        )
        if not qa_result.approved:
            state.transition_to(
                EpisodeState.FAILED,
                agent="ScriptQA",
                note="; ".join(qa_result.findings),
            )
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

        state.transition_to(
            EpisodeState.GENERATING_IMAGES,
            agent=self.name,
            note="production ready for image gen",
        )
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
                "requires": [
                    "approved_audio",
                    "source_manifest",
                    "executor",
                    "provider",
                ],
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
        logger.info(
            f"Visual strategy: {gen_summary['generative_clips_used']} generative clips, "
            f"{gen_summary['generative_seconds_used']}s of {gen_summary['max_seconds']}s max"
        )

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
        captions_files = (
            captions_result.data.get("files", {}) if captions_result.success else {}
        )

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
        state.transition_to(
            EpisodeState.FINAL_QA, agent=self.name, note="video assembled"
        )
        state.save(fs.paths.state_json)

        # Transition to WAITING_FINAL_APPROVAL
        state.transition_to(
            EpisodeState.WAITING_FINAL_APPROVAL,
            agent=self.name,
            note="ready for approval",
        )
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
        logger.info(
            "Legacy RunPod cleanup disabled; use transactional request recovery"
        )
        return []

    @property
    def name(self) -> str:
        return "Director"
