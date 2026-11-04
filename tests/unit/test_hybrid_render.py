import json
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet, sha256
from src.hybrid.compiled import CompiledEpisode, FrameSpec, OperationalPipeline
from src.hybrid.execution import Executor
from src.hybrid.planner import Config
from src.hybrid.render import (
    LocalRenderer,
    Scene,
    _publish_new,
    measure_loudness,
    probe,
)

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


def test_renderer_renders_a_manifest_from_the_operational_compiled_pipeline(tmp_path):
    source_manifest, audio, _srt = fixtures(tmp_path)
    episode = CompiledEpisode.compile(
        "EPX",
        audio,
        (
            FrameSpec("R001", 0, 1, "primeiro quadro", "ação inicial"),
            FrameSpec("R002", 1, 2, "segundo quadro", "ação final"),
        ),
    )
    pipeline = OperationalPipeline(
        episode,
        Executor(tmp_path / "control.db", Config()),
        source_manifest,
        workspace=tmp_path / "compiled",
        endpoint="local-test",
        image_cost=Decimal(".01"),
        imported_assets={
            "R001": source_manifest.assets[0],
            "R002": source_manifest.assets[1],
        },
        blocked_scenes={"R001", "R002"},
    )
    approved = pipeline.approved_manifest()
    output = tmp_path / "compiled-master.mkv"

    receipt = LocalRenderer(width=320, height=180).render_compiled(
        pipeline,
        [Scene(asset, 1.0) for asset in approved.assets],
        approved,
        audio,
        None,
        output,
        hold=3,
    )

    assert output.is_file()
    assert receipt["manifest_sha256"] == approved.checksum
    assert receipt["subtitles_sha256"] is None


def test_mp4_delivery_derives_aac_master_without_burning_subtitles(tmp_path):
    manifest, audio, _srt = fixtures(tmp_path)
    output = tmp_path / "delivery.mp4"

    receipt = LocalRenderer(width=320, height=180).render(
        [Scene(asset, 1.0) for asset in manifest.assets],
        manifest,
        audio,
        None,
        output,
        hold=3,
    )

    streams = probe(output)["streams"]
    assert (
        next(stream for stream in streams if stream["codec_type"] == "audio")[
            "codec_name"
        ]
        == "aac"
    )
    assert receipt["audio_operation"] == "derived_master"
    assert receipt["subtitles_sha256"] is None


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
    with pytest.raises(ValueError, match="thread"):
        LocalRenderer(width=320, height=180, filter_complex_threads=None)


def test_renderer_accepts_bounded_parallel_scene_workers_and_encoder_threads():
    renderer = LocalRenderer(width=320, height=180, scene_workers=2, encoder_threads=1)

    assert (renderer.scene_workers, renderer.encoder_threads) == (2, 1)
    with pytest.raises(ValueError, match="scene worker"):
        LocalRenderer(scene_workers=0)
    with pytest.raises(ValueError, match="encoder thread"):
        LocalRenderer(encoder_threads=0)


def test_renderer_applies_declared_thread_budget_to_every_ffmpeg_encode(
    tmp_path, monkeypatch
):
    import src.hybrid.render as render_module

    manifest, audio, _srt = fixtures(tmp_path)
    commands = []
    original = render_module.command

    def tracked(args, **kwargs):
        commands.append(tuple(args))
        return original(args, **kwargs)

    monkeypatch.setattr(render_module, "command", tracked)
    receipt = LocalRenderer(
        width=320,
        height=180,
        scene_workers=2,
        encoder_threads=1,
        filter_complex_threads=1,
    ).render(
        [Scene(asset, 1.0) for asset in manifest.assets],
        manifest,
        audio,
        None,
        tmp_path / "bounded.mp4",
        hold=3,
    )

    ffmpeg_commands = [args for args in commands if args[0] == "ffmpeg"]
    assert ffmpeg_commands
    scene_commands = [args for args in ffmpeg_commands if "-vf" in args]
    assert scene_commands
    for args in scene_commands:
        thread_positions = [i for i, value in enumerate(args) if value == "-threads:v"]
        assert len(thread_positions) == 2
        assert thread_positions[0] < args.index("-i")
        assert thread_positions[1] > args.index("-c:v")
        assert args[args.index("-filter_threads") + 1] == "1"
        assert args.index("-filter_threads") < args.index("-vf")

    graph = next(args for args in ffmpeg_commands if "-filter_complex" in args)
    graph_inputs = [i for i, value in enumerate(graph) if value == "-i"]
    graph_decoder_threads = [
        i for i, value in enumerate(graph) if value == "-threads:v"
    ][:-1]
    assert len(graph_decoder_threads) == len(graph_inputs)
    assert all(
        thread < source for thread, source in zip(graph_decoder_threads, graph_inputs)
    )
    assert graph[graph.index("-filter_complex_threads") + 1] == "1"

    master = next(
        args
        for args in ffmpeg_commands
        if "-c:v" in args and args[args.index("-c:v") + 1] == "copy"
    )
    assert master.index("-threads:v") < master.index("-i")
    audio_input = [i for i, value in enumerate(master) if value == "-i"][1]
    audio_threads = [i for i, value in enumerate(master) if value == "-threads:a"]
    assert len(audio_threads) == 2
    assert audio_threads[0] < audio_input < audio_threads[1]
    assert master[master.index("-filter_threads") + 1] == "1"
    assert receipt["scene_workers"] == 2
    assert receipt["encoder_threads"] == 1
    assert receipt["filter_complex_threads"] == 1


def test_loudness_analysis_applies_decoder_and_filter_thread_budgets(monkeypatch):
    captured = []

    class Result:
        returncode = 0
        stderr = '{"input_i":"-16.0","input_tp":"-1.5"}'

    def fake_run(args, **kwargs):
        captured.append(tuple(args))
        return Result()

    monkeypatch.setattr("src.hybrid.render.subprocess.run", fake_run)

    measured = measure_loudness("delivery.mp4", threads=2, filter_threads=1)

    assert measured == {"integrated_lufs": -16.0, "true_peak_dbtp": -1.5}
    assert captured == [
        (
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-v",
            "info",
            "-threads",
            "2",
            "-i",
            "delivery.mp4",
            "-map",
            "0:a:0",
            "-vn",
            "-sn",
            "-filter_threads",
            "1",
            "-af",
            "loudnorm=I=-16:TP=-1:print_format=json",
            "-f",
            "null",
            "-",
        )
    ]


def test_renderer_defaults_to_delivery_geometry_and_fps():
    renderer = LocalRenderer()

    assert (renderer.width, renderer.height, renderer.fps) == (1920, 1080, 30)
    assert renderer.filter_complex_threads == 1
