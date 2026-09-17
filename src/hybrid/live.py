"""LIVE provider adapters used exclusively through :class:`Executor`.

They keep provider credentials and signed URLs process-local.  A submitted remote
ID is checkpointed before any polling/download. Recovery calls only provider GET
endpoints and reuses the same quarantine location; it can never submit again.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import urllib.request
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlsplit

from PIL import Image

from src.hybrid.execution import Job, ProviderResult

MAX_API_BYTES = 1 << 20
MAX_MEDIA_BYTES = 256 << 20


def _remote_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", value):
        raise ValueError("safe durable request ID required")
    return value


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        raise ValueError("provider redirects are forbidden")


def _url(url: str, *, hosts: set[str]) -> str:
    if not isinstance(url, str) or not url or url != url.strip() or "\\" in url:
        raise ValueError("strict HTTPS provider URL required")
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in hosts
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.port not in (None, 443)
        or len(parsed.path) > 2048
        or len(parsed.query) > 4096
    ):
        raise ValueError("provider URL violates allowlist")
    return url


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _RejectRedirect())


def _download(url: str, destination: Path, *, hosts: set[str]) -> None:
    """Bounded no-redirect/proxy download; URLs remain process-local."""
    _url(url, hosts=hosts)
    request = urllib.request.Request(url, method="GET")
    with _opener().open(request, timeout=180) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > MAX_MEDIA_BYTES:
            raise ValueError("provider media exceeds byte limit")
        total = 0
        while chunk := response.read(min(1 << 20, MAX_MEDIA_BYTES + 1 - total)):
            total += len(chunk)
            if total > MAX_MEDIA_BYTES:
                raise ValueError("provider media exceeds byte limit")
            destination.write(chunk)
        if total == 0:
            raise ValueError("provider media is empty")


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

    allowed_result_hosts: ClassVar[set[str]] = set()

    def __init__(self, quarantine: Path, *, downloader: Callable | None = None):
        self.quarantine = Path(quarantine).resolve()
        self.downloader = downloader

    def _destination(self, request_id: str) -> Path:
        if not request_id or Path(request_id).name != request_id:
            raise ValueError("safe local request ID required")
        return self.quarantine / request_id / f"result{self.suffix}"

    def _quarantine(self, request_id: str, url: str, checkpoint: Callable) -> Path:
        destination = self._destination(request_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self._validate(destination)
            return destination
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        flags |= getattr(os, "O_BINARY", 0)
        descriptor = os.open(destination, flags, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            if self.downloader:
                self.downloader(url, stream)
            else:
                _download(url, stream, hosts=self.allowed_result_hosts)
            stream.flush()
            os.fsync(stream.fileno())
        checkpoint(partial=str(destination))
        self._validate(destination)
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
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=180) as response:
            payload = __import__("json").loads(response.read().decode())
        request_id = payload.get("request_id") if isinstance(payload, dict) else ""
        if not isinstance(request_id, str) or not request_id:
            raise RuntimeError("FAL queue response did not contain request_id")
        return type("FalSubmission", (), {"request_id": request_id})()


class FalFluxProvider(_QuarantineProvider):
    """FAL FLUX image-edit adapter with exact-reference staging and GET recovery."""

    suffix = ".png"
    # FAL documents both legacy ``v3`` and current ``v3b`` CDN result URLs.
    allowed_result_hosts: ClassVar[set[str]] = {"v3.fal.media", "v3b.fal.media"}

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

    def _resolve(self, job: Job, request_id: str, provider_id: str, checkpoint: Callable) -> ProviderResult:
        # ``fal_client`` exposes result(application, request_id), not ``get``.
        response = self.client.result(job.endpoint, provider_id) if hasattr(self.client, "result") else self.client.get(provider_id)
        output = self._quarantine(request_id, _one_url(response), checkpoint)
        self._validate_for_job(output, job)
        return ProviderResult(output, job.cost)

    async def submit(self, job: Job, request_id: str, checkpoint: Callable) -> ProviderResult:
        arguments = await asyncio.to_thread(self._stage, job)
        handle = await asyncio.to_thread(self._submit_once, job.endpoint, arguments)
        provider_id = getattr(handle, "request_id", "")
        if not isinstance(provider_id, str) or not provider_id:
            raise RuntimeError("FAL submit did not return a durable request ID")
        checkpoint(provider_id=provider_id)
        return await asyncio.to_thread(self._resolve, job, request_id, provider_id, checkpoint)

    async def recover(
        self, job: Job, request_id: str, provider_id: str, partial: str, checkpoint: Callable
    ) -> ProviderResult:
        if not provider_id:
            raise ValueError("FAL recovery requires durable provider request ID")
        return await asyncio.to_thread(self._resolve, job, request_id, provider_id, checkpoint)


class _RunPodHttpTransport:
    """Small REST transport; only this deployment boundary reads RUNPOD_API_KEY."""

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.environ.get("RUNPOD_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("RUNPOD_API_KEY is NOT CONFIGURED")

    def _request(self, method: str, endpoint: str, suffix: str, payload=None):
        if endpoint != "seedance-v1-5-pro-i2v" or not suffix or "?" in suffix or "#" in suffix:
            raise ValueError("approved RunPod Seedance endpoint required")
        data = None if payload is None else __import__("json").dumps(payload, separators=(",", ":")).encode()
        request = urllib.request.Request(
            f"https://api.runpod.ai/v2/{endpoint}/{suffix}",
            data=data,
            method=method,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        with _opener().open(request, timeout=180) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > MAX_API_BYTES:
                raise ValueError("provider API response exceeds byte limit")
            body = response.read(MAX_API_BYTES + 1)
            if len(body) > MAX_API_BYTES:
                raise ValueError("provider API response exceeds byte limit")
            return __import__("json").loads(body.decode())

    def post(self, endpoint: str, payload: dict) -> dict:
        return self._request("POST", endpoint, "runsync", payload)

    def get(self, endpoint: str, remote_id: str) -> dict:
        return self._request("GET", endpoint, f"status/{remote_id}")


class RunPodSeedanceProvider(_QuarantineProvider):
    """RunPod Seedance hero adapter with POST once and status GET-only recovery."""

    suffix = ".mp4"
    allowed_result_hosts: ClassVar[set[str]] = {"video.runpod.ai"}

    def __init__(
        self,
        quarantine: Path,
        *,
        transport=None,
        image_stager: Callable | None = None,
        downloader: Callable | None = None,
    ):
        super().__init__(quarantine, downloader=downloader)
        self.transport = transport or _RunPodHttpTransport()
        self.image_stager = image_stager or self._stage_with_fal

    @staticmethod
    def _stage_with_fal(path: Path) -> str:
        import fal_client

        return fal_client.upload_file(path)

    @staticmethod
    def _input(job: Job) -> dict:
        source = job.payload.get("input")
        required = {
            "image",
            "prompt",
            "duration",
            "resolution",
            "aspect_ratio",
            "camera_fixed",
            "generate_audio",
        }
        if not isinstance(source, dict) or set(source) != required:
            raise ValueError("exact Seedance public input schema required")
        if (
            not isinstance(source["prompt"], str)
            or not source["prompt"].strip()
            or source["duration"] != 5
            or source["resolution"] != "720p"
            or source["aspect_ratio"] != "16:9"
            or type(source["camera_fixed"]) is not bool
            or source["generate_audio"] is not False
        ):
            raise ValueError("Seedance hero contract mismatch")
        return dict(source)

    def _staged_payload(self, job: Job) -> dict:
        job.manifest.verify(job.mode)
        source = self._input(job)
        exact = [asset.path.resolve() for asset in job.manifest.assets]
        local = Path(source["image"]).resolve()
        if len(exact) != 1 or local != exact[0]:
            raise ValueError("Seedance image must be the exact frozen source")
        source["image"] = self.image_stager(local)
        if not isinstance(source["image"], str) or not source["image"].startswith("https://"):
            raise ValueError("Seedance staging must return an HTTPS image URL")
        return {"input": source}

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
        videos = [s for s in streams if s.get("codec_type") == "video"]
        if len(videos) != 1 or (videos[0].get("width"), videos[0].get("height")) != (1280, 720):
            raise ValueError("video validation failed")
        if any(s.get("codec_type") == "audio" for s in streams):
            raise ValueError("video validation failed")

    def _resolve_response(self, job: Job, request_id: str, response: dict, checkpoint: Callable) -> ProviderResult:
        if response.get("status") != "COMPLETED":
            raise RuntimeError(f"RunPod remote job is not complete: {response.get('status', 'UNKNOWN')}")
        output_data = response.get("output")
        if not isinstance(output_data, dict) or type(output_data.get("cost")) not in (int, float):
            raise ValueError("Seedance completed response schema mismatch")
        url = output_data.get("video_url", output_data.get("result"))
        if not isinstance(url, str):
            raise TypeError("Seedance completed response has no video URL")
        output = self._quarantine(request_id, url, checkpoint)
        return ProviderResult(output, Decimal(str(output_data["cost"])))

    def _resolve(self, job: Job, request_id: str, provider_id: str, checkpoint: Callable) -> ProviderResult:
        return self._resolve_response(job, request_id, self.transport.get(job.endpoint, provider_id), checkpoint)

    async def submit(self, job: Job, request_id: str, checkpoint: Callable) -> ProviderResult:
        payload = await asyncio.to_thread(self._staged_payload, job)
        response = await asyncio.to_thread(self.transport.post, job.endpoint, payload)
        provider_id = _remote_id(response.get("id") if isinstance(response, dict) else None)
        checkpoint(provider_id=provider_id)
        if response.get("status") == "COMPLETED":
            return await asyncio.to_thread(self._resolve_response, job, request_id, response, checkpoint)
        return await asyncio.to_thread(self._resolve, job, request_id, provider_id, checkpoint)

    async def recover(
        self, job: Job, request_id: str, provider_id: str, partial: str, checkpoint: Callable
    ) -> ProviderResult:
        provider_id = _remote_id(provider_id)
        return await asyncio.to_thread(self._resolve, job, request_id, provider_id, checkpoint)
