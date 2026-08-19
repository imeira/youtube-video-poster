# ARCHITECTURE
## Hybrid AI Animation Studio

**Version:** 1.0
**Date:** 2026-08-19

---

## 1. Overview

```
USER (Telegram) → Director Agent → [Specialized Agents] → YouTube → Telegram
                        ↓
                   Budget Guard (all paid ops)
                        ↓
                   State Store (persistent JSON)
                        ↓
              ┌─────────┴─────────┐
         LOCAL GPU              RUNPOD (on-demand)
         SD 1.5 + LCM          i2v (Wan 2.2)
         ffmpeg animation       LoRA training
         TTS (edge-tts)         (SECURE cloud)
```

## 2. Design Principles

1. **LOCAL-FIRST** — local GPU does the bulk; cloud only for decisive moments
2. **CONSISTENT IMAGE > VIDEO** — generate stills, animate locally, use generative video sparingly
3. **PROVIDER ABSTRACTION** — business logic never depends on a specific provider implementation
4. **STATELESS AGENTS** — all state persists in JSON files; agents read/write state, don't hold it
5. **BUDGET GUARD IS GATEKEEPER** — no paid operation proceeds without Budget Guard approval
6. **SILENCE ≠ APPROVAL** — HITL approvals are explicit and time-stamped

## 3. Provider Abstractions (§54)

Every external capability is an interface. Implementations are swappable.

```python
# All providers follow this pattern
class Provider(ABC):
    @abstractmethod
    def estimate_cost(self, **params) -> CostEstimate: ...
    @abstractmethod
    def execute(self, **params) -> Result: ...
```

| Interface | Local Impl | Cloud Impl | Future |
|---|---|---|---|
| `LLMProvider` | ollama | openai, anthropic, nous, deepseek, fireworks | — |
| `ImageProvider` | SD1.5+LCM (local) | RunPod ComfyUI | fal.ai, stable-video |
| `VideoProvider` | ffmpeg (Ken Burns/parallax) | RunPod Wan 2.2 i2v | Hailuo, Kling, Veo |
| `GPUComputeProvider` | LocalGPUProvider | RunPodGPUProvider | Modal, Replicate |
| `TTSProvider` | edge-tts (free) | Azure Speech (~$0.07/ep) | Piper, XTTS |
| `MusicProvider` | local library | — | Suno |
| `StorageProvider` | local filesystem | — | S3, R2 |
| `NotificationProvider` | Telegram bot | — | Discord, Email |
| `PublishProvider` | — | YouTube Data API v3 | — |

### 3.1 GPUComputeProvider

```python
class GPUComputeProvider(ABC):
    @abstractmethod
    def list_gpus(self) -> list[GPU]: ...
    @abstractmethod
    def get_price(self, gpu_id: str) -> Price: ...
    @abstractmethod
    def provision(self, gpu_id: str, image: str, **opts) -> PodHandle: ...
    @abstractmethod
    def terminate(self, pod_id: str) -> None: ...
    @abstractmethod
    def cleanup_orphans(self) -> list[str]: ...
```

**RunPod implementation:** uses `runpod` Python SDK (v1.12.0), SECURE cloud preferred (B6 finding: community cloud unreliable), `try/finally` for guaranteed shutdown, orphan check on startup.

## 4. Component Map

```
src/
├── config/           # config.yaml loader, validation
├── state/            # State machine, episode state store
├── budget/           # Budget Guard, cost ledger
├── providers/        # All provider implementations
│   ├── llm/
│   ├── image/        # SD1.5+LCM local, RunPod ComfyUI
│   ├── video/        # ffmpeg local, RunPod i2v
│   ├── gpu/          # LocalGPUProvider, RunPodGPUProvider
│   ├── tts/          # edge-tts, azure
│   ├── notification/ # Telegram
│   └── publish/      # YouTube
├── agents/           # Director + specialized agents
├── pipeline/         # Pipeline orchestrator, scene schemas
├── qa/               # Visual QA, narrative QA
├── storage/          # Episode filesystem, cache, manifest
├── observability/    # Logging, metrics, audit trail
└── cli/              # Entry point: "Poste um vídeo no..."
```

## 5. Episode Filesystem (§15)

```
projects/episodes/           # OUTSIDE OneDrive (C7)
└── EP000001/
    ├── request.json         # User input
    ├── plan.json            # Pre-production plan
    ├── state.json           # Current state machine state
    ├── manifest.json        # Full audit trail (§88)
    ├── costs.json           # Cost ledger (§62)
    ├── research/            # Biblical sources, references
    ├── script/              # Script, narration text
    ├── characters/          # Character Bible (YAML + PNGs)
    ├── storyboard/          # Scene list with timestamps
    ├── audio/               # narration.wav, master.wav, SFX, music
    ├── images/              # Generated stills (approved + rejected)
    ├── animation/           # Local animation clips
    ├── cloud_clips/         # RunPod i2v results
    ├── subtitles/           # transcript.txt, captions.srt, captions.vtt
    ├── thumbnails/          # Thumbnail variations
    ├── metadata/            # title, description, tags, chapters
    ├── qa/                  # QA results per scene
    ├── renders/             # Final video render
    └── logs/                # Per-step operation logs
```

## 6. Technology Stack

| Layer | Technology | Version | Source |
|---|---|---|---|
| Language | Python | 3.12.1 | B1 |
| PyTorch | 2.7.1+cu118 | cu118 (NOT cu126) | B1 |
| Image gen | diffusers + SD 1.5 + LCM LoRA | — | B2, B3 |
| Character consistency | IP-Adapter (h94/IP-Adapter) | — | B4 |
| Video anim | ffmpeg 9.0 (libx264, zoompan, xfade) | — | B0 |
| TTS | edge-tts (ThalitaNeural) | — | B5 |
| Word align | faster-whisper (tiny, CPU int8) | 1.2.1 | B5 |
| Cloud GPU | RunPod SDK | 1.12.0 | B6 |
| YouTube | google-api-python-client | 2.194.0 | — |
| Bot API | Telegram Bot API (long polling) | — | — |
| Venv | C:\Users\meira\hermes-studio-venv | Python 3.12 | B1 |

## 7. Runtime Constraints (from benchmark)

| Constraint | Value | Impact |
|---|---|---|
| VRAM | 4 GB | SD1.5 fp16 fits (2.87 GB peak); IP-Adapter needs VAE on CPU |
| PyTorch | cu118 only | Cannot use newer CUDA features |
| Encoder | libx264 CPU only | NVENC broken (driver 528.79 < 610) |
| IP-Adapter + LCM | OOM if both loaded | Choose one per scene generation mode |
| RunPod community | Containers don't start | Use SECURE cloud ($0.74/hr 4090) |
| PYTHONPATH | Must be `""` | Hermes venv contaminates studio venv |
