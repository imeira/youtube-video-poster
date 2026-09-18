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
from src.hybrid.observability import StructuredEventLog

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
        "agents/script_qa.py", "agents/thumbnail.py", "hybrid/editorial.py",
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
        self.events = StructuredEventLog(self.root / "revision-events.jsonl")

    def event(self, status, *, stage, **fields):
        """Append sanitized public lifecycle evidence; raw requests never enter it."""
        plan = getattr(self, "control", {}).get("plan", {})
        return self.events.emit(status=status, episode="EP8", revision=plan.get("revision"),
            stage=stage, request_id=getattr(self, "control", {}).get("plan_hash"), **fields)

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
                    deployment=None, image_workers=3, qa_workers=2, ffmpeg_threads=1, chat_id="TEST",
                    heroes=False, hero_endpoint="", hero_cost=None):
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
        successor_brief = None
        revision = 1
        if predecessors:
            brief_path = Path(predecessors).resolve().parent / "successor-brief.json"
            if brief_path.is_file():
                successor_brief = read(brief_path)
                expected = {(item.get("kind"), item["sha256"]) for item in rejected}
                bound = {(item.get("kind"), item["sha256"])
                         for item in successor_brief.get("predecessor_identities", [])}
                revision = successor_brief.get("revision")
                if (expected != bound or type(revision) is not int or revision < 2
                        or successor_brief.get("publication_authorized") is not False):
                    raise ValueError("successor brief does not bind the rejected predecessors")
        bindings = {str(Path(predecessors).resolve()): sha256(predecessors)} if predecessors else {}
        if successor_brief:
            bindings[str(brief_path)] = sha256(brief_path)
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
            elif (set(contract) != ({"adapter", "endpoint", "image_cost", "non_image_reserve", "hero"}
                                    if heroes else {"adapter", "endpoint", "image_cost", "non_image_reserve"})
                    or not contract["adapter"] or not contract["endpoint"]):
                raise ValueError("invalid LIVE deployment contract")
            for key in ("image_cost", "non_image_reserve"):
                amount = Decimal(contract[key])
                if not amount.is_finite() or amount < 0 or amount >= 6:
                    raise ValueError("invalid LIVE deployment budget")
            if Decimal(contract["image_cost"]) <= 0 or (adapter and adapter != contract["adapter"]):
                raise ValueError("LIVE endpoint price/adapter mismatch")
            if heroes:
                hero = contract["hero"]
                if (not isinstance(hero, dict) or set(hero) != {"endpoint_id", "unit_cost_usd", "authority"}
                        or hero["endpoint_id"] != hero_endpoint or str(hero["unit_cost_usd"]) != str(hero_cost)
                        or not isinstance(hero["authority"], str) or not hero["authority"].strip()):
                    raise ValueError("audited LIVE RunPod hero deployment contract required")
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
        from src.hybrid.editorial import EpisodeRequest, build_adaptive_plan, parse_episode_request
        try:
            parsed_request = parse_episode_request(request)
        except ValueError:
            # Keep the Python API compatible while materializing every field.
            parsed_request = EpisodeRequest(
                episode_id="EP8", channel="@EraUmaVezBibliaAnimada",
                language="Português do Brasil", locale="pt-BR", theme=TITLE,
                passage="Gênesis 15–18", audience_ages=(6, 10), raw=request,
            )
        indispensable_events = [
            {"id": "promise-stars", "label": "A promessa sob as estrelas", "importance": "HIGH"},
            {"id": "new-names", "label": "Abrão e Sarai recebem novos nomes", "importance": "HIGH"},
            {"id": "promised-son", "label": "O filho prometido é anunciado", "importance": "CRITICAL"},
            {"id": "visitors", "label": "A promessa é repetida junto à tenda", "importance": "HIGH"},
            {"id": "waiting", "label": "Esperar com esperança", "importance": "NORMAL"},
        ]
        editorial_plan = build_adaptive_plan(parsed_request, indispensable_events, budget_usd="6")
        if mode == "LIVE":
            # The executable price contract, not a generic fixture estimate,
            # governs the pre-spend reconciliation for the approved plan.
            stills = Decimal(contract["image_cost"]) * editorial_plan["estimated_scene_count"]
            reserve = Decimal(contract["non_image_reserve"])
            editorial_plan["estimated_costs_usd"] = {
                "stills": str(stills.quantize(Decimal("0.01"))),
                "tts": "0.00", "hero_clips": "0.00", "assembly": "0.00",
                "total": str((stills + reserve).quantize(Decimal("0.01"))),
            }
            editorial_plan["approved_tolerances"] = {
                "words": 0, "duration_seconds": 0, "scenes": 0, "cost_usd": "0.01"}
            editorial_plan["plan_identity"] = digest({key: value for key, value in editorial_plan.items()
                                                        if key != "plan_identity"})
        # Hero generation is deliberately a separately priced opt-in.  The
        # selection is part of the plan hash and never enables itself.
        if type(heroes) is not bool:
            raise ValueError("heroes flag must be explicit boolean")
        candidates = editorial_plan["hero_candidates"]
        if not heroes:
            hero_plan = dict(enabled=False, endpoint="", unit_cost_usd="0",
                             selected_event_ids=[], selected_scene_ids=[],
                             authorization_required=True,
                             fallback="LOCAL_FULL_MOTION")
        else:
            if not isinstance(hero_endpoint, str) or not hero_endpoint.strip():
                raise ValueError("explicit hero endpoint required")
            try:
                unit_cost = Decimal(str(hero_cost))
            except (InvalidOperation, ValueError, TypeError) as error:
                raise ValueError("explicit hero price required") from error
            if not unit_cost.is_finite() or unit_cost <= 0:
                raise ValueError("explicit positive hero price required")
            selected = [item for item in candidates if item["importance"] in {"HIGH", "CRITICAL"}]
            reserved = unit_cost * len(selected)
            if reserved >= Decimal("6"):
                raise ValueError("hero budget exceeds revision budget")
            hero_plan = dict(enabled=True, endpoint=hero_endpoint.strip(), unit_cost_usd=str(unit_cost),
                             reserved_usd=str(reserved),
                             selected_event_ids=[item["event_id"] for item in selected],
                             # Script/timing maps event candidates to scene IDs later.  This
                             # empty binding cannot grant a provider request by itself.
                             selected_scene_ids=[], authorization_required=True,
                             fallback="LOCAL_FULL_MOTION")
        plan = dict(route="ep8-new-revision-v2", revision=revision, mode=mode, request=request,
                            episode_request=parsed_request.to_dict(), editorial_plan=editorial_plan,
                            successor_brief=successor_brief,
                    references=refs, predecessors=rejected, bindings=bindings, adapter=adapter,
                    deployment=contract,
                    chat_id=str(chat_id), image_workers=image_workers, qa_workers=qa_workers,
                    ffmpeg_threads=ffmpeg_threads, correction_waves=1, heroes=heroes, hero_plan=hero_plan, budget_usd="6",
                    provider_contract="transactional-executor-v1; exact-price-and-authority; recover-only",
                    publication_authorized=False)
        self.control = dict(plan=plan, plan_hash=digest(plan), status="WAITING_PLAN_APPROVAL",
                            stages={}, approvals={}, history=[])
        for asset in manifest.assets:
            self.fresh(asset.path)
        self.save()
        self.event("COMPLETE", stage="request", category="plan")
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
        self.event("COMPLETE", stage="gate", category="plan-approval", agent=reviewer)
        return self.status()

    def status(self):
        plan = self.load()
        return dict(status=self.control["status"], revision=plan["revision"], mode=plan["mode"],
                    plan_hash=self.control["plan_hash"], artifacts=self.control.get("artifacts", {}),
                    approvals=self.control["approvals"],
                    publication_authorized=bool(self.control.get("publication_authorized", False)))

    def approve(self, kind, artifact_hash, reviewer):
        self.load()
        expected = {"visual-freeze": "WAITING_VISUAL_FREEZE_APPROVAL", "thumbnail": "WAITING_THUMBNAIL_APPROVAL", "video": "WAITING_VIDEO_APPROVAL"}
        if kind not in expected or self.control["status"] != expected[kind]:
            raise ValueError("separate approval gate is out of order")
        artifact = self.control["artifacts"][kind.replace("-", "_")]
        if not reviewer.strip() or artifact_hash != artifact["sha256"] or sha256(artifact["path"]) != artifact_hash:
            raise ValueError("exact artifact hash and reviewer required")
        self.control["approvals"][kind] = dict(reviewer=reviewer, sha256=artifact_hash,
                                                plan_hash=self.control["plan_hash"])
        approval_dir = self.root / f"r{self.control['plan']['revision']:03d}" / "EP8" / "approval"
        approval_dir.mkdir(parents=True, exist_ok=True)
        if kind in {"thumbnail", "video"}:
            from src.approval.receipts import ApprovalReceipt, save_approval_receipt
            approval_receipt = ApprovalReceipt.approve(kind, artifact["path"], reviewer)
            if approval_receipt.artifact_sha256 != artifact_hash:
                raise ValueError("approval receipt differs from frozen artifact")
            save_approval_receipt(approval_dir / f"approval-{kind}.json", approval_receipt)
        else:
            atomic_json(approval_dir / "approval-visual-freeze.json", {
                "artifact_kind": kind, "artifact_path": artifact["path"],
                "artifact_sha256": artifact_hash, "approver": reviewer,
                "plan_hash": self.control["plan_hash"]})
        self.control["status"] = {"visual-freeze": "READY_RENDER", "thumbnail": "READY_VIDEO_DELIVERY", "video": "WAITING_PUBLICATION_AUTHORIZATION"}[kind]
        if kind == "video":
            self._freeze_publication_package()
        self.save()
        self.event("COMPLETE", stage="gate", category=kind, agent=reviewer,
                   result_sha256=artifact_hash)
        return self.status()

    def authorize_publication(self, *, command: str, reviewer: str):
        """Authorize, but never perform, a later publication operation."""
        self.load()
        if self.control["status"] != "WAITING_PUBLICATION_AUTHORIZATION" or not reviewer.strip():
            raise ValueError("publication authorization is out of order")
        if command != "AUTORIZAR PUBLICAÇÃO EP8":
            raise ValueError("exact separate publication command required")
        for kind in ("visual-freeze", "thumbnail", "video"):
            approval = self.control["approvals"].get(kind, {})
            artifact = self.control["artifacts"][kind.replace("-", "_")]
            if (approval.get("sha256") != artifact["sha256"]
                    or approval.get("plan_hash") != self.control["plan_hash"]
                    or sha256(artifact["path"]) != artifact["sha256"]):
                raise ValueError("all independent media approvals must remain valid")
        revision_dir = self.root / f"r{self.control['plan']['revision']:03d}" / "EP8"
        approval_dir = revision_dir / "approval"
        metadata_path = revision_dir / "metadata" / "youtube.json"
        from src.approval.receipts import load_approval_receipt, require_publication_authorization
        thumbnail_receipt = load_approval_receipt(approval_dir / "approval-thumbnail.json")
        video_receipt = load_approval_receipt(approval_dir / "approval-video.json")
        authorization = require_publication_authorization(command=command,
            expected_command="AUTORIZAR PUBLICAÇÃO EP8", video=video_receipt,
            thumbnail=thumbnail_receipt, metadata_sha256=sha256(metadata_path))
        receipt = {**asdict(authorization), "reviewer": reviewer,
            "plan_hash": self.control["plan_hash"], "upload_performed": False}
        target = approval_dir / "publication-authorization.json"
        if target.exists() and read(target) != receipt:
            raise ValueError("publication authorization receipt is immutable")
        atomic_json(target, receipt)
        if read(target) != receipt:
            raise ValueError("publication authorization readback mismatch")
        self.control["publication_authorized"] = True
        self.control["publication_authorization"] = receipt
        self.control["status"] = "READY_FOR_PUBLICATION"
        self.save()
        self.event("COMPLETE", stage="gate", category="publication-authorization", agent=reviewer,
                   result_sha256=sha256(target))
        return self.status()

    def _freeze_publication_package(self):
        """Bind delivery inputs after video approval; authorization never uploads or rewrites it."""
        plan = self.control["plan"]
        revision_dir = self.root / f"r{plan['revision']:03d}" / "EP8"
        approval_dir = revision_dir / "approval"
        metadata = revision_dir / "metadata" / "youtube.json"
        named_paths = {
            "video": Path(self.control["artifacts"]["video"]["path"]),
            "thumbnail": Path(self.control["artifacts"]["thumbnail"]["path"]),
            "metadata": metadata,
            "captions_srt": revision_dir / "subtitles" / "captions.srt",
            "captions_vtt": revision_dir / "subtitles" / "captions.vtt",
            "transcript": revision_dir / "subtitles" / "transcript.txt",
        }
        approvals = []
        for kind in ("visual-freeze", "thumbnail", "video"):
            receipt_path = approval_dir / f"approval-{kind}.json"
            if not receipt_path.is_file():
                raise ValueError("all approval receipts required for publication package")
            approvals.append({"kind": kind, "path": str(receipt_path.resolve()),
                              "sha256": sha256(receipt_path)})
        if any(not path.is_file() for path in named_paths.values()):
            raise ValueError("all delivery artifacts required for publication package")
        artifacts = {name: {"path": str(path.resolve()), "sha256": sha256(path)}
                     for name, path in named_paths.items()}
        package = {
            "schema_version": 1,
            "plan_hash": self.control["plan_hash"],
            "revision": plan["revision"],
            "plan": {"route": plan["route"], "mode": plan["mode"], "episode_id": "EP8"},
            "artifacts": artifacts,
            "approval_receipts": approvals,
            "publication_authorized": False,
            "upload_performed": False,
        }
        package["package_hash"] = digest(package)
        target = revision_dir / "publication-package.json"
        if target.exists() and read(target) != package:
            raise ValueError("publication package is immutable")
        atomic_json(target, package)
        if read(target) != package:
            raise ValueError("publication package readback mismatch")
        outputs = {str(target.resolve()): sha256(target)}
        outputs.update({str(path.resolve()): sha256(path) for path in named_paths.values()})
        outputs.update({entry["path"]: entry["sha256"] for entry in approvals})
        previous = self.control["stages"].get("publication_package")
        receipt = {"status": "COMPLETE", "result": {"package": str(target.resolve()),
                   "package_hash": package["package_hash"]}, "outputs": outputs,
                   "elapsed_seconds": 0}
        if previous and previous != receipt:
            raise ValueError("publication package stage is immutable")
        self.control["stages"]["publication_package"] = receipt

    def reject(self, reason, *, reviewer="operator", directives=None):
        plan = self.load()
        if not reason.strip():
            raise ValueError("rejection reason required")
        feedback_directives = [str(item).strip() for item in (directives or [reason]) if str(item).strip()]
        if not reviewer.strip() or not feedback_directives:
            raise ValueError("complete rejection feedback required")
        old = {k: v for k, v in self.control.items() if k != "history"}
        old.update(status="SUPERSEDED", rejection_reason=reason,
                   rejection_reviewer=reviewer, rejection_directives=feedback_directives)
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
        successor_revision = plan["revision"] + 1
        media_artifacts = []
        for kind in ("video", "thumbnail"):
            artifact = self.control.get("artifacts", {}).get(kind)
            if artifact and re.fullmatch(r"[0-9a-f]{64}", artifact.get("sha256", "")):
                media_artifacts.append({"kind": kind, "sha256": artifact["sha256"]})
        if len(media_artifacts) == 2:
            from src.hybrid.editorial import create_successor_brief
            successor_brief = create_successor_brief(
                {"status": "SUPERSEDED", "revision": plan["revision"],
                 "episode_id": "EP8", "artifacts": media_artifacts},
                {"reason": reason, "reviewer": reviewer, "directives": feedback_directives},
                successor_revision=successor_revision)
        else:
            successor_brief = {"episode_id": "EP8", "revision": successor_revision,
                "supersedes_revision": plan["revision"],
                "rejection_feedback": {"reason": reason, "reviewer": reviewer,
                    "directives": feedback_directives},
                "thumbnail_constraints": {"requires_new_composition": True},
                "publication_authorized": False}
            successor_brief["feedback_identity"] = digest(successor_brief["rejection_feedback"])
            successor_brief["successor_identity"] = digest(successor_brief)
        new = {**plan, "revision": successor_revision, "predecessors": rejected,
               "supersedes": self.control["plan_hash"], "successor_brief": successor_brief,
               "publication_authorized": False}
        self.control = dict(plan=new, plan_hash=digest(new), status="WAITING_PLAN_APPROVAL",
                            stages={}, approvals={}, history=[*self.control["history"], old])
        self.save()
        return self.status()

    async def stage(self, name, operation, *, recoverable=False):
        receipt = self.control["stages"].get(name)
        if receipt and receipt["status"] == "COMPLETE":
            self.event("COMPLETE", stage=name, recovery=True,
                       result_sha256=digest(receipt["outputs"]))
            return receipt["result"]
        if receipt and not recoverable:
            raise ValueError(f"ambiguous {name}; explicit reconciliation or rejection required")
        self.control["stages"][name] = dict(status="STARTED")
        self.save()
        self.event("RUNNING", stage=name, recovery=bool(receipt))
        started = time.perf_counter()
        try:
            result, paths = await operation()
        except Exception as error:
            self.event("FAILED", stage=name, error=str(error), recovery=bool(receipt))
            raise
        self.load()  # revalidate upstream inputs after any external work
        self.control["stages"][name] = dict(status="COMPLETE", result=result,
            outputs={str(Path(p).resolve()): sha256(p) for p in paths},
            elapsed_seconds=time.perf_counter() - started)
        self.save()
        self.event("COMPLETE", stage=name, recovery=bool(receipt),
                   result_sha256=digest(self.control["stages"][name]["outputs"]),
                   cost=result.get("cost") if isinstance(result, dict) else None)
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
        if plan.get("heroes") and (not getattr(deps, "hero", None)
                                    or not callable(getattr(deps.hero, "submit", None))
                                    or not callable(getattr(deps.hero, "recover", None))):
            raise ValueError("incomplete injected hero provider")
        if plan["mode"] == "LIVE":
            if plan.get("heroes") and type(getattr(deps, "hero", None)).__name__ != "RunPodHeroProvider":
                # The checked-in LIVE deployment has no bound RunPod hero
                # authority.  Do not silently fall through to an image adapter
                # or make a speculative paid request.
                raise ValueError("LIVE hero preflight requires an audited RunPod hero adapter")
            if plan.get("heroes"):
                hero_contract = plan.get("deployment", {}).get("hero", {})
                if (not hero_contract.get("authority") or deps.hero.endpoint_id != hero_contract.get("endpoint_id")
                        or plan["hero_plan"]["endpoint"] != hero_contract.get("endpoint_id")
                        or plan["hero_plan"]["unit_cost_usd"] != str(hero_contract.get("unit_cost_usd"))):
                    raise ValueError("LIVE RunPod hero differs from audited deployment contract")
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

    async def execute_heroes(self, plan, plan_hash, manifest, images, scenes, provider, target):
        """Run approved hero work once, or recover it without another POST.

        The manifest is the write-ahead checkpoint at this provider boundary.
        A record without a provider ID is deliberately unrecoverable: the submit
        outcome may be zero or one remote jobs, so a new POST is unsafe.
        """
        from src.hybrid.execution import Job

        config = plan["hero_plan"]
        target = Path(target)
        if not config.get("enabled"):
            payload = read(target) if target.exists() else {"schema_version": 2, "enabled": False, "plan_hash": plan_hash,
                       "receipts": [], "fallback_scenes": [scene["scene_id"] for scene in scenes]}
            if not target.exists():
                atomic_json(target, payload)
            return {"clips": {}, "manifest": payload}
        if getattr(provider, "mode", None) != plan["mode"]:
            raise ValueError("hero provider mode mismatch")
        if target.exists():
            state = read(target)
            if (state.get("schema_version") != 2 or state.get("plan_hash") != plan_hash
                    or state.get("endpoint") != config["endpoint"]):
                raise ValueError("hero manifest binding mismatch")
        else:
            state = {"schema_version": 2, "enabled": True, "plan_hash": plan_hash,
                     "endpoint": config["endpoint"], "unit_cost_usd": config["unit_cost_usd"],
                     "receipts": [], "fallback_scenes": []}
            atomic_json(target, state)
        selected = [scene for scene in scenes if scene.get("importance") in {"HIGH", "CRITICAL"}]
        known = {entry["scene_id"]: entry for entry in state["receipts"]}
        clips = {}
        lock = asyncio.Semaphore(min(2, plan["image_workers"]))

        async def one(scene):
            scene_id = scene["scene_id"]
            image = images[scene_id]
            entry = known.get(scene_id)
            payload = {"input": {"image": str(image.path), "prompt": scene["motion_intent"],
                       "duration": 5, "resolution": "720p", "aspect_ratio": "16:9",
                       "camera_fixed": True, "generate_audio": False}}
            job = Job(scene=scene_id, category="hero", mode=plan["mode"], endpoint=config["endpoint"],
                      payload=payload, manifest=manifest, cost=Decimal(config["unit_cost_usd"]))
            if entry and entry.get("request_id") != job.request_id:
                raise ValueError("hero request binding mismatch")
            if entry and entry["status"] == "COMPLETE":
                path = Path(entry["clip_path"])
                if not path.is_file() or sha256(path) != entry["clip_sha256"]:
                    raise ValueError("hero clip checkpoint mismatch")
                clips[scene_id] = FrozenAsset.approve(path, "hero-provider", plan["mode"])
                return
            if entry and entry["status"] == "FALLBACK_LOCAL":
                return
            if entry and not entry.get("provider_id"):
                raise ValueError("ambiguous hero submission; reconcile without a new POST")
            def checkpoint(**values):
                entry.update(values)
                entry["status"] = "PENDING"
                atomic_json(target, state)
            if entry is None:
                entry = {"scene_id": scene_id, "request_id": job.request_id, "provider_id": "",
                         "status": "SUBMITTING", "payload": payload, "clip_path": "", "clip_sha256": ""}
                state["receipts"].append(entry)
                known[scene_id] = entry
                atomic_json(target, state)
            try:
                async with lock:
                    result = (await provider.recover(job, job.request_id, entry["provider_id"], "", checkpoint)
                              if entry.get("provider_id") else await provider.submit(job, job.request_id, checkpoint))
            except (TimeoutError, asyncio.CancelledError):
                entry["status"] = "AMBIGUOUS"
                atomic_json(target, state)
                raise
            except Exception as error:
                # A durable provider ID makes a terminal error non-ambiguous; it
                # is a local-motion fallback, never an implicit retry.
                if entry.get("provider_id"):
                    entry.update(status="FALLBACK_LOCAL", terminal_reason=str(error))
                    if scene_id not in state["fallback_scenes"]:
                        state["fallback_scenes"].append(scene_id)
                    atomic_json(target, state)
                    return
                entry["status"] = "AMBIGUOUS"
                atomic_json(target, state)
                raise
            path = Path(result.path).resolve()
            if not path.is_file():
                raise ValueError("hero provider result missing")
            entry.update(status="COMPLETE", clip_path=str(path), clip_sha256=sha256(path),
                         actual_cost_usd=str(result.actual_cost))
            atomic_json(target, state)
            clips[scene_id] = FrozenAsset.approve(path, "hero-provider", plan["mode"])

        await asyncio.gather(*(one(scene) for scene in selected))
        return {"clips": clips, "manifest": state}

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
        self.event("RUNNING", stage="provider", provider=type(deps).__name__, model=mode)
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
            from src.hybrid.editorial import bind_research_claims, build_editorial_reports
            claim_for_ref = {}
            claims = []
            for index, reference in enumerate(sorted({ref for segment in script["segments"] for ref in segment.get("source_refs", [])})):
                claim_id = f"SRC{index + 1:03d}"
                claim_for_ref[reference] = claim_id
                claims.append({"claim_id": claim_id, "text": f"A paráfrase deste segmento está limitada a {reference}.",
                    "source_ref": reference, "classification": "PARAPHRASE_BASIS"})
            claims.append({"claim_id": "FAMILY", "text": "Aplicação familiar editorial, não fato bíblico.",
                "source_ref": "Editorial infantil EP8", "classification": "CONTEXT"})
            draft_segments = []
            for segment in script["segments"]:
                refs = segment.get("source_refs", [])
                draft_segments.append({**segment,
                    "claim_ids": [claim_for_ref[ref] for ref in refs] if refs else ["FAMILY"],
                    "editorial_kind": "ORIGINAL_PARAPHRASE" if refs else "FAMILY_REFLECTION"})
            bound = bind_research_claims(draft_segments, claims)
            script = {**script, "segments": bound["segments"],
                "research_identity": bound["research_identity"], "script_identity": bound["script_identity"]}
            successor_brief = plan.get("successor_brief")
            if successor_brief:
                feedback_identity = successor_brief["feedback_identity"]
                directives = successor_brief["rejection_feedback"]["directives"]
                script["feedback_identity"] = feedback_identity
                script["rejection_directives"] = list(directives)
                script["segments"] = [{**segment,
                    "feedback_identity": feedback_identity,
                    "visual_action": segment["visual_action"] + "; successor directives: " + "; ".join(directives)}
                    for segment in script["segments"]]
            reports = build_editorial_reports(bound,
                licenses=[{"asset_id": "canonical-references", "license": "approved production authority",
                    "source": plan["references"]["authority"], "commercial_use": True,
                    "attribution_required": False, "attribution": ""}],
                omissions=["Gênesis 16 e trechos fora do arco"],
                simplifications=["Frases curtas e explicações para crianças de 6–10 anos"],
                human_review_points=["Revisar representação não humana de Deus"])
            if reports["status"] != "PASS":
                raise ValueError("editorial reports blocked production")
            qa = ScriptQAAgent().review(script)
            if not qa.approved:
                raise ValueError("ScriptQA failed: " + str(qa.findings))
            validate_ep8_script(script)
            atomic_json(script_path, script)
            self.fresh(script_path)
            atomic_json(p.qa_dir / "script.json", asdict(qa))
            atomic_json(p.qa_dir / "editorial_reports.json", reports)
            biblical_report = script.get("biblical_accuracy_report")
            if mode == "LIVE" and plan.get("adapter") == "src.hybrid.revision_live:factory":
                if not isinstance(biblical_report, dict) or biblical_report.get("status") != "PASS":
                    raise ValueError("independent biblical accuracy report required for LIVE")
                atomic_json(p.qa_dir / "biblical-accuracy.json", biblical_report)
            outputs = [script_path, p.qa_dir / "script.json", p.qa_dir / "editorial_reports.json",
                       *p.research_dir.glob("*.json")]
            if mode == "LIVE" and plan.get("adapter") == "src.hybrid.revision_live:factory":
                outputs.append(p.qa_dir / "biblical-accuracy.json")
            return script, outputs

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
            return dict(duration=duration, words=result.word_timestamps,
                boundary_source=result.metadata["boundary_source"]), [p.narration_wav, timeline_path, board_path]

        audio_result = await self.stage("audio_storyboard", audio_stage, recoverable=mode == "TEST")
        references = Manifest.load(plan["references"]["manifest"])
        reference_by_hash = {asset.sha256: asset for asset in references.assets}
        connected_board_path = p.storyboard_dir / 'connected.json'

        async def post_audio_contracts_stage():
            from src.hybrid.editorial import build_ep8_character_bible, qa_audio_transcript, validate_storyboard
            approved_references = {}
            for character_id in ("abraham", "sarah"):
                reference_hash = plan["references"]["characters"][character_id]
                asset = reference_by_hash[reference_hash]
                approved_references[character_id] = [{"path": asset.path, "sha256": asset.sha256,
                    "status": "APPROVED", "reviewer": plan["references"]["authority"],
                    "generation_method": "APPROVED_IMAGE_TO_IMAGE_REFERENCE"}]
            character_bible = build_ep8_character_bible(approved_references)
            atomic_json(p.characters_dir / "character-bible.json", character_bible)
            raw_scenes = read(board_path)["scenes"]
            segments = {segment["id"]: segment for segment in script["segments"]}
            connected_scenes = []
            for index, scene in enumerate(raw_scenes):
                segment = segments[scene["scene_id"]]
                characters = [item for item in scene["characters"] if item in {"abraham", "sarah"}]
                connected_scenes.append({**scene, "segment_id": scene["scene_id"],
                    "duration": scene["end"] - scene["start"], "characters": characters,
                    "location": "acampamento de Abraão e paisagem de Canaã",
                    "action": scene["visual_action"], "emotion": "esperança serena",
                    "importance": "HIGH" if index in {0, len(raw_scenes) // 2, len(raw_scenes) - 1} else "NORMAL",
                    "visual_prompt": scene["image_prompt"],
                    "negative_prompt": "God depicted, infant Isaac, text, watermark, fear, violence",
                    "camera": ("slow push-in" if index % 3 == 0 else "gentle left pan" if index % 3 == 1 else "slow pull-back"),
                    "motion_intent": "subtle parallax preserving faces and canonical identity",
                    "transition": "soft dissolve", "sfx": [],
                    "source_claim_ids": segment["claim_ids"],
                    "feedback_identity": script.get("feedback_identity", "INITIAL_REVISION"),
                    "character_reference_hashes": {item: plan["references"]["characters"][item] for item in characters},
                    "hero_candidate": index in {0, len(raw_scenes) // 2, len(raw_scenes) - 1}})
            connected_storyboard = {"scenes": connected_scenes,
                "character_bible_identity": character_bible["bible_identity"],
                "burned_captions": False, "closing_hold_seconds": plan["editorial_plan"]["closing_hold_seconds"]}
            storyboard_report = validate_storyboard(connected_storyboard, script, character_bible,
                audio_duration_seconds=audio_result["duration"])
            connected_board_path = p.storyboard_dir / "connected.json"
            atomic_json(connected_board_path, connected_storyboard)
            atomic_json(p.qa_dir / "storyboard.json", storyboard_report)
            transcript = " ".join(segment["narration"].strip() for segment in script["segments"])
            bound_transcript_path = p.audio_dir / "transcript-bound.txt"
            bound_transcript_path.write_text(transcript, encoding="utf-8")
            audio_report = qa_audio_transcript({"decoded": True, "duration_seconds": audio_result["duration"],
                "sample_rate_hz": 16000, "channels": 1, "boundary_source": "WordBoundary",
                "word_boundaries": audio_result["words"]}, transcript, script,
                sidecars={"transcript": True, "srt": True, "vtt": True, "burned_in_video": False})
            atomic_json(p.qa_dir / "audio.json", audio_report)
            return {"storyboard_sha256": sha256(connected_board_path)}, [p.characters_dir / "character-bible.json", connected_board_path, p.qa_dir / "storyboard.json", bound_transcript_path, p.qa_dir / "audio.json"]

        await self.stage("post_audio_contracts", post_audio_contracts_stage, recoverable=True)
        connected_scenes = read(connected_board_path)["scenes"]
        # Re-load the same immutable manifest after the editorial contracts have bound it.
        references = Manifest.load(plan["references"]["manifest"])
        for asset in references.assets:
            self.fresh(asset.path)
        state = EpisodeStateStore(episode_id, current_state=EpisodeState.GENERATING_IMAGES)
        state.save(p.state_json)
        pipeline = director.activate_compiled_production(episode_id,
            approved_audio=FrozenAsset.approve(p.narration_wav, "ScriptQA + WordBoundary", mode),
            source_manifest=references, database=directory / "executor.sqlite3", endpoint=deps.endpoint,
            image_cost=Decimal(deps.image_cost), storyboard_path=connected_board_path,
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
            scenes = {s["scene_id"]: s for s in read(connected_board_path)["scenes"]}
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
                    self.event("COMPLETE", stage="qa", category="visual", agent=decision["reviewer"],
                               scene=scene_id, result_sha256=decision["result_sha256"])

            async def completed(receipt):
                tasks.append(asyncio.create_task(review(receipt)))

            auth, prices = await authority(pipeline.baseline_jobs())
            self.event("RUNNING", stage="provider", provider=type(deps.images).__name__,
                       cost=sum((Decimal(price.amount) for price in prices.values()), Decimal(0)))
            try:
                await pipeline.dispatch_baselines(deps.images, authorizations=auth, prices=prices, on_completed=completed)
                await asyncio.gather(*tasks)
            finally:
                await asyncio.gather(*tasks, return_exceptions=True)
            corrections = {}
            for scene_id, job in list(pipeline.run._active_images.items()):
                receipt = pipeline.run.executor.inspect(job.request_id)
                if receipt.get("qa") is False:
                    corrections[scene_id] = job.payload["prompt"] + " Correct the rejected visual; retain canonical identity."
            if corrections:
                correction_jobs = [pipeline.run.remediation_job(scene_id, prompt) for scene_id, prompt in corrections.items()]
                auth, prices = await authority(correction_jobs)
                prior_task_count = len(tasks)
                async def correction_completed(receipt):
                    tasks.append(asyncio.create_task(review(receipt, 1)))
                await pipeline.dispatch_remediation_wave(corrections, deps.images,
                    authorizations=auth, prices=prices, on_completed=correction_completed)
                await asyncio.gather(*tasks[prior_task_count:])
            if not pipeline.render_ready():
                raise ValueError("visual QA failed after one correction wave")
            manifest = pipeline.approved_manifest()
            # The revision route currently has one final encode.  Keep every
            # unselected/unavailable hero on the complete local motion path;
            # never serialise scene rendering behind a remote clip.  This
            # manifest is durable package evidence even when heroes are off.
            hero_config = plan["hero_plan"]
            selected_events = hero_config["selected_event_ids"]
            high_scenes = [scene for scene in connected_scenes
                           if scene["importance"] in {"HIGH", "CRITICAL"}]
            selected_scenes = [
                {"event_id": event_id, "scene_id": scene["scene_id"],
                 "importance": scene["importance"], "duration": scene["duration"]}
                for event_id, scene in zip(selected_events, high_scenes, strict=False)
            ]
            hero_manifest = {
                "schema_version": 2,
                "enabled": hero_config["enabled"],
                "plan_hash": self.control["plan_hash"],
                "authorization": self.control["plan_approval"],
                "endpoint": hero_config["endpoint"],
                "unit_cost_usd": hero_config["unit_cost_usd"],
                "selected": selected_scenes,
                "receipts": [],
                "fallback": "LOCAL_FULL_MOTION",
                "fallback_scenes": [scene["scene_id"] for scene in connected_scenes],
                "remote_io": False,
            }
            # A five-second slot and a dedicated audited provider are required
            # before remote I/O is ever enabled.  Without both, normal local
            # motion remains complete rather than becoming a bottleneck.
            hero_manifest_path = p.animation_dir / "hero-manifest.json"
            atomic_json(hero_manifest_path, hero_manifest)
            pipeline.run.executor.sync_cost_ledger(p.costs_json, episode_id=episode_id, budget=cfg.budget)
            paths = [*pipeline.workspace.rglob("*.json"), *pipeline.workspace.rglob("*.png"),
                     p.costs_json, hero_manifest_path]
            return dict(manifest=str(pipeline.workspace / "manifest.json"),
                        hero_manifest=str(hero_manifest_path)), paths

        result = await self.stage("images", images_stage, recoverable=True)
        manifest = Manifest.load(result["manifest"])
        hero_manifest_path = Path(result["hero_manifest"])
        for asset in manifest.assets:
            self.fresh(asset.path)
        freeze = self.fresh(manifest.sheet.path)
        freeze["manifest_sha256"] = sha256(result["manifest"])
        approved_freeze = self.control["approvals"].get("visual-freeze", {})
        if approved_freeze.get("sha256") != freeze["sha256"]:
            self.control["artifacts"] = {
                **self.control.get("artifacts", {}),
                "visual_freeze": freeze,
            }
            self.save()
            return await self.deliver("visual-freeze", plan, directory, deps)

        async def heroes_stage():
            images = {frame.scene_id: asset for frame, asset in zip(pipeline.episode.frames, manifest.assets, strict=True)}
            result = await self.execute_heroes(plan, self.control["plan_hash"], manifest, images,
                connected_scenes, deps.hero if plan["heroes"] else None, hero_manifest_path)
            return {"hero_manifest": str(hero_manifest_path),
                    "clip_hashes": {scene: asset.sha256 for scene, asset in result["clips"].items()}}, [hero_manifest_path]

        await self.stage("heroes", heroes_stage, recoverable=True)

        motion_plan_path = p.animation_dir / "motion-plan.json"

        async def motion_plan_stage():
            motion_plan = {"schema_version": 1, "manifest_sha256": sha256(result["manifest"]),
                "storyboard_sha256": sha256(connected_board_path), "scenes": [
                    {"scene_id": scene["scene_id"], "operation": ("push_in", "pan_left", "pull_back")[index % 3],
                     "duration": scene["duration"], "transition": scene["transition"]}
                    for index, scene in enumerate(connected_scenes)]}
            atomic_json(motion_plan_path, motion_plan)
            return motion_plan, [motion_plan_path]

        motion_plan = await self.stage("motion_plan", motion_plan_stage, recoverable=True)
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
            hero_state = read(hero_manifest_path)
            hero_clips = {}
            for entry in hero_state.get("receipts", []):
                if entry.get("status") == "COMPLETE":
                    clip_path = Path(entry["clip_path"])
                    if sha256(clip_path) != entry.get("clip_sha256"):
                        raise ValueError("hero manifest clip hash mismatch")
                    hero_clips[entry["scene_id"]] = FrozenAsset.approve(clip_path, "hero-provider", mode)
            receipt = render_once(pipeline.episode, manifest, p.final_video, script["closing_duration_s"], motion_plan=motion_plan,
                                  hero_clips=hero_clips)
            receipt.update(audio_operation="derived_master", subtitles_sha256=None, mode=mode,
                           compilation=pipeline.episode.checksum, manifest=manifest.checksum,
                           hero_manifest_sha256=sha256(hero_manifest_path),
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
            metadata = None  # built after the thumbnail is frozen so every sidecar is hash-bound
            thumbnail = await ThumbnailAgent().run(episode_id,
                images=[dict(scene_id=f.scene_id, image_path=str(a.path)) for f, a in zip(pipeline.episode.frames, manifest.assets)],
                scenes=connected_scenes, thumbnails_dir=str(p.thumbnails_dir),
                copy_contract=ThumbnailContract(HEADLINE, TITLE, SUBTITLE))
            if not thumbnail.success:
                raise ValueError(thumbnail.error)
            self.fresh(thumbnail.data["thumbnail_path"])
            from src.hybrid.editorial import build_youtube_metadata, digest as editorial_digest, validate_youtube_metadata
            duration = float(receipt["expected_duration"])
            if mode == "TEST":
                metadata_duration = max(duration, 30.1)
                chapters = [
                    {"start_seconds": 0.0, "title": "A promessa"},
                    {"start_seconds": 10.0, "title": "Esperar com fé"},
                    {"start_seconds": 20.0, "title": "A visita e a esperança"},
                ]
            else:
                metadata_duration = duration
                chapters = []
                for scene in connected_scenes:
                    start = float(scene["start"])
                    if not chapters or start - chapters[-1]["start_seconds"] >= 10:
                        chapters.append({"start_seconds": start, "title": scene["visual_action"][:80]})
                if len(chapters) < 3:
                    raise ValueError("LIVE metadata needs three chapters at least ten seconds apart")
            editorial_reports = read(p.qa_dir / "editorial_reports.json")
            metadata = build_youtube_metadata(chapters=chapters, duration_seconds=metadata_duration,
                transcript_artifacts={"transcript": {key: value for key, value in self.fresh(p.transcript_txt).items() if key in {"path", "sha256"}},
                    "srt": {key: value for key, value in self.fresh(p.captions_srt).items() if key in {"path", "sha256"}},
                    "vtt": {key: value for key, value in self.fresh(p.captions_vtt).items() if key in {"path", "sha256"}}},
                thumbnail={key: value for key, value in self.fresh(thumbnail.data["thumbnail_path"]).items() if key in {"path", "sha256"}},
                license_report_identity=editorial_reports["report_identity"])
            metadata.update(language="pt-BR",
                references=sorted({r for s in script["segments"] for r in s["source_refs"]}),
                licenses={"visual_assets": "TEST fixtures" if mode == "TEST" else deps.visual_license,
                    "music": "No music used"}, mode=mode, publication_authorized=False)
            metadata["hero_manifest"] = {"path": str(hero_manifest_path),
                "sha256": sha256(hero_manifest_path)}
            metadata["metadata_identity"] = editorial_digest({key: value for key, value in metadata.items() if key != "metadata_identity"})
            if mode == "LIVE":
                validate_youtube_metadata(metadata, duration_seconds=duration)
            atomic_json(p.metadata_dir / "youtube.json", metadata)
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
        artifacts = {**self.control.get("artifacts", {}),
            "thumbnail": self.fresh(thumbnail["thumbnail_path"]), "video": self.fresh(p.final_video)}
        self.control["artifacts"] = artifacts
        self.save()  # freeze both media before any delivery intent
        return await self.deliver("thumbnail", plan, directory, deps)

    async def deliver(self, kind, plan, directory, deps=None):
        artifact = self.control["artifacts"][kind.replace("-", "_")]
        if sha256(artifact["path"]) != artifact["sha256"]:
            raise ValueError("frozen delivery hash mismatch")
        if kind == "video":
            approval = self.control["approvals"].get("thumbnail", {})
            if (approval.get("sha256") != self.control["artifacts"]["thumbnail"]["sha256"]
                    or approval.get("plan_hash") != self.control["plan_hash"]):
                raise ValueError("exact thumbnail approval required before video delivery")
        name = f"telegram_{kind.replace('-', '_')}"
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
            method = deps.messenger.send_photo if kind in {"thumbnail", "visual-freeze"} else deps.messenger.send_video
            message_id = await method(plan["chat_id"], artifact["path"],
                f"{plan['mode']} EP8 revision {plan['revision']} {kind}; SHA256 {artifact['sha256']}; approval required")
            value = dict(**intent, message_id=message_id)
            self.validate_delivery(value, intent)
            atomic_json(receipt_path, value)
        self.validate_delivery(value, intent)
        self.load()
        self.control["stages"][name] = dict(status="COMPLETE", intent=intent, result=value,
            outputs={str(receipt_path.resolve()): sha256(receipt_path)}, elapsed_seconds=0)
        self.control["status"] = {"visual-freeze": "WAITING_VISUAL_FREEZE_APPROVAL", "thumbnail": "WAITING_THUMBNAIL_APPROVAL", "video": "WAITING_VIDEO_APPROVAL"}[kind]
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
            if segment.get("editorial_kind") == "FAMILY_REFLECTION" and reference == "Editorial infantil EP8":
                continue
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
    parser.add_argument("action", choices=("create-plan", "approve-plan", "run", "resume", "status", "approve", "reject", "authorize-publication", "bootstrap-history"))
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--mode", choices=("TEST", "LIVE"), default="TEST")
    parser.add_argument("--request", default="New EP8: Abraham and Sarah")
    parser.add_argument("--predecessors", type=Path)
    parser.add_argument("--references", type=Path)
    parser.add_argument("--adapter")
    parser.add_argument("--deployment", type=Path)
    parser.add_argument("--chat-id", default="TEST")
    parser.add_argument("--heroes", action="store_true", help="opt in to separately priced hero clips")
    parser.add_argument("--hero-endpoint", default="")
    parser.add_argument("--hero-cost")
    parser.add_argument("--plan-hash")
    parser.add_argument("--artifact-hash")
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--kind", choices=("visual-freeze", "thumbnail", "video"))
    parser.add_argument("--reason", default="")
    parser.add_argument("--command", default="")
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--image-workers", type=int, default=3)
    parser.add_argument("--qa-workers", type=int, default=2)
    args = parser.parse_args(argv)
    try:
        with studio_factory(args.workspace) as harness:
            if args.action == "bootstrap-history":
                if not args.source_root:
                    raise ValueError("--source-root required for historical bootstrap")
                from src.hybrid.editorial import bootstrap_ep8_history
                result = bootstrap_ep8_history(args.source_root)
                args.workspace.mkdir(parents=True, exist_ok=True)
                outputs = {
                    args.workspace / "historical-bootstrap.json": result,
                    args.workspace / "predecessors.json": result["predecessor_identities"],
                    args.workspace / "successor-brief.json": result["brief"],
                }
                for output, payload in outputs.items():
                    if output.exists() and read(output) != payload:
                        raise ValueError("historical bootstrap output is immutable")
                    atomic_json(output, payload)
                result = {
                    "status": "BOOTSTRAPPED", "read_only": True,
                    "source_root": result["source_root"],
                    "source_manifest_identity": result["source_manifest_identity"],
                    "predecessor_identities": result["predecessor_identities"],
                    "brief_identity": result["brief"]["successor_identity"],
                    "revision": result["brief"]["revision"],
                    "publication_authorized": False,
                    "outputs": {path.name: sha256(path) for path in outputs},
                }
            elif args.action == "create-plan":
                result = harness.create_plan(mode=args.mode, request=args.request, predecessors=args.predecessors,
                    references=args.references, adapter=args.adapter, chat_id=args.chat_id,
                    deployment=args.deployment,
                    image_workers=args.image_workers, qa_workers=args.qa_workers,
                    heroes=args.heroes, hero_endpoint=args.hero_endpoint, hero_cost=args.hero_cost)
            elif args.action == "approve-plan":
                result = harness.approve_plan(args.plan_hash, args.reviewer)
            elif args.action == "approve":
                result = harness.approve(args.kind, args.artifact_hash, args.reviewer)
            elif args.action == "authorize-publication":
                result = harness.authorize_publication(command=args.command, reviewer=args.reviewer)
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