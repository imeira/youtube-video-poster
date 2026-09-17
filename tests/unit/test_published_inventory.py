from __future__ import annotations

import hashlib
import json

from src.qa.published_inventory import PublishedInventory


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def test_inventory_reads_only_published_episode_artifacts(tmp_path):
    published = tmp_path / "EP1"
    _write(published / "state.json", json.dumps({"episode_id": "EP1", "current_state": "PUBLISHED"}))
    _write(published / "script" / "script.json", "{\"segments\":[]}")
    _write(published / "subtitles" / "captions.vtt", "WEBVTT\n")
    _write(published / "metadata" / "metadata.json", json.dumps({"licenses": {"music": "none", "visual_assets": "canonical"}}))
    _write(tmp_path / "EP2" / "state.json", json.dumps({"episode_id": "EP2", "current_state": "FINAL_QA"}))
    _write(tmp_path / "EP2" / "script" / "script.json", "unpublished")

    inventory = PublishedInventory.scan(tmp_path, exclude_episode_id="EP8")

    assert inventory.episode_ids == ("EP1",)
    assert inventory.script_hashes == {hashlib.sha256((published / "script" / "script.json").read_bytes()).hexdigest()}
    assert inventory.artifact_hashes == {hashlib.sha256((published / "subtitles" / "captions.vtt").read_bytes()).hexdigest()}
