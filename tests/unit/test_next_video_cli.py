"""Public, generic, offline acceptance coverage for ``studio next-video``."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)


def run_next_video(*args: str) -> tuple[int, dict]:
    completed = subprocess.run(
        [str(PYTHON), "-m", "src.cli.main", "next-video", *args],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": ""},
        text=True,
        capture_output=True,
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


def compiled_packet(root: Path) -> Path:
    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.compiled import compile_storyboard

    root.mkdir()
    audio = root / "audio.wav"
    subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000:duration=2", "-ac", "2", str(audio),
    ], check=True)
    reference = root / "reference.png"
    Image.new("RGB", (64, 64), "navy").save(reference)
    asset = FrozenAsset.approve(reference, "generic TEST fixture", "TEST")
    sheet = root / "sheet.png"
    contact_sheet((asset,), sheet)
    Manifest.freeze(
        (asset,), FrozenAsset.approve(sheet, "generic TEST fixture", "TEST"),
        "generic TEST fixture", "TEST",
    ).save(root / "manifest.json")
    compile_storyboard(
        "TEST-GENERIC-NEXT-VIDEO", FrozenAsset.approve(audio, "generic TEST fixture", "TEST"),
        [{"scene_id": "S001", "start": 0, "end": 2, "image_prompt": "generic sky", "action": "generic sky"}],
    ).save(root / "compiled.json")
    from src.hybrid.autonomous_cli import compiled_policy_document

    (root / "policy.json").write_text(
        json.dumps(compiled_policy_document()), encoding="utf-8"
    )
    return root


def test_next_video_public_start_worker_resume_status_and_offline_gates(tmp_path):
    workspace, packet = tmp_path / "workspace", compiled_packet(tmp_path / "packet")
    code, started = run_next_video("start", "--workspace", str(workspace), "--compiled-input", str(packet))
    assert code == 0, started
    assert started["mode"] == "TEST" and started["concurrency"] == 1
    assert started["queued"]["status"] == "PENDING"
    request_id = started["request_id"]

    code, worked = run_next_video("worker", "--once", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 0, worked
    assert worked["status"] == "WAITING" and worked["next_action"] == "HUMAN_THUMBNAIL"

    queue_files_before = {p.relative_to(workspace): p.read_bytes() for p in (workspace / "TEST").rglob("*") if p.is_file()}
    code, status = run_next_video("status", "--workspace", str(workspace), "--request-id", request_id)
    queue_files_after = {p.relative_to(workspace): p.read_bytes() for p in (workspace / "TEST").rglob("*") if p.is_file()}
    assert code == 0, status
    assert status["queue"]["snapshot"] == "CURRENT"
    assert status["runner"]["state"] == "WAITING_THUMBNAIL_APPROVAL"
    assert queue_files_before == queue_files_after

    code, resumed = run_next_video("resume", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 0, resumed
    assert resumed["queue"]["next_action"] == "HUMAN_THUMBNAIL"
    assert resumed["publication"] == "DISABLED"


def test_next_video_denies_live_and_concurrency_mutation_before_queue_write(tmp_path):
    workspace, packet = tmp_path / "workspace", compiled_packet(tmp_path / "packet")
    code, live = run_next_video("start", "--workspace", str(workspace), "--compiled-input", str(packet), "--mode", "LIVE")
    assert code == 1 and live["status"] == "BLOCKED"
    assert not (workspace / "TEST" / "worker.sqlite3").exists()

    code, started = run_next_video("start", "--workspace", str(workspace), "--compiled-input", str(packet), "--concurrency", "2")
    assert code == 0, started
    queue_before = (workspace / "TEST" / "worker.sqlite3").read_bytes()
    code, changed = run_next_video("resume", "--workspace", str(workspace), "--request-id", started["request_id"], "--concurrency", "1")
    assert code == 1 and changed["status"] == "BLOCKED"
    assert (workspace / "TEST" / "worker.sqlite3").read_bytes() == queue_before


def test_next_video_denies_production_root_descendants_and_resolved_alias_before_state_access(tmp_path, monkeypatch):
    from src.hybrid import next_video

    packet = compiled_packet(tmp_path / "packet")
    for protected in ("C:/HermesStudio/production", r"C:\\HermesStudio\\production\\child"):
        with pytest.raises(ValueError, match="production root"):
            next_video.start(Path(protected), packet, mode="TEST", concurrency=1)
    # Model a native junction resolving from a scratch spelling into production.
    monkeypatch.setattr(next_video.Path, "resolve", lambda self: Path("C:/HermesStudio/production/alias"))
    with pytest.raises(ValueError, match="production root"):
        next_video._workspace(tmp_path / "junction-to-production")


def test_next_video_status_uses_fenced_queue_snapshot_not_forged_runner_state(tmp_path):
    workspace, packet = tmp_path / "workspace", compiled_packet(tmp_path / "packet")
    code, started = run_next_video("start", "--workspace", str(workspace), "--compiled-input", str(packet))
    assert code == 0, started
    request_id = started["request_id"]
    assert run_next_video("worker", "--once", "--workspace", str(workspace), "--request-id", request_id)[0] == 0
    runner_path = workspace / "TC" / request_id[:16] / "runner" / "runner.json"
    runner_path.write_text('{"state":"PUBLISHED"}', encoding="utf-8")
    before = {p.relative_to(workspace): p.read_bytes() for p in (workspace / "TEST").rglob("*") if p.is_file()}
    code, projected = run_next_video("status", "--workspace", str(workspace), "--request-id", request_id)
    after = {p.relative_to(workspace): p.read_bytes() for p in (workspace / "TEST").rglob("*") if p.is_file()}
    assert code == 0, projected
    assert projected["runner"]["state"] == "WAITING_THUMBNAIL_APPROVAL"
    assert projected["runner"]["fencing_token"] == projected["queue"]["fencing_token"]
    assert projected["runner"]["snapshot"] == "CURRENT"
    assert projected["publication"] == "DISABLED"
    assert before == after


def test_next_video_status_fails_closed_when_snapshot_missing_or_invalid(tmp_path):
    workspace, packet = tmp_path / "workspace", compiled_packet(tmp_path / "packet")
    code, started = run_next_video("start", "--workspace", str(workspace), "--compiled-input", str(packet))
    assert code == 0, started
    snapshot = workspace / "TEST" / "worker.snapshot.json"
    snapshot.unlink()
    code, projected = run_next_video("status", "--workspace", str(workspace), "--request-id", started["request_id"])
    assert code == 0, projected
    assert projected["runner"] == {"status": "UNVERIFIED", "snapshot": "STALE"}
    assert projected["publication"] == "DISABLED"
