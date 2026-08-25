# PROVIDERS SPECIFICATION
## Hybrid AI Animation Studio

**Version:** 1.0
**Date:** 2026-08-19
**Reference:** §54 (provider abstraction)

---

## 1. Provider Interfaces

All external capabilities are abstracted behind interfaces. Business logic depends on interfaces, never on specific implementations.

### 1.1 LLMProvider

```python
class LLMProvider(ABC):
    @abstractmethod
    async def complete(self, messages: list[dict], **opts) -> str: ...
    @abstractmethod
    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float: ...
```

**Implementations:** openai, anthropic, nous, deepseek, fireworks, gemini, xai, qwen, zai, ollama (local)

### 1.2 ImageProvider

```python
class ImageProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: str, negative: str, width: int, height: int,
                       reference_images: list[str] = None, seed: int = None) -> ImageResult: ...
    @abstractmethod
    def estimate_cost(self, **params) -> float: ...
    @abstractmethod
    def estimate_time(self, **params) -> float: ...
```

**Implementations:**
- `LocalSD15Provider`: SD1.5+LCM (7.1s/img, $0), SD1.5+IP-Adapter (65s/img, $0)
- `RunPodComfyUIProvider`: ComfyUI headless on RunPod (for higher quality / SDXL)

### 1.3 VideoProvider

```python
class VideoProvider(ABC):
    @abstractmethod
    async def image_to_video(self, image_path: str, prompt: str, duration: int,
                              motion: str = None) -> VideoResult: ...
    @abstractmethod
    def estimate_cost(self, duration: int, gpu: str) -> float: ...
```

**Implementations:**
- `LocalFFmpegProvider`: Ken Burns, parallax, transitions (measured: 7min/4min episode, $0)
- `RunPodI2VProvider`: Wan 2.2 image-to-video (estimated: $0.39 for 20s @ 480p on 4090 SECURE)

### 1.4 GPUComputeProvider (§53)

```python
class GPUComputeProvider(ABC):
    @abstractmethod
    def list_gpus(self) -> list[GPU]: ...
    @abstractmethod
    def get_gpu(self, gpu_id: str) -> GPUInfo: ...
    @abstractmethod
    def provision(self, gpu_id: str, image: str, cloud: str = "SECURE",
                   container_disk_gb: int = 40, **opts) -> PodHandle: ...
    @abstractmethod
    def get_pod(self, pod_id: str) -> PodInfo: ...
    @abstractmethod
    def terminate_pod(self, pod_id: str) -> None: ...
    @abstractmethod
    def cleanup_orphans(self) -> list[str]: ...
```

**Implementations:**
- `LocalGPUProvider`: GTX 1050 Ti (4GB, sm_61, cu118)
- `RunPodGPUProvider`: 48 GPUs available, SECURE cloud preferred (B6 finding)

### 1.5 TTSProvider

```python
class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str, voice: str, rate: str, pitch: str) -> TTSResult: ...
    # TTSResult includes audio_path + sentence_timestamps + word_timestamps
```

**Implementations:**
- `EdgeTTSProvider`: ThalitaNeural, free, RTF 0.048x, SentenceBoundary timestamps
- `AzureSpeechProvider`: same voice, licensed for commercial, ~$0.07/episode

### 1.6 NotificationProvider

```python
class NotificationProvider(ABC):
    @abstractmethod
    async def send_message(self, chat_id: str, text: str,
                           inline_keyboard: list[list[dict]] = None) -> int: ...
    @abstractmethod
    async def send_photo(self, chat_id: str, photo_path: str, caption: str = "") -> int: ...
    @abstractmethod
    async def send_video(self, chat_id: str, video_path: str, caption: str = "") -> int: ...
```

**Implementation:** `TelegramBotProvider` (long polling, @HermesLocalIMJBot, chat_id=141718934)

### 1.7 PublishProvider

```python
class PublishProvider(ABC):
    @abstractmethod
    async def upload(self, video_path: str, metadata: dict,
                     thumbnail: str, captions: str) -> PublishResult: ...
    @abstractmethod
    async def add_to_playlist(self, video_id: str, playlist: str) -> None: ...
```

**Implementation:** `YouTubeProvider` (Data API v3, requires audit for public publishing)

## 2. Provider Configuration

Providers are configured in `config.yaml` and selected at runtime:

```yaml
providers:
  llm:
    default: nous  # cost-optimized routing
    fallback: [deepseek, openai]
  image:
    default: local_sd15_lcm  # 7.1s/image, $0
    quality: local_sd15_ipadapter  # 65s/image, $0, character consistency
    cloud: runpod_comfyui  # for SDXL / higher quality
  video:
    local: local_ffmpeg  # Ken Burns, parallax
    cloud: runpod_wan22_i2v  # image-to-video
  gpu:
    local: local_gtx1050ti
    cloud: runpod
  tts:
    default: edge_tts  # free
    fallback: azure_speech  # licensed, $0.07/ep
  notification: telegram
  publish: youtube
```

## 3. Provider Selection Rules

1. **Cost-optimized:** prefer free local providers, use cloud only when quality gain justifies cost
2. **Fallback chain:** each provider has a fallback (§77: RunPod → local i2v → local parallax → pan/zoom)
3. **Budget-gated:** cloud providers require Budget Guard approval before execution
4. **Future-proof:** new providers (Hailuo, Kling, Veo, Sora) can be added by implementing the interface
