import json

from src.pipeline.artifact_manifest import (
    build_manifest,
    manifest_matches,
    write_manifest_atomic,
)


def test_manifest_invalidates_cache_when_an_input_changes(tmp_path):
    image = tmp_path / "SC001.png"
    audio = tmp_path / "narration.mp3"
    image.write_bytes(b"image-v1")
    audio.write_bytes(b"audio-v1")
    config = {"fps": 30, "transition_seconds": 0.25}
    timeline = [{"scene_id": "SC001", "duration": 4.782}]

    original = build_manifest([image, audio], timeline, config)
    manifest_path = tmp_path / "render_manifest.json"
    manifest_path.write_text(json.dumps(original), encoding="utf-8")

    assert manifest_matches(manifest_path, [image, audio], timeline, config)

    image.write_bytes(b"image-v2")

    assert not manifest_matches(manifest_path, [image, audio], timeline, config)


def test_manifest_is_written_atomically_without_leaving_temp_file(tmp_path):
    manifest_path = tmp_path / "render_manifest.json"
    manifest = {"schema_version": 1, "fingerprint": "abc123"}

    write_manifest_atomic(manifest_path, manifest)

    assert json.loads(manifest_path.read_text(encoding="utf-8")) == manifest
    assert not (tmp_path / "render_manifest.json.tmp").exists()
