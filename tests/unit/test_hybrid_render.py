import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet, sha256
from src.hybrid.render import LocalRenderer, Scene, _publish_new, probe

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="real ffmpeg/ffprobe unavailable",
)


def fixtures(root):
    assets = []
    for name, color in (("a", "red"), ("b", "blue")):
        path = root / f"{name}.png"
        Image.new("RGB", (320, 180), color).save(path)
        assets.append(FrozenAsset.approve(path, "human", "TEST"))
    sheet = root / "sheet.png"
    contact_sheet(assets, sheet)
    manifest = Manifest.freeze(
        assets, FrozenAsset.approve(sheet, "human", "TEST"), "human", "TEST"
    )
    audio = root / "approved.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2:sample_rate=48000",
            "-c:a",
            "pcm_s16le",
            str(audio),
        ],
        check=True,
        capture_output=True,
    )
    srt = root / "approved.srt"
    srt.write_text(
        "1\n00:00:00,000 --> 00:00:01,900\nLegenda aprovada\n", encoding="utf-8"
    )
    return (
        manifest,
        FrozenAsset.approve(audio, "human", "TEST"),
        FrozenAsset.approve(srt, "human", "TEST"),
    )


def decoded_audio(path):
    return subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-f",
            "s16le",
            "-c:a",
            "pcm_s16le",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout


def test_real_local_render_transition_subtitle_audio_hold_and_rerender(tmp_path):
    manifest, audio, srt = fixtures(tmp_path)
    scenes = [Scene(a, 1.0) for a in manifest.assets]
    renderer = LocalRenderer(width=320, height=180)
    output = tmp_path / "master.mkv"
    receipt = renderer.render(scenes, manifest, audio, srt, output, hold=3)
    media = probe(output)
    assert float(media["format"]["duration"]) == pytest.approx(5, abs=0.06)
    assert {s["codec_type"] for s in media["streams"]} >= {"video", "audio"}
    assert decoded_audio(output) == decoded_audio(audio.path)
    assert sha256(audio.path) == audio.sha256
    assert receipt["api_cost"] == 0 and receipt["mode"] == "TEST"
    assert receipt["subtitles_sha256"] == srt.sha256
    assert receipt["transition_seconds"] == 0.25
    assert receipt["scene_render_seconds"] == [1.25, 1.0]
    assert Path(receipt["output"]).is_file()
    saved = json.loads(output.with_name(output.name + ".receipt.json").read_text())
    assert saved == receipt
    # Burned-in SRT changes actual video pixels, not merely metadata.
    frame = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(output),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    assert any(all(c > 190 for c in frame[i : i + 3]) for i in range(0, len(frame), 3))
    hold_frame = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            "4",
            "-i",
            str(output),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        check=True,
        capture_output=True,
    ).stdout
    assert not any(
        all(c > 190 for c in hold_frame[i : i + 3])
        for i in range(0, len(hold_frame), 3)
    )
    again = renderer.render(
        scenes, manifest, audio, srt, tmp_path / "rerender.mkv", hold=3
    )
    assert again["api_cost"] == 0


def test_render_rejects_unapproved_changed_assets_invalid_srt_and_hold(tmp_path):
    manifest, audio, srt = fixtures(tmp_path)
    renderer = LocalRenderer(width=320, height=180)
    scenes = [Scene(a, 1) for a in manifest.assets]
    for hold in (2, 6):
        with pytest.raises(ValueError):
            renderer.render(
                scenes, manifest, audio, srt, tmp_path / "bad.mkv", hold=hold
            )
    srt.path.write_text("inferred captions are forbidden")
    with pytest.raises(ValueError, match="hash"):
        renderer.render(scenes, manifest, audio, srt, tmp_path / "bad.mkv")
    malformed = FrozenAsset.approve(srt.path, "human", "TEST")
    with pytest.raises(ValueError, match="SRT"):
        renderer.render(scenes, manifest, audio, malformed, tmp_path / "bad.mkv")
    assert not (tmp_path / "bad.mkv").exists()


def test_real_local_render_without_unapproved_subtitles(tmp_path):
    manifest, audio, _srt = fixtures(tmp_path)
    scenes = [Scene(asset, 1.0) for asset in manifest.assets]
    output = tmp_path / "master_without_subtitles.mkv"

    receipt = LocalRenderer(width=320, height=180).render(
        scenes, manifest, audio, None, output, hold=3
    )

    assert output.is_file()
    assert receipt["subtitles_sha256"] is None
    assert decoded_audio(output) == decoded_audio(audio.path)
    assert output.with_name(output.name + ".receipt.json").is_file()


def test_publish_new_never_clobbers_a_racing_output(tmp_path):
    source = tmp_path / "source.mkv"
    output = tmp_path / "master.mkv"
    source.write_bytes(b"new")
    output.write_bytes(b"existing")

    with pytest.raises(FileExistsError):
        _publish_new(source, output)

    assert output.read_bytes() == b"existing"
    assert source.read_bytes() == b"new"


def test_renderer_accepts_a_bounded_ffmpeg_thread_budget():
    renderer = LocalRenderer(width=320, height=180, filter_complex_threads=2)
    assert renderer.filter_complex_threads == 2
    with pytest.raises(ValueError, match="thread"):
        LocalRenderer(width=320, height=180, filter_complex_threads=0)


def test_renderer_accepts_bounded_parallel_scene_workers_and_encoder_threads():
    renderer = LocalRenderer(width=320, height=180, scene_workers=2, encoder_threads=1)

    assert (renderer.scene_workers, renderer.encoder_threads) == (2, 1)
    with pytest.raises(ValueError, match="scene worker"):
        LocalRenderer(scene_workers=0)
    with pytest.raises(ValueError, match="encoder thread"):
        LocalRenderer(encoder_threads=0)


def test_renderer_defaults_to_delivery_geometry_and_fps():
    renderer = LocalRenderer()

    assert (renderer.width, renderer.height, renderer.fps) == (1920, 1080, 30)
