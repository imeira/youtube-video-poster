"""Durable control plane for autonomous production with exactly two human gates.

This module is deliberately provider-agnostic: paid work always reaches a provider
through ``Executor.run``.  TEST is supported for local integration fixtures only.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.hybrid.artifacts import FrozenAsset, Manifest
from src.hybrid.cas import content_key
from src.hybrid.execution import Executor, Job
from src.hybrid.locks import file_lock, file_slot
from src.hybrid.standing_budget import StandingBudgetLedger


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
    return sha256(
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        ).encode()
    ).hexdigest()


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
    mode: str
    episode_cap_usd: Decimal
    month_cap_usd: Decimal
    experiment_cap_usd: Decimal
    allowed_providers: frozenset[str]
    publication: dict[str, str]
    publication_enabled: bool
    digest: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AutonomousPolicy:
        if not isinstance(data, dict) or data.get("schema") != 1:
            raise PolicyError("policy schema 1 is required")
        version = data.get("version")
        mode = data.get("mode")
        providers = data.get("allowed_providers")
        publication = data.get("publication")
        if not isinstance(version, str) or not version.strip():
            raise PolicyError("policy version is required")
        if mode != "TEST":
            raise PolicyError("policy mode must explicitly be TEST")
        if (
            not isinstance(providers, list)
            or not providers
            or not all(isinstance(item, str) and item for item in providers)
        ):
            raise PolicyError("allowed_providers must be a non-empty string list")
        if (
            not isinstance(publication, dict)
            or type(publication.get("enabled")) is not bool
        ):
            raise PolicyError("publication policy must explicitly set enabled")
        if publication["enabled"] and not all(
            isinstance(publication.get(key), str) and publication[key].strip()
            for key in ("channel", "visibility")
        ):
            raise PolicyError("publication policy requires channel and visibility")
        return cls(
            version=version,
            mode=mode,
            episode_cap_usd=_decimal(data.get("episode_cap_usd"), "episode_cap_usd"),
            month_cap_usd=_decimal(data.get("month_cap_usd"), "month_cap_usd"),
            experiment_cap_usd=_decimal(
                data.get("experiment_cap_usd"), "experiment_cap_usd"
            ),
            allowed_providers=frozenset(providers),
            publication={
                key: publication[key]
                for key in ("channel", "visibility")
                if key in publication
            },
            publication_enabled=publication["enabled"],
            digest=_digest_json(data),
        )

    def authorize_paid(
        self,
        provider: str,
        cost: Decimal | None,
        *,
        monthly_spend: Decimal,
        experiment_spend: Decimal,
        experiment: bool = False,
    ) -> None:
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
            return {
                "schema": 1,
                "requests": {},
                "totals": {
                    "money_usd": "0",
                    "quota_units": 0,
                    "tokens_cached": 0,
                    "tokens_input": 0,
                    "tokens_output": 0,
                    "tokens_reasoning": 0,
                },
            }
        return json.loads(self.path.read_text(encoding="utf-8"))

    def record(
        self,
        request_id: str,
        *,
        model: str,
        tokens_input: int,
        tokens_cached: int,
        tokens_output: int,
        tokens_reasoning: int,
        latency_s: float,
        money_usd: str,
        quota_units: int,
    ) -> None:
        if (
            not request_id
            or not model
            or min(
                tokens_input,
                tokens_cached,
                tokens_output,
                tokens_reasoning,
                quota_units,
            )
            < 0
            or latency_s < 0
        ):
            raise ValueError("invalid metric values")
        amount = _decimal(money_usd, "money_usd")
        document = self.load()
        if request_id in document["requests"]:
            return
        record = {
            "model": model,
            "tokens_input": tokens_input,
            "tokens_cached": tokens_cached,
            "tokens_output": tokens_output,
            "tokens_reasoning": tokens_reasoning,
            "latency_s": latency_s,
            "money_usd": str(amount),
            "quota_units": quota_units,
        }
        document["requests"][request_id] = record
        totals = document["totals"]
        totals["money_usd"] = str(
            _decimal(totals["money_usd"], "stored money") + amount
        )
        for key in (
            "quota_units",
            "tokens_cached",
            "tokens_input",
            "tokens_output",
            "tokens_reasoning",
        ):
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

    def put(
        self, kind: str, inputs: list[Path], parameters: dict[str, Any], output: Path
    ) -> str:
        output = Path(output)
        if not output.is_file():
            raise ValueError("cache output must exist")
        key = self._key(kind, inputs, parameters)
        target = self.root / kind / key
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(output.read_bytes())
        if (
            sha256(target.read_bytes()).hexdigest()
            != sha256(output.read_bytes()).hexdigest()
        ):
            raise ValueError("cache key collision")
        _atomic_json(
            target.with_suffix(".json"),
            {"key": key, "output_sha256": sha256(target.read_bytes()).hexdigest()},
        )
        return key

    def get(
        self, kind: str, inputs: list[Path], parameters: dict[str, Any]
    ) -> Path | None:
        target = self.root / kind / self._key(kind, inputs, parameters)
        if not target.is_file():
            return None
        try:
            receipt = json.loads(
                target.with_suffix(".json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("cache hash receipt is missing or corrupt") from error
        if receipt != {
            "key": self._key(kind, inputs, parameters),
            "output_sha256": sha256(target.read_bytes()).hexdigest(),
        }:
            raise ValueError("cache output hash mismatch")
        return target


class AutonomousTwoGateRunner:
    """Resume-safe generic runner. It never manufactures QA, gates, or publication."""

    def __init__(
        self,
        root: Path,
        episode_id: str,
        policy: AutonomousPolicy,
        executor: Executor,
        ledger: StandingBudgetLedger | None = None,
    ):
        if ledger is None:
            raise PolicyError("a compatible TEST standing budget ledger is required")
        expected_ledger = {
            "policy_sha256": policy.digest,
            "mode": "TEST",
            "episode": int(policy.episode_cap_usd * 1000000),
            "month": int(policy.month_cap_usd * 1000000),
            "experiment": int(policy.experiment_cap_usd * 1000000),
        }
        if any(
            ledger.binding.get(key) != value for key, value in expected_ledger.items()
        ):
            raise PolicyError("standing budget ledger is incompatible with TEST policy")
        if not episode_id.startswith("TEST-"):
            raise PolicyError("TEST runner requires a TEST episode namespace")
        self.root = Path(root)
        self.episode_id = episode_id
        self.policy = policy
        self.executor = executor
        self.ledger = ledger
        self.path = self.root / "runner.json"
        self.lock_path = self.root / "runner.lock"
        self._jobs: dict[str, Job] = {}

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            raise RuntimeError("runner has not been started")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data.get("policy_sha256") != self.policy.digest:
            raise PolicyError("policy changed; explicit new runner required")
        if data.get("mode") != "TEST" or data.get("namespace") != "TEST":
            raise PolicyError("runner state is not in the TEST namespace")
        return data

    def _save(self, document: dict[str, Any]) -> dict[str, Any]:
        _atomic_json(self.path, document)
        return document

    @staticmethod
    def _job_binding(job: Job) -> dict[str, str]:
        return {"request_id": job.request_id, "job_sha256": _digest_json(job.__dict__)}

    @staticmethod
    def _job_spec(job: Job) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(job), default=str, sort_keys=True))

    @staticmethod
    def _job_from_spec(spec: dict[str, Any]) -> Job:
        def asset(raw: dict[str, Any]) -> FrozenAsset:
            return FrozenAsset(
                Path(raw["path"]), raw["sha256"], raw["reviewer"], raw["mode"]
            )

        raw = spec["manifest"]
        manifest = Manifest(
            tuple(asset(item) for item in raw["assets"]),
            asset(raw["sheet"]),
            raw["reviewer"],
            raw["mode"],
        )
        return Job(
            spec["scene"],
            spec["category"],
            spec["mode"],
            spec["endpoint"],
            spec["payload"],
            manifest,
            Decimal(spec["cost"]),
            spec.get("predecessor", ""),
        )

    def _rebuild_jobs(self, document: dict[str, Any]) -> None:
        jobs = [self._job_from_spec(item) for item in document.get("job_specs", [])]
        self.validate_resume_jobs(jobs)

    def assert_packet(self, packet: dict[str, Any]) -> None:
        if _digest_json(packet) != self._load()["packet_sha256"]:
            raise ValueError("packet bytes do not bind the started runner")

    def validate_resume_jobs(self, jobs: list[Job]) -> dict[str, dict[str, str]]:
        expected = {
            item["request_id"]: {
                "request_id": item["request_id"],
                "job_sha256": item["job_sha256"],
            }
            for item in self._load()["jobs"]
        }
        actual = {job.request_id: self._job_binding(job) for job in jobs}
        if actual != expected:
            raise ValueError("resume jobs do not match hash-bound started jobs")
        self._jobs = {job.request_id: job for job in jobs}
        return actual

    def start(self, packet: dict[str, Any], jobs: list[Job]) -> dict[str, Any]:
        with file_lock(self.lock_path):
            return self._start(packet, jobs)

    def _start(self, packet: dict[str, Any], jobs: list[Job]) -> dict[str, Any]:
        if self.path.exists():
            existing = self._load()
            if (
                existing["episode_id"] != self.episode_id
                or existing["packet_sha256"] != _digest_json(packet)
                or existing["jobs"] != [self._job_binding(job) for job in jobs]
            ):
                raise ValueError(
                    "started runner conflicts with episode, packet, or jobs; never overwrite"
                )
            return existing
        if not self.episode_id or not isinstance(packet, dict) or not jobs:
            raise ValueError("episode, packet and at least one job are required")
        seen = set()
        serialized = []
        total = Decimal(0)
        for job in jobs:
            if job.request_id in seen:
                raise ValueError("jobs must have unique request ids")
            seen.add(job.request_id)
            if job.mode != "TEST":
                raise PolicyError(
                    "LIVE is not qualified by this runner; use existing production path"
                )
            self.policy.authorize_paid(
                job.endpoint,
                job.cost,
                monthly_spend=Decimal(0),
                experiment_spend=Decimal(0),
            )
            total += job.cost
            serialized.append(self._job_binding(job))
            self._jobs[job.request_id] = job
        if total > self.policy.episode_cap_usd:
            raise PolicyError("episode jobs exceed permanent budget ceiling")
        return self._save(
            {
                "schema": 3,
                "mode": "TEST",
                "namespace": "TEST",
                "episode_id": self.episode_id,
                "policy_sha256": self.policy.digest,
                "packet_sha256": _digest_json(packet),
                "jobs": serialized,
                "job_specs": [self._job_spec(job) for job in jobs],
                "qa": {},
                "media": {},
                "delivery": {},
                "gates": {},
                "state": "READY",
                "created_at": time.time(),
            }
        )

    def add_successors(self, jobs: list[Job]) -> dict[str, Any]:
        """Append immutable TEST successors and invalidate dependent media gates."""
        with file_lock(self.lock_path):
            document = self._load()
            if document["state"] in {"PUBLISHED", "PUBLISH_INTENT"}:
                raise PolicyError("published or ambiguous delivery cannot accept successors")
            known = {item["request_id"]: item for item in document["jobs"]}
            total = sum(Decimal(spec["cost"]) for spec in document["job_specs"])
            added = False
            for job in jobs:
                binding = self._job_binding(job)
                existing = known.get(job.request_id)
                if existing is not None:
                    if existing.get("job_sha256") != binding["job_sha256"]:
                        raise ValueError("successor request ID conflicts with durable job")
                    continue
                if job.mode != "TEST" or not job.predecessor:
                    raise PolicyError("only TEST hash-bound successor jobs are accepted")
                if job.predecessor not in known:
                    raise ValueError("successor predecessor is not bound to this runner")
                self.policy.authorize_paid(job.endpoint, job.cost, monthly_spend=Decimal(0), experiment_spend=Decimal(0))
                total += job.cost
                if total > self.policy.episode_cap_usd:
                    raise PolicyError("successor jobs exceed permanent budget ceiling")
                document["jobs"].append(binding)
                document["job_specs"].append(self._job_spec(job))
                known[job.request_id] = binding
                for predecessor in document["jobs"]:
                    if predecessor["request_id"] == job.predecessor:
                        predecessor["superseded_by"] = job.request_id
                        break
                added = True
            if not added:
                return document
            document["media"] = {}
            document["delivery"] = {}
            document.pop("technical_qa", None)
            document["gates"] = {}
            document["state"] = "RUNNING"
            return self._save(document)

    async def resume(
        self,
        provider,
        independent_qa: Callable[[dict], dict],
        jobs: list[Job] | None = None,
    ) -> dict[str, Any]:
        async with file_slot([self.lock_path]):
            return await self._resume(provider, independent_qa, jobs)

    async def _resume(
        self,
        provider,
        independent_qa: Callable[[dict], dict],
        jobs: list[Job] | None = None,
    ) -> dict[str, Any]:
        document = self._load()
        if jobs is not None:
            self.validate_resume_jobs(jobs)
        else:
            self._rebuild_jobs(document)
        if document["state"] not in {"READY", "RUNNING"}:
            return document
        document["state"] = "RUNNING"
        self._save(document)
        for binding in document["jobs"]:
            request_id = binding["request_id"]
            receipt = self.executor.inspect(request_id)
            if receipt is None:
                job = self._jobs.get(request_id)
                if job is None:
                    raise RuntimeError(
                        "runner resume needs the original hash-bound Job objects"
                    )
                if self.ledger is not None:
                    self.ledger.reserve(
                        request_id, self.episode_id, job.endpoint, job.cost
                    )
                try:
                    await self.executor.run(job, provider)
                except asyncio.CancelledError:
                    if self.ledger is not None:
                        self.ledger.mark_unknown(request_id)
                    raise
                except Exception:
                    if self.ledger is not None:
                        self.ledger.mark_unknown(request_id)
                    raise
                receipt = self.executor.inspect(request_id)
            if receipt.get("status") != "COMPLETE":
                if self.ledger is not None:
                    self.ledger.mark_unknown(request_id)
                raise RuntimeError(
                    "incomplete job requires resume_with_jobs; no ambiguous resubmit"
                )
            result_path = Path(receipt.get("result", ""))
            if not result_path.is_file() or sha256(
                result_path.read_bytes()
            ).hexdigest() != receipt.get("result_sha256"):
                if self.ledger is not None:
                    self.ledger.mark_unknown(request_id)
                raise ValueError("completed receipt output hash mismatch")
            if self.ledger is not None:
                self.ledger.settle(request_id, receipt["actual_cost"])
            if request_id not in document["qa"]:
                verdict = independent_qa(receipt)
                if asyncio.iscoroutine(verdict):
                    verdict = await verdict
                identity = (
                    verdict.get("reviewer_identity")
                    if isinstance(verdict, dict)
                    else None
                )
                if (
                    not isinstance(verdict, dict)
                    or verdict.get("request_id") != request_id
                    or verdict.get("result_sha256") != receipt["result_sha256"]
                    or not isinstance(identity, dict)
                    or not all(
                        isinstance(identity.get(k), str) and identity[k].strip()
                        for k in ("subject", "issuer")
                    )
                    or identity["subject"] == "generator"
                ):
                    document["qa"][request_id] = {
                        "approved": False,
                        "codes": ["QA_CONTRACT_INVALID"],
                    }
                    document["state"] = "REJECTED"
                    self._save(document)
                    raise ValueError(
                        "independent QA must bind request_id, result_sha256, and non-generator identity"
                    )
                if (
                    verdict.get("approved") is not True
                    or not isinstance(verdict.get("reviewer"), str)
                    or not verdict["reviewer"].strip()
                ):
                    document["qa"][request_id] = {
                        "approved": False,
                        "codes": verdict.get("codes", ["QA_FAIL"]),
                        "request_id": request_id,
                        "result_sha256": receipt["result_sha256"],
                        "reviewer": verdict.get("reviewer", ""),
                        "reviewer_identity": identity,
                    }
                    document["state"] = "REJECTED"
                    return self._save(document)
                self.executor.qa(
                    request_id, receipt["result_sha256"], True, verdict["reviewer"]
                )
                document["qa"][request_id] = {
                    "approved": True,
                    "result_sha256": receipt["result_sha256"],
                    "reviewer": verdict["reviewer"],
                    "reviewer_identity": identity,
                    "codes": verdict.get("codes", []),
                }
                self._save(document)
        document["state"] = "MEDIA_PENDING"
        return self._save(document)

    async def resume_with_jobs(
        self, jobs: list[Job], provider, independent_qa: Callable[[dict], dict]
    ) -> dict[str, Any]:
        return await self.resume(provider, independent_qa, jobs)

    def record_qa(self, request_id: str, receipt: dict, verdict: dict) -> None:
        """Record only a PASS with identity evidence; FAIL is a terminal remediation signal."""
        if verdict.get("approved") is not True:
            raise ValueError(
                "QA FAIL blocks human gates; remediation successor required"
            )
        identity = verdict.get("reviewer_identity")
        if (
            not isinstance(identity, dict)
            or not identity.get("subject")
            or not identity.get("issuer")
        ):
            raise ValueError("independent QA requires verifier identity evidence")
        if request_id not in {item["request_id"] for item in self._load()["jobs"]}:
            raise ValueError("QA job not bound to runner")
        path = Path(receipt.get("result", ""))
        if (
            receipt.get("status") != "COMPLETE"
            or not path.is_file()
            or sha256(path.read_bytes()).hexdigest() != receipt.get("result_sha256")
        ):
            raise ValueError("QA receipt hash mismatch")
        self.executor.qa(
            request_id, receipt["result_sha256"], True, verdict["reviewer"]
        )

    def stage_delivery(
        self, thumbnail: Path, video: Path, *, qa: dict[str, Any]
    ) -> dict[str, Any]:
        with file_lock(self.lock_path):
            return self._stage_delivery(thumbnail, video, qa=qa)

    def _stage_delivery(
        self, thumbnail: Path, video: Path, *, qa: dict[str, Any]
    ) -> dict[str, Any]:
        """Open a gate only for QA'd, hash-bound, decodable TEST media."""
        document = self._load()
        active = [
            item["request_id"]
            for item in document["jobs"]
            if "superseded_by" not in item
        ]
        if (
            document["state"] != "MEDIA_PENDING"
            or not all(request_id in document["qa"] for request_id in active)
            or any(document["qa"][request_id].get("approved") is False for request_id in active)
        ):
            raise ValueError("media staging requires completed hash-bound source QA")
        if (
            qa.get("approved") is not True
            or qa.get("mode") != "TEST"
            or qa.get("subject") == "generator"
        ):
            document["state"] = "REJECTED"
            self._save(document)
            raise ValueError("technical QA requires independent TEST assertions")
        artifacts = {"thumbnail": Path(thumbnail), "video": Path(video)}
        for kind, artifact in artifacts.items():
            if not artifact.is_file():
                raise ValueError("staged artifact is missing")
            if kind == "thumbnail":
                from PIL import Image

                with Image.open(artifact) as image:
                    image.verify()
            else:
                checked = subprocess.run(
                    [
                        "ffprobe",
                        "-v",
                        "error",
                        "-show_streams",
                        "-of",
                        "json",
                        str(artifact),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if checked.returncode or not any(
                    stream.get("codec_type") == "video"
                    for stream in json.loads(checked.stdout).get("streams", [])
                ):
                    raise ValueError("staged video is not decodable")
        document["media"] = {
            kind: {
                "path": str(path.resolve()),
                "sha256": sha256(path.read_bytes()).hexdigest(),
            }
            for kind, path in artifacts.items()
        }
        document["delivery"] = {
            "mode": "TEST",
            "receipts": {
                kind: {"artifact_sha256": value["sha256"]}
                for kind, value in document["media"].items()
            },
        }
        document["technical_qa"] = qa
        document["state"] = "WAITING_THUMBNAIL_APPROVAL"
        return self._save(document)

    def record_human_gate(
        self, kind: str, artifact: Path, *, approved: bool, reviewer: str
    ) -> dict[str, Any]:
        with file_lock(self.lock_path):
            return self._record_human_gate(
                kind, artifact, approved=approved, reviewer=reviewer
            )

    def _record_human_gate(
        self, kind: str, artifact: Path, *, approved: bool, reviewer: str
    ) -> dict[str, Any]:
        document = self._load()
        expected = {
            "thumbnail": "WAITING_THUMBNAIL_APPROVAL",
            "video": "WAITING_VIDEO_APPROVAL",
        }
        if kind not in expected or document["state"] != expected[kind]:
            raise ValueError("human gate is out of order")
        artifact = Path(artifact)
        expected_media = document.get("media", {}).get(kind, {})
        if str(artifact.resolve()) != expected_media.get("path"):
            raise ValueError(
                "human gate artifact must be the exact staged delivery artifact"
            )
        if (
            not artifact.is_file()
            or not reviewer.strip()
            or not isinstance(approved, bool)
        ):
            raise ValueError(
                "human gate requires existing artifact, boolean decision and reviewer"
            )
        if kind == "thumbnail":
            from PIL import Image

            with Image.open(artifact) as image:
                image.verify()
        else:
            checked = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_streams",
                    "-of",
                    "json",
                    str(artifact),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if checked.returncode or not any(
                item.get("codec_type") == "video"
                for item in json.loads(checked.stdout).get("streams", [])
            ):
                raise ValueError("video gate requires a decodable video artifact")
        digest = sha256(artifact.read_bytes()).hexdigest()
        if digest != expected_media.get("sha256"):
            raise ValueError(
                "human gate artifact bytes no longer match staged delivery"
            )
        document["gates"][kind] = {
            "artifact_path": str(artifact.resolve()),
            "artifact_sha256": digest,
            "approved": approved,
            "reviewer": reviewer,
        }
        if not approved:
            document["state"] = "REJECTED"
        elif kind == "thumbnail":
            document["state"] = "WAITING_VIDEO_APPROVAL"
        else:
            document["state"] = "READY_TO_PUBLISH"
        return self._save(document)

    def publish(self, publisher: Callable[[dict], dict]) -> dict[str, Any]:
        with file_lock(self.lock_path):
            return self._publish(publisher)

    def _publish(self, publisher: Callable[[dict], dict]) -> dict[str, Any]:
        document = self._load()
        if not self.policy.publication_enabled:
            raise PolicyError(
                "publication is disabled by policy; production remains permitted"
            )
        if document["state"] == "PUBLISHED":
            return document
        if document["state"] == "PUBLISH_INTENT":
            raise RuntimeError(
                "ambiguous publish intent requires remote reconciliation; no replay"
            )
        if document["state"] != "READY_TO_PUBLISH":
            raise PolicyError("publication requires both exact human approvals")
        gates = document["gates"]
        if not gates.get("thumbnail", {}).get("approved") or not gates.get(
            "video", {}
        ).get("approved"):
            raise PolicyError("publication requires two approved hash-bound gates")
        for kind in ("thumbnail", "video"):
            gate = gates[kind]
            path = Path(gate.get("artifact_path", ""))
            if not path.is_file() or sha256(path.read_bytes()).hexdigest() != gate.get(
                "artifact_sha256"
            ):
                raise PolicyError("approved artifact bytes no longer match gate")
        package = {
            "episode_id": self.episode_id,
            "policy_version": self.policy.version,
            "channel": self.policy.publication["channel"],
            "visibility": self.policy.publication["visibility"],
            "thumbnail_sha256": gates["thumbnail"]["artifact_sha256"],
            "video_sha256": gates["video"]["artifact_sha256"],
        }
        document["state"] = "PUBLISH_INTENT"
        document["publication_intent"] = package
        self._save(document)
        receipt = publisher(package)
        if (
            not isinstance(receipt, dict)
            or not receipt.get("video_id")
            or not isinstance(receipt.get("readback"), dict)
            or receipt["readback"].get("video_id") != receipt["video_id"]
        ):
            raise RuntimeError("publisher readback must bind the exact approved video")
        document["publication"] = receipt
        document["state"] = "PUBLISHED"
        return self._save(document)


class AutonomousCompiledTestAdapter:
    """Connect TEST compiled image work to the durable two-gate runner.

    This is deliberately a narrow image-production bridge.  It shares the
    compiled pipeline's Executor, writes the runner's durable state first, and
    promotes only hash-bound QA-passed candidates back into the compiled
    manifest.  It does not render, stage media, open gates, publish, or enable
    LIVE work.
    """

    def __init__(self, runner: AutonomousTwoGateRunner, pipeline):
        self.runner = runner
        self.pipeline = pipeline
        if runner.episode_id != pipeline.episode.episode_id:
            raise ValueError("runner and compiled pipeline episode IDs must match")
        if pipeline.episode.audio.mode != "TEST":
            raise PolicyError("compiled autonomous adapter is TEST-only")
        if runner.executor is not pipeline.run.executor:
            raise ValueError("runner and pipeline must share one Executor instance")
        if any(job.mode != "TEST" for job in pipeline.baseline_jobs()):
            raise PolicyError("compiled autonomous adapter accepts TEST jobs only")
        if runner.path.is_file():
            self._rehydrate_compiled_lineage(runner._load())

    def _rehydrate_compiled_lineage(self, document: dict[str, Any]) -> None:
        """Recover dynamic correction/hero maps from hash-bound runner job specs."""
        baseline_ids = {job.request_id for job in self.pipeline.baseline_jobs()}
        for spec in document.get("job_specs", []):
            job = self.runner._job_from_spec(spec)
            if job.request_id in baseline_ids:
                continue
            if job.category == "correction":
                self.pipeline.run._corrections[job.scene] = job
                self.pipeline.run._active_images[job.scene] = job
            elif job.category in {"hero", "hero_retry"}:
                self.pipeline.run._heroes[job.scene] = job
            else:
                raise ValueError("runner contains unsupported compiled successor category")

    def packet(self) -> dict[str, str]:
        """Stable binding for the immutable compiled inputs, not an approval."""
        return {
            "schema": "compiled-test-adapter-v1",
            "episode_id": self.pipeline.episode.episode_id,
            "compiled_sha256": self.pipeline.episode.checksum,
            "source_manifest_sha256": self.pipeline.run.source_manifest.checksum,
        }

    def start(self) -> dict[str, Any]:
        jobs = list(self.pipeline.baseline_jobs())
        if not jobs:
            raise ValueError("compiled adapter requires at least one dispatchable baseline")
        return self.runner.start(self.packet(), jobs)

    async def resume(self, provider, independent_qa: Callable[[dict], dict]) -> dict[str, Any]:
        """Resume runner dispatch, then promote only its exact completed QA PASSes."""

        async def qa_after_technical_check(receipt: dict) -> dict:
            scene_id = receipt.get("request", {}).get("scene")
            category = receipt.get("request", {}).get("category")
            if category in {"hero", "hero_retry"}:
                verdict = independent_qa(receipt)
                if asyncio.iscoroutine(verdict):
                    verdict = await verdict
                return verdict
            packet = self.pipeline.prepare_qa_packets((scene_id,)).get(scene_id)
            if not packet or packet.get("technical_pass") is not True:
                return {
                    "approved": False,
                    "reviewer": "technical-test-qa",
                    "reviewer_identity": {
                        "subject": "technical-test-qa",
                        "issuer": "TEST local inspection",
                    },
                    "request_id": receipt.get("request_id"),
                    "result_sha256": receipt.get("result_sha256"),
                    "codes": (packet or {}).get("errors", ["TECHNICAL_QA_FAIL"]),
                }
            verdict = independent_qa(receipt)
            if asyncio.iscoroutine(verdict):
                verdict = await verdict
            return verdict

        document = await self.runner.resume(provider, qa_after_technical_check)
        self._synchronize_runner_qa(document)
        if document["state"] == "MEDIA_PENDING":
            self._promote_runner_passes(document)
        return document

    def _synchronize_runner_qa(self, document: dict[str, Any]) -> None:
        """Project durable runner verdicts into the compiled lineage exactly once."""
        for request_id, verdict in document.get("qa", {}).items():
            receipt = self.runner.executor.inspect(request_id)
            if not receipt or receipt.get("status") != "COMPLETE":
                raise ValueError("runner QA has no completed executor receipt")
            if verdict.get("result_sha256") != receipt.get("result_sha256"):
                raise ValueError("runner QA does not bind executor receipt")
            if self.runner.executor.inspect(request_id).get("qa") is not None:
                continue
            job = self.pipeline.run._completed_job_for_result(
                receipt["request"]["scene"], receipt["result_sha256"]
            )
            if job.request_id != request_id:
                raise ValueError("runner QA receipt is not the active compiled lineage")
            reviewer = verdict.get("reviewer")
            if not isinstance(reviewer, str) or not reviewer.strip():
                raise ValueError("runner QA reviewer is missing")
            self.pipeline.record_visual_qa(
                job.scene,
                receipt["result_sha256"],
                verdict.get("approved") is True,
                reviewer,
            )

    async def remediate(
        self, corrections: dict[str, str], provider, independent_qa: Callable[[dict], dict]
    ) -> dict[str, Any]:
        """Create only QA-rejected compiled successors, then resume the same runner."""
        self._synchronize_runner_qa(self.runner._load())
        jobs = [
            self.pipeline.run.remediation_job(scene_id, prompt)
            for scene_id, prompt in corrections.items()
        ]
        self.runner.add_successors(jobs)
        return await self.resume(provider, independent_qa)

    async def run_heroes(
        self,
        cost: Decimal,
        endpoint: str,
        provider,
        independent_qa: Callable[[dict], dict],
    ) -> dict[str, Any]:
        """Run eligible compiled heroes through the existing runner and ledger."""
        jobs = self.pipeline.run.eligible_hero_jobs(cost, endpoint)
        if not jobs:
            raise ValueError("no eligible compiled hero jobs")
        self.runner.add_successors(list(jobs))
        return await self.resume(provider, independent_qa)

    def _promote_runner_passes(self, document: dict[str, Any]) -> None:
        for request_id, verdict in document.get("qa", {}).items():
            if verdict.get("approved") is not True:
                continue
            receipt = self.runner.executor.inspect(request_id)
            if (
                not receipt
                or receipt.get("status") != "COMPLETE"
                or verdict.get("result_sha256") != receipt.get("result_sha256")
                or not isinstance(verdict.get("reviewer"), str)
                or not verdict["reviewer"].strip()
            ):
                raise ValueError("runner PASS is incomplete or does not bind compiled receipt")
            job = self.pipeline.run._completed_job_for_result(
                receipt["request"]["scene"], receipt["result_sha256"]
            )
            if job.request_id != request_id:
                raise ValueError("runner PASS is not the active compiled receipt")
            self.pipeline.record_visual_qa(
                job.scene,
                receipt["result_sha256"],
                True,
                verdict["reviewer"],
            )

    @staticmethod
    def _file_digest(path: Path) -> str:
        return sha256(Path(path).read_bytes()).hexdigest()

    def _sidecars(self, directory: Path) -> dict[str, dict[str, str]]:
        """Create TEST-labelled, separate caption sidecars from compiled timing."""
        directory.mkdir(parents=True, exist_ok=True)
        rows = []
        for index, frame in enumerate(self.pipeline.episode.frames, 1):
            start, end = frame.start, frame.end

            def stamp(seconds: float, separator: str) -> str:
                milliseconds = round(seconds * 1000)
                hours, milliseconds = divmod(milliseconds, 3_600_000)
                minutes, milliseconds = divmod(milliseconds, 60_000)
                seconds, milliseconds = divmod(milliseconds, 1000)
                return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{milliseconds:03}"

            text = f"TEST ONLY — scene {frame.scene_id}: {frame.semantic_action}"
            rows.append((index, stamp(start, ","), stamp(end, ","), text))
        transcript = directory / "transcript.TEST.txt"
        transcript.write_text("\n".join(row[3] for row in rows) + "\n", encoding="utf-8")
        srt = directory / "captions.TEST.srt"
        srt.write_text(
            "\n".join(f"{n}\n{start} --> {end}\n{text}\n" for n, start, end, text in rows),
            encoding="utf-8",
        )
        vtt = directory / "captions.TEST.vtt"
        vtt.write_text(
            "WEBVTT\n\n" + "\n".join(
                f"{n}\n{start.replace(',', '.')} --> {end.replace(',', '.')}\n{text}\n"
                for n, start, end, text in rows
            ),
            encoding="utf-8",
        )
        return {
            kind: {"path": str(path.resolve()), "sha256": self._file_digest(path)}
            for kind, path in {"transcript": transcript, "srt": srt, "vtt": vtt}.items()
        }

    def _verify_existing_delivery(self, evidence: dict[str, Any], manifest) -> None:
        if evidence.get("mode") != "TEST" or evidence.get("manifest_sha256") != manifest.checksum:
            raise ValueError("existing delivery evidence does not bind this compiled manifest")
        for kind, item in {**evidence.get("media", {}), **evidence.get("sidecars", {})}.items():
            path = Path(item.get("path", ""))
            if not path.is_file() or self._file_digest(path) != item.get("sha256"):
                raise ValueError(f"existing {kind} bytes do not match delivery evidence")

    def stage_local_delivery(
        self, renderer, independent_media_qa: Callable[[dict], dict], *, hold: int = 4
    ) -> dict[str, Any]:
        """Render approved TEST pixels locally, preserve sidecars, then open gate one.

        The independent callback is a decision boundary, not a human gate.  It
        must bind the exact delivery bytes and may reject; no TEST decision is
        inferred from successful FFmpeg execution.
        """
        if self.pipeline.episode.audio.mode != "TEST":
            raise PolicyError("compiled local delivery is TEST-only")
        document = self.runner._load()
        manifest = self.pipeline.approved_manifest()
        delivery = self.pipeline.workspace / "delivery"
        evidence_path = delivery / "evidence.json"
        if document["state"] != "MEDIA_PENDING":
            if document["state"] in {
                "WAITING_THUMBNAIL_APPROVAL", "WAITING_VIDEO_APPROVAL", "READY_TO_PUBLISH"
            } and evidence_path.is_file():
                evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                self._verify_existing_delivery(evidence, manifest)
                return document
            raise ValueError("local delivery requires completed compiled image QA")
        if evidence_path.is_file():
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self._verify_existing_delivery(evidence, manifest)
            media = evidence["media"]
        else:
            delivery.mkdir(parents=True, exist_ok=True)
            thumbnail = delivery / "thumbnail.TEST.png"
            video = delivery / "master.mp4"
            if thumbnail.exists() or video.exists():
                raise ValueError("partial delivery exists without hash-bound evidence")
            source = manifest.assets[0].path
            shutil.copyfile(source, thumbnail)
            if self._file_digest(thumbnail) != manifest.assets[0].sha256:
                raise ValueError("local thumbnail copy hash mismatch")
            receipt = renderer.render_compiled(
                self.pipeline,
                self.pipeline.render_scenes(manifest),
                manifest,
                self.pipeline.episode.audio,
                None,
                video,
                hold=hold,
            )
            if self._file_digest(video) != receipt.get("output_sha256"):
                raise ValueError("renderer receipt does not bind local master bytes")
            media = {
                "thumbnail": {"path": str(thumbnail.resolve()), "sha256": self._file_digest(thumbnail)},
                "video": {"path": str(video.resolve()), "sha256": self._file_digest(video)},
            }
            evidence = {
                "schema": "compiled-test-local-delivery-v1",
                "mode": "TEST",
                "manifest_sha256": manifest.checksum,
                "compiled_sha256": self.pipeline.episode.checksum,
                "thumbnail_sha256": media["thumbnail"]["sha256"],
                "video_sha256": media["video"]["sha256"],
                "media": media,
                "sidecars": self._sidecars(delivery),
                "render_receipt": receipt,
            }
            _atomic_json(evidence_path, evidence)
        verdict = independent_media_qa(dict(evidence))
        if asyncio.iscoroutine(verdict):
            raise ValueError("independent local media QA must be synchronous")
        identity = verdict.get("reviewer_identity") if isinstance(verdict, dict) else None
        if (
            not isinstance(verdict, dict)
            or verdict.get("approved") is not True
            or verdict.get("mode") != "TEST"
            or not isinstance(verdict.get("reviewer"), str)
            or not verdict["reviewer"].strip()
            or not isinstance(identity, dict)
            or not identity.get("subject")
            or not identity.get("issuer")
            or any(verdict.get(f"{kind}_sha256") != media[kind]["sha256"] for kind in media)
            or verdict.get("sidecars_sha256")
            != {kind: item["sha256"] for kind, item in evidence["sidecars"].items()}
            or verdict.get("manifest_sha256") != manifest.checksum
        ):
            raise ValueError("independent TEST media QA rejected or did not bind delivery evidence")
        evidence["independent_technical_qa"] = verdict
        _atomic_json(evidence_path, evidence)
        return self.runner.stage_delivery(
            Path(media["thumbnail"]["path"]), Path(media["video"]["path"]), qa={
                "approved": True,
                "mode": "TEST",
                "subject": identity["subject"],
                "issuer": identity["issuer"],
                "reviewer": verdict["reviewer"],
                "evidence_sha256": self._file_digest(evidence_path),
            }
        )
