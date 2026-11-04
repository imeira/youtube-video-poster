"""Spawned host acceptance for the existing durable TEST worker."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.compiled import compile_storyboard
from src.hybrid.worker import EpisodeTaskQueue

ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)


def _packet(tmp_path: Path) -> Path:
    packet = tmp_path / "packet"
    packet.mkdir()
    audio = packet / "audio.wav"
    subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000:duration=1", "-ac", "2", str(audio),
    ], check=True)
    reference = packet / "reference.png"
    Image.new("RGB", (64, 64), "navy").save(reference)
    frozen = FrozenAsset.approve(reference, "TEST fixture", "TEST")
    sheet = packet / "sheet.png"
    contact_sheet((frozen,), sheet)
    Manifest.freeze((frozen,), FrozenAsset.approve(sheet, "TEST fixture", "TEST"), "TEST fixture", "TEST").save(packet / "manifest.json")
    compile_storyboard("TEST-HOST-RECOVERY", FrozenAsset.approve(audio, "TEST fixture", "TEST"), [{"scene_id": "R001", "start": 0, "end": 1, "image_prompt": "céu", "action": "céu"}]).save(packet / "compiled.json")
    (packet / "policy.json").write_text(json.dumps({
        "schema": 1, "version": "compiled-test-internal-v1", "mode": "TEST",
        "episode_cap_usd": "1.00", "month_cap_usd": "1.00", "experiment_cap_usd": "1.00",
        "allowed_providers": ["test-local-fixture"], "publication": {"enabled": False},
    }), encoding="utf-8")
    return packet


def _cli(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(PYTHON), "-m", "src.cli.main", "autonomous", *args],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": "", **(env or {})},
        capture_output=True, text=True, check=False,
    )


def _json(result: subprocess.CompletedProcess[str]) -> dict:
    return json.loads(result.stdout)


def test_host_spawn_recovers_checkpointed_fixture_once_and_waits_at_gate(tmp_path):
    """A killed host recovers the checkpointed TEST ID without another POST."""
    workspace = tmp_path / "workspace"
    started = _cli("start", "--workspace", str(workspace), "--compiled-input", str(_packet(tmp_path)))
    assert started.returncode == 0, started.stderr
    request_id = _json(started)["request_id"]
    assert _cli("enqueue", "--workspace", str(workspace), "--request-id", request_id).returncode == 0

    first = _cli(
        "host", "--workspace", str(workspace), "--once", "--lease-ttl-seconds", "1",
        "--test-crash-after-checkpoint",
    )
    assert first.returncode == 86
    episode_id = "TEST-HOST-RECOVERY"
    queue_path = workspace / "TEST" / "worker.sqlite3"
    queue = EpisodeTaskQueue(queue_path)
    checkpointed = queue.status(episode_id)
    assert checkpointed["status"] == "RUNNING"
    root = workspace / "TC" / request_id[:16]
    # The executor is the durable checkpoint evidence; the worker died after it.
    from src.hybrid.execution import Executor
    from src.hybrid.planner import Config
    with sqlite3.connect(root / "executor.sqlite3") as db:
        request_id = db.execute("SELECT id FROM jobs").fetchone()[0]
    checkpoint = Executor(root / "executor.sqlite3", Config(limit=__import__("decimal").Decimal("1.00"))).inspect(request_id)
    assert checkpoint["provider_id"].startswith("TEST-")
    assert checkpoint["status"] == "SUBMITTED"

    with sqlite3.connect(queue.leases.database) as db:
        expires_at = db.execute("SELECT expires_at FROM episode_leases WHERE episode_id=?", (episode_id,)).fetchone()[0]
    while time.time() <= expires_at:
        time.sleep(0.01)

    second = _cli("host", "--workspace", str(workspace), "--once", "--lease-ttl-seconds", "1")
    assert second.returncode == 0, second.stderr
    resumed = _json(second)
    assert resumed["state"] == "WAITING_THUMBNAIL_APPROVAL"
    final = queue.status(episode_id)
    assert final["status"] == "WAITING"
    assert final["next_action"] == "HUMAN_THUMBNAIL"
    assert final["worker_id"] != checkpointed["worker_id"]
    assert int(final["worker_id"].rsplit("-", 1)[1]) != int(checkpointed["worker_id"].rsplit("-", 1)[1])
    counter = json.loads((root / "provider" / "fixture-post-count.json").read_text(encoding="utf-8"))
    assert counter == {"posts": 1}
    completed = Executor(root / "executor.sqlite3", Config(limit=__import__("decimal").Decimal("1.00"))).inspect(request_id)
    assert completed["status"] == "COMPLETE"
    assert completed["provider_id"] == checkpoint["provider_id"]


def test_host_rejects_live_and_unknown_recovery_never_resubmits(tmp_path):
    workspace = tmp_path / "workspace"
    rejected = _cli("host", "--workspace", str(workspace), "--host-mode", "LIVE")
    assert rejected.returncode != 0
    assert "TEST" in rejected.stderr

    queue = EpisodeTaskQueue(workspace / "TEST" / "worker.sqlite3")
    queue.enqueue("TEST-UNKNOWN", str(workspace), "fixture")
    now = time.time()
    lease = queue.leases.acquire("TEST-UNKNOWN", "dead-host:run", now=now, ttl_seconds=1)
    queue.claim("TEST-UNKNOWN", "dead-host", lease, now=now)
    queue.unknown("TEST-UNKNOWN", "dead-host", lease)
    with sqlite3.connect(queue.leases.database) as db:
        db.execute("UPDATE episode_leases SET expires_at=0 WHERE episode_id='TEST-UNKNOWN'")
    idle = _cli("host", "--workspace", str(workspace), "--once")
    assert idle.returncode == 0
    assert _json(idle)["status"] == "IDLE"
    assert queue.status("TEST-UNKNOWN")["status"] == "UNKNOWN_REQUIRES_RECONCILIATION"
