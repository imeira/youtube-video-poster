"""Durable TEST-only Azure Speech admission consumer.

This composes the existing authenticated budget bridge and durable Executor; it
never creates a ledger, reads Azure credentials, or constructs the Azure SDK.
LIVE stays unavailable because the installed executor/ledger admission contract
is TEST-only and Azure has no authenticated provider billing/readback adapter.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from src.hybrid.budget_authorization import AuthenticatedBudgetBridge
from src.hybrid.execution import Executor, Job, ProviderResult
from src.hybrid.locks import file_lock, reject_reparse_path
from src.providers.tts.azure_speech_stock_tts import VOICE, OfflineAzureSpeechFixture

AZURE_SPEECH_ENDPOINT = "azure-speech-stock-tts"


class SubmissionUnknown(RuntimeError):
    """A paid-equivalent fixture operation is durably ambiguous and cannot replay."""


class AzureSpeechTransport(Protocol):
    def synthesize(self, *, text: str, voice: str, output_path: Path) -> None: ...


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _scope(operation_id: str, job: Job, *, output_root: Path) -> dict[str, str]:
    if not isinstance(operation_id, str) or not operation_id or len(operation_id) > 128:
        raise ValueError("stable Azure operation id is required")
    payload = job.payload
    if job.mode != "TEST" or job.endpoint != AZURE_SPEECH_ENDPOINT or not isinstance(payload, dict) or set(payload) != {"text", "voice", "output_path"}:
        raise ValueError("Azure durable consumer requires the closed TEST TTS job contract")
    text, voice, output_path = payload["text"], payload["voice"], payload["output_path"]
    if not isinstance(text, str) or not text.strip() or not isinstance(voice, str) or not voice or not isinstance(output_path, str) or not output_path:
        raise ValueError("Azure durable consumer requires exact script, voice, and output path")
    if not isinstance(job.cost, Decimal) or not job.cost.is_finite() or job.cost <= 0:
        raise ValueError("Azure durable consumer requires a positive exact quoted maximum")
    output = Path(output_path)
    if not output.is_absolute():
        raise ValueError("Azure output path must be absolute")
    output = reject_reparse_path(output)
    try:
        output.relative_to(output_root)
    except ValueError:
        raise ValueError("Azure output path escapes configured output root") from None
    return {
        "schema": "1", "operation_id": operation_id, "request_id": job.request_id,
        "endpoint": job.endpoint, "voice": voice,
        "script_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "price_usd": str(job.cost), "output_path": str(output),
    }


class AzureSpeechFixtureTransport:
    """Explicit local adapter-SDK boundary fixture; it cannot be LIVE authority."""
    def __init__(self, fixture: OfflineAzureSpeechFixture, *, timeout: bool = False) -> None:
        self.fixture, self.timeout, self.calls = fixture, timeout, 0

    def synthesize(self, *, text: str, voice: str, output_path: Path) -> None:
        self.calls += 1
        if self.timeout:
            raise TimeoutError("fixture timeout after submit boundary")
        self.fixture.synthesize(text, voice, output_path)


class _AzureSpeechExecutorProvider:
    mode = "TEST"

    def __init__(self, transport: AzureSpeechTransport) -> None:
        self.transport = transport

    async def submit(self, job: Job, request_id: str, checkpoint: Callable[..., None]) -> ProviderResult:
        payload = job.payload
        output = reject_reparse_path(Path(payload["output_path"]))
        output.parent.mkdir(parents=True, exist_ok=True)
        output = reject_reparse_path(output)
        # The local request id is the only available TEST fixture identity.  It
        # is checkpointed before the output is accepted, exactly once.
        checkpoint(provider_id=f"test-azure-{request_id}")
        await asyncio.to_thread(self.transport.synthesize, text=payload["text"], voice=payload["voice"], output_path=output)
        if not output.is_file() or not output.stat().st_size:
            raise ValueError("Azure fixture did not produce audio")
        return ProviderResult(output, job.cost)

    async def recover(self, *_args: Any) -> ProviderResult:
        raise SubmissionUnknown("Azure submission recovery requires authenticated provider readback; no resubmit")


class AzureSpeechDurableConsumer:
    """TEST consumer that joins authenticated budget admission to durable intent.

    The immutable operation scope is independently written before fixture SDK
    entry. A timeout turns the bridge claim UNKNOWN; any fresh process sees it
    and stops before the fixture transport can receive a second request.
    """
    def __init__(self, *, root: Path, output_root: Path, bridge: AuthenticatedBudgetBridge, executor: Executor,
                 transport_factory: Callable[[], AzureSpeechTransport]) -> None:
        if not isinstance(bridge, AuthenticatedBudgetBridge) or not isinstance(executor, Executor):
            raise TypeError("authenticated budget bridge and durable executor are required")
        if not Path(root).is_absolute() or not Path(output_root).is_absolute():
            raise ValueError("Azure state and output roots must be absolute")
        self.root = reject_reparse_path(Path(root))
        self.output_root = reject_reparse_path(Path(output_root))
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = reject_reparse_path(self.root)
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.output_root = reject_reparse_path(self.output_root)
        self.bridge, self.executor, self.transport_factory = bridge, executor, transport_factory
        self.intent_root = self.root / "azure-operations"
        self.lock_root = self.root / "locks"

    def _intent_path(self, operation_id: str) -> Path:
        if any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for char in operation_id):
            raise ValueError("stable Azure operation id is invalid")
        return self.intent_root / f"{operation_id}.json"

    def _write_or_verify_scope(self, scope: dict[str, str]) -> None:
        path = self._intent_path(scope["operation_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        body = _canonical(scope)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            existing = path.read_bytes()
            if existing != body:
                raise ValueError("immutable Azure operation scope differs from existing intent")
            return
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != body:
            raise ValueError("Azure operation intent readback mismatch")

    async def synthesize(self, *, operation_id: str, job: Job) -> dict[str, object]:
        scope = _scope(operation_id, job, output_root=self.output_root)
        if operation_id != job.request_id:
            raise ValueError("Azure operation id must equal the durable executor request id")
        lock = self.lock_root / f"{operation_id}.lock"
        with file_lock(lock):
            self._write_or_verify_scope(scope)
            if scope["voice"] != VOICE:
                raise ValueError("Azure durable consumer only permits the approved stock voice")
            # The bridge prepares the Executor's durable intent and binds the
            # same canonical request ID into its authenticated ledger claim.
            claim = self.bridge.admit_executor_intent(executor=self.executor, job=job)
            if claim["state"] == "UNKNOWN":
                raise SubmissionUnknown("Azure submission is UNKNOWN; authenticated readback required before any successor")
            if claim["state"] != "RESERVED":
                raise SubmissionUnknown("Azure durable claim is not runnable")
            existing = self.executor.inspect(job.request_id)
            if existing is not None and existing.get("status") == "COMPLETE":
                receipt = existing
            else:
                try:
                    receipt = await self.executor.run(job, _AzureSpeechExecutorProvider(self.transport_factory()), submit_prepared=True)
                except BaseException as error:
                    # SUBMITTING is a durable post-dispatch boundary; cancellation is
                    # ambiguous too and must preserve the existing exposure.
                    row = self.executor.inspect(job.request_id)
                    if row is not None and row.get("status") in {"SUBMITTING", "SUBMITTED"}:
                        self.bridge.mark_submission_unknown(request_id=job.request_id)
                    if isinstance(error, asyncio.CancelledError):
                        raise
                    raise SubmissionUnknown("Azure submission outcome UNKNOWN; automatic resubmit is forbidden") from error
        return {"status": receipt["status"], "request_id": job.request_id,
                "audio_path": receipt["result"], "operation_id": operation_id}
