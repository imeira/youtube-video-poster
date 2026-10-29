"""Fresh-process CLI acceptance coverage for the TEST-only two-gate harness."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

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


def test_cli_worker_runs_existing_fixture_once_through_explicit_two_gates(tmp_path):
    """The durable worker resumes a real runner; gates require separate CLI approvals."""
    from decimal import Decimal

    from src.hybrid.autonomous_cli import manifest, packet, runner
    from src.hybrid.execution import Job

    workspace = tmp_path / "workspace"
    raw = b"Poste um video no @test\nIdioma: pt-BR\nPlaylist: TEST\nTema: worker fixture\n"
    request_id = sha256(b"worker-cli-fixture\0" + raw).hexdigest()
    root = workspace / "TEST" / request_id
    root.mkdir(parents=True)
    (root / "prompt.txt").write_bytes(raw)
    request = {"channel": "@test", "language": "pt-BR", "playlist": "TEST", "theme": "worker fixture"}
    job = Job("TEST-WORKER-S001", "first", "TEST", "test-local-fixture", {"fixture": "local-only"}, manifest(root), Decimal("0.01"))
    run = runner(root, request_id)
    run.start(packet(raw, request), [job])
    (root / "harness.json").write_text(json.dumps({"request_id": request_id, "prompt_sha256": sha256(raw).hexdigest(), "request": request}), encoding="utf-8")

    code, queued = run_cli("enqueue", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 0, queued
    code, first = run_cli("run", "--once", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 0, first
    assert first["status"] == "WAITING"
    assert first["next_action"] == "HUMAN_THUMBNAIL"
    assert (root / "thumbnail.TEST.png").is_file()
    assert (root / "video.TEST.mp4").is_file()

    code, thumbnail = run_cli("approve", "--workspace", str(workspace), "--request-id", request_id, "--kind", "thumbnail", "--test-approval")
    assert code == 0, thumbnail
    code, second = run_cli("run", "--once", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 0, second
    assert second["status"] == "WAITING"
    assert second["next_action"] == "HUMAN_VIDEO"
    code, video = run_cli("approve", "--workspace", str(workspace), "--request-id", request_id, "--kind", "video", "--test-approval")
    assert code == 0, video
    code, final = run_cli("run", "--once", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 0, final
    assert final["status"] == "DONE"
    assert final["state"] == "READY_TO_PUBLISH"


def test_cli_worker_status_reads_writer_snapshot_without_touching_queue_files(tmp_path):
    """Status is a JSON projection, not SQLite bootstrap or a live WAL read."""
    from src.hybrid.worker import EpisodeTaskQueue

    workspace = tmp_path / "workspace"
    request_id = "a" * 64
    root = workspace / "TEST" / request_id
    root.mkdir(parents=True)
    (root / "runner.json").write_text(json.dumps({"episode_id": "TEST-SNAPSHOT"}), encoding="utf-8")
    queue = EpisodeTaskQueue(workspace / "TEST" / "worker.sqlite3")
    queue.enqueue("TEST-SNAPSHOT", str(root), "fixture")
    before = {
        path.relative_to(workspace): path.read_bytes()
        for path in workspace.rglob("*") if path.is_file()
    }

    code, result = run_cli(
        "worker-status", "--workspace", str(workspace), "--request-id", request_id
    )
    after = {
        path.relative_to(workspace): path.read_bytes()
        for path in workspace.rglob("*") if path.is_file()
    }

    assert code == 0, result
    assert result["episode_id"] == "TEST-SNAPSHOT"
    assert result["status"] == "PENDING"
    assert result["snapshot"] == "CURRENT"
    assert "root" not in result
    assert before == after


def test_cli_compiled_test_packet_is_snapshot_only_and_rejects_mutation(tmp_path):
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
        "schema": 1, "version": "compiled-test-internal-v1", "mode": "TEST", "episode_cap_usd": "1.00",
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
    request_id = rejected["request_id"]
    code, rejected = run_cli("resume", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 1 and rejected["status"] == "BLOCKED"
    assert "owned queue worker" in rejected["error"]
    runner_root = workspace / "TC" / request_id[:16] / "runner"

    before = {path.relative_to(runner_root): path.read_bytes() for path in runner_root.rglob("*") if path.is_file()}
    code, compiled_status = run_cli("status", "--workspace", str(workspace), "--request-id", request_id)
    after = {path.relative_to(runner_root): path.read_bytes() for path in runner_root.rglob("*") if path.is_file()}
    assert code == 0, compiled_status
    assert compiled_status["operational_metrics"]["status"] == "CURRENT"
    assert before == after

    for action, extra in (("remediate", ("--correction", "R001=clear sky")), ("heroes", ())):
        code, blocked = run_cli(action, "--workspace", str(workspace), "--request-id", request_id, *extra)
        assert code == 1 and blocked["status"] == "BLOCKED"
        assert "owned queue worker" in blocked["error"]
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


def test_cli_compiled_packet_worker_reaches_both_gates_without_repeat_post(tmp_path):
    """The owned queue uses Director render once and resumes only from gates."""
    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.compiled import compile_storyboard

    packet = tmp_path / "packet"
    packet.mkdir()
    audio = packet / "audio.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=48000:duration=2", "-ac", "2", str(audio)],
        check=True,
    )
    reference = packet / "reference.png"
    Image.new("RGB", (64, 64), "navy").save(reference)
    frozen = FrozenAsset.approve(reference, "TEST fixture", "TEST")
    sheet = packet / "sheet.png"
    contact_sheet((frozen,), sheet)
    Manifest.freeze((frozen,), FrozenAsset.approve(sheet, "TEST fixture", "TEST"), "TEST fixture", "TEST").save(packet / "manifest.json")
    compile_storyboard(
        "TEST-COMPILED-WORKER", FrozenAsset.approve(audio, "TEST fixture", "TEST"),
        [{"scene_id": "R001", "start": 0, "end": 2, "image_prompt": "céu", "action": "céu"}],
    ).save(packet / "compiled.json")
    (packet / "policy.json").write_text(json.dumps({
        "schema": 1, "version": "compiled-test-internal-v1", "mode": "TEST",
        "episode_cap_usd": "1.00", "month_cap_usd": "1.00", "experiment_cap_usd": "1.00",
        "allowed_providers": ["test-local-fixture"], "publication": {"enabled": False},
    }), encoding="utf-8")
    workspace = tmp_path / "workspace"

    code, started = run_cli("start", "--workspace", str(workspace), "--compiled-input", str(packet))
    assert code == 0, started
    assert started["state"] == "READY"
    assert started["artifacts"] == {}
    request_id = started["request_id"]
    root = workspace / "TC" / request_id[:16]
    assert not list((root / "provider").glob("*.TEST.png"))

    code, queued = run_cli("enqueue", "--workspace", str(workspace), "--request-id", request_id)
    assert code == 0, queued
    assert queued["status"] == "PENDING"
    code, first = run_cli("run", "--once", "--workspace", str(workspace))
    assert code == 0, first
    assert first["state"] == "WAITING_THUMBNAIL_APPROVAL"
    provider = list((root / "provider").glob("*.TEST.png"))
    assert len(provider) == 1
    assert (workspace / "episodes" / "TEST-COMPILED-WORKER" / "renders" / "final.mp4").is_file()

    code, approved = run_cli("approve", "--workspace", str(workspace), "--request-id", request_id, "--kind", "thumbnail", "--test-approval")
    assert code == 0, approved
    code, second = run_cli("run", "--once", "--workspace", str(workspace))
    assert code == 0, second
    assert second["state"] == "WAITING_VIDEO_APPROVAL"
    assert list((root / "provider").glob("*.TEST.png")) == provider
    assert len(list((workspace / "episodes" / "TEST-COMPILED-WORKER" / "qa").glob("*render*json"))) == 3

    for action, extra in (("resume", ()), ("remediate", ("--correction", "R001=clear sky")), ("heroes", ())):
        code, blocked = run_cli(action, "--workspace", str(workspace), "--request-id", request_id, *extra)
        assert code == 1 and "owned queue worker" in blocked["error"]


def test_forged_compiled_queue_task_fails_closed_before_root_access():
    """A legacy queue row is rejected before it can derive or read its root."""
    from src.hybrid.autonomous_cli import _fixture_worker_handler

    with pytest.raises(RuntimeError, match="configured workspace is invalid"):
        asyncio.run(
            _fixture_worker_handler(
                {
                    "kind": "compiled",
                    "root": object(),
                    "episode_id": "TEST-FORGED",
                    "_worker_id": "worker-1",
                    "fencing_token": 1,
                }
            )
        )


def test_director_delivery_adapter_rejects_nested_receipt_before_video_copy(tmp_path):
    """A nested renderer receipt cannot authorize a copy from an external path."""
    from src.hybrid.autonomous import AutonomousCompiledTestAdapter

    external = tmp_path / "foreign.mp4"
    external.write_bytes(b"foreign")
    with pytest.raises(TypeError, match="unexpected keyword argument 'recovery_capability'"):
        AutonomousCompiledTestAdapter.stage_director_delivery_owned(
            object(), external, {"renderer_receipt": {"output_sha256": "forged"}},
            lambda _evidence: {"approved": True}, ownership=object(), recovery_capability=None,
        )
    assert external.read_bytes() == b"foreign"


def test_compiled_test_loader_uses_director_adapter_factory(tmp_path, monkeypatch):
    """The CLI handoff must retain the Director as compiled-run control plane."""
    from decimal import Decimal

    from PIL import Image

    from src.agents.director import DirectorAgent
    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.autonomous_cli import start_compiled
    from src.hybrid.compiled import compile_storyboard

    packet = tmp_path / "packet"
    packet.mkdir()
    audio = packet / "audio.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
         "sine=frequency=440:sample_rate=48000:duration=2", "-ac", "2", str(audio)],
        check=True,
    )
    reference = packet / "reference.png"
    Image.new("RGB", (64, 64), "navy").save(reference)
    asset = FrozenAsset.approve(reference, "TEST fixture", "TEST")
    sheet = packet / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze(
        (asset,), FrozenAsset.approve(sheet, "TEST fixture", "TEST"), "TEST fixture", "TEST"
    )
    compile_storyboard(
        "TEST-DIRECTOR-HANDOFF", FrozenAsset.approve(audio, "TEST fixture", "TEST"),
        [{"scene_id": "R001", "start": 0, "end": 2, "image_prompt": "céu", "action": "céu"}],
    ).save(packet / "compiled.json")
    manifest.save(packet / "manifest.json")
    policy_document = {
        "schema": 1, "version": "compiled-test-internal-v1", "mode": "TEST",
        "episode_cap_usd": "1.00", "month_cap_usd": "1.00", "experiment_cap_usd": "1.00",
        "allowed_providers": ["test-local-fixture"], "publication": {"enabled": False},
    }
    (packet / "policy.json").write_text(json.dumps(policy_document), encoding="utf-8")
    original = DirectorAgent.create_autonomous_compiled_test_adapter
    calls = []

    def tracked(self, pipeline, workspace, policy):
        calls.append((pipeline.run.executor, Path(workspace), policy))
        return original(self, pipeline, workspace, policy)

    monkeypatch.setattr(DirectorAgent, "create_autonomous_compiled_test_adapter", tracked)
    result = start_compiled(tmp_path / "workspace", packet, None)

    assert result["state"] == "READY"
    assert len(calls) == 1
    executor, workspace, policy = calls[0]
    assert executor.config.limit == Decimal("1.00")
    assert workspace.name == "runner"
    assert policy == policy_document
