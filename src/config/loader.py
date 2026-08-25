"""Configuration loader — reads config.yaml into typed dataclasses.

§99: config.yaml is the single source of truth for all configurable parameters.
Secrets (API keys, tokens) live in .env, NEVER here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# ── Dataclasses for each config section ───────────────────────────────────────

@dataclass(frozen=True)
class BudgetConfig:
    currency: str = "USD"
    target_usd: float = 4.00
    warning_usd: float = 5.00
    hard_limit_usd: float = 6.00
    require_approval_above_limit: bool = True


@dataclass(frozen=True)
class CostEstimateConfig:
    image_usd: float = 0.015
    generative_video_second_usd: float = 0.10


@dataclass(frozen=True)
class EpisodeDurationConfig:
    min_minutes: int = 3
    max_minutes: int = 15


@dataclass(frozen=True)
class TTSConfig:
    language: str = "pt-BR"
    voice_name: str = "pt-BR-ThalitaNeural"
    rate: str = "-8%"
    pitch: str = "+1Hz"
    provider: str = "edge-tts"
    preserve_voice_across_episodes: bool = True
    azure_fallback: bool = True


@dataclass(frozen=True)
class GenerativeVideoConfig:
    enabled: bool = True
    provider: str = "runpod"
    only_for_high_value_scenes: bool = True
    max_clips_per_episode: int = 5
    max_seconds_per_episode: int = 30
    preferred_clip_duration_seconds: int = 4
    maximum_clip_duration_seconds: int = 8
    cost_limit_per_clip_usd: float = 1.0
    prefer_i2v: bool = True


@dataclass(frozen=True)
class RunPodConfig:
    enabled: bool = True
    shutdown_after_job: bool = True
    max_retries_per_scene: int = 2
    preferred_cloud: str = "SECURE"
    fallback_cloud: str = "COMMUNITY"
    preferred_i2v_gpu: str = "NVIDIA GeForce RTX 4090"
    preferred_image_gpu: str = "NVIDIA RTX A5000"
    preferred_lora_gpu: str = "NVIDIA RTX A4000"


@dataclass(frozen=True)
class HardwareConfig:
    profile: str = "LOW_VRAM_4GB"
    pytorch_cuda_version: str = "cu118"
    encoder_primary: str = "libx264"
    encoder_preset: str = "veryfast"
    encoder_crf: int = 20
    lcm_lora: bool = True


@dataclass(frozen=True)
class StudioConfig:
    """Top-level configuration loaded from config.yaml."""
    project_name: str
    default_language: str
    default_channel: str
    budget: BudgetConfig
    cost_estimates: CostEstimateConfig
    episode_duration: EpisodeDurationConfig
    tts: TTSConfig
    generative_video: GenerativeVideoConfig
    runpod: RunPodConfig
    hardware: HardwareConfig
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def episodes_dir(self) -> Path:
        """§C7: episodes OUTSIDE OneDrive to avoid I/O contention."""
        return Path(os.environ.get("STUDIO_EPISODES_DIR", "C:/HermesStudio/episodes"))


# ── Loader ────────────────────────────────────────────────────────────────────

_CONFIG_CACHE: StudioConfig | None = None


def load_config(config_path: str | Path | None = None) -> StudioConfig:
    """Load config.yaml and return a typed StudioConfig.

    Args:
        config_path: Path to config.yaml. If None, searches for config.yaml
                     in the project root (parent of src/).

    Returns:
        StudioConfig dataclass.
    """
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None and config_path is None:
        return _CONFIG_CACHE

    if config_path is None:
        # Default: project root (parent of src/)
        config_path = Path(__file__).resolve().parents[2] / "config.yaml"
    else:
        config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"config.yaml not found at {config_path}")

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    budget_raw = raw.get("budget", {}).get("episode", {})
    cost_raw = raw.get("cost_estimates", {})
    ep_raw = raw.get("episode", {}).get("typical_duration", {})
    tts_raw = raw.get("tts", {})
    gv_raw = raw.get("generative_video", {})
    rp_raw = raw.get("runpod", {})
    hw_raw = raw.get("hardware", {})

    config = StudioConfig(
        project_name=raw.get("project", {}).get("name", "Hybrid AI Animation Studio"),
        default_language=raw.get("project", {}).get("default_language", "pt-BR"),
        default_channel=raw.get("project", {}).get("default_channel", "@EraUmaVezBibliaAnimada"),
        budget=BudgetConfig(
            currency=raw.get("budget", {}).get("currency", "USD"),
            target_usd=budget_raw.get("target_usd", 4.00),
            warning_usd=budget_raw.get("warning_usd", 5.00),
            hard_limit_usd=budget_raw.get("hard_limit_usd", 6.00),
            require_approval_above_limit=budget_raw.get("require_approval_above_limit", True),
        ),
        cost_estimates=CostEstimateConfig(
            image_usd=cost_raw.get("image_usd", 0.015),
            generative_video_second_usd=cost_raw.get("generative_video_second_usd", 0.10),
        ),
        episode_duration=EpisodeDurationConfig(
            min_minutes=ep_raw.get("min_minutes", 3),
            max_minutes=ep_raw.get("max_minutes", 15),
        ),
        tts=TTSConfig(
            language=tts_raw.get("language", "pt-BR"),
            voice_name=tts_raw.get("preferred_voice", {}).get("name", "pt-BR-ThalitaNeural"),
            rate=tts_raw.get("preferred_voice", {}).get("rate", "-8%"),
            pitch=tts_raw.get("preferred_voice", {}).get("pitch", "+1Hz"),
            provider=tts_raw.get("provider", "edge-tts"),
            preserve_voice_across_episodes=tts_raw.get("preserve_voice_across_episodes", True),
            azure_fallback=tts_raw.get("azure_fallback", True),
        ),
        generative_video=GenerativeVideoConfig(
            enabled=gv_raw.get("enabled", True),
            provider=gv_raw.get("provider", "runpod"),
            only_for_high_value_scenes=gv_raw.get("only_for_high_value_scenes", True),
            max_clips_per_episode=gv_raw.get("max_clips_per_episode", 5),
            max_seconds_per_episode=gv_raw.get("max_seconds_per_episode", 30),
            preferred_clip_duration_seconds=gv_raw.get("preferred_clip_duration_seconds", 4),
            maximum_clip_duration_seconds=gv_raw.get("maximum_clip_duration_seconds", 8),
            cost_limit_per_clip_usd=gv_raw.get("cost_limit_per_clip_usd", 1.0),
            prefer_i2v=gv_raw.get("prefer_i2v", True),
        ),
        runpod=RunPodConfig(
            enabled=rp_raw.get("enabled", True),
            shutdown_after_job=rp_raw.get("shutdown_after_job", True),
            max_retries_per_scene=rp_raw.get("retries", {}).get("max_per_scene", 2),
            preferred_cloud=rp_raw.get("preferred_cloud", "SECURE"),
            fallback_cloud=rp_raw.get("fallback_cloud", "COMMUNITY"),
            preferred_i2v_gpu=rp_raw.get("preferred_gpus", {}).get("i2v", "NVIDIA GeForce RTX 4090"),
            preferred_image_gpu=rp_raw.get("preferred_gpus", {}).get("image_gen", "NVIDIA RTX A5000"),
            preferred_lora_gpu=rp_raw.get("preferred_gpus", {}).get("lora_training", "NVIDIA RTX A4000"),
        ),
        hardware=HardwareConfig(
            profile=hw_raw.get("profile", "LOW_VRAM_4GB"),
            pytorch_cuda_version=hw_raw.get("pytorch", {}).get("cuda_version", "cu118"),
            encoder_primary=hw_raw.get("encoder", {}).get("primary", "libx264"),
            encoder_preset=hw_raw.get("encoder", {}).get("preset", "veryfast"),
            encoder_crf=hw_raw.get("encoder", {}).get("crf", 20),
            lcm_lora=hw_raw.get("profiles", {}).get("LOW_VRAM_4GB", {}).get("lcm_lora", True),
        ),
        raw=raw,
    )

    _CONFIG_CACHE = config
    return config


def get_config() -> StudioConfig:
    """Get the cached config, loading if necessary."""
    return load_config()
