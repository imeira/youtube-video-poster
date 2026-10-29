"""Async director render boundary keeps FFmpeg work outside the event loop."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock

import pytest

from src.agents.director import DirectorAgent
from src.config.loader import get_config
from src.hybrid.render import commit_renderer_completion
from src.qa.final_render import FinalRenderQA
from src.state.machine import EpisodeState, EpisodeStateStore
from src.storage.episode_fs import EpisodeFS


def _render_inputs(manifest_sha256: str = "manifest-a"):
    """Minimal immutable compiled input surface for Director flight tests."""
    audio = SimpleNamespace(sha256="audio-a")
    manifest = SimpleNamespace(checksum=manifest_sha256)
    scene = SimpleNamespace(
        image=SimpleNamespace(sha256="image-a"), clip=None,
    )
    return SimpleNamespace(
        episode=SimpleNamespace(audio=audio),
        approved_manifest=lambda: manifest,
        render_scenes=lambda received_manifest: [scene],
    )


def _renderer():
    return SimpleNamespace(
        width=1920,
        height=1080,
        fps=30,
        filter_complex_threads=1,
        scene_workers=1,
        encoder_threads=1,
    )


@pytest.mark.asyncio
async def test_finalizer_keeps_event_loop_running_while_sync_renderer_is_blocked():
    director = DirectorAgent.__new__(DirectorAgent)
    started = threading.Event()
    release = threading.Event()
    render_thread_ids: list[int] = []
    main_thread = threading.get_ident()

    def blocked_render(*_args, **_kwargs):
        render_thread_ids.append(threading.get_ident())
        started.set()
        assert release.wait(timeout=5)
        return {"output_sha256": "receipt"}

    director.render_compiled_video = blocked_render
    director.prepare_delivery_sidecars = AsyncMock(return_value={"sidecar": "ready"})

    pending = asyncio.create_task(
        director.finalize_compiled_delivery("EP-ASYNC", pipeline=_render_inputs(), renderer=_renderer())
    )
    assert await asyncio.to_thread(started.wait, 2)

    loop_progressed = False
    await asyncio.sleep(0)
    loop_progressed = True
    assert loop_progressed is True
    assert not pending.done()
    assert render_thread_ids == [render_thread_ids[0]]
    assert render_thread_ids[0] != main_thread

    release.set()
    result = await pending

    assert result == {
        "render_receipt": {"output_sha256": "receipt"},
        "sidecars": {"sidecar": "ready"},
    }


@pytest.mark.asyncio
@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="real ffmpeg/ffprobe unavailable",
)
async def test_finalizer_offloads_tiny_ffmpeg_and_existing_qa_validates_exact_receipt(
    tmp_path,
):
    director = DirectorAgent.__new__(DirectorAgent)
    output = Path(tmp_path) / "tiny-final.mp4"

    def render_with_ffmpeg(*_args, **_kwargs):
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=1",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1:sample_rate=48000",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-af", "loudnorm=I=-16:TP=-2",
                "-c:a", "aac", "-shortest", str(output),
            ],
            check=True,
            capture_output=True,
        )
        return {
            "output": str(output),
            "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "subtitles_sha256": None,
            "hold_seconds": 4,
            "audio_operation": "derived_master",
        }

    director.render_compiled_video = render_with_ffmpeg
    director.prepare_delivery_sidecars = AsyncMock(return_value={})

    delivery = await director.finalize_compiled_delivery(
        "EP-TINY", pipeline=_render_inputs(), renderer=_renderer()
    )

    assert output.is_file()
    assert FinalRenderQA(expected_geometry=(320, 180)).review(
        output, delivery["render_receipt"]
    ).approved


@pytest.mark.asyncio
async def test_cancelled_render_keeps_episode_writer_until_thread_unwinds_and_retry_reuses_it():
    director = DirectorAgent.__new__(DirectorAgent)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def blocked_render(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        started.set()
        assert release.wait(timeout=5)
        return {"output_sha256": "receipt"}

    director.render_compiled_video = blocked_render
    director.prepare_delivery_sidecars = AsyncMock(return_value={"sidecar": "ready"})

    cancelled = asyncio.create_task(
        director.finalize_compiled_delivery("EP-OWNED", pipeline=_render_inputs(), renderer=_renderer())
    )
    assert await asyncio.to_thread(started.wait, 2)
    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled

    retry = asyncio.create_task(
        director.finalize_compiled_delivery("EP-OWNED", pipeline=_render_inputs(), renderer=_renderer())
    )
    await asyncio.sleep(0)
    assert calls == 1
    assert not retry.done()

    release.set()
    assert await retry == {
        "render_receipt": {"output_sha256": "receipt"},
        "sidecars": {"sidecar": "ready"},
    }
    assert calls == 1


@pytest.mark.asyncio
async def test_restart_reuses_hash_verified_render_receipt_without_another_encode(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent(config=get_config())
    fs = EpisodeFS("EP-RESTART", director.config)
    fs.create_dirs()
    video_bytes = b"already-encoded-final"
    fs.paths.final_video.write_bytes(video_bytes)
    expected_hash = hashlib.sha256(video_bytes).hexdigest()
    pipeline = _render_inputs()
    renderer = _renderer()
    contract = director._compiled_render_contract("EP-RESTART", pipeline, renderer, 4)
    contract_sha256 = director._compiled_render_contract_sha256(contract)
    intent = {
        "schema": "compiled-render-intent-v1",
        "render_contract": contract,
        "render_contract_sha256": contract_sha256,
    }
    intent_path = fs.paths.qa_dir / "compiled_render_intent.json"
    director._write_exclusive_render_json(
            intent_path, intent, "test intent", canonical_episode_root=fs.paths.root
        )
    commit_renderer_completion(
        fs.paths.qa_dir / "renderer_completion_receipt.json",
        intent_sha256=hashlib.sha256(director._canonical_render_json(intent)).hexdigest(),
        render_contract_sha256=contract_sha256,
        receipt={"hold_seconds": 4, "output_sha256": expected_hash, "subtitles_sha256": None},
    )
    EpisodeStateStore(
        episode_id="EP-RESTART", current_state=EpisodeState.FINAL_QA
    ).save(fs.paths.state_json)
    director.prepare_delivery_sidecars = AsyncMock(return_value={"sidecar": "ready"})

    result = await director.finalize_compiled_delivery(
        "EP-RESTART", pipeline=pipeline, renderer=renderer
    )

    assert result["render_receipt"]["renderer_receipt"]["output_sha256"] == expected_hash
    director.prepare_delivery_sidecars.assert_awaited_once_with("EP-RESTART", ANY)


@pytest.mark.asyncio
async def test_inflight_episode_rejects_changed_manifest_or_hold_instead_of_returning_stale_flight(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent(config=get_config())
    EpisodeFS("EP-CONTRACT", director.config).create_dirs()
    started, release = threading.Event(), threading.Event()

    def blocked_render(*_args, **_kwargs):
        started.set()
        assert release.wait(timeout=5)
        return {"output_sha256": "receipt"}

    director.render_compiled_video = blocked_render
    first = asyncio.create_task(
        director.render_compiled_video_async("EP-CONTRACT", _render_inputs("manifest-a"), _renderer())
    )
    assert await asyncio.to_thread(started.wait, 2)

    with pytest.raises(ValueError, match="contract conflict"):
        await director.render_compiled_video_async("EP-CONTRACT", _render_inputs("manifest-b"), _renderer())
    with pytest.raises(ValueError, match="contract conflict"):
        await director.render_compiled_video_async("EP-CONTRACT", _render_inputs("manifest-a"), _renderer(), hold=5)

    release.set()
    assert await first == {"output_sha256": "receipt"}


@pytest.mark.asyncio
async def test_fixed_process_render_pool_never_derives_capacity_from_first_renderer():
    director = DirectorAgent.__new__(DirectorAgent)
    director._compiled_render_pool_size = 1
    started, release = [], threading.Event()

    def blocked_render(episode_id, *_args, **_kwargs):
        started.append(episode_id)
        assert release.wait(timeout=5)
        return {"output_sha256": episode_id}

    director.render_compiled_video = blocked_render
    wide_renderer = _renderer()
    wide_renderer.scene_workers = 8
    wide_renderer.encoder_threads = 8
    first = asyncio.create_task(
        director.render_compiled_video_async("EP-POOL-A", _render_inputs(), wide_renderer)
    )
    for _ in range(20):
        if started:
            break
        await asyncio.sleep(0.01)
    assert started == ["EP-POOL-A"]
    second = asyncio.create_task(
        director.render_compiled_video_async("EP-POOL-B", _render_inputs(), _renderer())
    )
    await asyncio.sleep(0.05)
    assert started == ["EP-POOL-A"]

    release.set()
    assert (await first)["output_sha256"] == "EP-POOL-A"
    assert (await second)["output_sha256"] == "EP-POOL-B"


@pytest.mark.asyncio
async def test_recovery_rejects_legacy_or_underbound_receipt_before_reusing_output(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent(config=get_config())
    fs = EpisodeFS("EP-UNDERBOUND", director.config)
    fs.create_dirs()
    fs.paths.final_video.write_bytes(b"already-encoded-final")
    (fs.paths.qa_dir / "compiled_render_receipt.json").write_text(
        json.dumps({"hold_seconds": 4, "output_sha256": hashlib.sha256(fs.paths.final_video.read_bytes()).hexdigest()}),
        encoding="utf-8",
    )
    EpisodeStateStore(episode_id="EP-UNDERBOUND", current_state=EpisodeState.FINAL_QA).save(fs.paths.state_json)

    with pytest.raises(ValueError, match="quarantined"):
        director.render_compiled_video("EP-UNDERBOUND", _render_inputs(), _renderer())


def test_renderer_return_without_bound_durable_completion_is_quarantined(tmp_path, monkeypatch):
    """A video alone, even with a renderer return value, is never recovery evidence."""
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent(config=get_config())
    fs = EpisodeFS("EP-NO-COMPLETION", director.config)
    fs.create_dirs()
    EpisodeStateStore(
        episode_id="EP-NO-COMPLETION", current_state=EpisodeState.PLANNING_ANIMATION
    ).save(fs.paths.state_json)

    def unsafe_renderer(*args, **kwargs):
        args[5].write_bytes(b"unbound-final")
        return {
            "hold_seconds": kwargs["hold"],
            "output_sha256": hashlib.sha256(args[5].read_bytes()).hexdigest(),
            "subtitles_sha256": None,
        }

    renderer = _renderer()
    renderer.render_compiled = unsafe_renderer
    with pytest.raises(ValueError, match="completion"):
        director.render_compiled_video("EP-NO-COMPLETION", _render_inputs(), renderer)


def test_recovery_rejects_tampered_final_or_current_inputs_without_encoding(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    director = DirectorAgent(config=get_config())

    def completed(episode_id):
        fs = EpisodeFS(episode_id, director.config)
        fs.create_dirs()
        EpisodeStateStore(episode_id=episode_id, current_state=EpisodeState.ASSEMBLING).save(fs.paths.state_json)
        pipeline, renderer = _render_inputs(), _renderer()
        contract = director._compiled_render_contract(episode_id, pipeline, renderer, 4)
        contract_sha256 = director._compiled_render_contract_sha256(contract)
        intent = {
            "schema": "compiled-render-intent-v1",
            "render_contract": contract,
            "render_contract_sha256": contract_sha256,
        }
        intent_path = fs.paths.qa_dir / "compiled_render_intent.json"
        director._write_exclusive_render_json(
            intent_path, intent, "test intent", canonical_episode_root=fs.paths.root
        )
        fs.paths.final_video.write_bytes(b"bound-final")
        commit_renderer_completion(
            fs.paths.qa_dir / "renderer_completion_receipt.json",
            intent_sha256=hashlib.sha256(director._canonical_render_json(intent)).hexdigest(),
            render_contract_sha256=contract_sha256,
            receipt={
                "hold_seconds": 4,
                "output_sha256": hashlib.sha256(b"bound-final").hexdigest(),
                "subtitles_sha256": None,
            },
        )
        return fs, pipeline, renderer

    video_fs, video_pipeline, video_renderer = completed("EP-TAMPERED-VIDEO")
    video_fs.paths.final_video.write_bytes(b"altered-final")
    with pytest.raises(ValueError, match="final bytes"):
        director.render_compiled_video("EP-TAMPERED-VIDEO", video_pipeline, video_renderer)
    assert EpisodeStateStore.load(video_fs.paths.state_json).current_state is EpisodeState.ASSEMBLING

    input_fs, _pipeline, input_renderer = completed("EP-TAMPERED-INPUT")
    with pytest.raises(ValueError, match="current inputs"):
        director.render_compiled_video("EP-TAMPERED-INPUT", _render_inputs("manifest-b"), input_renderer)
    assert EpisodeStateStore.load(input_fs.paths.state_json).current_state is EpisodeState.ASSEMBLING


def test_fresh_processes_share_canonical_render_contract_and_encode_once(tmp_path, monkeypatch):
    """The OS lock spans recovery and receipt publication across fresh processes."""
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    episode_id = "EP-TWO-PROCESS"
    director = DirectorAgent(config=get_config())
    fs = EpisodeFS(episode_id, director.config)
    fs.create_dirs()
    EpisodeStateStore(
        episode_id=episode_id, current_state=EpisodeState.PLANNING_ANIMATION
    ).save(fs.paths.state_json)
    counter = tmp_path / "encoder-count.txt"
    started = tmp_path / "encoder-started.txt"
    script = r'''
import hashlib
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

from src.agents.director import DirectorAgent
from src.config.loader import get_config
from src.hybrid.render import commit_renderer_completion

episode_id = os.environ["EPISODE_ID"]
counter = Path(os.environ["ENCODER_COUNTER"])
started = Path(os.environ["ENCODER_STARTED"])
manifest = SimpleNamespace(checksum="manifest-a")
scene = SimpleNamespace(image=SimpleNamespace(sha256="image-a"), clip=None)
pipeline = SimpleNamespace(
    episode=SimpleNamespace(audio=SimpleNamespace(sha256="audio-a")),
    approved_manifest=lambda: manifest,
    render_scenes=lambda received_manifest: [scene],
)

def render_compiled(*args, **kwargs):
    descriptor = os.open(counter, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write("1")
    started.write_text("entered", encoding="utf-8")
    time.sleep(0.5)
    output = args[5]
    output.write_bytes(b"two-process-final")
    receipt = {
        "hold_seconds": kwargs["hold"],
        "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "subtitles_sha256": None,
    }
    commit_renderer_completion(
        kwargs["completion_receipt_path"],
        intent_sha256=kwargs["render_intent_sha256"],
        render_contract_sha256=kwargs["render_contract_sha256"],
        receipt=receipt,
    )
    return receipt

renderer = SimpleNamespace(
    width=1920, height=1080, fps=30, filter_complex_threads=1,
    scene_workers=1, encoder_threads=1, render_compiled=render_compiled,
)
print(json.dumps(DirectorAgent(config=get_config()).render_compiled_video(episode_id, pipeline, renderer)))
'''
    env = os.environ.copy()
    repo = Path(__file__).resolve().parents[2]
    env.update(
        STUDIO_EPISODES_DIR=str(tmp_path),
        EPISODE_ID=episode_id,
        ENCODER_COUNTER=str(counter),
        ENCODER_STARTED=str(started),
        PYTHONPATH=str(repo) + os.pathsep + env.get("PYTHONPATH", ""),
    )
    first = subprocess.Popen(
        [sys.executable, "-c", script], cwd=repo, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + 20
    while not started.exists() and time.monotonic() < deadline:
        if first.poll() is not None:
            first_stdout, first_stderr = first.communicate()
            pytest.fail(
                "first process never entered the fake encoder: "
                f"stdout={first_stdout!r} stderr={first_stderr!r}"
            )
        time.sleep(0.02)
    assert started.exists(), "first process never entered the fake encoder"
    second = subprocess.Popen(
        [sys.executable, "-c", script], cwd=repo, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    first_stdout, first_stderr = first.communicate(timeout=15)
    second_stdout, second_stderr = second.communicate(timeout=15)
    assert first.returncode == 0, first_stderr
    assert second.returncode == 0, second_stderr

    first_receipt = json.loads(first_stdout)
    second_receipt = json.loads(second_stdout)
    receipt_path = fs.paths.qa_dir / "compiled_render_receipt.json"
    persisted_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert counter.read_text(encoding="utf-8") == "1"
    assert first_receipt == second_receipt == persisted_receipt
    assert persisted_receipt["render_contract"]["canonical_episode_root"] == str(fs.paths.root.resolve())


@pytest.mark.parametrize("kind", ["intent", "completion", "director"])
@pytest.mark.parametrize("via_ancestor", [False, True])
def test_render_receipt_snapshot_rejects_symlink_target_and_ancestor_before_accept(
    tmp_path, kind, via_ancestor
):
    """Every render evidence read is rooted in the lexical episode directory."""
    root = tmp_path / "episode"
    qa_dir = root / "qa"
    qa_dir.mkdir(parents=True)
    payload = {"schema": "test"}
    target = tmp_path / f"{kind}-target.json"
    target.write_bytes(DirectorAgent._canonical_render_json(payload))
    if via_ancestor:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / f"{kind}.json").write_bytes(target.read_bytes())
        link = root / "linked-qa"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"symlinks unavailable: {error}")
        evidence = link / f"{kind}.json"
    else:
        evidence = qa_dir / f"{kind}.json"
        try:
            evidence.symlink_to(target)
        except OSError as error:
            pytest.skip(f"symlinks unavailable: {error}")

    with pytest.raises(ValueError, match="symlink|unavailable"):
        DirectorAgent._read_canonical_render_json(
            evidence, {"schema"}, f"{kind} receipt", canonical_episode_root=root
        )


@pytest.mark.parametrize(
    ("crash_phase", "expected_recovery"),
    [("after_completion", True), ("after_final_before_completion", False)],
)
def test_fresh_process_crash_boundaries_prove_recovery_or_quarantine(
    tmp_path, monkeypatch, crash_phase, expected_recovery
):
    """A fresh process proves completion recovery; unbound finals stay quarantined."""
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    episode_id = f"EP-CRASH-{crash_phase}"
    director = DirectorAgent(config=get_config())
    fs = EpisodeFS(episode_id, director.config)
    fs.create_dirs()
    EpisodeStateStore(episode_id=episode_id, current_state=EpisodeState.PLANNING_ANIMATION).save(
        fs.paths.state_json
    )
    counter = tmp_path / f"{crash_phase}-encode-count.txt"
    script = r'''
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from src.agents.director import DirectorAgent
from src.config.loader import get_config
from src.hybrid.render import commit_renderer_completion

manifest = SimpleNamespace(checksum="manifest-a")
scene = SimpleNamespace(image=SimpleNamespace(sha256="image-a"), clip=None)
pipeline = SimpleNamespace(
    episode=SimpleNamespace(audio=SimpleNamespace(sha256="audio-a")),
    approved_manifest=lambda: manifest,
    render_scenes=lambda received_manifest: [scene],
)
phase = os.environ["CRASH_PHASE"]
counter = Path(os.environ["ENCODER_COUNTER"])

def render_compiled(*args, **kwargs):
    if phase == "recover":
        raise AssertionError("recovery attempted a second encode")
    descriptor = os.open(counter, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write("1")
    output = args[5]
    output.write_bytes(b"crash-boundary-final")
    if phase == "after_final_before_completion":
        os._exit(87)
    receipt = {"hold_seconds": kwargs["hold"], "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(), "subtitles_sha256": None}
    commit_renderer_completion(
        kwargs["completion_receipt_path"], intent_sha256=kwargs["render_intent_sha256"],
        render_contract_sha256=kwargs["render_contract_sha256"], receipt=receipt,
        canonical_episode_root=kwargs["canonical_episode_root"],
    )
    os._exit(86)

renderer = SimpleNamespace(width=1920, height=1080, fps=30, filter_complex_threads=1, scene_workers=1, encoder_threads=1, render_compiled=render_compiled)
DirectorAgent(config=get_config()).render_compiled_video(os.environ["EPISODE_ID"], pipeline, renderer)
'''
    repo = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env.update(
        STUDIO_EPISODES_DIR=str(tmp_path), EPISODE_ID=episode_id, ENCODER_COUNTER=str(counter),
        CRASH_PHASE=crash_phase, PYTHONPATH=str(repo) + os.pathsep + env.get("PYTHONPATH", ""),
    )
    crashed = subprocess.run(
        [sys.executable, "-c", script], cwd=repo, env=env, capture_output=True, text=True, check=False
    )
    assert crashed.returncode == (86 if expected_recovery else 87), crashed.stderr
    env["CRASH_PHASE"] = "recover"
    recovered = subprocess.run(
        [sys.executable, "-c", script], cwd=repo, env=env, capture_output=True, text=True, check=False
    )
    if expected_recovery:
        assert recovered.returncode == 0, recovered.stderr
        assert counter.read_text(encoding="utf-8") == "1"
        assert (fs.paths.qa_dir / "compiled_render_receipt.json").is_file()
    else:
        assert recovered.returncode != 0
        assert "quarantined" in recovered.stderr
        assert counter.read_text(encoding="utf-8") == "1"
        assert not (fs.paths.qa_dir / "compiled_render_receipt.json").exists()
