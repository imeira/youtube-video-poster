"""Fresh-process compiled worker recovery and owned-gate contracts."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from decimal import Decimal
from pathlib import Path

import pytest

from src.hybrid.autonomous import (
    AutonomousPolicy,
    AutonomousTwoGateRunner,
    RunnerOwnership,
)
from src.hybrid.execution import Executor
from src.hybrid.planner import Config
from src.hybrid.standing_budget import StandingBudgetLedger

ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)


def _policy() -> AutonomousPolicy:
    return AutonomousPolicy.from_dict({"schema": 1, "version": "worker-owned-test-v1", "mode": "TEST", "episode_cap_usd": "1.00", "month_cap_usd": "1.00", "experiment_cap_usd": "1.00", "allowed_providers": ["test-local-fixture"], "publication": {"enabled": False}})


def _runner(tmp_path):
    policy = _policy()
    ledger = StandingBudgetLedger(tmp_path / "standing.sqlite3", policy_sha256=policy.digest, episode_limit_usd=policy.episode_cap_usd, month_limit_usd=policy.month_cap_usd, experiment_limit_usd=policy.experiment_cap_usd, mode="TEST")
    return AutonomousTwoGateRunner(tmp_path / "runner", "TEST-COMPILED-OWNED", policy, Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal("1.00"))), ledger, ownership_assertion=lambda ownership: ownership.fencing_token == 9)


def _cli(*args: str) -> tuple[int, dict]:
    completed = subprocess.run([str(PYTHON), "-m", "src.cli.main", "autonomous", *args], cwd=ROOT, env={**os.environ, "PYTHONPATH": ""}, capture_output=True, text=True, check=False)
    return completed.returncode, json.loads(completed.stdout)


def _packet(tmp_path: Path) -> Path:
    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.compiled import compile_storyboard

    packet = tmp_path / "packet"
    packet.mkdir()
    audio = packet / "audio.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2", "-ac", "2", str(audio)], check=True)
    reference = packet / "reference.png"
    Image.new("RGB", (64, 64), "navy").save(reference)
    frozen = FrozenAsset.approve(reference, "TEST fixture", "TEST")
    sheet = packet / "sheet.png"
    contact_sheet((frozen,), sheet)
    Manifest.freeze((frozen,), FrozenAsset.approve(sheet, "TEST fixture", "TEST"), "TEST fixture", "TEST").save(packet / "manifest.json")
    compile_storyboard("TEST-COMPILED-RECOVERY", FrozenAsset.approve(audio, "TEST fixture", "TEST"), [{"scene_id": "R001", "start": 0, "end": 2, "image_prompt": "céu", "action": "céu"}]).save(packet / "compiled.json")
    (packet / "policy.json").write_text(json.dumps({"schema": 1, "version": "compiled-test-internal-v1", "mode": "TEST", "episode_cap_usd": "1.00", "month_cap_usd": "1.00", "experiment_cap_usd": "1.00", "allowed_providers": ["test-local-fixture"], "publication": {"enabled": False}}), encoding="utf-8")
    return packet


def test_owned_human_gate_api_requires_live_runner_ownership(tmp_path):
    runner = _runner(tmp_path)
    ownership = RunnerOwnership("TEST-COMPILED-OWNED", "worker-a", 9)
    with pytest.raises(RuntimeError, match="has not been started"):
        runner.record_human_gate_owned("thumbnail", tmp_path / "missing.png", approved=True, reviewer="test", ownership=ownership)
    with pytest.raises(PermissionError, match="fenced ownership"):
        runner.record_human_gate("thumbnail", tmp_path / "missing.png", approved=True, reviewer="test")


def test_compiled_worker_cli_fresh_process_two_gates_terminal_once(tmp_path):
    """Real CLI/handler processes use one provider POST and one Director encode."""
    workspace = tmp_path / "workspace"
    code, started = _cli("start", "--workspace", str(workspace), "--compiled-input", str(_packet(tmp_path)))
    assert code == 0, started
    request_id = started["request_id"]
    root = workspace / "TC" / request_id[:16]
    assert _cli("enqueue", "--workspace", str(workspace), "--request-id", request_id)[1]["status"] == "PENDING"
    code, thumbnail_wait = _cli("run", "--once", "--workspace", str(workspace))
    assert code == 0 and thumbnail_wait["state"] == "WAITING_THUMBNAIL_APPROVAL"
    provider_outputs = sorted((root / "provider").glob("*.TEST.png"))
    assert len(provider_outputs) == 1
    completion = workspace / "episodes" / "TEST-COMPILED-RECOVERY" / "qa" / "renderer_completion_receipt.json"
    assert completion.is_file()
    completion_bytes = completion.read_bytes()
    code, thumbnail = _cli("approve", "--workspace", str(workspace), "--request-id", request_id, "--kind", "thumbnail", "--test-approval")
    assert code == 0, thumbnail
    code, video_wait = _cli("run", "--once", "--workspace", str(workspace))
    assert code == 0 and video_wait["state"] == "WAITING_VIDEO_APPROVAL"
    assert sorted((root / "provider").glob("*.TEST.png")) == provider_outputs
    assert completion.read_bytes() == completion_bytes
    code, ready = _cli("approve", "--workspace", str(workspace), "--request-id", request_id, "--kind", "video", "--test-approval")
    assert code == 0, ready
    code, done = _cli("run", "--once", "--workspace", str(workspace))
    assert code == 0 and done["status"] == "DONE"
    code, idle = _cli("run", "--once", "--workspace", str(workspace))
    assert code == 0 and idle["status"] == "IDLE"
    assert sorted((root / "provider").glob("*.TEST.png")) == provider_outputs
    assert completion.read_bytes() == completion_bytes


@pytest.mark.asyncio
async def test_cancelled_slow_director_encode_becomes_unknown_and_fresh_worker_stays_idle(
    tmp_path, monkeypatch
):
    """A detached Director encode never loses its reconciliation barrier."""
    from src.hybrid import autonomous_cli
    from src.hybrid.render import LocalRenderer
    from src.hybrid.worker import DurableEpisodeWorker, EpisodeTaskQueue

    workspace = tmp_path / "workspace"
    autonomous_cli.start_compiled(workspace, _packet(tmp_path), None)
    episode_id = "TEST-COMPILED-RECOVERY"
    queue = EpisodeTaskQueue(workspace / "TEST" / "worker.sqlite3")
    queue.enqueue(episode_id, str(workspace), "compiled")

    entered, release = threading.Event(), threading.Event()
    render_calls: list[float] = []
    original_render = LocalRenderer.render_compiled

    def slow_render(self, *args, **kwargs):
        render_calls.append(time.monotonic())
        entered.set()
        assert release.wait(10), "test must release detached encode"
        return original_render(self, *args, **kwargs)

    monkeypatch.setattr(LocalRenderer, "render_compiled", slow_render)
    renewals: list[float] = []
    original_renew = queue.leases.renew

    def observe_renew(*args, **kwargs):
        renewals.append(time.monotonic())
        return original_renew(*args, **kwargs)

    monkeypatch.setattr(queue.leases, "renew", observe_renew)
    first = DurableEpisodeWorker(
        queue, "first", autonomous_cli._fixture_worker_handler,
        lease_ttl_seconds=30, poll_seconds=0.01, shutdown_deadline_seconds=0.05,
    )
    running = asyncio.create_task(first.run_once())
    assert await asyncio.to_thread(entered.wait, 10)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert queue.status(episode_id)["status"] == "UNKNOWN_REQUIRES_RECONCILIATION"
    renewals_at_unknown = len(renewals)

    # Force the persisted lease past its TTL without waking reconciliation.  The
    # fresh CLI process must still find UNKNOWN ineligible, and the stopped
    # heartbeat must not write another renewal while the detached encode runs.
    with sqlite3.connect(queue.leases.database) as db:
        db.execute(
            "UPDATE episode_leases SET expires_at=? WHERE episode_id=?",
            (time.time() - 1, episode_id),
        )
    await asyncio.sleep(0.2)
    assert len(renewals) == renewals_at_unknown
    code, idle = await asyncio.to_thread(
        _cli, "run", "--once", "--workspace", str(workspace)
    )
    assert code == 0, idle
    assert idle["status"] == "IDLE"
    assert len(render_calls) == 1

    # Reap the intentionally detached encode; it must not advance the queue.
    release.set()
    completion = workspace / "episodes" / episode_id / "qa" / "renderer_completion_receipt.json"
    deadline = time.monotonic() + 30
    while not completion.is_file() and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    assert completion.is_file()
    assert queue.status(episode_id)["status"] == "UNKNOWN_REQUIRES_RECONCILIATION"