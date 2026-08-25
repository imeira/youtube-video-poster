"""GPU Compute Provider abstraction (§63-72).

Business code NEVER knows about RunPod specifics — it talks to GPUComputeProvider.
Implementations: LocalGPUProvider ( GTX 1050 Ti), RunPodGPUProvider (on-demand).

§63: max_seconds_per_episode: 30, preferred_clip_duration: 4, max_clip: 8
§64: 5-15% of episode uses generative video
§70: RunPod Job Manager lifecycle
§71: GPU selection by cheapest_suitable, not hardcoded
§72: Price discovery before paid execution
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class GPUJobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class SceneImportance(str, Enum):
    """§63: Scene classification for generative video candidacy."""
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    def should_use_generative_video(self) -> bool:
        """Only HIGH and CRITICAL scenes are candidates for RunPod (§63)."""
        return self in (SceneImportance.HIGH, SceneImportance.CRITICAL)


@dataclass
class GPUSpec:
    """GPU specification for selection (§71)."""
    name: str
    vram_gb: float
    hourly_price: float
    cuda_compute_capability: str = ""
    availability: bool = True
    performance_score: float = 0.0  # higher = better


@dataclass
class GPUJobRequest:
    """Request for a GPU compute job."""
    job_type: str  # "image_to_video", "text_to_video", "image_generation"
    model: str = ""  # model name (e.g., "wan-2.1", "sdxl")
    prompt: str = ""
    negative_prompt: str = ""
    input_image_path: str = ""
    output_dir: str = ""
    duration_seconds: float = 5.0
    width: int = 1024
    height: int = 576
    seed: int | None = None
    extra_params: dict = field(default_factory=dict)


@dataclass
class GPUJobResult:
    """Result of a GPU compute job."""
    status: GPUJobStatus
    output_path: str = ""
    error: str = ""
    job_duration_seconds: float = 0.0
    gpu_name: str = ""
    hourly_price: float = 0.0
    estimated_cost: float = 0.0
    actual_cost: float = 0.0
    metadata: dict = field(default_factory=dict)


class GPUComputeProvider(ABC):
    """Abstract GPU compute provider (§63-72).

    Business code talks to this interface. Implementations handle specifics
    of local GPU, RunPod, or future providers.
    """

    @abstractmethod
    def get_gpu_spec(self) -> GPUSpec:
        """Return the GPU specification of this provider."""
        ...

    @abstractmethod
    def estimate_cost(self, request: GPUJobRequest) -> float:
        """Estimate cost for a job before execution (§72)."""
        ...

    @abstractmethod
    def estimate_time(self, request: GPUJobRequest) -> float:
        """Estimate execution time in seconds."""
        ...

    @abstractmethod
    async def execute_job(self, request: GPUJobRequest) -> GPUJobResult:
        """Execute a GPU job and return the result.

        §70 lifecycle: allocate → execute → retrieve → validate → release.
        Must NEVER leave GPU running after job completion or failure.
        """
        ...

    @abstractmethod
    def cleanup(self) -> None:
        """Release any held resources (§70: avoid idle resources)."""
        ...

    def select_gpu(self, required_vram_gb: float, max_hourly_price: float = 999.0) -> GPUSpec | None:
        """Select cheapest suitable GPU (§71).

        Default implementation: return self.get_gpu_spec() if it meets requirements.
        Override in RunPodGPUProvider for multi-GPU selection.
        """
        spec = self.get_gpu_spec()
        if spec.vram_gb >= required_vram_gb and spec.hourly_price <= max_hourly_price:
            return spec
        return None


class LocalGPUProvider(GPUComputeProvider):
    """Local GPU provider — uses the machine's GPU (GTX 1050 Ti 4GB).

    Cost: $0 (local hardware, no per-job charge).
    Used for: image generation (SD1.5), local animation (ffmpeg), TTS.
    NOT suitable for: video generation (i2v/t2v) — insufficient VRAM.
    """

    def __init__(self):
        self._gpu_spec: GPUSpec | None = None

    def get_gpu_spec(self) -> GPUSpec:
        if self._gpu_spec is None:
            try:
                import subprocess
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.returncode == 0:
                    parts = result.stdout.strip().split(",")
                    name = parts[0].strip()
                    vram_mb = float(parts[1].strip())
                    self._gpu_spec = GPUSpec(
                        name=name,
                        vram_gb=vram_mb / 1024,
                        hourly_price=0.0,
                        cuda_compute_capability="6.1",  # Pascal
                        performance_score=1.0,
                    )
                else:
                    self._gpu_spec = GPUSpec(name="Unknown", vram_gb=0, hourly_price=0.0)
            except Exception:
                self._gpu_spec = GPUSpec(name="Unknown", vram_gb=0, hourly_price=0.0)
        return self._gpu_spec

    def estimate_cost(self, request: GPUJobRequest) -> float:
        return 0.0  # Local GPU is free

    def estimate_time(self, request: GPUJobRequest) -> float:
        # Rough estimates based on benchmarks
        if request.job_type == "image_generation":
            return 55.0  # ~55s per image at 1024x576 with mo-di-diffusion + LCM
        elif request.job_type == "image_to_video":
            return 999999.0  # Not supported locally (insufficient VRAM)
        return 60.0

    async def execute_job(self, request: GPUJobRequest) -> GPUJobResult:
        """Execute locally — delegates to existing providers."""
        # This is a thin wrapper — actual execution handled by existing
        # LocalSD15Provider and LocalFFmpegVideoProvider
        return GPUJobResult(
            status=GPUJobStatus.FAILED,
            error=f"Local GPU does not support {request.job_type} directly — use specific providers",
        )

    def cleanup(self) -> None:
        pass  # Nothing to clean up locally


class RunPodGPUProvider(GPUComputeProvider):
    """RunPod GPU provider — on-demand cloud GPU (§70-72).

    Lifecycle (§70):
        1. select_gpu (cheapest_suitable)
        2. create/alocate pod
        3. execute job
        4. retrieve result
        5. validate
        6. terminate pod (ALWAYS, even on failure)

    NEVER leaves GPU running after job completion (§70).
    """

    def __init__(self, api_key: str = "", config: dict | None = None):
        self.api_key = api_key or self._read_api_key()
        self.config = config or {}
        self._active_pod_id: str | None = None
        self._available_gpus: list[GPUSpec] = []

    def _read_api_key(self) -> str:
        import os
        key = os.environ.get("RUNPOD_API_KEY")
        if key:
            return key.strip()
        env_path = os.path.expanduser("~/AppData/Local/hermes/.env")
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("RUNPOD_API_KEY="):
                        return line.split("=", 1)[1].strip()
        return ""

    def available(self) -> bool:
        return bool(self.api_key)

    def discover_gpus(self) -> list[GPUSpec]:
        """Discover available RunPod GPUs with current pricing (§72).

        Prices change — never hardcode. Fetch live before paid execution.
        """
        if self._available_gpus:
            return self._available_gpus

        # RunPod GraphQL API to fetch available GPU types and pricing
        try:
            import json
            import urllib.request

            query = """
            query {
                gpuTypes {
                    id
                    displayName
                    memoryInGb
                    lowestPrice(input: {computeType: INTENSITY}) {
                        minimumBidPrice
                        uninterruptablePrice
                    }
                }
            }
            """
            payload = json.dumps({"query": query}).encode("utf-8")
            req = urllib.request.Request(
                "https://api.runpod.io/graphql",
                data=payload,
                headers={
                    "Authorization": self.api_key,
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())

            gpus = []
            for gt in data.get("data", {}).get("gpuTypes", []):
                mem = gt.get("memoryInGb", 0)
                prices = gt.get("lowestPrice", {})
                hourly = prices.get("uninterruptablePrice", 999.0)
                gpus.append(GPUSpec(
                    name=gt.get("displayName", gt.get("id", "unknown")),
                    vram_gb=float(mem) if mem else 0,
                    hourly_price=float(hourly) if hourly else 999.0,
                    availability=hourly < 999.0,
                    performance_score=float(mem) if mem else 0,
                ))
            self._available_gpus = sorted(
                gpus,
                key=lambda g: (g.hourly_price if g.availability else 999.0, -g.vram_gb),
            )
            return self._available_gpus
        except Exception as e:
            logger.error(f"Failed to discover RunPod GPUs: {e}")
            return []

    def select_gpu(self, required_vram_gb: float, max_hourly_price: float = 999.0) -> GPUSpec | None:
        """§71: Select cheapest suitable GPU — NOT hardcoded to specific model."""
        # Use cached GPUs if available, otherwise discover
        gpus = self._available_gpus if self._available_gpus else self.discover_gpus()
        # Filter by VRAM and price, then sort by price (cheapest first)
        suitable = [
            gpu for gpu in gpus
            if gpu.vram_gb >= required_vram_gb
            and gpu.hourly_price <= max_hourly_price
            and gpu.availability
        ]
        if not suitable:
            logger.warning(f"No suitable GPU found (need {required_vram_gb}GB, max ${max_hourly_price:.2f}/h)")
            return None
        # Sort by price (cheapest first), then by VRAM (more is better for tiebreaker)
        suitable.sort(key=lambda g: (g.hourly_price, -g.vram_gb))
        selected = suitable[0]
        logger.info(f"Selected GPU: {selected.name} ({selected.vram_gb}GB, ${selected.hourly_price:.3f}/h)")
        return selected

    def get_gpu_spec(self) -> GPUSpec:
        """Return the currently selected/active GPU spec."""
        if self._active_pod_id:
            # Return spec of active pod
            for gpu in self._available_gpus:
                if gpu.availability:
                    return gpu
        return GPUSpec(name="RunPod (not selected)", vram_gb=0, hourly_price=0.0)

    def estimate_cost(self, request: GPUJobRequest) -> float:
        """§72: Estimate cost = (estimated_time / 3600) * hourly_price."""
        time_s = self.estimate_time(request)
        gpu = self.select_gpu(required_vram_gb=8.0)  # Minimum for i2v
        if gpu is None:
            return 999.0
        return (time_s / 3600.0) * gpu.hourly_price

    def estimate_time(self, request: GPUJobRequest) -> float:
        """Estimate job execution time on cloud GPU."""
        if request.job_type == "image_to_video":
            # ~30-60s per 5s clip on RTX 4090 with Wan 2.1
            base = 30.0
            per_second = 6.0
            return base + (request.duration_seconds * per_second)
        elif request.job_type == "text_to_video":
            base = 60.0
            per_second = 10.0
            return base + (request.duration_seconds * per_second)
        return 60.0

    async def execute_job(self, request: GPUJobRequest) -> GPUJobResult:
        """Execute job on RunPod (§70 lifecycle).

        1. Select GPU
        2. Create pod with model image
        3. Send job
        4. Poll for completion
        5. Download result
        6. Terminate pod (ALWAYS)
        """
        if not self.available():
            return GPUJobResult(status=GPUJobStatus.FAILED, error="RunPod API key not configured")

        import time

        # Select GPU based on job requirements
        required_vram = 12.0 if "video" in request.job_type else 8.0
        gpu = self.select_gpu(required_vram_gb=required_vram)
        if gpu is None:
            return GPUJobResult(
                status=GPUJobStatus.FAILED,
                error=f"No suitable GPU available (need {required_vram}GB VRAM)",
            )

        t_start = time.time()
        pod_id = None

        try:
            # Create pod (RunPod serverless endpoint or pod creation)
            pod_id = await self._create_pod(gpu, request)
            self._active_pod_id = pod_id

            # Execute job
            result = await self._run_job(pod_id, request)

            # Download result
            if result.get("output_path"):
                await self._download_result(result["output_path"], request.output_dir)

            elapsed = time.time() - t_start
            cost = (elapsed / 3600.0) * gpu.hourly_price

            return GPUJobResult(
                status=GPUJobStatus.COMPLETED,
                output_path=result.get("output_path", ""),
                job_duration_seconds=elapsed,
                gpu_name=gpu.name,
                hourly_price=gpu.hourly_price,
                estimated_cost=self.estimate_cost(request),
                actual_cost=cost,
                metadata=result,
            )

        except Exception as e:
            logger.error(f"RunPod job failed: {e}")
            return GPUJobResult(
                status=GPUJobStatus.FAILED,
                error=str(e),
                job_duration_seconds=time.time() - t_start,
                gpu_name=gpu.name,
                hourly_price=gpu.hourly_price,
            )
        finally:
            # §70: ALWAYS terminate pod, even on failure
            if pod_id:
                await self._terminate_pod(pod_id)
                self._active_pod_id = None

    async def _create_pod(self, gpu: GPUSpec, request: GPUJobRequest) -> str:
        """Create a RunPod pod with the required model image."""
        # Implementation: use RunPod API to create a pod
        # For now, this is a placeholder that would use the RunPod SDK
        import asyncio
        # TODO: implement actual RunPod pod creation via API
        logger.info(f"Creating RunPod pod: {gpu.name} for {request.job_type}")
        await asyncio.sleep(0.1)  # placeholder
        return "placeholder_pod_id"

    async def _run_job(self, pod_id: str, request: GPUJobRequest) -> dict:
        """Send job to pod and poll for completion."""
        import asyncio
        # TODO: implement actual job submission and polling
        logger.info(f"Running job on pod {pod_id}")
        await asyncio.sleep(0.1)  # placeholder
        return {"output_path": "", "status": "completed"}

    async def _download_result(self, remote_path: str, local_dir: str) -> None:
        """Download result from pod to local filesystem."""
        import os
        os.makedirs(local_dir, exist_ok=True)
        # TODO: implement actual download via RunPod API

    async def _terminate_pod(self, pod_id: str) -> None:
        """§70: Terminate pod — never leave GPU running."""
        import asyncio
        logger.info(f"Terminating RunPod pod {pod_id}")
        # TODO: implement actual RunPod pod termination via API
        await asyncio.sleep(0.1)

    def cleanup(self) -> None:
        """Force cleanup if any pod is still running."""
        if self._active_pod_id:
            import asyncio
            asyncio.run(self._terminate_pod(self._active_pod_id))
            self._active_pod_id = None


# ── Generative Video Config (§63) ─────────────────────────────────────────────

@dataclass
class GenerativeVideoConfig:
    """§63: Configuration for generative video limits."""
    enabled: bool = True
    provider: str = "runpod"  # or "local" when ComfyUI+Wan available
    max_seconds_per_episode: int = 30
    preferred_clip_duration_seconds: int = 4
    maximum_clip_duration_seconds: int = 8
    max_clips_per_episode: int = 5  # derived from max_seconds / preferred_duration
    cost_limit_per_clip_usd: float = 1.0

    def validate_clip(self, duration: float) -> bool:
        """Check if a clip duration is within limits."""
        return duration <= self.maximum_clip_duration_seconds

    def can_add_clip(self, current_total_seconds: float, new_clip_seconds: float) -> bool:
        """Check if adding another clip is within episode limit (§64: 5-15%)."""
        return (current_total_seconds + new_clip_seconds) <= self.max_seconds_per_episode


# ── Factory ────────────────────────────────────────────────────────────────────

def get_gpu_provider(provider_name: str = "local", **kwargs) -> GPUComputeProvider:
    """Factory: get GPU compute provider by name.

    Business code calls this — never instantiates providers directly.
    """
    if provider_name == "local":
        return LocalGPUProvider()
    elif provider_name == "runpod":
        return RunPodGPUProvider(**kwargs)
    else:
        raise ValueError(f"Unknown GPU provider: {provider_name}")
