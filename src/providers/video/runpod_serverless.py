"""Injected, resumable RunPod serverless adapter for approved hero clips.

This module contains no concrete HTTP client. Deployment code must inject a
transport, keeping tests and the default hybrid path offline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from src.hybrid.artifacts import atomic_json, sha256
from src.hybrid.execution import Job, ProviderResult
from src.hybrid.planner import money


_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_REQUEST_ID = re.compile(r"^[0-9a-f]{64}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class RunPodState(str, Enum):
    SUBMITTING = "SUBMITTING"
    IN_QUEUE = "IN_QUEUE"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class RunPodError(RuntimeError):
    """Base error for a durable RunPod job."""


class RunPodFailed(RunPodError):
    pass


class RunPodCancelled(RunPodError):
    pass


class RunPodTimeout(RunPodCancelled):
    pass


@dataclass(frozen=True)
class PollPolicy:
    initial_delay: float = 1.0
    multiplier: float = 2.0
    maximum_delay: float = 15.0
    timeout: float = 900.0

    def __post_init__(self) -> None:
        values = (
            self.initial_delay,
            self.multiplier,
            self.maximum_delay,
            self.timeout,
        )
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
            raise ValueError("poll policy values must be finite")
        if self.initial_delay <= 0 or self.multiplier < 1 or self.maximum_delay <= 0 or self.timeout <= 0:
            raise ValueError("poll policy requires positive bounded delays")
        if self.initial_delay > self.maximum_delay:
            raise ValueError("initial delay cannot exceed maximum delay")


class RunPodTransport(Protocol):
    async def submit(self, endpoint_id: str, payload: dict) -> dict: ...
    async def status(self, endpoint_id: str, job_id: str) -> dict: ...
    async def cancel(self, endpoint_id: str, job_id: str) -> None: ...
    async def download(self, url: str) -> bytes: ...


class Clock(Protocol):
    def time(self) -> float: ...
    async def sleep(self, seconds: float) -> None: ...


class RunPodHeroProvider:
    """A single-submit, checkpoint-before-poll serverless provider."""

    def __init__(
        self,
        *,
        endpoint_id: str,
        transport: RunPodTransport,
        clock: Clock,
        state_dir: Path | str,
        output_dir: Path | str,
        poll_policy: PollPolicy | None = None,
        mode: str = "TEST",
        result_hosts: set[str] | frozenset[str] | None = None,
    ) -> None:
        if not _ID.fullmatch(endpoint_id):
            raise ValueError("invalid RunPod endpoint ID")
        if mode not in {"TEST", "LIVE"}:
            raise ValueError("provider mode must be TEST or LIVE")
        self.endpoint_id = endpoint_id
        self.transport = transport
        self.clock = clock
        self.state_dir = Path(state_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.poll_policy = poll_policy or PollPolicy()
        self.mode = mode
        hosts = {"video.runpod.ai"} if result_hosts is None else result_hosts
        if not hosts or any(
            not isinstance(host, str)
            or host != host.lower()
            or urlsplit(f"//{host}").hostname != host
            or urlsplit(f"//{host}").path
            for host in hosts
        ):
            raise ValueError("result hosts must be exact lowercase hostnames")
        self.result_hosts = frozenset(hosts)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _state_path(self, request_id: str) -> Path:
        if not _REQUEST_ID.fullmatch(request_id):
            raise ValueError("invalid local request ID")
        return self.state_dir / f"{request_id}.json"

    def _load(self, request_id: str) -> dict[str, Any] | None:
        path = self._state_path(request_id)
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("request_id") != request_id or value.get("endpoint_id") != self.endpoint_id:
            raise ValueError("RunPod checkpoint identity mismatch")
        return value

    def _save(self, state: dict[str, Any]) -> None:
        atomic_json(self._state_path(state["request_id"]), state)

    def _claim_submission(self, state: dict[str, Any]) -> None:
        """Atomically create the write-ahead record that owns the sole POST."""
        path = self._state_path(state["request_id"])
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError as error:
            raise RuntimeError("submission already checkpointed; recover instead") from error
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream, sort_keys=True, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _safe_final_output(path: Path) -> bool:
        try:
            info = path.lstat()
        except FileNotFoundError:
            return False
        attributes = getattr(info, "st_file_attributes", 0)
        if stat.S_ISLNK(info.st_mode) or attributes & 0x400:
            raise ValueError("unsafe final output link or reparse point")
        if not stat.S_ISREG(info.st_mode):
            raise ValueError("unsafe final output is not a regular file")
        return True

    def _validate_payload(self, job: Job) -> None:
        if job.endpoint != self.endpoint_id:
            raise ValueError("RunPod endpoint binding mismatch")
        if job.category not in {"hero", "hero_retry"}:
            raise ValueError("RunPod hero provider accepts only hero jobs")
        if set(job.payload) != {"input"} or not isinstance(job.payload["input"], dict):
            raise ValueError("RunPod serverless payload must contain exactly one input envelope")
        body = job.payload["input"]
        expected = {
            "image",
            "prompt",
            "duration",
            "resolution",
            "aspect_ratio",
            "camera_fixed",
            "generate_audio",
        }
        if set(body) != expected:
            raise ValueError("RunPod hero input contract mismatch")
        if not isinstance(body["image"], str) or not body["image"]:
            raise ValueError("hero input image required")
        if not isinstance(body["prompt"], str) or not body["prompt"].strip():
            raise ValueError("hero motion prompt required")
        if (
            type(body["duration"]) is not int
            or body["duration"] != 5
            or body["resolution"] != "720p"
            or body["aspect_ratio"] != "16:9"
            or body["camera_fixed"] is not True
            or body["generate_audio"] is not False
        ):
            raise ValueError("hero input must be 5s 720p 16:9, fixed camera, without audio")

    @staticmethod
    def _parse_status(response: dict[str, Any], expected_id: str) -> RunPodState:
        if not isinstance(response, dict) or response.get("id") != expected_id:
            raise ValueError("RunPod response job ID mismatch")
        raw = response.get("status")
        if raw == "IN_PROGRESS":
            raw = RunPodState.RUNNING.value
        try:
            return RunPodState(raw)
        except (TypeError, ValueError) as error:
            raise ValueError("unknown RunPod job status") from error

    async def submit(self, job: Job, request_id: str, checkpoint) -> ProviderResult:
        self._validate_payload(job)
        if request_id != job.request_id:
            raise ValueError("request identity mismatch")
        started_at = self.clock.time()
        state = {
            "schema_version": 1,
            "request_id": request_id,
            "endpoint_id": self.endpoint_id,
            "provider_id": "",
            "status": RunPodState.SUBMITTING.value,
            "started_at": started_at,
            "deadline_at": started_at + self.poll_policy.timeout,
            "cancel_claimed": False,
            "terminal_reason": "",
            "result_path": "",
            "result_sha256": "",
            "actual_cost": "",
        }
        self._claim_submission(state)
        try:
            response = await asyncio.wait_for(
                self.transport.submit(self.endpoint_id, job.payload),
                timeout=self.poll_policy.timeout,
            )
        except BaseException:
            state["terminal_reason"] = "SUBMISSION_OUTCOME_UNKNOWN"
            self._save(state)
            raise
        provider_id = response.get("id") if isinstance(response, dict) else None
        if not isinstance(provider_id, str) or not _ID.fullmatch(provider_id):
            state["terminal_reason"] = "SUBMISSION_RESPONSE_INVALID"
            self._save(state)
            raise ValueError("invalid RunPod job ID")
        state["provider_id"] = provider_id
        self._save(state)
        checkpoint(provider_id=provider_id)
        status = self._parse_status(response, provider_id)
        state["status"] = status.value
        self._save(state)
        return await self._drive(job, state, response)

    async def _cancel_once(self, state: dict[str, Any], reason: str) -> None:
        latest = self._load(state["request_id"])
        if latest is not None:
            state = latest
        if state.get("status") == RunPodState.CANCELLED.value:
            return
        if not state.get("cancel_claimed"):
            state["cancel_claimed"] = True
            state["terminal_reason"] = reason
            self._save(state)
        try:
            await asyncio.wait_for(
                self.transport.cancel(self.endpoint_id, state["provider_id"]),
                timeout=self.poll_policy.maximum_delay,
            )
        except BaseException:
            # A local cancellation intent is not evidence that the remote job stopped.
            # Keep the non-terminal checkpoint so a later recovery can reconcile it.
            self._save(state)
            raise
        state["status"] = RunPodState.CANCELLED.value
        self._save(state)

    async def _drive(self, job: Job, state: dict[str, Any], response: dict[str, Any]) -> ProviderResult:
        delay = self.poll_policy.initial_delay
        try:
            while True:
                latest = self._load(state["request_id"])
                if latest is not None:
                    state = latest
                status = RunPodState(state["status"])
                if status == RunPodState.COMPLETED:
                    try:
                        return await self._complete(job, state, response)
                    except ValueError:
                        state["status"] = RunPodState.FAILED.value
                        state["terminal_reason"] = "RESULT_VERIFICATION_FAILED"
                        self._save(state)
                        raise
                if status == RunPodState.FAILED:
                    raise RunPodFailed(state.get("terminal_reason") or "RunPod job failed")
                if status == RunPodState.CANCELLED:
                    reason = state.get("terminal_reason") or "RunPod job cancelled"
                    if reason == "TIMEOUT":
                        raise RunPodTimeout(reason)
                    raise RunPodCancelled(reason)
                remaining = float(state["deadline_at"]) - self.clock.time()
                if remaining <= 0:
                    await self._cancel_once(state, "TIMEOUT")
                    raise RunPodTimeout("TIMEOUT")
                await self.clock.sleep(min(delay, remaining))
                latest = self._load(state["request_id"])
                if latest is not None and latest.get("cancel_claimed"):
                    state = latest
                    continue
                if self.clock.time() >= float(state["deadline_at"]):
                    await self._cancel_once(state, "TIMEOUT")
                    raise RunPodTimeout("TIMEOUT")
                try:
                    response = await asyncio.wait_for(
                        self.transport.status(self.endpoint_id, state["provider_id"]),
                        timeout=min(remaining, self.poll_policy.maximum_delay),
                    )
                except TimeoutError:
                    await self._cancel_once(state, "TIMEOUT")
                    raise RunPodTimeout("TIMEOUT") from None
                latest = self._load(state["request_id"])
                if latest is not None and latest.get("cancel_claimed"):
                    state = latest
                    continue
                if self.clock.time() >= float(state["deadline_at"]):
                    await self._cancel_once(state, "TIMEOUT")
                    raise RunPodTimeout("TIMEOUT")
                status = self._parse_status(response, state["provider_id"])
                state["status"] = status.value
                if status in {RunPodState.FAILED, RunPodState.CANCELLED}:
                    state["terminal_reason"] = str(response.get("error") or status.value)
                self._save(state)
                delay = min(delay * self.poll_policy.multiplier, self.poll_policy.maximum_delay)
        except asyncio.CancelledError:
            await asyncio.shield(self._cancel_once(state, "CALLER_CANCELLED"))
            raise

    async def _complete(self, job: Job, state: dict[str, Any], response: dict[str, Any]) -> ProviderResult:
        output = response.get("output")
        if not isinstance(output, dict) or set(output) != {"url", "sha256"}:
            raise ValueError("completed RunPod response must contain URL and SHA-256")
        url, expected = output["url"], output["sha256"]
        parsed = urlsplit(url) if isinstance(url, str) else None
        if (
            parsed is None
            or url != url.strip()
            or "\\" in url
            or parsed.scheme != "https"
            or parsed.hostname not in self.result_hosts
            or parsed.username
            or parsed.password
            or parsed.fragment
            or parsed.port not in (None, 443)
            or len(parsed.path) > 2048
            or len(parsed.query) > 4096
            or not isinstance(expected, str)
            or not _SHA256.fullmatch(expected)
        ):
            raise ValueError("unsafe or unverifiable RunPod output")
        target = self.output_dir / f"{state['request_id']}.mp4"
        if self._safe_final_output(target):
            if sha256(target) != expected:
                raise ValueError("existing hero output hash mismatch")
        else:
            content = await asyncio.wait_for(
                self.transport.download(url), timeout=self.poll_policy.timeout
            )
            if not isinstance(content, bytes) or hashlib.sha256(content).hexdigest() != expected:
                raise ValueError("downloaded hero output hash mismatch")
            fd, temporary = tempfile.mkstemp(dir=self.output_dir, suffix=".download")
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(temporary, target)
                except FileExistsError:
                    if not self._safe_final_output(target) or sha256(target) != expected:
                        raise ValueError("final hero output already exists with different bytes") from None
            finally:
                Path(temporary).unlink(missing_ok=True)
        actual_cost = money(response.get("actual_cost", job.cost))
        state.update(
            status=RunPodState.COMPLETED.value,
            result_path=str(target),
            result_sha256=expected,
            actual_cost=str(actual_cost),
        )
        self._save(state)
        return ProviderResult(target, actual_cost)

    async def cancel(self, request_id: str) -> None:
        """Persist an explicit cancellation and send its control request once."""
        state = self._load(request_id)
        if state is None:
            raise ValueError("cannot cancel an uncheckpointed RunPod job")
        if not state.get("provider_id"):
            raise ValueError("cannot cancel a submission without a durable RunPod job ID")
        status = RunPodState(state["status"])
        if status == RunPodState.CANCELLED:
            return
        if status in {RunPodState.COMPLETED, RunPodState.FAILED}:
            raise ValueError("cannot cancel a terminal RunPod job")
        await self._cancel_once(state, "CALLER_CANCELLED")

    def _persisted_result(self, state: dict[str, Any]) -> ProviderResult:
        path_value = state.get("result_path")
        expected = state.get("result_sha256")
        if not path_value or not isinstance(expected, str) or not _SHA256.fullmatch(expected):
            raise ValueError("completed RunPod checkpoint is missing verified output")
        path = Path(path_value).resolve()
        if path.parent != self.output_dir or not path.is_file() or sha256(path) != expected:
            raise ValueError("completed RunPod output no longer verifies")
        return ProviderResult(path, money(state["actual_cost"]))

    async def recover_local(self, job: Job, request_id: str, checkpoint) -> ProviderResult:
        """Reconcile a local ID saved before the executor checkpoint completed."""
        state = self._load(request_id)
        provider_id = state.get("provider_id") if state is not None else None
        if not isinstance(provider_id, str) or not _ID.fullmatch(provider_id):
            raise ValueError("no durable local RunPod job ID to reconcile")
        checkpoint(provider_id=provider_id)
        return await self.recover(job, request_id, provider_id, "", checkpoint)

    async def recover(self, job: Job, request_id: str, provider_id: str, partial: str, checkpoint) -> ProviderResult:
        """Resume a checkpointed job without issuing another submission."""
        self._validate_payload(job)
        if request_id != job.request_id or not _ID.fullmatch(provider_id):
            raise ValueError("recovery identity mismatch")
        if partial:
            raise ValueError("RunPod recovery does not accept unverified partial paths")
        state = self._load(request_id)
        if state is None or state.get("provider_id") != provider_id:
            raise ValueError("RunPod recovery checkpoint mismatch")
        status = RunPodState(state["status"])
        if state.get("cancel_claimed") and status != RunPodState.CANCELLED:
            await self._cancel_once(state, state.get("terminal_reason") or "CALLER_CANCELLED")
            state = self._load(request_id) or state
            status = RunPodState(state["status"])
        if status == RunPodState.COMPLETED and state.get("result_path"):
            return self._persisted_result(state)
        if status == RunPodState.FAILED:
            raise RunPodFailed(state.get("terminal_reason") or "RunPod job failed")
        if status == RunPodState.CANCELLED:
            reason = state.get("terminal_reason") or "RunPod job cancelled"
            if reason == "TIMEOUT":
                raise RunPodTimeout(reason)
            raise RunPodCancelled(reason)
        known_complete = status == RunPodState.COMPLETED
        if not known_complete and self.clock.time() >= float(state["deadline_at"]):
            await self._cancel_once(state, "TIMEOUT")
            raise RunPodTimeout("TIMEOUT")
        remaining = float(state["deadline_at"]) - self.clock.time()
        try:
            response = await asyncio.wait_for(
                self.transport.status(self.endpoint_id, provider_id),
                timeout=(
                    self.poll_policy.timeout
                    if known_complete
                    else min(remaining, self.poll_policy.maximum_delay)
                ),
            )
        except TimeoutError:
            if not known_complete:
                await self._cancel_once(state, "TIMEOUT")
                raise RunPodTimeout("TIMEOUT") from None
            raise
        latest = self._load(request_id)
        if latest is not None and latest.get("cancel_claimed"):
            state = latest
            reason = state.get("terminal_reason") or "RunPod job cancelled"
            if reason == "TIMEOUT":
                raise RunPodTimeout(reason)
            raise RunPodCancelled(reason)
        if not known_complete and self.clock.time() >= float(state["deadline_at"]):
            await self._cancel_once(state, "TIMEOUT")
            raise RunPodTimeout("TIMEOUT")
        status = self._parse_status(response, provider_id)
        state["status"] = status.value
        if status in {RunPodState.FAILED, RunPodState.CANCELLED}:
            state["terminal_reason"] = str(response.get("error") or status.value)
        self._save(state)
        return await self._drive(job, state, response)
