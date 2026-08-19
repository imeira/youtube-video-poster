"""Tests for episode filesystem (§15)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.storage.episode_fs import EpisodeFS


@pytest.fixture
def episodes_dir(tmp_path, monkeypatch):
    """Point episodes dir to tmp_path (outside OneDrive, C7)."""
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    return tmp_path


class TestEpisodeFS:
    def test_create_dirs(self, episodes_dir):
        """§15: All episode subdirectories should be created."""
        from src.config.loader import get_config
        config = get_config()
        fs = EpisodeFS("EP000001", config)
        fs.create_dirs()
        
        root = episodes_dir / "EP000001"
        assert root.exists()
        assert (root / "research").exists()
        assert (root / "images").exists()
        assert (root / "audio" / "music").exists()
        assert (root / "audio" / "sfx").exists()
        assert (root / "renders").exists()
        assert (root / "cloud_clips").exists()
        assert (root / "qa").exists()
        assert (root / "metadata").exists()

    def test_save_request(self, episodes_dir):
        """§5: Save user request."""
        from src.config.loader import get_config
        config = get_config()
        fs = EpisodeFS("EP000002", config)
        fs.create_dirs()
        fs.save_request(theme="Davi e Golias")
        
        import json
        with open(fs.paths.request_json, encoding="utf-8") as f:
            req = json.load(f)
        assert req["theme"] == "Davi e Golias"
        assert req["language"] == "pt-BR"
        assert req["youtube_channel"] == "@EraUmaVezBibliaAnimada"

    def test_image_path(self, episodes_dir):
        """Scene image path should follow naming convention."""
        from src.config.loader import get_config
        config = get_config()
        fs = EpisodeFS("EP000003", config)
        path = fs.image_path("SC001")
        assert "SC001.png" in str(path)
        assert "images" in str(path)

    def test_episode_exists_check(self, episodes_dir):
        """§14: Check if episode exists for resume."""
        from src.config.loader import get_config
        config = get_config()
        fs = EpisodeFS("EP000004", config)
        assert not fs.exists()
        fs.create_dirs()
        assert fs.exists()
