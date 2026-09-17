"""Immutable inventory of published episode evidence for originality gates."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class PublishedInventory:
    episode_ids: tuple[str, ...]
    script_hashes: set[str]
    artifact_hashes: set[str]

    @classmethod
    def scan(cls, episodes_dir: Path | str, *, exclude_episode_id: str = "") -> PublishedInventory:
        root = Path(episodes_dir)
        if not root.exists():
            return cls((), set(), set())
        episode_ids: list[str] = []
        scripts: set[str] = set()
        artifacts: set[str] = set()
        for episode in sorted(path for path in root.iterdir() if path.is_dir()):
            if episode.name == exclude_episode_id:
                continue
            try:
                state = json.loads((episode / "state.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(state, dict) or state.get("current_state") != "PUBLISHED":
                continue
            episode_ids.append(episode.name)
            script = episode / "script" / "script.json"
            captions = episode / "subtitles" / "captions.vtt"
            if script.is_file():
                scripts.add(_sha256(script))
            if captions.is_file():
                artifacts.add(_sha256(captions))
            try:
                metadata = json.loads((episode / "metadata" / "metadata.json").read_text(encoding="utf-8"))
                thumbnail = Path(metadata.get("thumbnail", "")) if isinstance(metadata, dict) else None
            except (OSError, json.JSONDecodeError, TypeError):
                thumbnail = None
            if thumbnail and thumbnail.is_file():
                artifacts.add(_sha256(thumbnail))
        return cls(tuple(episode_ids), scripts, artifacts)
