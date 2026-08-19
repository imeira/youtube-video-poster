"""Provider base classes — abstract interfaces for all external capabilities.

§54: Provider abstraction — business logic never depends on specific implementations.
Future providers (Hailuo, Kling, Veo, Sora) can be added by implementing the interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ProviderResult:
    """Base result from any provider."""
    success: bool
    output_path: str = ""
    cost: float = 0.0
    error: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseProvider(ABC):
    """Base provider interface (§54)."""

    @abstractmethod
    def estimate_cost(self, **params) -> float:
        """Estimate the cost of an operation."""
        ...

    @abstractmethod
    async def execute(self, **params) -> ProviderResult:
        """Execute the operation."""
        ...


# ── LLMProvider ───────────────────────────────────────────────────────────────

class LLMProvider(BaseProvider):
    """Abstract interface for LLM providers."""

    @abstractmethod
    async def complete(self, messages: list[dict], **opts) -> str:
        """Generate a completion."""
        ...


# ── ImageProvider ─────────────────────────────────────────────────────────────

@dataclass
class ImageResult(ProviderResult):
    """Result from image generation."""
    image_path: str = ""
    seed: int = 0
    generation_time: float = 0.0


class ImageProvider(BaseProvider):
    """Abstract interface for image generation providers."""

    @abstractmethod
    async def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 512,
        height: int = 512,
        reference_images: list[str] | None = None,
        seed: int | None = None,
    ) -> ImageResult:
        """Generate an image from a prompt."""
        ...

    @abstractmethod
    def estimate_time(self, width: int, height: int, steps: int) -> float:
        """Estimate generation time in seconds."""
        ...


# ── VideoProvider ──────────────────────────────────────────────────────────────

@dataclass
class VideoResult(ProviderResult):
    """Result from video generation."""
    video_path: str = ""
    duration_seconds: float = 0.0
    generation_time: float = 0.0


class VideoProvider(BaseProvider):
    """Abstract interface for video generation providers."""

    @abstractmethod
    async def image_to_video(
        self,
        image_path: str,
        prompt: str,
        duration: int = 4,
        motion: str = "",
    ) -> VideoResult:
        """Generate video from an image (i2v, §73)."""
        ...


# ── GPUComputeProvider (§53) ─────────────────────────────────────────────────

@dataclass
class GPU:
    """A GPU offering."""
    id: str
    display_name: str
    memory_in_gb: int
    secure_price: float = 0.0
    community_price: float = 0.0
    spot_price: float = 0.0


@dataclass
class PodHandle:
    """Handle to a provisioned GPU pod."""
    pod_id: str
    gpu: str
    cloud: str
    hourly_price: float
    status: str = "provisioning"


class GPUComputeProvider(BaseProvider):
    """Abstract interface for GPU compute providers (§53).

    Implementations:
    - LocalGPUProvider: GTX 1050 Ti (local)
    - RunPodGPUProvider: 48 GPUs, SECURE cloud preferred (B6)
    """

    @abstractmethod
    def list_gpus(self) -> list[GPU]:
        """List available GPU types."""
        ...

    @abstractmethod
    def get_gpu(self, gpu_id: str) -> GPU:
        """Get details for a specific GPU."""
        ...

    @abstractmethod
    def provision(
        self,
        gpu_id: str,
        image: str,
        cloud: str = "SECURE",
        container_disk_gb: int = 40,
        **opts,
    ) -> PodHandle:
        """Provision a GPU pod (§55: ALLOCATE)."""
        ...

    @abstractmethod
    def get_pod(self, pod_id: str) -> dict[str, Any]:
        """Get pod status."""
        ...

    @abstractmethod
    def terminate_pod(self, pod_id: str) -> None:
        """Terminate a pod (§55: SHUTDOWN)."""
        ...

    @abstractmethod
    def cleanup_orphans(self) -> list[str]:
        """Find and terminate orphaned pods (§56: orphan resource check)."""
        ...

    def estimate_cost(self, gpu_id: str, duration_seconds: float, cloud: str = "SECURE") -> float:
        """Estimate cost for a job."""
        gpu = self.get_gpu(gpu_id)
        price = gpu.secure_price if cloud == "SECURE" else gpu.community_price
        return (duration_seconds / 3600) * price


# ── TTSProvider ───────────────────────────────────────────────────────────────

@dataclass
class TTSResult(ProviderResult):
    """Result from TTS synthesis."""
    audio_path: str = ""
    duration_seconds: float = 0.0
    generation_time: float = 0.0
    sentence_timestamps: list[dict] = field(default_factory=list)
    word_timestamps: list[dict] = field(default_factory=list)


class TTSProvider(BaseProvider):
    """Abstract interface for TTS providers."""

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: str,
        rate: str = "",
        pitch: str = "",
    ) -> TTSResult:
        """Synthesize speech from text."""
        ...


# ── NotificationProvider ───────────────────────────────────────────────────────

class NotificationProvider(BaseProvider):
    """Abstract interface for notification providers (Telegram)."""

    @abstractmethod
    async def send_message(
        self,
        chat_id: str,
        text: str,
        inline_keyboard: list[list[dict]] | None = None,
    ) -> int:
        """Send a text message. Returns message_id."""
        ...

    @abstractmethod
    async def send_photo(self, chat_id: str, photo_path: str, caption: str = "") -> int:
        """Send a photo."""
        ...

    @abstractmethod
    async def send_video(self, chat_id: str, video_path: str, caption: str = "") -> int:
        """Send a video."""
        ...


# ── PublishProvider ────────────────────────────────────────────────────────────

@dataclass
class PublishResult(ProviderResult):
    """Result from publishing."""
    video_id: str = ""
    video_url: str = ""


class PublishProvider(BaseProvider):
    """Abstract interface for publishing providers (YouTube)."""

    @abstractmethod
    async def upload(
        self,
        video_path: str,
        metadata: dict,
        thumbnail: str = "",
        captions: str = "",
    ) -> PublishResult:
        """Upload video to platform."""
        ...

    @abstractmethod
    async def add_to_playlist(self, video_id: str, playlist: str) -> None:
        """Add video to playlist."""
        ...
