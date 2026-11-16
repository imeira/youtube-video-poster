"""Fresh-process public-CLI acceptance for the two-gate TEST factory.

These tests deliberately exercise the public entry point through the offline
wrapper.  They use real local media and TEST transports, never a provider.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import zipfile
from hashlib import sha256
from pathlib import Path

import pytest
from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.compiled import compile_storyboard

ROOT = Path(__file__).resolve().parents[2]
WRAPPER = ROOT / "scripts" / "run_offline_tests.py"


class _PublicCLI:
    def __init__(self, root: Path):
        self.audit = root / "commands"
        self.audit.mkdir()
        self.sequence = 0
        home = root / "home"
        home.mkdir()
        self.env = {name: os.environ[name] for name in (
            "PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "ComSpec", "COMSPEC",
            "PATHEXT", "TMPDIR", "TMP", "TEMP",
        ) if name in os.environ}
        self.env.update(
            HOME=str(home), USERPROFILE=str(home), APPDATA=str(home),
            LOCALAPPDATA=str(home), HERMES_HOME=str(home), HF_HOME=str(home),
            XDG_CACHE_HOME=str(home), PYTHONPATH="", PYTHONDONTWRITEBYTECODE="1",
            HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
            STUDIO_EPISODES_DIR=str(root / "episodes"),
        )

    def __call__(self, *args: str) -> subprocess.CompletedProcess[bytes]:
        self.sequence += 1
        retained = self.audit / f"{self.sequence:03}"
        retained.mkdir()
        argv = [sys.executable, "-B", "-X", "utf8", str(WRAPPER), "--cli", "autonomous", *args]
        pins = {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in (
            "scripts/run_offline_tests.py", "src/hybrid/autonomous_cli.py",
            "src/hybrid/autonomous.py", "src/hybrid/publication_plan.py",
            "src/agents/director.py", "tests/unit/test_factory_public_e2e.py",
        )}
        command = {"schema": "public-cli-test-command/v1", "argv": argv,
                   "cwd": str(ROOT), "environment": self.env, "source_pins": pins}
        (retained / "command.json").write_text(json.dumps(command, indent=2), encoding="utf-8")
        started = time.monotonic()
        child = subprocess.Popen(argv, cwd=ROOT, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        timed_out = False
        try:
            stdout, stderr = child.communicate(timeout=120)
        except subprocess.TimeoutExpired:
            timed_out = True
            child.kill()
            stdout, stderr = child.communicate()
        (retained / "stdout.bin").write_bytes(stdout)
        (retained / "stderr.bin").write_bytes(stderr)
        receipt = {**command, "pid": child.pid, "exit_code": child.returncode,
                   "timed_out": timed_out, "elapsed_seconds": time.monotonic() - started,
                   "source_pins_after": {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in pins},
                   "outputs": {name: {"sha256": sha256(data).hexdigest(), "bytes": len(data)}
                               for name, data in (("stdout.bin", stdout), ("stderr.bin", stderr))}}
        (retained / "receipt.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
        assert not timed_out, f"CLI timeout; retained command: {retained}"
        assert receipt["source_pins_after"] == pins
        return subprocess.CompletedProcess(argv, child.returncode, stdout, stderr)


@pytest.fixture
def cli(tmp_path: Path, request):
    session = _PublicCLI(tmp_path)
    try:
        yield session
    finally:
        # Preserve actual runtime and raw command logs outside disposable basetemp.
        # Archive existence is not acceptance; the JUnit carries that verdict.
        xmlpath = request.config.getoption("xmlpath", default=None)
        if xmlpath:
            identity = sha256(request.node.nodeid.encode()).hexdigest()[:16]
            destination = Path(xmlpath).parent / "public-cli-runtime" / identity
            destination.mkdir(parents=True, exist_ok=False)
            members = {}
            with zipfile.ZipFile(destination / "runtime.zip", "x", compression=zipfile.ZIP_STORED) as archive:
                for path in sorted(tmp_path.rglob("*")):
                    if not path.is_file():
                        continue
                    key = path.relative_to(tmp_path).as_posix()
                    data = path.read_bytes()
                    archive.writestr(key, data)
                    members[key] = {"sha256": sha256(data).hexdigest(), "bytes": len(data)}
            manifest = {"schema": "public-cli-test-runtime/v1", "nodeid": request.node.nodeid,
                        "acceptance": "see pytest JUnit verdict",
                        "archive_sha256": sha256((destination / "runtime.zip").read_bytes()).hexdigest(),
                        "members": members}
            (destination / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def _json(result: subprocess.CompletedProcess[bytes]) -> dict:
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def _compiled_packet(tmp_path: Path) -> Path:
    packet = tmp_path / "p"
    packet.mkdir()
    audio = packet / "a.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1", "-ac", "2", str(audio)], check=True)
    image = packet / "i.png"
    Image.new("RGB", (64, 64), "navy").save(image)
    frozen = FrozenAsset.approve(image, "TEST fixture", "TEST")
    sheet = packet / "s.png"
    contact_sheet((frozen,), sheet)
    Manifest.freeze((frozen,), FrozenAsset.approve(sheet, "TEST fixture", "TEST"), "TEST fixture", "TEST").save(packet / "manifest.json")
    compile_storyboard("TEST-PUBLIC-TWO-GATE", FrozenAsset.approve(audio, "TEST fixture", "TEST"), [{"scene_id": "R001", "start": 0, "end": 1, "image_prompt": "céu", "action": "céu"}]).save(packet / "compiled.json")
    (packet / "policy.json").write_text(json.dumps({"schema": 1, "version": "compiled-test-internal-v1", "mode": "TEST", "episode_cap_usd": "1.00", "month_cap_usd": "1.00", "experiment_cap_usd": "1.00", "allowed_providers": ["test-local-fixture"], "publication": {"enabled": False}}), encoding="utf-8")
    return packet


def _plan(tmp_path: Path) -> Path:
    plan = tmp_path / "q.json"
    plan.write_text(json.dumps({"schema": "two-gate-publication/v2", "episode_id": "TEST-PUBLIC-TWO-GATE", "channel": "TEST-local-only", "playlist_id": "TEST-playlist", "visibility": "public", "publish_at": None, "timezone": "America/Sao_Paulo"}), encoding="utf-8")
    return plan


def _start(cli, tmp_path):
    workspace, packet, plan = tmp_path / "w", _compiled_packet(tmp_path), _plan(tmp_path)
    request_id = _json(cli("start", "--workspace", str(workspace), "--compiled-input", str(packet), "--publication-plan-file", str(plan)))["request_id"]
    _json(cli("enqueue", "--workspace", str(workspace), "--request-id", request_id))
    assert _json(cli("run", "--workspace", str(workspace), "--once"))["state"] == "WAITING_THUMBNAIL_APPROVAL"
    root = workspace / "TC" / request_id[:16]
    assert json.loads((root / "compiled-harness.json").read_bytes())["request_id"] == request_id
    return workspace, root, request_id


def _posts(root):
    value = json.loads((root / "provider" / "fixture-post-count.json").read_bytes())
    assert set(value) == {"posts"} and type(value["posts"]) is int
    return value["posts"]


def _calls(root):
    value = json.loads((root / "publication-transport.json").read_bytes())
    assert isinstance(value["calls"], list)
    return [entry["method"] for entry in value["calls"]]


def test_public_cli_runs_exactly_two_gates_and_resumes_without_transport_replay(tmp_path: Path, cli):
    workspace, root, request_id = _start(cli, tmp_path)
    assert _posts(root) == 1
    assert not (root / "publication-transport.json").exists()
    waiting = _json(cli("approve", "--workspace", str(workspace), "--request-id", request_id, "--kind", "thumbnail", "--test-approval"))
    assert waiting["state"] == "WAITING_VIDEO_APPROVAL"
    # Approval wakes a task; a real worker establishes the next waiting gate.
    assert _json(cli("run", "--workspace", str(workspace), "--once"))["state"] == "WAITING_VIDEO_APPROVAL"
    published = _json(cli("approve", "--workspace", str(workspace), "--request-id", request_id, "--kind", "video", "--test-approval", "--approve-and-publish"))
    assert published["mode"] == "TEST" and published["state"] == "PUBLISHED"
    receipts = sorted((root / "gate-receipts").glob("*.json"))
    assert [path.name for path in receipts] == ["thumbnail.json", "video.json"]
    for path in receipts:
        gate = json.loads(path.read_bytes())
        assert gate["request_id"] == request_id and gate["approved"] is True
        assert gate["artifact_sha256"] == published["artifacts"][gate["kind"]]["sha256"]
    calls = _calls(root)
    assert calls.count("upload_private") == calls.count("configure") == calls.count("release") == 1
    release_index = calls.index("release")
    assert "readback" in calls[:release_index], "private readback was not recorded before release"
    assert "readback" in calls[release_index + 1:], "final readback was not recorded after release"
    pending = _json(cli("worker-status", "--workspace", str(workspace), "--request-id", request_id))
    assert pending["status"] == "PENDING"
    terminal = _json(cli("run", "--workspace", str(workspace), "--once"))
    assert terminal["state"] == "PUBLISHED" and terminal["next_action"] == "TERMINAL"
    completed = _json(cli("worker-status", "--workspace", str(workspace), "--request-id", request_id))
    assert completed["status"] == "DONE" and completed["attempts"] == pending["attempts"] + 1
    after = _calls(root)
    assert _posts(root) == 1
    for operation in ("upload_private", "configure", "release"):
        assert after.count(operation) == calls.count(operation)


def test_public_cli_rejects_video_first_and_tampered_publication_plan(tmp_path: Path, cli):
    workspace, root, request_id = _start(cli, tmp_path)
    before = (root / "runner" / "runner.json").read_bytes()
    wrong_order = cli("approve", "--workspace", str(workspace), "--request-id", request_id, "--kind", "video", "--test-approval", "--approve-and-publish")
    assert wrong_order.returncode != 0
    assert (root / "runner" / "runner.json").read_bytes() == before
    assert not (root / "publication-transport.json").exists()
    # Mutate the admitted copy, not the source already snapshotted by start.
    admitted = root / "publication-plan.json"
    value = json.loads(admitted.read_bytes())
    value["playlist_id"] = "TEST-tampered-playlist"
    admitted.write_text(json.dumps(value), encoding="utf-8")
    tampered = cli("approve", "--workspace", str(workspace), "--request-id", request_id, "--kind", "thumbnail", "--test-approval")
    assert tampered.returncode != 0, "admitted publication-plan tamper was accepted"
    assert (root / "runner" / "runner.json").read_bytes() == before
    assert not (root / "publication-transport.json").exists()
    assert _posts(root) == 1
