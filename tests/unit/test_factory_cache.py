import json
import shutil
import subprocess
import threading
from pathlib import Path

import pytest
from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.render import LocalRenderer, Scene, _store_cache_output
from src.hybrid.technical_qa import inspect_image_cached

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="real ffmpeg/ffprobe unavailable",
)


def _assets(root):
    assets = []
    for name, color in (("a", "red"), ("b", "blue")):
        path = root / f"{name}.png"
        Image.new("RGB", (320, 180), color).save(path)
        assets.append(FrozenAsset.approve(path, "independent-qa", "TEST"))
    sheet = root / "sheet.png"
    contact_sheet(assets, sheet)
    manifest = Manifest.freeze(
        assets, FrozenAsset.approve(sheet, "independent-qa", "TEST"), "independent-qa", "TEST"
    )
    audio_path = root / "approved.wav"
    subprocess.run([
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
        "sine=frequency=440:duration=2:sample_rate=48000", "-c:a", "pcm_s16le", str(audio_path),
    ], check=True, capture_output=True)
    return manifest, FrozenAsset.approve(audio_path, "independent-qa", "TEST")


def test_technical_cache_reuses_exact_packet_but_rejects_tampered_receipt(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate.png"
    Image.new("RGB", (1280, 720), "green").save(candidate)
    cache = tmp_path / "technical-cache.json"
    import src.hybrid.technical_qa as qa

    decodes = 0
    original = qa._inspect_image_uncached

    def counted(path):
        nonlocal decodes
        decodes += 1
        return original(path)

    monkeypatch.setattr(qa, "_inspect_image_uncached", counted)
    first = inspect_image_cached(candidate, cache_path=cache, contract={"route": "baseline"})
    second = inspect_image_cached(candidate, cache_path=cache, contract={"route": "baseline"})

    assert first["technical_pass"] is True
    assert second == first
    assert decodes == 1
    assert first["promotion_authorized"] is False

    stored = json.loads(cache.read_text(encoding="utf-8"))
    stored["packet"]["result_sha256"] = "0" * 64
    cache.write_text(json.dumps(stored), encoding="utf-8")

    repaired = inspect_image_cached(candidate, cache_path=cache, contract={"route": "baseline"})
    assert repaired["result_sha256"] == first["result_sha256"]
    assert decodes == 2


def test_renderer_reuses_verified_scene_and_master_cache_then_rerenders_only_tampered_scene(tmp_path, monkeypatch):
    import src.hybrid.render as render_module

    manifest, audio = _assets(tmp_path)
    scenes = [Scene(asset, 1.0) for asset in manifest.assets]
    commands = []
    original = render_module.command

    def tracked(args, **kwargs):
        commands.append(tuple(args))
        return original(args, **kwargs)

    monkeypatch.setattr(render_module, "command", tracked)
    renderer = LocalRenderer(width=320, height=180, scene_workers=1, encoder_threads=1)
    cache_dir = tmp_path / "cache"
    first = renderer.render(scenes, manifest, audio, None, tmp_path / "first.mkv", hold=3, cache_dir=cache_dir)
    second = renderer.render(scenes, manifest, audio, None, tmp_path / "second.mkv", hold=3, cache_dir=cache_dir)

    scene_encodes = [args for args in commands if args[0] == "ffmpeg" and "-vf" in args]
    final_encodes = [args for args in commands if args[0] == "ffmpeg" and "-filter_complex" in args]
    assert len(scene_encodes) == 2
    assert len(final_encodes) == 1
    assert first["cache"]["master_hit"] is False
    assert second["cache"]["master_hit"] is True

    receipt = next((cache_dir / "scenes").glob("*.json"))
    cached_scene = Path(json.loads(receipt.read_text(encoding="utf-8"))["output"])
    cached_scene.write_bytes(b"tampered")

    third = renderer.render(scenes, manifest, audio, None, tmp_path / "third.mkv", hold=3, cache_dir=cache_dir)
    scene_encodes = [args for args in commands if args[0] == "ffmpeg" and "-vf" in args]
    final_encodes = [args for args in commands if args[0] == "ffmpeg" and "-filter_complex" in args]
    assert len(scene_encodes) == 3
    assert len(final_encodes) == 2
    assert third["cache"]["scene_hits"] == 1
    assert third["cache"]["master_hit"] is False


def test_technical_cache_rejects_field_tamper_and_unknown_packet_schema(tmp_path, monkeypatch):
    candidate = tmp_path / "candidate.png"
    Image.new("RGB", (1280, 720), "green").save(candidate)
    cache = tmp_path / "technical-cache.json"
    import src.hybrid.technical_qa as qa

    decodes = 0
    original = qa._inspect_image_uncached

    def counted(path):
        nonlocal decodes
        decodes += 1
        return original(path)

    monkeypatch.setattr(qa, "_inspect_image_uncached", counted)
    expected = inspect_image_cached(candidate, cache_path=cache, contract={"route": "baseline"})
    stored = json.loads(cache.read_text(encoding="utf-8"))
    stored["packet"]["technical_pass"] = False
    cache.write_text(json.dumps(stored), encoding="utf-8")

    repaired = inspect_image_cached(candidate, cache_path=cache, contract={"route": "baseline"})
    assert repaired == expected
    assert decodes == 2

    stored = json.loads(cache.read_text(encoding="utf-8"))
    stored["packet"]["unexpected"] = "substitution"
    cache.write_text(json.dumps(stored), encoding="utf-8")
    assert inspect_image_cached(candidate, cache_path=cache, contract={"route": "baseline"}) == expected
    assert decodes == 3


def test_renderer_cache_key_binds_renderer_implementation_identity(tmp_path, monkeypatch):
    import src.hybrid.render as render_module

    manifest, audio = _assets(tmp_path)
    scenes = [Scene(asset, 1.0) for asset in manifest.assets]
    renderer = LocalRenderer(width=320, height=180, scene_workers=1, encoder_threads=1)
    cache_dir = tmp_path / "cache"
    renderer.render(scenes, manifest, audio, None, tmp_path / "first.mkv", hold=3, cache_dir=cache_dir)
    monkeypatch.setattr(render_module, "_renderer_identity", lambda: {"implementation": "changed"})

    rerendered = renderer.render(scenes, manifest, audio, None, tmp_path / "second.mkv", hold=3, cache_dir=cache_dir)
    assert rerendered["cache"] == {"enabled": True, "scene_hits": 0, "master_hit": False}


def test_master_cache_hit_rejects_cache_tampered_after_validation_before_copy(tmp_path, monkeypatch):
    import src.hybrid.render as render_module

    manifest, audio = _assets(tmp_path)
    scenes = [Scene(asset, 1.0) for asset in manifest.assets]
    renderer = LocalRenderer(width=320, height=180, scene_workers=1, encoder_threads=1)
    cache_dir = tmp_path / "cache"
    renderer.render(scenes, manifest, audio, None, tmp_path / "first.mkv", hold=3, cache_dir=cache_dir)
    original_copy = render_module._copy_new

    def mutate_after_cache_validation(source, output, *, expected_sha256):
        Path(source).write_bytes(b"tampered-between-validation-and-copy")
        return original_copy(source, output, expected_sha256=expected_sha256)

    monkeypatch.setattr(render_module, "_copy_new", mutate_after_cache_validation)
    with pytest.raises(ValueError, match="cache artifact changed before copy"):
        renderer.render(scenes, manifest, audio, None, tmp_path / "second.mkv", hold=3, cache_dir=cache_dir)
    assert not (tmp_path / "second.mkv").exists()


def test_master_cache_hit_copies_instead_of_aliasing_mutable_output(tmp_path):
    manifest, audio = _assets(tmp_path)
    scenes = [Scene(asset, 1.0) for asset in manifest.assets]
    renderer = LocalRenderer(width=320, height=180, scene_workers=1, encoder_threads=1)
    cache_dir = tmp_path / "cache"
    renderer.render(scenes, manifest, audio, None, tmp_path / "first.mkv", hold=3, cache_dir=cache_dir)
    receipt = next((cache_dir / "masters").glob("*.json"))
    cached_master = Path(json.loads(receipt.read_text(encoding="utf-8"))["output"])
    cached_sha256 = cached_master.read_bytes()

    hit = renderer.render(scenes, manifest, audio, None, tmp_path / "second.mkv", hold=3, cache_dir=cache_dir)
    assert hit["cache"]["master_hit"] is True
    (tmp_path / "second.mkv").write_bytes(b"caller-mutated-output")
    assert cached_master.read_bytes() == cached_sha256


def test_cache_store_serializes_simultaneous_writers_into_verified_artifact(tmp_path):
    cache_receipt = tmp_path / "cache" / "scenes" / "same-key.json"
    first, second = tmp_path / "first.mp4", tmp_path / "second.mp4"
    first.write_bytes(b"first-render")
    second.write_bytes(b"second-render")
    binding = {"kind": "scene", "key": "same"}
    barrier = threading.Barrier(2)
    failures = []

    def store(source):
        try:
            barrier.wait()
            _store_cache_output(cache_receipt, source, binding)
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            failures.append(error)

    workers = [threading.Thread(target=store, args=(source,)) for source in (first, second)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join()
    assert not failures
    receipt = json.loads(cache_receipt.read_text(encoding="utf-8"))
    cached = Path(receipt["output"])
    assert cached.parent == cache_receipt.parent
    assert receipt["output_sha256"] == __import__("hashlib").sha256(cached.read_bytes()).hexdigest()


def test_technical_cache_rejects_directory_as_cache_path(tmp_path):
    candidate = tmp_path / "candidate.png"
    Image.new("RGB", (1280, 720), "green").save(candidate)
    cache_directory = tmp_path / "cache-directory"
    cache_directory.mkdir()

    with pytest.raises(ValueError, match="cache path must be a file"):
        inspect_image_cached(candidate, cache_path=cache_directory, contract={"route": "baseline"})
