"""LIVE provider adapters used exclusively through :class:`Executor`.

They keep provider credentials and signed URLs process-local.  A submitted remote
ID is checkpointed before any polling/download. Recovery calls only provider GET
endpoints and reuses the same quarantine location; it can never submit again.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import urllib.request
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from PIL import Image

from src.hybrid.execution import Job, ProviderResult


def _download(url: str, destination: Path) -> None:
    """Download ephemeral provider output without recording its signed URL."""
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError("provider result must be an HTTPS URL")
    with urllib.request.urlopen(url, timeout=180) as response:
        with destination.open("wb") as stream:
            shutil.copyfileobj(response, stream)


def _one_url(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("url", "video", "image"):
            if isinstance(value.get(key), str):
                return value[key]
        for key in ("images", "videos", "output"):
            if key in value:
                return _one_url(value[key])
    if isinstance(value, list) and len(value) == 1:
        return _one_url(value[0])
    raise ValueError("provider response must contain exactly one output URL")


class _QuarantineProvider:
    mode = "LIVE"
    suffix = ""

    def __init__(self, quarantine: Path, *, downloader: Callable | None = None):
        self.quarantine = Path(quarantine).resolve()
        self.downloader = downloader or _download

    def _destination(self, request_id: str) -> Path:
        if not request_id or Path(request_id).name != request_id:
            raise ValueError("safe local request ID required")
        return self.quarantine / request_id / f"result{self.suffix}"

    def _quarantine(self, request_id: str, url: str) -> Path:
        destination = self._destination(request_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return destination
        with tempfile.NamedTemporaryFile(
            prefix="download-", suffix=self.suffix, dir=destination.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
        try:
            self.downloader(url, temporary)
            self._validate(temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return destination

    def _validate(self, path: Path) -> None:
        raise NotImplementedError


class _FalQueueSubmitter:
    """One raw queue POST; urllib performs no automatic POST retry."""

    def submit_once(self, endpoint: str, arguments: dict):
        key = os.environ.get("FAL_KEY", "")
        if not key:
            raise RuntimeError("FAL_KEY is NOT CONFIGURED")
        if not endpoint.startswith("fal-ai/") or any(piece in endpoint for piece in ("?", "#", "..")):
            raise ValueError("safe FAL application endpoint required")
        request = urllib.request.Request(
            f"https://queue.fal.run/{endpoint}",
            data=__import__("json").dumps(arguments, separators=(",", ":")).encode(),
            method="POST",
            headers={"Authorization": f"Key {key}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=180) as response:
                payload = __import__("json").loads(response.read().decode())
        except Exception:
            # The Executor keeps INTENT without a remote ID: human reconciliation only.
            raise
        request_id = payload.get("request_id") if isinstance(payload, dict) else ""
        if not isinstance(request_id, str) or not request_id:
            raise RuntimeError("FAL queue response did not contain request_id")
        return type("FalSubmission", (), {"request_id": request_id})()


class FalFluxProvider(_QuarantineProvider):
    """FAL FLUX image-edit adapter with exact-reference staging and GET recovery."""

    suffix = ".png"

    def __init__(
        self, quarantine: Path, *, client=None, submitter=None, downloader: Callable | None = None
    ):
        super().__init__(quarantine, downloader=downloader)
        injected = client is not None
        if client is None:
            import fal_client

            client = fal_client
        self.client = client
        self.submitter = submitter or (client if injected else _FalQueueSubmitter())

    def _submit_once(self, endpoint: str, arguments: dict):
        """Use the SDK only when injected in tests; production POST never retries."""
        if hasattr(self.submitter, "submit_once"):
            return self.submitter.submit_once(endpoint, arguments)
        return self.submitter.submit(endpoint, arguments)

    @staticmethod
    def _expected_geometry(job: Job) -> tuple[int, int]:
        geometry = job.payload.get("image_size", {})
        width, height = geometry.get("width"), geometry.get("height")
        if type(width) is not int or type(height) is not int or width < 2 or height < 2:
            raise ValueError("explicit even image geometry required")
        return width, height

    def _validate(self, path: Path) -> None:
        try:
            with Image.open(path) as image:
                image.load()
                if image.format != "PNG" or image.mode != "RGB":
                    raise ValueError("image contract requires RGB PNG")
        except OSError as error:
            raise ValueError("image validation failed") from error

    def _validate_for_job(self, path: Path, job: Job) -> None:
        self._validate(path)
        with Image.open(path) as image:
            if image.size != self._expected_geometry(job):
                raise ValueError("image geometry contract mismatch")

    def _stage(self, job: Job) -> dict:
        payload = dict(job.payload)
        requested = payload.get("image_urls")
        exact_assets = [str(asset.path) for asset in job.manifest.assets]
        if requested != exact_assets:
            raise ValueError("payload references must exactly match frozen manifest")
        payload["image_urls"] = [self.client.upload_file(asset.path) for asset in job.manifest.assets]
        return payload

    def _resolve(self, job: Job, request_id: str, provider_id: str) -> ProviderResult:
        # ``fal_client`` exposes result(application, request_id), not ``get``.
        response = self.client.result(job.endpoint, provider_id) if hasattr(self.client, "result") else self.client.get(provider_id)
        output = self._quarantine(request_id, _one_url(response))
        self._validate_for_job(output, job)
        return ProviderResult(output, job.cost)

    async def submit(self, job: Job, request_id: str, checkpoint: Callable) -> ProviderResult:
        arguments = await asyncio.to_thread(self._stage, job)
        handle = await asyncio.to_thread(self._submit_once, job.endpoint, arguments)
        provider_id = getattr(handle, "request_id", "")
        if not isinstance(provider_id, str) or not provider_id:
            raise RuntimeError("FAL submit did not return a durable request ID")
        checkpoint(provider_id=provider_id)
        return await asyncio.to_thread(self._resolve, job, request_id, provider_id)

    async def recover(
        self, job: Job, request_id: str, provider_id: str, partial: str, checkpoint: Callable
    ) -> ProviderResult:
        if not provider_id:
            raise ValueError("FAL recovery requires durable provider request ID")
        return await asyncio.to_thread(self._resolve, job, request_id, provider_id)


class _RunPodHttpTransport:
    """Small REST transport; only this deployment boundary reads RUNPOD_API_KEY."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("RUNPOD_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("RUNPOD_API_KEY is NOT CONFIGURED")

    def _request(self, method: str, endpoint: str, suffix: str, payload=None):
        if not endpoint or "/" in endpoint:
            raise ValueError("RunPod endpoint ID required")
        data = None if payload is None else __import__("json").dumps(payload).encode()
        request = urllib.request.Request(
            f"https://api.runpod.ai/v2/{endpoint}/{suffix}",
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            return __import__("json").loads(response.read().decode())

    def post(self, endpoint: str, payload: dict) -> dict:
        return self._request("POST", endpoint, "run", payload)

    def get(self, endpoint: str, remote_id: str) -> dict:
        return self._request("GET", endpoint, f"status/{remote_id}")


class RunPodSeedanceProvider(_QuarantineProvider):
    """RunPod Seedance hero adapter with POST once and status GET-only recovery."""

    suffix = ".mp4"

    def __init__(self, quarantine: Path, *, transport=None, downloader: Callable | None = None):
        super().__init__(quarantine, downloader=downloader)
        self.transport = transport or _RunPodHttpTransport()

    def _validate(self, path: Path) -> None:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        if result.returncode:
            raise ValueError("video validation failed")
        try:
            streams = __import__("json").loads(result.stdout)["streams"]
        except (KeyError, ValueError) as error:
            raise ValueError("video validation failed") from error
        if len([s for s in streams if s.get("codec_type") == "video"]) != 1:
            raise ValueError("video validation failed")

    def _resolve(self, job: Job, request_id: str, provider_id: str) -> ProviderResult:
        response = self.transport.get(job.endpoint, provider_id)
        if response.get("status") != "COMPLETED":
            raise RuntimeError(f"RunPod remote job is not complete: {response.get('status', 'UNKNOWN')}")
        output = self._quarantine(request_id, _one_url(response.get("output")))
        return ProviderResult(output, job.cost)

    async def submit(self, job: Job, request_id: str, checkpoint: Callable) -> ProviderResult:
        response = await asyncio.to_thread(self.transport.post, job.endpoint, job.payload)
        provider_id = response.get("id") if isinstance(response, dict) else ""
        if not isinstance(provider_id, str) or not provider_id:
            raise RuntimeError("RunPod submit did not return a durable request ID")
        checkpoint(provider_id=provider_id)
        return await asyncio.to_thread(self._resolve, job, request_id, provider_id)

    async def recover(
        self, job: Job, request_id: str, provider_id: str, partial: str, checkpoint: Callable
    ) -> ProviderResult:
        if not provider_id:
            raise ValueError("RunPod recovery requires durable provider request ID")
        return await asyncio.to_thread(self._resolve, job, request_id, provider_id)
