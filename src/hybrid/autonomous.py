"""Durable control plane for autonomous production with exactly two human gates.

This module is deliberately provider-agnostic: paid work always reaches a provider
through ``Executor.run``.  TEST is supported for local integration fixtures only.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.hybrid.cas import content_key
from src.hybrid.execution import Executor, Job


class PolicyError(ValueError):
    """A policy is missing, malformed, or insufficient to authorize an action."""


def _decimal(value: object, field: str) -> Decimal:
    if value is None:
        raise PolicyError(f"{field} is required")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise PolicyError(f"{field} must be a finite decimal") from error
    if not amount.is_finite() or amount < 0:
        raise PolicyError(f"{field} must be a non-negative finite decimal")
    return amount


def _digest_json(value: object) -> str:
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


@dataclass(frozen=True)
class AutonomousPolicy:
    version: str
    episode_cap_usd: Decimal
    month_cap_usd: Decimal
    experiment_cap_usd: Decimal
    allowed_providers: frozenset[str]
    publication: dict[str, str]
    digest: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AutonomousPolicy:
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise PolicyError("policy schema 1 is required")
        version = data.get("version")
        providers = data.get("allowed_providers")
        publication = data.get("publication")
        if not isinstance(version, str) or not version.strip():
            raise PolicyError("policy version is required")
        if not isinstance(providers, list) or not providers or not all(isinstance(item, str) and item for item in providers):
            raise PolicyError("allowed_providers must be a non-empty string list")
        if not isinstance(publication, dict) or publication.get("enabled") is not True:
            raise PolicyError("publication policy must be explicitly enabled")
        if not all(isinstance(publication.get(key), str) and publication[key].strip() for key in ("channel", "visibility")):
            raise PolicyError("publication policy requires channel and visibility")
        return cls(
            version=version,
            episode_cap_usd=_decimal(data.get("episode_cap_usd"), "episode_cap_usd"),
            month_cap_usd=_decimal(data.get("month_cap_usd"), "month_cap_usd"),
            experiment_cap_usd=_decimal(data.get("experiment_cap_usd"), "experiment_cap_usd"),
            allowed_providers=frozenset(providers),
            publication={key: publication[key] for key in ("channel", "visibility")},
            digest=_digest_json(data),
        )

    def authorize_paid(self, provider: str, cost: Decimal | None, *, monthly_spend: Decimal, experiment_spend: Decimal, experiment: bool = False) -> None:
        if provider not in self.allowed_providers:
            raise PolicyError("provider is not allowed by policy")
        if cost is None:
            raise PolicyError("unknown cost is exposure and blocks paid dispatch")
        amount = _decimal(cost, "cost")
        if amount <= 0:
            raise PolicyError("paid cost must be positive")
        if amount > self.episode_cap_usd or monthly_spend + amount > self.month_cap_usd:
            raise PolicyError("paid dispatch exceeds permanent budget ceiling")
        if experiment and experiment_spend + amount > self.experiment_cap_usd:
            raise PolicyError("experiment ceiling exceeded")


class PersistentMetrics:
    """Idempotent request metrics; subscription quota is never currency."""

    def __init__(self, path: Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {"schema": 1, "requests": {}, "totals": {"money_usd": "0", "quota_units": 0, "tokens_cached": 0, "tokens_input": 0, "tokens_output": 0, "tokens_reasoning": 0}}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def record(self, request_id: str, *, model: str, tokens_input: int, tokens_cached: int, tokens_output: int, tokens_reasoning: int, latency_s: float, money_usd: str, quota_units: int) -> None:
        if not request_id or not model or min(tokens_input, tokens_cached, tokens_output, tokens_reasoning, quota_units) < 0 or latency_s < 0:
            raise ValueError("invalid metric values")
        amount = _decimal(money_usd, "money_usd")
        document = self.load()
        if request_id in document["requests"]:
            return
        record = {"model": model, "tokens_input": tokens_input, "tokens_cached": tokens_cached, "tokens_output": tokens_output, "tokens_reasoning": tokens_reasoning, "latency_s": latency_s, "money_usd": str(amount), "quota_units": quota_units}
        document["requests"][request_id] = record
        totals = document["totals"]
        totals["money_usd"] = str(_decimal(totals["money_usd"], "stored money") + amount)
        for key in ("quota_units", "tokens_cached", "tokens_input", "tokens_output", "tokens_reasoning"):
            totals[key] += record[key]
        _atomic_json(self.path, document)


class ArtifactCache:
    """Small cache for TTS segments and rendered clips, keyed by bytes and config."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _key(self, kind: str, inputs: list[Path], parameters: dict[str, Any]) -> str:
        if kind not in {"tts", "render"}:
            raise ValueError("cache kind must be tts or render")
        return content_key(inputs, {"kind": kind, **parameters})

    def put(self, kind: str, inputs: list[Path], parameters: dict[str, Any], output: Path) -> str:
        output = Path(output)
        if not output.is_file():
            raise ValueError("cache output must exist")
        key = self._key(kind, inputs, parameters)
        target = self.root / kind / key
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(output.read_bytes())
        if sha256(target.read_bytes()).hexdigest() != sha256(output.read_bytes()).hexdigest():
            raise ValueError("cache key collision")
        return key

    def get(self, kind: str, inputs: list[Path], parameters: dict[str, Any]) -> Path | None:
        target = self.root / kind / self._key(kind, inputs, parameters)
        return target if target.is_file() else None


class AutonomousTwoGateRunner:
    """Resume-safe generic runner. It never manufactures QA, gates, or publication."""

    def __init__(self, root: Path, episode_id: str, policy: AutonomousPolicy, executor: Executor):
        self.root = Path(root)
        self.episode_id = episode_id
        self.policy = policy
        self.executor = executor
        self.path = self.root / "runner.json"
        self._jobs: dict[str, Job] = {}

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            raise RuntimeError("runner has not been started")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("policy_sha256") != self.policy.digest:
            raise PolicyError("policy changed; explicit new runner required")
        return data

    def _save(self, document: dict[str, Any]) -> dict[str, Any]:
        _atomic_json(self.path, document)
        return document

    def start(self, packet: dict[str, Any], jobs: list[Job]) -> dict[str, Any]:
        if self.path.exists():
            return self._load()
        if not self.episode_id or not isinstance(packet, dict) or not jobs:
            raise ValueError("episode, packet and at least one job are required")
        seen = set()
        serialized = []
        total = Decimal(0)
        for job in jobs:
            if job.request_id in seen:
                raise ValueError("jobs must have unique request ids")
            seen.add(job.request_id)
            self.policy.authorize_paid(job.endpoint, job.cost, monthly_spend=Decimal(0), experiment_spend=Decimal(0))
            total += job.cost
            serialized.append(job.request_id)
            self._jobs[job.request_id] = job
        if total > self.policy.episode_cap_usd:
            raise PolicyError("episode jobs exceed permanent budget ceiling")
        return self._save({"schema": 1, "episode_id": self.episode_id, "policy_sha256": self.policy.digest, "packet_sha256": _digest_json(packet), "jobs": serialized, "qa": {}, "gates": {}, "state": "READY", "created_at": time.time()})

    async def resume(self, provider, independent_qa: Callable[[dict], dict]) -> dict[str, Any]:
        document = self._load()
        if document["state"] != "READY":
            return document
        document["state"] = "RUNNING"
        self._save(document)
        for request_id in document["jobs"]:
            receipt = self.executor.inspect(request_id)
            if receipt is None:
                job = self._jobs.get(request_id)
                if job is None:
                    raise RuntimeError("runner resume needs the original hash-bound Job objects")
                await self.executor.run(job, provider)
                receipt = self.executor.inspect(request_id)
            if receipt.get("status") != "COMPLETE":
                raise RuntimeError("incomplete job requires resume_with_jobs; no ambiguous resubmit")
            if request_id not in document["qa"]:
                verdict = independent_qa(receipt)
                if not isinstance(verdict, dict) or verdict.get("approved") is not True or not isinstance(verdict.get("reviewer"), str) or not verdict["reviewer"].strip() or verdict["reviewer"].startswith("generator"):
                    raise ValueError("independent QA PASS with distinct reviewer is required")
                self.executor.qa(request_id, receipt["result_sha256"], True, verdict["reviewer"])
                document["qa"][request_id] = {"result_sha256": receipt["result_sha256"], "reviewer": verdict["reviewer"], "codes": verdict.get("codes", [])}
                self._save(document)
        document["state"] = "WAITING_THUMBNAIL_APPROVAL"
        return self._save(document)

    async def resume_with_jobs(self, jobs: list[Job], provider, independent_qa: Callable[[dict], dict]) -> dict[str, Any]:
        document = self._load()
        if document["state"] != "READY":
            return document
        indexed = {job.request_id: job for job in jobs}
        if set(indexed) != set(document["jobs"]):
            raise ValueError("resume jobs do not bind the started runner")
        for job in jobs:
            self.policy.authorize_paid(job.endpoint, job.cost, monthly_spend=Decimal(0), experiment_spend=Decimal(0))
            await self.executor.run(job, provider)
        return await self.resume(provider, independent_qa)

    def record_human_gate(self, kind: str, artifact: Path, *, approved: bool, reviewer: str) -> dict[str, Any]:
        document = self._load()
        expected = {"thumbnail": "WAITING_THUMBNAIL_APPROVAL", "video": "WAITING_VIDEO_APPROVAL"}
        if kind not in expected or document["state"] != expected[kind]:
            raise ValueError("human gate is out of order")
        artifact = Path(artifact)
        if not artifact.is_file() or not reviewer.strip() or not isinstance(approved, bool):
            raise ValueError("human gate requires existing artifact, boolean decision and reviewer")
        digest = sha256(artifact.read_bytes()).hexdigest()
        document["gates"][kind] = {"artifact_sha256": digest, "approved": approved, "reviewer": reviewer}
        if not approved:
            document["state"] = "PAUSED_RESOURCE_LIMIT"
        elif kind == "thumbnail":
            document["state"] = "WAITING_VIDEO_APPROVAL"
        else:
            document["state"] = "READY_TO_PUBLISH"
        return self._save(document)

    def publish(self, publisher: Callable[[dict], dict]) -> dict[str, Any]:
        document = self._load()
        if document["state"] == "PUBLISHED":
            return document
        if document["state"] != "READY_TO_PUBLISH":
            raise PolicyError("publication requires both exact human approvals")
        gates = document["gates"]
        if not gates.get("thumbnail", {}).get("approved") or not gates.get("video", {}).get("approved"):
            raise PolicyError("publication requires two approved hash-bound gates")
        package = {"episode_id": self.episode_id, "policy_version": self.policy.version, "channel": self.policy.publication["channel"], "visibility": self.policy.publication["visibility"], "thumbnail_sha256": gates["thumbnail"]["artifact_sha256"], "video_sha256": gates["video"]["artifact_sha256"]}
        receipt = publisher(package)
        if not isinstance(receipt, dict) or not receipt.get("video_id") or receipt.get("readback") != package["video_sha256"]:
            raise RuntimeError("publisher readback must bind the exact approved video")
        document["publication"] = receipt
        document["state"] = "PUBLISHED"
        return self._save(document)
