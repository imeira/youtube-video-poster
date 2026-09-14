"""Deterministic compiled production packets.

This module deliberately contains no model client.  A compiler receives the
already-approved narration, semantic timestamps, and prompts, then releases
only the next hash-bound work eligible in the durable ``Executor`` ledger.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from decimal import Decimal
from itertools import pairwise

from src.hybrid.artifacts import FrozenAsset, Manifest, digest
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
        self._baselines = {
            frame.scene_id: self._job_for(frame, "first", image_cost)
            for frame in episode.frames
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
