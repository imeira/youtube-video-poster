"""Public, generic, offline acceptance coverage for ``studio next-video``."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from hashlib import sha256
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


def run_crashing_public_start(workspace: Path, packet: Path, checkpoint: str) -> subprocess.CompletedProcess:
    """Crash a public API call without adding a production CLI test switch."""
    program = """
import os
from pathlib import Path
from src.hybrid import autonomous_cli, next_video
checkpoint = os.environ.pop('NEXT_VIDEO_TEST_CHECKPOINT')
def crash(name):
    if name == checkpoint:
        os._exit(93)
autonomous_cli._start_checkpoint = crash
next_video._start_checkpoint = crash
next_video.start(Path(os.environ.pop('NEXT_VIDEO_TEST_WORKSPACE')), Path(os.environ.pop('NEXT_VIDEO_TEST_PACKET')), mode='TEST', concurrency=1)
"""
    return subprocess.run(
        [str(PYTHON), "-c", program], cwd=ROOT,
        env={**os.environ, "PYTHONPATH": "", "NEXT_VIDEO_TEST_CHECKPOINT": checkpoint,
             "NEXT_VIDEO_TEST_WORKSPACE": str(workspace), "NEXT_VIDEO_TEST_PACKET": str(packet)},
        text=True, capture_output=True, check=False,
    )


def queue_row_count(workspace: Path) -> int:
    with sqlite3.connect(workspace / "TEST" / "worker.sqlite3") as db:
        return db.execute("SELECT COUNT(*) FROM episode_tasks").fetchone()[0]


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
    code, conflicting_start = run_next_video("start", "--workspace", str(workspace), "--compiled-input", str(packet), "--concurrency", "1")
    assert code == 1 and conflicting_start["status"] == "BLOCKED"
    assert (workspace / "TEST" / "worker.sqlite3").read_bytes() == queue_before
    code, changed = run_next_video("resume", "--workspace", str(workspace), "--request-id", started["request_id"], "--concurrency", "1")
    assert code == 1 and changed["status"] == "BLOCKED"
    assert (workspace / "TEST" / "worker.sqlite3").read_bytes() == queue_before


@pytest.mark.parametrize("checkpoint", ("after_mkdir", "after_runner", "after_metadata", "after_queue"))
def test_next_video_crashed_start_recovers_exact_packet_once_without_duplicate_queue(tmp_path, checkpoint):
    workspace, packet = tmp_path / "workspace", compiled_packet(tmp_path / "packet")
    crashed = run_crashing_public_start(workspace, packet, checkpoint)
    assert crashed.returncode == 93, (crashed.stdout, crashed.stderr)

    code, started = run_next_video("start", "--workspace", str(workspace), "--compiled-input", str(packet))
    assert code == 0, started
    code, replayed = run_next_video("start", "--workspace", str(workspace), "--compiled-input", str(packet))
    assert code == 0, replayed
    assert replayed["request_id"] == started["request_id"]
    assert queue_row_count(workspace) == 1
    code, resumed = run_next_video("resume", "--workspace", str(workspace), "--request-id", started["request_id"])
    assert code == 0, resumed


def test_next_video_preexisting_namespace_without_intent_fails_closed_and_preserves_it(tmp_path):
    workspace, packet = tmp_path / "workspace", compiled_packet(tmp_path / "packet")
    from src.hybrid import autonomous_cli

    packet_bytes = b"autonomous-compiled-test-v1\0" + b"".join(
        (packet / name).read_bytes() for name in autonomous_cli.COMPILED_PACKET_FILES
    )
    root = workspace / "TC" / sha256(packet_bytes).hexdigest()[:16]
    root.mkdir(parents=True)
    sentinel = root / "do-not-overwrite"
    sentinel.write_text("preserve", encoding="utf-8")
    code, rejected = run_next_video("start", "--workspace", str(workspace), "--compiled-input", str(packet))
    assert code == 1, rejected
    assert "legacy namespace" in rejected["error"]
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_next_video_two_fresh_cli_starts_share_one_request_and_queue_row(tmp_path):
    workspace, packet = tmp_path / "workspace", compiled_packet(tmp_path / "packet")
    command = [str(PYTHON), "-m", "src.cli.main", "next-video", "start", "--workspace", str(workspace), "--compiled-input", str(packet)]
    environment = {**os.environ, "PYTHONPATH": ""}
    first = subprocess.Popen(command, cwd=ROOT, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    second = subprocess.Popen(command, cwd=ROOT, env=environment, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    results = [process.communicate(timeout=120) for process in (first, second)]
    assert first.returncode == second.returncode == 0, results
    documents = [json.loads(stdout) for stdout, _stderr in results]
    assert documents[0]["request_id"] == documents[1]["request_id"]
    assert queue_row_count(workspace) == 1


def test_next_video_denies_production_root_descendants_and_resolved_alias_before_state_access(tmp_path, monkeypatch):
    from src.hybrid import next_video

    packet = compiled_packet(tmp_path / "packet")
    for protected in (
        "C:/HermesStudio/production",
        r"C:\\HermesStudio\\production\\child",
        "C://HermesStudio//production//child",
        r"c:\\HermesStudio\\production\\child\\..\\child",
    ):
        with pytest.raises(ValueError, match="production root"):
            next_video.start(Path(protected), packet, mode="TEST", concurrency=1)
    for unsupported in (r"\\server\\share", r"\\?\\C:\\HermesStudio\\production", "C:relative"):
        with pytest.raises(ValueError, match="Windows path"):
            next_video._workspace(unsupported)
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


def test_next_video_worker_rejects_forged_terminal_runner_before_done_projection(tmp_path):
    workspace, packet = tmp_path / "workspace", compiled_packet(tmp_path / "packet")
    code, started = run_next_video("start", "--workspace", str(workspace), "--compiled-input", str(packet))
    assert code == 0, started
    request_id = started["request_id"]
    runner_path = workspace / "TC" / request_id[:16] / "runner" / "runner.json"
    document = json.loads(runner_path.read_text(encoding="utf-8"))
    document["state"] = "PUBLISHED"
    runner_path.write_text(json.dumps(document), encoding="utf-8")

    code, rejected = run_next_video("worker", "--once", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 1, rejected
    assert "terminal publication state" in rejected["error"]
    code, projected = run_next_video("status", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 0, projected
    assert projected["queue"]["status"] == "PENDING"
    assert projected["queue"]["next_action"] == "RESUME"
    assert projected["queue"]["state"] != "PUBLISHED"


def test_next_video_worker_rejects_forged_ready_terminal_before_done_projection(tmp_path):
    workspace, packet = tmp_path / "workspace", compiled_packet(tmp_path / "packet")
    code, started = run_next_video("start", "--workspace", str(workspace), "--compiled-input", str(packet))
    assert code == 0, started
    request_id = started["request_id"]
    runner_path = workspace / "TC" / request_id[:16] / "runner" / "runner.json"
    document = json.loads(runner_path.read_text(encoding="utf-8"))
    document["state"] = "READY_TO_PUBLISH"
    runner_path.write_text(json.dumps(document), encoding="utf-8")

    code, rejected = run_next_video("worker", "--once", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 1, rejected
    assert "durable gate evidence" in rejected["error"]
    assert queue_row_count(workspace) == 1
    code, projected = run_next_video("status", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 0, projected
    assert projected["queue"]["status"] == "PENDING"


def test_next_video_worker_rejects_shape_valid_matching_hash_gate_forgery(tmp_path):
    """A caller-controlled runner document cannot mint the two TEST gates."""
    workspace, packet = tmp_path / "workspace", compiled_packet(tmp_path / "packet")
    code, started = run_next_video("start", "--workspace", str(workspace), "--compiled-input", str(packet))
    assert code == 0, started
    request_id = started["request_id"]
    assert run_next_video("worker", "--once", "--workspace", str(workspace), "--request-id", request_id)[0] == 0
    runner_path = workspace / "TC" / request_id[:16] / "runner" / "runner.json"
    document = json.loads(runner_path.read_text(encoding="utf-8"))
    document["state"] = "READY_TO_PUBLISH"
    document["gates"] = {
        kind: {
            "artifact_path": media["path"], "artifact_sha256": media["sha256"],
            "approved": True, "reviewer": "forged matching hash",
        }
        for kind, media in document["media"].items()
    }
    runner_path.write_text(json.dumps(document), encoding="utf-8")

    code, rejected = run_next_video("worker", "--once", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 1, rejected
    code, projected = run_next_video("status", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 0, projected
    assert projected["queue"]["status"] == "WAITING"
    assert projected["queue"]["next_action"] == "HUMAN_THUMBNAIL"


def test_next_video_real_hash_bound_gates_reach_disabled_publication_terminal(tmp_path):
    workspace, packet = tmp_path / "workspace", compiled_packet(tmp_path / "packet")
    code, started = run_next_video("start", "--workspace", str(workspace), "--compiled-input", str(packet))
    assert code == 0, started
    request_id = started["request_id"]
    assert run_next_video("worker", "--once", "--workspace", str(workspace), "--request-id", request_id)[0] == 0
    from src.hybrid.autonomous_cli import _approve_compiled_gate

    _approve_compiled_gate(workspace, request_id, "thumbnail")
    assert run_next_video("worker", "--once", "--workspace", str(workspace), "--request-id", request_id)[0] == 0
    _approve_compiled_gate(workspace, request_id, "video")
    code, terminal = run_next_video("worker", "--once", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 0, terminal
    assert terminal["status"] == "DONE"
    code, projected = run_next_video("status", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 0, projected
    assert projected["queue"]["state"] == "READY_TO_PUBLISH"
    assert projected["publication"] == "DISABLED"


def test_next_video_terminal_rejects_gate_receipt_with_foreign_episode_or_artifact(tmp_path):
    workspace, packet = tmp_path / "workspace", compiled_packet(tmp_path / "packet")
    code, started = run_next_video("start", "--workspace", str(workspace), "--compiled-input", str(packet))
    assert code == 0, started
    request_id = started["request_id"]
    assert run_next_video("worker", "--once", "--workspace", str(workspace), "--request-id", request_id)[0] == 0
    from src.hybrid.autonomous_cli import _approve_compiled_gate

    _approve_compiled_gate(workspace, request_id, "thumbnail")
    assert run_next_video("worker", "--once", "--workspace", str(workspace), "--request-id", request_id)[0] == 0
    _approve_compiled_gate(workspace, request_id, "video")
    receipt_path = workspace / "TC" / request_id[:16] / "gate-receipts" / "video.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["episode_id"] = "TEST-FOREIGN"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")

    code, rejected = run_next_video("worker", "--once", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 1, rejected
    assert "gate receipt" in rejected["error"]
    code, projected = run_next_video("status", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 0, projected
    assert projected["queue"]["status"] == "PENDING"
    assert projected["queue"]["next_action"] == "RESUME"


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
