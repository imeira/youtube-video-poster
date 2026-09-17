"""Public fresh EP8 studio factory. No import or publication route is reachable.

The control document is the atomic authority for a revision and both human gates.
Completed stages bind bytes; provider ambiguity remains pending for reconciliation.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import math
import os
import re
import subprocess
import time
from dataclasses import asdict
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, atomic_json, digest, sha256
from src.hybrid.locks import try_lock

TITLE = "A promessa de um filho para Abraão e Sara"
THEME = TITLE + " — Gênesis 15–18"


HEADLINE = "UMA PROMESSA IMPOSSÍVEL?"
SUBTITLE = "— Gênesis 15–18"


def implementation_paths(mode):
    """Resolve shipped implementation from the package, independent of cwd."""
    root = Path(__file__).resolve().parents[1]
    paths = [root / name for name in (
        "hybrid/revision.py", "hybrid/production.py", "hybrid/render.py",
        "qa/final_render.py", "qa/post_production.py", "qa/production_evidence.py",
        "agents/script_qa.py", "agents/thumbnail.py",
        "providers/notification/telegram_provider.py", "hybrid/assets/ep8_promise_v1.json")]
    if mode == "LIVE":
        paths.append(root / "hybrid/revision_live.py")
    return paths


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def fingerprint(path):
    """64-bit difference hash catches resized/recompressed rejected stills."""
    with Image.open(path) as image:
        pixels = image.convert("L").resize((9, 8)).tobytes()
    return pixel_fingerprint(pixels)


def pixel_fingerprint(pixels):
    bits = [pixels[y * 9 + x] > pixels[y * 9 + x + 1] for y in range(8) for x in range(8)]
    return f"{sum(int(bit) << i for i, bit in enumerate(bits)):016x}"


def identity(path):
    path = Path(path)
    value = dict(path=str(path), sha256=sha256(path))
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        value["perceptual"] = fingerprint(path)
    elif path.suffix.lower() == ".mp4":
        from src.hybrid.render import probe
        duration = float(probe(path)["format"]["duration"])
        samples = []
        for fraction in (.1, .5, .9):
            result = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-ss", str(duration * fraction),
                "-i", str(path), "-frames:v", "1", "-vf", "scale=9:8,format=gray", "-threads", "1",
                "-f", "rawvideo", "pipe:1"], capture_output=True, check=True, timeout=60)
            if len(result.stdout) != 72:
                raise ValueError("video perceptual sample missing")
            samples.append(pixel_fingerprint(result.stdout))
        value["video_perceptual"] = samples
    return value


class RevisionHarness:
    """Single-workspace owner; deployment dependencies must be explicitly injected."""

    def __init__(self, workspace, *, dependencies=None, test_options=None):
        self.root = Path(workspace).resolve()
        self.path = self.root / "revision.json"
        self.dependencies = dependencies
        self.test_options = test_options or {}
        self.lock = None

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = try_lock(self.root / "revision.lock")
        if self.lock is None:
            raise ValueError("revision workspace busy")
        return self

    def __exit__(self, *args):
        os.close(self.lock)
        self.lock = None

    def save(self):
        if self.lock is None:
            raise ValueError("workspace lock required")
        atomic_json(self.path, self.control)

    def load(self):
        self.control = read(self.path)
        plan = self.control["plan"]
        if digest(plan) != self.control["plan_hash"]:
            raise ValueError("plan hash mismatch")
        if any(str(p.resolve()) not in plan["bindings"] for p in implementation_paths(plan["mode"])):
            raise ValueError("implementation bindings missing; new plan required")
        for path, expected in plan["bindings"].items():
            if sha256(path) != expected:
                raise ValueError("input binding changed")
        for receipt in self.control["stages"].values():
            if receipt["status"] == "COMPLETE":
                for path, expected in receipt["outputs"].items():
                    if sha256(path) != expected:
                        raise ValueError("completed stage hash mismatch: " + path)
        return plan

    def create_plan(self, *, mode, request, predecessors=None, references=None, adapter=None,
                    deployment=None, image_workers=3, qa_workers=2, ffmpeg_threads=1, chat_id="TEST"):
        if self.path.exists():
            raise ValueError("plan exists; reject to create a new revision")
        if mode not in {"TEST", "LIVE"} or not request.strip():
            raise ValueError("explicit mode and request required")
        if any(type(n) is not int or not 1 <= n <= 8 for n in (image_workers, qa_workers, ffmpeg_threads)):
            raise ValueError("worker bounds must be integers from 1 to 8")
        if ffmpeg_threads != 1:
            raise ValueError("this single-encode route requires one FFmpeg thread")
        rejected = read(predecessors) if predecessors else []
        self._validate_predecessors(rejected)
        bindings = {str(Path(predecessors).resolve()): sha256(predecessors)} if predecessors else {}
        bindings.update({str(p.resolve()): sha256(p) for p in implementation_paths(mode)})
        contract = None
        if mode == "LIVE":
            if deployment is None:
                raise ValueError("LIVE requires an explicit deployment contract file")
            contract = read(deployment)
            bindings[str(Path(deployment).resolve())] = sha256(deployment)
            if contract.get("adapter") == "src.hybrid.revision_live:factory":
                from src.hybrid.revision_live import validate_contract, SCRIPT
                validate_contract(contract)
                bindings[str(SCRIPT.resolve())] = sha256(SCRIPT)
            elif (set(contract) != {"adapter", "endpoint", "image_cost", "non_image_reserve"}
                    or not contract["adapter"] or not contract["endpoint"]):
                raise ValueError("invalid LIVE deployment contract")
            for key in ("image_cost", "non_image_reserve"):
                amount = Decimal(contract[key])
                if not amount.is_finite() or amount < 0 or amount >= 6:
                    raise ValueError("invalid LIVE deployment budget")
            if Decimal(contract["image_cost"]) <= 0 or (adapter and adapter != contract["adapter"]):
                raise ValueError("LIVE endpoint price/adapter mismatch")
            adapter = contract["adapter"]
        if references:
            refs = read(references)
            bindings[str(Path(references).resolve())] = sha256(references)
        elif mode == "TEST":
            from src.hybrid.revision_fixtures import TestDependencies
            directory = self.root / "canonical"
            directory.mkdir(exist_ok=True)
            refs = TestDependencies(self.root / "fixture-inputs").canonical(directory)
        else:
            raise ValueError("LIVE requires approved canonical references")
        manifest = Manifest.load(refs["manifest"])
        manifest.verify(mode)
        if (set(refs["characters"]) != {"abraham", "sarah"} or not refs.get("authority")
                or not set(refs["characters"].values()) <= {a.sha256 for a in manifest.assets}):
            raise ValueError("approved Abraham/Sarah canonical bindings required")
        bindings.update({str(a.path): a.sha256 for a in (*manifest.assets, manifest.sheet)})
        for path in (Path(refs["manifest"]), manifest.sheet.path.with_suffix(".binding.json")):
            bindings[str(path.resolve())] = sha256(path)
        plan = dict(route="ep8-new-revision-v1", revision=1, mode=mode, request=request,
                    references=refs, predecessors=rejected, bindings=bindings, adapter=adapter,
                    deployment=contract,
                    chat_id=str(chat_id), image_workers=image_workers, qa_workers=qa_workers,
                    ffmpeg_threads=ffmpeg_threads, correction_waves=1, heroes=False, budget_usd="6",
                    provider_contract="transactional-executor-v1; exact-price-and-authority; recover-only",
                    publication_authorized=False)
        self.control = dict(plan=plan, plan_hash=digest(plan), status="WAITING_PLAN_APPROVAL",
                            stages={}, approvals={}, history=[])
        for asset in manifest.assets:
            self.fresh(asset.path)
        self.save()
        return self.status()

    @staticmethod
    def _validate_predecessors(items):
        if not isinstance(items, list):
            raise ValueError("predecessors must be a list of hash identities")
        for item in items:
            if not isinstance(item, dict) or not re.fullmatch(r"[0-9a-f]{64}", item.get("sha256", "")):
                raise ValueError("predecessor SHA256 required")
            if "perceptual" in item and not re.fullmatch(r"[0-9a-f]{16}", item["perceptual"]):
                raise ValueError("invalid perceptual fingerprint")
            if "video_perceptual" in item and (len(item["video_perceptual"]) != 3 or
                    any(not re.fullmatch(r"[0-9a-f]{16}", s) for s in item["video_perceptual"])):
                raise ValueError("invalid video perceptual fingerprint")

    def fresh(self, path):
        candidate = identity(path)
        for old in self.control["plan"]["predecessors"]:
            if candidate["sha256"] == old["sha256"]:
                raise ValueError("rejected artifact byte reuse")
            if "perceptual" in candidate and "perceptual" in old:
                if (int(candidate["perceptual"], 16) ^ int(old["perceptual"], 16)).bit_count() <= 4:
                    raise ValueError("rejected artifact perceptual reuse")
            if "video_perceptual" in candidate and "video_perceptual" in old:
                if all((int(a, 16) ^ int(b, 16)).bit_count() <= 4
                       for a, b in zip(candidate["video_perceptual"], old["video_perceptual"])):
                    raise ValueError("rejected video perceptual reuse")
        return candidate

    def approve_plan(self, plan_hash, reviewer):
        self.load()
        if not reviewer.strip() or plan_hash != self.control["plan_hash"]:
            raise ValueError("exact plan hash and reviewer required")
        if self.control["status"] != "WAITING_PLAN_APPROVAL":
            raise ValueError("plan approval is out of order")
        self.control["plan_approval"] = dict(plan_hash=plan_hash, reviewer=reviewer)
        self.control["status"] = "READY"
        self.save()
        return self.status()

    def status(self):
        plan = self.load()
        return dict(status=self.control["status"], revision=plan["revision"], mode=plan["mode"],
                    plan_hash=self.control["plan_hash"], artifacts=self.control.get("artifacts", {}),
                    approvals=self.control["approvals"], publication_authorized=False)

    def approve(self, kind, artifact_hash, reviewer):
        self.load()
        expected = {"thumbnail": "WAITING_THUMBNAIL_APPROVAL", "video": "WAITING_VIDEO_APPROVAL"}
        if kind not in expected or self.control["status"] != expected[kind]:
            raise ValueError("separate approval gate is out of order")
        artifact = self.control["artifacts"][kind]
        if not reviewer.strip() or artifact_hash != artifact["sha256"] or sha256(artifact["path"]) != artifact_hash:
            raise ValueError("exact artifact hash and reviewer required")
        self.control["approvals"][kind] = dict(reviewer=reviewer, sha256=artifact_hash,
                                                plan_hash=self.control["plan_hash"])
        self.control["status"] = "READY_VIDEO_DELIVERY" if kind == "thumbnail" else "WAITING_FINAL_APPROVAL"
        self.save()
        return self.status()

    def reject(self, reason):
        plan = self.load()
        if not reason.strip():
            raise ValueError("rejection reason required")
        old = {k: v for k, v in self.control.items() if k != "history"}
        old.update(status="SUPERSEDED", rejection_reason=reason)
        rejected = list(plan["predecessors"])
        # Include intermediate generated media, even when the run failed before delivery.
        directory = self.root / f"r{plan['revision']:03d}"
        if directory.exists():
            for path in directory.rglob("*"):
                if path.is_file() and (path.suffix.lower() in {".png", ".jpg", ".wav", ".mp3", ".mp4"}
                                       or path.name == "script.json"):
                    try:
                        rejected.append(identity(path))
                    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
                        # Partial provider/encoder output is still retired by exact bytes.
                        rejected.append(dict(path=str(path), sha256=sha256(path)))
        new = {**plan, "revision": plan["revision"] + 1, "predecessors": rejected,
               "supersedes": self.control["plan_hash"]}
        self.control = dict(plan=new, plan_hash=digest(new), status="WAITING_PLAN_APPROVAL",
                            stages={}, approvals={}, history=[*self.control["history"], old])
        self.save()
        return self.status()

    async def stage(self, name, operation, *, recoverable=False):
        receipt = self.control["stages"].get(name)
        if receipt and receipt["status"] == "COMPLETE":
            return receipt["result"]
        if receipt and not recoverable:
            raise ValueError(f"ambiguous {name}; explicit reconciliation or rejection required")
        self.control["stages"][name] = dict(status="STARTED")
        self.save()
        started = time.perf_counter()
        result, paths = await operation()
        self.load()  # revalidate upstream inputs after any external work
        self.control["stages"][name] = dict(status="COMPLETE", result=result,
            outputs={str(Path(p).resolve()): sha256(p) for p in paths},
            elapsed_seconds=time.perf_counter() - started)
        self.save()
        return result

    def get_dependencies(self, plan, directory):
        if plan["mode"] == "TEST":
            from src.hybrid.revision_fixtures import TestDependencies
            deps = self.dependencies or TestDependencies(directory / "providers", self.test_options)
        else:
            deps = self.dependencies
            if deps is None and plan.get("adapter"):
                module, factory = plan["adapter"].split(":", 1)
                deps = getattr(importlib.import_module(module), factory)(directory, plan)
            if deps is None:
                raise ValueError("LIVE deployment adapter required; no provider fallback")
        if deps.mode != plan["mode"] or deps.images.mode != plan["mode"]:
            raise ValueError("provider mode mismatch")
        required_methods = ((deps, "author_script"), (deps, "visual_qa"), (deps.tts, "synthesize"),
                            (deps.images, "submit"), (deps.images, "recover"),
                            (deps.messenger, "send_photo"), (deps.messenger, "send_video"))
        if any(not callable(getattr(owner, name, None)) for owner, name in required_methods):
            raise ValueError("incomplete deployment providers")
        if plan["mode"] == "LIVE":
            # Deployment performs credential/authority checks, never prints secrets.
            evidence = deps.preflight(plan)
            required = {"credentials", "script_authority", "tts_authority", "visual_qa_authority",
                        "telegram", "current_prices", "budget"}
            if not isinstance(evidence, dict) or any(evidence.get(k) is not True for k in required):
                raise ValueError("LIVE preflight incomplete")
            if plan["chat_id"] == "TEST":
                raise ValueError("LIVE Telegram destination required")
            if not callable(getattr(deps, "authorize", None)):
                raise ValueError("LIVE exact request authorizer required")
            if not hasattr(deps, "prior_spend") or not Decimal(deps.prior_spend).is_finite() or Decimal(deps.prior_spend) < 0:
                raise ValueError("LIVE accounted non-image spend required")
            if not getattr(deps, "visual_license", "").strip():
                raise ValueError("LIVE visual license required")
            contract = plan.get("deployment")
            if (not contract or deps.endpoint != contract["endpoint"]
                    or Decimal(deps.image_cost) != Decimal(contract["image_cost"])
                    or Decimal(deps.prior_spend) != Decimal(contract["non_image_reserve"])):
                raise ValueError("LIVE provider differs from approved deployment contract")
        return deps

    async def run(self):
        from src.agents.captions import CaptionsAgent
        from src.agents.director import DirectorAgent
        from src.agents.research import ResearchAgent
        from src.agents.script_qa import ScriptQAAgent
        from src.agents.thumbnail import ThumbnailAgent, ThumbnailContract
        from src.config.loader import load_config
        from src.hybrid.planner import Config
        from src.hybrid.production import render_once
        from src.hybrid.render import probe
        from src.qa.final_render import FinalRenderQA
        from src.qa.post_production import PostProductionNarrativeQA
        from src.qa.production_evidence import ProductionEvidenceQA
        from src.state.machine import EpisodeState, EpisodeStateStore
        from src.storage.episode_fs import EpisodeFS

        plan = self.load()
        approval = self.control.get("plan_approval", {})
        if approval.get("plan_hash") != self.control["plan_hash"] or not approval.get("reviewer"):
            raise ValueError("persisted plan approval required; silence never approves")
        if self.control["status"].startswith("WAITING_"):
            return self.status()
        directory = self.root / f"r{plan['revision']:03d}"
        directory.mkdir(exist_ok=True)
        if self.control["status"] == "READY_VIDEO_DELIVERY":
            return await self.deliver("video", plan, directory)
        deps = self.get_dependencies(plan, directory)
        mode = plan["mode"]
        cfg = SimpleNamespace(**load_config().__dict__, episodes_dir=directory)
        # This façade owns delivery; constructing legacy Telegram must not read secrets in TEST.
        director = DirectorAgent(config=cfg, approval_gate=SimpleNamespace())
        episode_id = "EP8"
        fs = EpisodeFS(episode_id, cfg)
        fs.create_dirs()
        p = fs.paths
        script_path = p.script_dir / "script.json"
        timeline_path = p.audio_dir / "timeline.json"
        board_path = p.storyboard_dir / "scenes.json"

        async def script_stage():
            research = await ResearchAgent().run(episode_id, THEME, str(p.research_dir))
            if not research.success:
                raise ValueError("research failed")
            script = await deps.author_script(plan, research.data)
            if mode == "LIVE" and script.get("evidence_mode") == "TEST":
                raise ValueError("TEST script evidence cannot enter LIVE")
            qa = ScriptQAAgent().review(script)
            if not qa.approved:
                raise ValueError("ScriptQA failed: " + str(qa.findings))
            validate_ep8_script(script)
            atomic_json(script_path, script)
            self.fresh(script_path)
            atomic_json(p.qa_dir / "script.json", asdict(qa))
            return script, [script_path, p.qa_dir / "script.json", *p.research_dir.glob("*.json")]

        script = await self.stage("script", script_stage, recoverable=mode == "TEST")

        async def audio_stage():
            result = await deps.tts.synthesize(script["narration"], output_path=p.narration_wav,
                                               voice="pt-BR-ThalitaNeural", rate="-8%", pitch="+1Hz")
            if not result.success or Path(result.audio_path).resolve() != p.narration_wav.resolve():
                raise ValueError("TTS failed or output escaped workspace")
            duration = float(probe(p.narration_wav)["format"]["duration"])
            if abs(duration - result.duration_seconds) > .033:
                raise ValueError("TTS duration mismatch")
            if "WordBoundary" not in result.metadata.get("boundary_source", ""):
                raise ValueError("real WordBoundary evidence required")
            if mode == "LIVE" and result.metadata["boundary_source"].startswith("TEST"):
                raise ValueError("TEST WordBoundary cannot enter LIVE")
            scenes, cues = semantic_timeline(script, result.word_timestamps, duration)
            atomic_json(timeline_path, dict(words=result.word_timestamps, duration=duration,
                                           cues=cues, mode=mode, boundary_source=result.metadata["boundary_source"]))
            atomic_json(board_path, dict(scenes=scenes))
            self.fresh(p.narration_wav)
            return dict(duration=duration), [p.narration_wav, timeline_path, board_path]

        await self.stage("audio_storyboard", audio_stage, recoverable=mode == "TEST")
        references = Manifest.load(plan["references"]["manifest"])
        for asset in references.assets:
            self.fresh(asset.path)
        state = EpisodeStateStore(episode_id, current_state=EpisodeState.GENERATING_IMAGES)
        state.save(p.state_json)
        pipeline = director.activate_compiled_production(episode_id,
            approved_audio=FrozenAsset.approve(p.narration_wav, "ScriptQA + WordBoundary", mode),
            source_manifest=references, database=directory / "executor.sqlite3", endpoint=deps.endpoint,
            image_cost=Decimal(deps.image_cost), storyboard_path=board_path,
            prior_spend=Decimal(getattr(deps, "prior_spend", 0)),
            executor_config=Config(concurrency=plan["image_workers"], limit=Decimal(plan["budget_usd"])))

        async def authority(jobs):
            if mode == "TEST":
                return {}, {}
            # Exact Job/Price/Authorization validation and durable reservation happen in Executor.
            auth, prices = deps.authorize(tuple(jobs), plan)
            if set(auth) != {j.request_id for j in jobs} or set(prices) != set(auth):
                raise ValueError("every LIVE request requires exact current authority")
            return auth, prices

        async def images_stage():
            semaphore = asyncio.Semaphore(plan["qa_workers"])
            scenes = {s["scene_id"]: s for s in read(board_path)["scenes"]}
            tasks = []

            async def review(receipt, wave=0):
                scene_id = receipt["request"]["scene"]
                job = pipeline.run._active_images[scene_id]
                qa_file = pipeline.workspace / "qa" / f"{job.request_id}.json"
                if qa_file.exists():
                    decision = read(qa_file)
                    if decision["result_sha256"] != receipt["result_sha256"]:
                        raise ValueError("stale visual QA")
                    if decision["approved"] != receipt.get("qa") or decision["reviewer"] != receipt.get("qa_reviewer"):
                        raise ValueError("visual QA differs from immutable executor receipt")
                    return
                if "qa" in receipt:
                    # Executor committed first; repair only the derived QA file/promotion.
                    pipeline.record_visual_qa(scene_id, receipt["result_sha256"], receipt["qa"], receipt["qa_reviewer"])
                    return
                async with semaphore:
                    candidate = Path(receipt["result"]).resolve()
                    if directory not in candidate.parents:
                        raise ValueError("generated candidate must be inside current revision")
                    self.fresh(receipt["result"])
                    packet = pipeline.prepare_qa_packets([scene_id])[scene_id]
                    packet["candidate_path"] = receipt["result"]
                    packet["wave"] = wave
                    intent = pipeline.workspace / "qa_intents" / f"{job.request_id}.json"
                    decision_path = pipeline.workspace / "qa_decisions" / f"{job.request_id}.json"
                    if decision_path.exists():
                        decision = read(decision_path)
                    else:
                        if intent.exists() and mode == "LIVE":
                            raise ValueError("ambiguous LIVE visual QA; reconciliation required")
                        atomic_json(intent, dict(request_id=job.request_id, result_sha256=packet["result_sha256"], mode=mode))
                        decision = await deps.visual_qa(packet, scenes[scene_id], plan["references"])
                    if (set(decision) != {"scene_id", "result_sha256", "approved", "reviewer"}
                            or decision["scene_id"] != scene_id or decision["result_sha256"] != packet["result_sha256"]
                            or type(decision["approved"]) is not bool or not decision["reviewer"].strip()):
                        raise ValueError("invalid independent visual QA")
                    atomic_json(decision_path, decision)
                    pipeline.record_visual_qa(**decision)

            async def completed(receipt):
                tasks.append(asyncio.create_task(review(receipt)))

            auth, prices = await authority(pipeline.baseline_jobs())
            try:
                await pipeline.dispatch_baselines(deps.images, authorizations=auth, prices=prices, on_completed=completed)
                await asyncio.gather(*tasks)
            finally:
                await asyncio.gather(*tasks, return_exceptions=True)
            for scene_id, job in list(pipeline.run._active_images.items()):
                receipt = pipeline.run.executor.inspect(job.request_id)
                if receipt.get("qa") is False:
                    correction = pipeline.run.remediation_job(scene_id, job.payload["prompt"] + " Correct the rejected visual; retain canonical identity.")
                    auth, prices = await authority([correction])
                    fixed = await pipeline.run.executor.run(correction, deps.images,
                        authorization=auth.get(correction.request_id), price=prices.get(correction.request_id))
                    await review(fixed, 1)
            if not pipeline.render_ready():
                raise ValueError("visual QA failed after one correction wave")
            manifest = pipeline.approved_manifest()
            pipeline.run.executor.sync_cost_ledger(p.costs_json, episode_id=episode_id, budget=cfg.budget)
            paths = [*pipeline.workspace.rglob("*.json"), *pipeline.workspace.rglob("*.png"), p.costs_json]
            return dict(manifest=str(pipeline.workspace / "manifest.json")), paths

        result = await self.stage("images", images_stage, recoverable=True)
        manifest = Manifest.load(result["manifest"])
        for asset in manifest.assets:
            self.fresh(asset.path)

        encode_started = "encode" in self.control["stages"]

        async def render_stage():
            durable = p.qa_dir / "encode.json"
            if encode_started:
                if not durable.is_file():
                    raise ValueError("ambiguous encode; reject or explicitly reconcile")
                recovered = read(durable)
                if (recovered.get("output_sha256") != sha256(p.final_video)
                        or recovered.get("compilation") != pipeline.episode.checksum
                        or recovered.get("manifest") != manifest.checksum):
                    raise ValueError("invalid durable encode receipt")
                return recovered, [p.final_video, durable]
            receipt = render_once(pipeline.episode, manifest, p.final_video, script["closing_duration_s"])
            receipt.update(audio_operation="derived_master", subtitles_sha256=None, mode=mode,
                           compilation=pipeline.episode.checksum, manifest=manifest.checksum,
                           expected_duration=pipeline.episode.frames[-1].end + script["closing_duration_s"])
            self.fresh(p.final_video)
            atomic_json(p.qa_dir / "encode.json", receipt)
            return receipt, [p.final_video, p.qa_dir / "encode.json"]

        receipt = await self.stage("encode", render_stage, recoverable=True)

        async def sidecars_stage():
            captions = await CaptionsAgent().run(episode_id, sentence_timestamps=read(timeline_path)["cues"],
                narration=script["narration"], subtitles_dir=str(p.subtitles_dir))
            if not captions.success:
                raise ValueError(captions.error)
            metadata = dict(title=TITLE, language="pt-BR", references=sorted({r for s in script["segments"] for r in s["source_refs"]}),
                licenses=dict(visual_assets="TEST fixtures" if mode == "TEST" else deps.visual_license, music="No music used"),
                mode=mode, publication_authorized=False,
                chapters=[dict(start=s["start"], title=s["visual_action"]) for s in read(board_path)["scenes"]])
            atomic_json(p.metadata_dir / "youtube.json", metadata)
            thumbnail = await ThumbnailAgent().run(episode_id,
                images=[dict(scene_id=f.scene_id, image_path=str(a.path)) for f, a in zip(pipeline.episode.frames, manifest.assets)],
                thumbnails_dir=str(p.thumbnails_dir), copy_contract=ThumbnailContract(HEADLINE, TITLE, SUBTITLE))
            if not thumbnail.success:
                raise ValueError(thumbnail.error)
            self.fresh(thumbnail.data["thumbnail_path"])
            return thumbnail.data, [*p.subtitles_dir.iterdir(), *p.metadata_dir.iterdir(), *p.thumbnails_dir.iterdir()]

        thumbnail = await self.stage("sidecars", sidecars_stage, recoverable=True)

        async def final_stage():
            shared = dict(script_path=script_path, captions_path=p.captions_vtt, metadata_path=p.metadata_dir / "youtube.json")
            evidence = ProductionEvidenceQA().review(**shared, manifest_path=result["manifest"],
                published_script_hashes={p["sha256"] for p in plan["predecessors"]})
            narrative = PostProductionNarrativeQA().review(**shared)
            final = FinalRenderQA().review(p.final_video, receipt)
            reports = dict(production=asdict(evidence), narrative=asdict(narrative), final=asdict(final), mode=mode)
            atomic_json(p.qa_dir / "final.json", reports)
            if not all(q.approved for q in (evidence, narrative, final)):
                raise ValueError("final QA failed: " + str(reports))
            return reports, [p.qa_dir / "final.json"]

        await self.stage("final_qa", final_stage, recoverable=True)
        artifacts = dict(thumbnail=self.fresh(thumbnail["thumbnail_path"]), video=self.fresh(p.final_video))
        self.control["artifacts"] = artifacts
        self.save()  # freeze both media before any delivery intent
        return await self.deliver("thumbnail", plan, directory, deps)

    async def deliver(self, kind, plan, directory, deps=None):
        artifact = self.control["artifacts"][kind]
        if sha256(artifact["path"]) != artifact["sha256"]:
            raise ValueError("frozen delivery hash mismatch")
        if kind == "video":
            approval = self.control["approvals"].get("thumbnail", {})
            if (approval.get("sha256") != self.control["artifacts"]["thumbnail"]["sha256"]
                    or approval.get("plan_hash") != self.control["plan_hash"]):
                raise ValueError("exact thumbnail approval required before video delivery")
        name = "telegram_" + kind
        receipt_path = directory / "EP8/qa" / f"telegram-{kind}.json"
        intent = dict(kind=kind, sha256=artifact["sha256"], mode=plan["mode"],
                      chat_id=plan["chat_id"], plan_hash=self.control["plan_hash"])
        previous = self.control["stages"].get(name)
        if previous:
            if previous.get("intent") != intent:
                raise ValueError("Telegram intent mismatch")
            if previous["status"] == "COMPLETE":
                value = previous["result"]
            elif receipt_path.is_file():
                value = read(receipt_path)
            else:
                raise ValueError(f"ambiguous {name}; reconciliation or rejection required")
        else:
            if receipt_path.exists():
                raise ValueError("unbound Telegram receipt")
            deps = deps or self.get_dependencies(plan, directory)
            self.control["stages"][name] = dict(status="STARTED", intent=intent)
            self.save()
            method = deps.messenger.send_photo if kind == "thumbnail" else deps.messenger.send_video
            message_id = await method(plan["chat_id"], artifact["path"],
                f"{plan['mode']} EP8 revision {plan['revision']} {kind}; SHA256 {artifact['sha256']}; approval required")
            value = dict(**intent, message_id=message_id)
            self.validate_delivery(value, intent)
            atomic_json(receipt_path, value)
        self.validate_delivery(value, intent)
        self.load()
        self.control["stages"][name] = dict(status="COMPLETE", intent=intent, result=value,
            outputs={str(receipt_path.resolve()): sha256(receipt_path)}, elapsed_seconds=0)
        self.control["status"] = "WAITING_THUMBNAIL_APPROVAL" if kind == "thumbnail" else "WAITING_VIDEO_APPROVAL"
        self.save()
        return self.status()

    @staticmethod
    def validate_delivery(value, intent):
        if (any(value.get(k) != v for k, v in intent.items())
                or type(value.get("message_id")) is not int or value["message_id"] <= 0):
            raise ValueError("valid exact Telegram message receipt required")



def validate_ep8_script(script):
    """Scope/naming guards supplement the independent generic child-safety review."""
    from src.agents.script_qa import ScriptQAAgent
    for segment in script["segments"]:
        text = ScriptQAAgent._fold(segment["narration"])
        if re.search(r"(?:ISAQUE|ISAAC).{0,24}(?:NASCEU|BORN)", text):
            raise ValueError("Isaac is not born in EP8")
        for reference in segment["source_refs"]:
            match = re.fullmatch(r"Gênesis (15|17|18):(\d+)(?:-(\d+))?", reference)
            if not match:
                raise ValueError("EP8 biblical source outside approved scope")
            chapter, first, last = int(match[1]), int(match[2]), int(match[3] or match[2])
            allowed = {15: [(1, 6)], 17: [(1, 9), (15, 21)], 18: [(1, 15)]}
            if not any(a <= first <= last <= b for a, b in allowed[chapter]):
                raise ValueError("EP8 biblical source outside approved scope")
            if chapter == 15 and re.search(r"\b(?:ABRAAO|ABRAHAM|SARA|SARAH)\b", text):
                raise ValueError("Abrão/Sarai required before Genesis 17")


def semantic_timeline(script, words, duration):
    """Allocate narrative segments to exact provider words, never uniform slicing."""
    if not words or not math.isfinite(duration) or duration <= 0:
        raise ValueError("WordBoundary timings required")
    previous = 0
    for word in words:
        start, end = word["start"], word["end"]
        if not all(math.isfinite(v) for v in (start, end)) or start < previous or end <= start or end > duration + .001:
            raise ValueError("invalid WordBoundary timing")
        previous = end
    normalize = lambda text: re.findall(r"\w+", text.casefold())
    tokens = [(t, w["start"], w["end"]) for w in words for t in normalize(w["word"])]
    cursor, cues = 0, []
    for segment in script["segments"]:
        expected = normalize(segment["narration"])
        chosen = tokens[cursor:cursor + len(expected)]
        if not expected or [w[0] for w in chosen] != expected:
            raise ValueError("WordBoundary text mismatch")
        cues.append(dict(start=chosen[0][1], end=chosen[-1][2], text=segment["narration"]))
        cursor += len(expected)
    if cursor != len(tokens):
        raise ValueError("unallocated WordBoundary words")
    scenes = []
    for i, (segment, cue) in enumerate(zip(script["segments"], cues)):
        start, end = cue["start"] if i else 0, cues[i+1]["start"] if i+1 < len(cues) else duration
        if end - start <= .25 or not segment.get("visual_action"):
            raise ValueError("semantic visual action/window required")
        scenes.append(dict(scene_id=segment["id"], start=start, end=end, visual_action=segment["visual_action"],
            narration=segment["narration"], source_refs=segment["source_refs"], characters=segment.get("characters", []),
            image_prompt=segment["visual_action"] + "; canonical Abraham/Sarah image editing; child safe; no depicted God; no infant Isaac; no text", hero=False))
    return scenes, cues


def studio_factory(workspace, **kwargs):
    """Public factory used by both Python clients and the revision CLI."""
    return RevisionHarness(workspace, **kwargs)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("create-plan", "approve-plan", "run", "resume", "status", "approve", "reject"))
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--mode", choices=("TEST", "LIVE"), default="TEST")
    parser.add_argument("--request", default="New EP8: Abraham and Sarah")
    parser.add_argument("--predecessors", type=Path)
    parser.add_argument("--references", type=Path)
    parser.add_argument("--adapter")
    parser.add_argument("--deployment", type=Path)
    parser.add_argument("--chat-id", default="TEST")
    parser.add_argument("--plan-hash")
    parser.add_argument("--artifact-hash")
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--kind", choices=("thumbnail", "video"))
    parser.add_argument("--reason", default="")
    parser.add_argument("--image-workers", type=int, default=3)
    parser.add_argument("--qa-workers", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        with studio_factory(args.workspace) as harness:
            if args.action == "create-plan":
                result = harness.create_plan(mode=args.mode, request=args.request, predecessors=args.predecessors,
                    references=args.references, adapter=args.adapter, chat_id=args.chat_id,
                    deployment=args.deployment,
                    image_workers=args.image_workers, qa_workers=args.qa_workers)
            elif args.action == "approve-plan":
                result = harness.approve_plan(args.plan_hash, args.reviewer)
            elif args.action == "approve":
                result = harness.approve(args.kind, args.artifact_hash, args.reviewer)
            elif args.action == "reject":
                result = harness.reject(args.reason)
            elif args.action in {"run", "resume"}:
                result = asyncio.run(harness.run())
            else:
                result = harness.status()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, AttributeError, ImportError,
            InvalidOperation, subprocess.SubprocessError) as error:
        print(json.dumps(dict(status="BLOCKED", error=str(error)), ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
