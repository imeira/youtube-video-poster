"""Episode filesystem — directory structure and file management.

§15: Episode directory structure.
§16: Checkpoints after expensive/approved stages.
§17: Idempotency — never regenerate approved assets.
§85-87: Storage policy — never delete canonical assets.
C7: episodes/ OUTSIDE OneDrive.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from src.config.loader import StudioConfig


# Directory names within an episode (§15)
EPISODE_SUBDIRS = [
    "research",
    "script",
    "characters",
    "storyboard",
    "audio",
    "audio/dialogue",
    "audio/music",
    "audio/sfx",
    "images",
    "animation",
    "cloud_clips",
    "subtitles",
    "thumbnails",
    "metadata",
    "qa",
    "renders",
    "logs",
]


@dataclass
class EpisodePaths:
    """All paths for a single episode."""
    root: Path
    request_json: Path
    plan_json: Path
    state_json: Path
    manifest_json: Path
    costs_json: Path
    research_dir: Path
    script_dir: Path
    characters_dir: Path
    storyboard_dir: Path
    audio_dir: Path
    images_dir: Path
    animation_dir: Path
    cloud_clips_dir: Path
    subtitles_dir: Path
    thumbnails_dir: Path
    metadata_dir: Path
    qa_dir: Path
    renders_dir: Path
    logs_dir: Path

    # Audio files
    narration_wav: Path
    master_wav: Path

    # Output files
    final_video: Path
    transcript_txt: Path
    captions_srt: Path
    captions_vtt: Path


class EpisodeFS:
    """Filesystem manager for an episode (§15)."""

    def __init__(self, episode_id: str, config: StudioConfig):
        self.episode_id = episode_id
        self.config = config
        self.root = config.episodes_dir / episode_id
        self.paths = self._build_paths()

    def _build_paths(self) -> EpisodePaths:
        root = self.root
        return EpisodePaths(
            root=root,
            request_json=root / "request.json",
            plan_json=root / "plan.json",
            state_json=root / "state.json",
            manifest_json=root / "manifest.json",
            costs_json=root / "costs.json",
            research_dir=root / "research",
            script_dir=root / "script",
            characters_dir=root / "characters",
            storyboard_dir=root / "storyboard",
            audio_dir=root / "audio",
            images_dir=root / "images",
            animation_dir=root / "animation",
            cloud_clips_dir=root / "cloud_clips",
            subtitles_dir=root / "subtitles",
            thumbnails_dir=root / "thumbnails",
            metadata_dir=root / "metadata",
            qa_dir=root / "qa",
            renders_dir=root / "renders",
            logs_dir=root / "logs",
            narration_wav=root / "audio" / "narration.wav",
            master_wav=root / "audio" / "master.wav",
            final_video=root / "renders" / "final.mp4",
            transcript_txt=root / "subtitles" / "transcript.txt",
            captions_srt=root / "subtitles" / "captions.srt",
            captions_vtt=root / "subtitles" / "captions.vtt",
        )

    def create_dirs(self) -> None:
        """Create all episode directories (§15)."""
        self.root.mkdir(parents=True, exist_ok=True)
        for subdir in EPISODE_SUBDIRS:
            (self.root / subdir).mkdir(parents=True, exist_ok=True)

    def save_request(self, theme: str, language: str = "", channel: str = "") -> None:
        """Save the user's request (§5)."""
        request = {
            "episode_id": self.episode_id,
            "theme": theme,
            "language": language or self.config.default_language,
            "youtube_channel": channel or self.config.default_channel,
            "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }
        with open(self.paths.request_json, "w", encoding="utf-8") as f:
            json.dump(request, f, indent=2, ensure_ascii=False)

    def image_path(self, scene_id: str, suffix: str = "") -> Path:
        """Get the image path for a scene."""
        name = f"{scene_id}{suffix}.png"
        return self.paths.images_dir / name

    def animation_clip_path(self, scene_id: str) -> Path:
        """Get the animation clip path for a scene."""
        return self.paths.animation_dir / f"{scene_id}.mp4"

    def cloud_clip_path(self, scene_id: str) -> Path:
        """Get the cloud clip path for a scene."""
        return self.paths.cloud_clips_dir / f"{scene_id}.mp4"

    def exists(self) -> bool:
        """Check if episode directory exists (for resume, §14)."""
        return self.root.exists()
