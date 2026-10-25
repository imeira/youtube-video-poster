"""Fresh-process CLI acceptance coverage for the TEST-only two-gate harness."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)


def run_cli(*args: str) -> tuple[int, dict]:
    env = {**os.environ, "PYTHONPATH": ""}
    completed = subprocess.run(
        [str(PYTHON), "-m", "src.cli.main", "autonomous", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


def test_cli_start_resume_status_and_test_only_publish_are_hash_bound(tmp_path):
    prompt_bytes = (
        b"Poste um video no @EraUmaVezBibliaAnimada\r\n"
        b"Idioma: Portugues do Brasil\r\n"
        b"Playlist: Aventuras do Antigo Testamento\r\n"
        b"Tema: 12. O sonho de Jac\xc3\xb3 e a escada para o c\xc3\xa9u \xe2\x80\x94 G\xc3\xaanesis 28\r\n"
    )
    prompt = tmp_path / "prompt.txt"
    prompt.write_bytes(prompt_bytes)
    workspace = tmp_path / "workspace"

    code, started = run_cli(
        "start", "--workspace", str(workspace), "--prompt-file", str(prompt)
    )
    assert code == 0, started
    assert started["mode"] == "TEST"
    assert started["state"] == "WAITING_THUMBNAIL_APPROVAL"
    assert started["prompt_sha256"] == sha256(prompt_bytes).hexdigest()
    assert Path(started["prompt_path"]).read_bytes() == prompt_bytes
    assert "not an episode delivery" in started["note"]
    request_id = started["request_id"]
    video_sha = started["artifacts"]["video"]["sha256"]
    video_mtime = Path(started["artifacts"]["video"]["path"]).stat().st_mtime_ns

    code, status = run_cli(
        "status", "--workspace", str(workspace), "--request-id", request_id
    )
    assert code == 0 and status["state"] == "WAITING_THUMBNAIL_APPROVAL"
    code, resumed = run_cli(
        "resume", "--workspace", str(workspace), "--request-id", request_id
    )
    assert code == 0
    assert resumed["artifacts"]["video"]["sha256"] == video_sha
    assert Path(resumed["artifacts"]["video"]["path"]).stat().st_mtime_ns == video_mtime

    code, thumb = run_cli(
        "approve",
        "--workspace",
        str(workspace),
        "--request-id",
        request_id,
        "--kind",
        "thumbnail",
        "--test-approval",
    )
    assert code == 0 and thumb["state"] == "WAITING_VIDEO_APPROVAL"
    code, ready = run_cli(
        "approve",
        "--workspace",
        str(workspace),
        "--request-id",
        request_id,
        "--kind",
        "video",
        "--test-approval",
    )
    assert code == 0 and ready["state"] == "READY_TO_PUBLISH"
    code, published = run_cli(
        "publish",
        "--workspace",
        str(workspace),
        "--request-id",
        request_id,
        "--test-publish",
    )
    assert code == 0 and published["state"] == "PUBLISHED"
    assert published["publication"]["mode"] == "TEST"


def test_cli_refuses_live_and_non_test_approval(tmp_path):
    prompt = tmp_path / "prompt.txt"
    prompt.write_bytes(
        b"Poste um video no @test\nIdioma: pt-BR\nPlaylist: TEST\nTema: fixture\n"
    )
    code, started = run_cli(
        "start", "--workspace", str(tmp_path), "--prompt-file", str(prompt)
    )
    assert code == 0
    code, blocked = run_cli(
        "approve",
        "--workspace",
        str(tmp_path),
        "--request-id",
        started["request_id"],
        "--kind",
        "thumbnail",
    )
    assert code == 1 and blocked["status"] == "BLOCKED"


def test_cli_compiled_test_packet_rehydrates_successors_and_rejects_mutation(tmp_path):
    """A fresh CLI process uses only a copied TEST packet and the durable executor."""
    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.compiled import compile_storyboard

    packet = tmp_path / "packet"
    packet.mkdir()
    audio = packet / "audio.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=48000:duration=6", "-ac", "2", str(audio)],
        check=True,
    )
    reference = packet / "reference.png"
    Image.new("RGB", (64, 64), "navy").save(reference)
    sheet = packet / "sheet.png"
    asset = FrozenAsset.approve(reference, "TEST fixture", "TEST")
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "TEST fixture", "TEST"), "TEST fixture", "TEST")
    episode = compile_storyboard(
        "TEST-COMPILED-CLI",
        FrozenAsset.approve(audio, "TEST fixture", "TEST"),
        [
            {"scene_id": "R001", "start": 0, "end": 1, "image_prompt": "sky", "action": "sky"},
            {"scene_id": "R002", "start": 1, "end": 6, "image_prompt": "sea", "action": "sea", "hero": True},
        ],
    )
    episode.save(packet / "compiled.json")
    manifest.save(packet / "manifest.json")
    (packet / "policy.json").write_text(json.dumps({
        "schema": 1, "version": "compiled-cli-test-v1", "mode": "TEST", "episode_cap_usd": "1.00",
        "month_cap_usd": "1.00", "experiment_cap_usd": "1.00",
        "allowed_providers": ["test-local-fixture"], "publication": {"enabled": False},
    }), encoding="utf-8")
    workspace = tmp_path / "workspace"

    code, rejected = run_cli(
        "start", "--workspace", str(workspace), "--compiled-input", str(packet),
        "--test-reject-scene", "R001",
    )
    assert code == 0, rejected
    assert rejected["kind"] == "compiled-test"
    assert rejected["state"] == "REJECTED"
    request_id = rejected["request_id"]
    runner_root = workspace / "TC" / request_id[:16] / "runner"
    metrics_db = runner_root / "operational-metrics.sqlite3"
    ready = tmp_path / "compiled-wal-writer-ready"
    writer = subprocess.Popen(
        [str(PYTHON), "-c", "from pathlib import Path; import sqlite3,sys,time; db=sqlite3.connect(sys.argv[1]); db.execute('PRAGMA journal_mode=WAL'); db.execute('BEGIN IMMEDIATE'); db.execute('PRAGMA user_version=9'); Path(sys.argv[2]).write_text('ready'); time.sleep(15); db.rollback(); db.close()", str(metrics_db), str(ready)],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": ""},
    )
    try:
        for _ in range(40):
            if ready.exists():
                break
            time.sleep(0.05)
        assert ready.exists()
        assert metrics_db.with_name(metrics_db.name + "-wal").exists()
        before = {path.relative_to(runner_root): path.read_bytes() for path in runner_root.rglob("*") if path.is_file() and not path.name.endswith("-shm")}
        code, compiled_status = run_cli("status", "--workspace", str(workspace), "--request-id", request_id)
        after = {path.relative_to(runner_root): path.read_bytes() for path in runner_root.rglob("*") if path.is_file() and not path.name.endswith("-shm")}
    finally:
        writer.terminate()
        writer.wait(timeout=10)
    assert code == 0, compiled_status
    assert compiled_status["operational_metrics"]["status"] == "CURRENT"
    assert before == after

    code, remediated = run_cli(
        "remediate", "--workspace", str(workspace), "--request-id", request_id,
        "--correction", "R001=clear sky",
    )
    assert code == 0, remediated
    assert remediated["state"] == "MEDIA_PENDING"
    code, heroed = run_cli("heroes", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 0, heroed
    assert heroed["state"] == "WAITING_THUMBNAIL_APPROVAL"
    video = Path(heroed["artifacts"]["video"]["path"])
    video_mtime = video.stat().st_mtime_ns

    code, resumed = run_cli("resume", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 0, resumed
    assert video.stat().st_mtime_ns == video_mtime
    copied_input = workspace / "TC" / request_id[:16] / "inputs" / "compiled.json"
    copied_input.write_text("{}", encoding="utf-8")
    code, blocked = run_cli("status", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 1 and blocked["status"] == "BLOCKED"


def test_cli_compiled_packet_rejects_live_policy_and_external_asset(tmp_path):
    packet = tmp_path / "packet"
    packet.mkdir()
    (packet / "compiled.json").write_text("{}", encoding="utf-8")
    (packet / "manifest.json").write_text("{}", encoding="utf-8")
    (packet / "policy.json").write_text(json.dumps({
        "schema": 1, "version": "live", "mode": "LIVE", "episode_cap_usd": "1", "month_cap_usd": "1",
        "experiment_cap_usd": "1", "allowed_providers": ["https://example.invalid"],
        "publication": {"enabled": True, "channel": "x", "visibility": "private"},
    }), encoding="utf-8")
    code, blocked = run_cli("start", "--workspace", str(tmp_path / "workspace"), "--compiled-input", str(packet))
    assert code == 1 and blocked["status"] == "BLOCKED"
