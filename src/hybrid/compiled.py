"""Deterministic compiled production packets.

This module deliberately contains no model client.  A compiler receives the
already-approved narration, semantic timestamps, and prompts, then releases
only the next hash-bound work eligible in the durable ``Executor`` ledger.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import asdict, dataclass
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

from src.hybrid.artifacts import (
    FrozenAsset,
    Manifest,
    atomic_json,
    contact_sheet,
    digest,
    sha256,
)
from src.hybrid.execution import Executor, Job, Provider


@dataclass(frozen=True)
class FrameSpec:
    """A semantic visual window compiled from approved narration timing."""

    scene_id: str
    start: float
    end: float
    prompt: str
    semantic_action: str
    hero: bool = False

    def __post_init__(self):
        if not self.scene_id.strip():
            raise ValueError("scene_id required")
        if self.start < 0 or self.end <= self.start:
            raise ValueError("positive ordered timestamps required")
        if not self.prompt.strip() or not self.semantic_action.strip():
            raise ValueError("semantic action and compiled prompt required")


@dataclass(frozen=True)
class CompiledEpisode:
    """Immutable episode input; changes create a distinct compilation hash."""

    episode_id: str
    audio: FrozenAsset
    frames: tuple[FrameSpec, ...]

    @classmethod
    def compile(cls, episode_id: str, audio: FrozenAsset, frames):
        if not episode_id.strip():
            raise ValueError("episode_id required")
        frames = tuple(frames)
        if not frames:
            raise ValueError("at least one semantic frame required")
        audio.verify(audio.mode)
        scene_ids = {frame.scene_id for frame in frames}
        if len(scene_ids) != len(frames):
            raise ValueError("duplicate scene_id")
        ordered = tuple(sorted(frames, key=lambda frame: (frame.start, frame.scene_id)))
        for previous, current in pairwise(ordered):
            if current.start < previous.end:
                raise ValueError("semantic timestamps overlap")
        return cls(episode_id, audio, ordered)

    @property
    def checksum(self):
        return digest(asdict(self))

    def save(self, path):
        atomic_json(path, {"episode": asdict(self), "checksum": self.checksum})

    @classmethod
    def load(cls, path):
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        episode = raw["episode"]
        audio = FrozenAsset(**{**episode["audio"], "path": Path(episode["audio"]["path"])})
        result = cls.compile(episode["episode_id"], audio, (FrameSpec(**frame) for frame in episode["frames"]))
        if raw.get("checksum") != result.checksum:
            raise ValueError("compiled episode checksum mismatch")
        return result


def compile_storyboard(episode_id: str, audio: FrozenAsset, scenes) -> CompiledEpisode:
    """Compile already-authored visual prompts; it never asks a model per frame."""
    frames = []
    for scene in scenes:
        frames.append(
            FrameSpec(
                scene_id=str(scene.get("scene_id", "")),
                start=float(scene["start"]),
                end=float(scene["end"]),
                prompt=str(scene.get("image_prompt", "")),
                semantic_action=str(scene.get("visual_action") or scene.get("action") or ""),
                hero=bool(scene.get("hero", False)),
            )
        )
    return CompiledEpisode.compile(episode_id, audio, frames)


class ProductionRun:
    """Data-driven release policy around the existing transactional executor."""

    def __init__(
        self,
        episode: CompiledEpisode,
        executor: Executor,
        source_manifest: Manifest,
        *,
        endpoint: str,
        image_cost: Decimal,
        imported_scenes=(),
        blocked_scenes=(),
    ):
        if not endpoint.strip() or image_cost <= 0:
            raise ValueError("explicit endpoint and positive image cost required")
        episode.audio.verify(episode.audio.mode)
        source_manifest.verify(episode.audio.mode)
        self.episode = episode
        self.executor = executor
        self.source_manifest = source_manifest
        self.endpoint = endpoint
        self.image_cost = image_cost
        self.imported_scenes = frozenset(imported_scenes)
        self.blocked_scenes = frozenset(blocked_scenes)
        if not self.imported_scenes <= self.blocked_scenes:
            raise ValueError("imported scenes must be permanently non-submittable")
        known_scenes = {frame.scene_id for frame in episode.frames}
        if not self.blocked_scenes <= known_scenes:
            raise ValueError("blocked scene is absent from compiled episode")
        self._baselines = {
            frame.scene_id: self._job_for(frame, "first", image_cost)
            for frame in episode.frames
            if frame.scene_id not in self.blocked_scenes
        }
        self._heroes: dict[str, Job] = {}

    def _job_for(self, frame: FrameSpec, category: str, cost: Decimal, *, predecessor="", prompt=None):
        return Job(
            scene=frame.scene_id,
            category=category,
            mode=self.episode.audio.mode,
            endpoint=self.endpoint,
            payload={
                "episode": self.episode.episode_id,
                "compilation": self.episode.checksum,
                "prompt": prompt or frame.prompt,
                "semantic_action": frame.semantic_action,
                "start": frame.start,
                "end": frame.end,
            },
            manifest=self.source_manifest,
            cost=cost,
            predecessor=predecessor,
        )

    async def dispatch_baselines(self, provider: Provider):
        if not self._baselines:
            return {}
        receipts = await asyncio.gather(
            *(self.executor.run(job, provider) for job in self._baselines.values())
        )
        return {job.scene: receipt for job, receipt in zip(self._baselines.values(), receipts)}

    def _completed_job_for_result(self, scene_id: str, result_sha256: str):
        jobs = [self._baselines.get(scene_id), self._heroes.get(scene_id)]
        for job in jobs:
            if job is None:
                continue
            receipt = self.executor.inspect(job.request_id)
            if receipt and receipt.get("result_sha256") == result_sha256:
                return job
        raise ValueError("result hash is not an eligible compiled receipt")

    def record_visual_qa(self, scene_id: str, result_sha256: str, approved: bool, reviewer: str):
        job = self._completed_job_for_result(scene_id, result_sha256)
        self.executor.qa(job.request_id, result_sha256, approved, reviewer)

    def eligible_hero_jobs(self, cost: Decimal, endpoint: str):
        if cost <= 0 or not endpoint.strip():
            raise ValueError("explicit hero endpoint and positive cost required")
        eligible = []
        for frame in self.episode.frames:
            if not frame.hero or frame.scene_id in self._heroes:
                continue
            baseline = self.executor.inspect(self._baselines[frame.scene_id].request_id)
            if baseline and baseline.get("qa") is True:
                hero = Job(
                    scene=frame.scene_id,
                    category="hero",
                    mode=self.episode.audio.mode,
                    endpoint=endpoint,
                    payload={
                        "episode": self.episode.episode_id,
                        "compilation": self.episode.checksum,
                        "prompt": frame.prompt,
                        "semantic_action": frame.semantic_action,
                    },
                    manifest=self.source_manifest,
                    cost=cost,
                    predecessor=self._baselines[frame.scene_id].request_id,
                )
                self._heroes[frame.scene_id] = hero
                if self.executor.inspect(hero.request_id) is None:
                    eligible.append(hero)
        return tuple(eligible)

    def remediation_job(self, scene_id: str, correction_prompt: str):
        baseline = self._baselines.get(scene_id)
        receipt = self.executor.inspect(baseline.request_id) if baseline else None
        if not receipt or receipt.get("qa") is not False:
            raise ValueError("remediation requires rejected hash-bound baseline QA")
        frame = next(frame for frame in self.episode.frames if frame.scene_id == scene_id)
        return self._job_for(
            frame,
            "correction",
            self.image_cost,
            predecessor=baseline.request_id,
            prompt=correction_prompt,
        )

    def render_ready(self):
        baselines = [self.executor.inspect(job.request_id) for job in self._baselines.values()]
        if not all(row and row.get("qa") is True for row in baselines):
            return False
        heroes = [self.executor.inspect(job.request_id) for job in self._heroes.values()]
        return all(row and row.get("qa") is True for row in heroes)


class OperationalPipeline:
    """Persisted operational boundary for compiled image production and QA."""

    def __init__(
        self,
        episode: CompiledEpisode,
        executor: Executor,
        source_manifest: Manifest,
        *,
        workspace: Path,
        endpoint: str,
        image_cost: Decimal,
    ):
        self.workspace = Path(workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.episode = episode
        self.run = ProductionRun(
            episode,
            executor,
            source_manifest,
            endpoint=endpoint,
            image_cost=image_cost,
        )
        self.episode.save(self.workspace / "compiled_episode.json")

    async def dispatch_baselines(self, provider: Provider):
        return await self.run.dispatch_baselines(provider)

    def _promote_candidate(self, scene_id: str, result_sha256: str, reviewer: str):
        job = self.run._completed_job_for_result(scene_id, result_sha256)
        receipt = self.run.executor.inspect(job.request_id)
        source = Path(receipt["result"])
        if sha256(source) != result_sha256:
            raise ValueError("candidate bytes changed after receipt")
        output = self.workspace / "approved_images" / f"{scene_id}-{result_sha256}{source.suffix}"
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            if sha256(output) != result_sha256:
                raise ValueError("approved candidate path collision")
        else:
            shutil.copyfile(source, output)
            if sha256(output) != result_sha256:
                output.unlink(missing_ok=True)
                raise ValueError("approved candidate copy hash mismatch")
        return FrozenAsset.approve(output, reviewer, self.episode.audio.mode)

    def record_visual_qa(self, scene_id: str, result_sha256: str, approved: bool, reviewer: str):
        self.run.record_visual_qa(scene_id, result_sha256, approved, reviewer)
        qa = {
            "scene_id": scene_id,
            "result_sha256": result_sha256,
            "approved": approved,
            "reviewer": reviewer,
            "compilation": self.episode.checksum,
        }
        atomic_json(self.workspace / "qa" / f"{scene_id}.json", qa)
        return self._promote_candidate(scene_id, result_sha256, reviewer) if approved else None

    def approved_manifest(self):
        assets = []
        for frame in self.episode.frames:
            receipt = self.run.executor.inspect(self.run._baselines[frame.scene_id].request_id)
            if not receipt or receipt.get("qa") is not True:
                raise ValueError("all active images require approved QA")
            qa_path = self.workspace / "qa" / f"{frame.scene_id}.json"
            if not qa_path.is_file():
                raise ValueError("approved QA report missing")
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            assets.append(self._promote_candidate(frame.scene_id, receipt["result_sha256"], qa["reviewer"]))
        sheet = self.workspace / "contact_sheet.png"
        contact_sheet(assets, sheet)
        manifest = Manifest.freeze(
            assets,
            FrozenAsset.approve(sheet, "compiled-qa", self.episode.audio.mode),
            "compiled-qa",
            self.episode.audio.mode,
        )
        manifest.save(self.workspace / "manifest.json")
        return manifest
