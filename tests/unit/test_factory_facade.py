"""Focused facade contracts for the versioned two-gate publication handoff."""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _plan(*, episode_id: str = "TEST-FACADE") -> dict:
    return {
        "schema": "two-gate-publication/v2",
        "episode_id": episode_id,
        "channel": "TEST-local-only",
        "playlist_id": "TEST-playlist",
        "visibility": "scheduled",
        "publish_at": "2026-12-01T12:00:00+00:00",
        "timezone": "UTC",
    }


def test_publication_plan_validation_returns_exact_canonical_binding():
    from src.hybrid.autonomous_cli import validate_publication_plan

    plan = _plan()

    assert validate_publication_plan(plan, episode_id="TEST-FACADE") == plan


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "two-gate-publication/v1"),
        ("episode_id", "OTHER"),
        ("visibility", "private"),
        ("publish_at", None),
        ("timezone", ""),
    ],
)
def test_publication_plan_validation_fails_closed_for_invalid_v2_fields(field, value):
    from src.hybrid.autonomous_cli import validate_publication_plan

    plan = _plan()
    plan[field] = value

    with pytest.raises(ValueError):
        validate_publication_plan(plan, episode_id="TEST-FACADE")


def test_start_intent_binds_local_copy_of_explicit_publication_plan(tmp_path: Path):
    from src.hybrid.autonomous_cli import (
        _compiled_start_intent,
        bind_publication_plan,
    )

    source = tmp_path / "plan.json"
    original = json.dumps(_plan(), sort_keys=True).encode("utf-8")
    source.write_bytes(original)
    copied = tmp_path / "request" / "publication-plan.json"

    binding = bind_publication_plan(source, copied, episode_id="TEST-FACADE")
    intent = _compiled_start_intent(
        tmp_path / "request", "a" * 64, b"seed", None, 1, publication_plan=binding
    )

    assert copied.read_bytes() == original
    assert intent["publication_plan"] == binding
    assert binding["path"] == str(copied.resolve())


def test_cli_start_forwards_explicit_publication_plan_file(tmp_path: Path, monkeypatch):
    from src.hybrid import autonomous_cli

    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(_plan()), encoding="utf-8")
    seen = {}

    def start_compiled(workspace, packet, reject_scene, *, provider_concurrency=1, on_admission=None, publication_plan_file=None):
        seen.update(workspace=workspace, packet=packet, publication_plan_file=publication_plan_file)
        return {"mode": "TEST"}

    monkeypatch.setattr(autonomous_cli, "start_compiled", start_compiled)

    assert autonomous_cli.main([
        "start", "--workspace", str(tmp_path / "workspace"),
        "--compiled-input", str(tmp_path / "packet"),
        "--publication-plan-file", str(plan_file),
    ]) == 0
    assert seen["publication_plan_file"] == plan_file


def test_public_cli_two_gate_plan_runs_owned_worker_and_test_publication(tmp_path: Path):
    """Exercise real public collaborators in fresh, guarded TEST processes."""
    import os
    import subprocess
    import sys

    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.compiled import compile_storyboard

    # Fixture construction stays inside the already guarded pytest process.
    # Parent monkeypatches cannot protect an ordinary python -c/-m child.
    root = Path(__file__).resolve().parents[2]
    wrapper = root / "scripts" / "run_offline_tests.py"
    packet = tmp_path / "p"
    packet.mkdir()
    audio = packet / "audio.wav"
    subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000:duration=2", "-ac", "2", str(audio),
    ], check=True, capture_output=True)
    reference = packet / "reference.png"
    Image.new("RGB", (64, 64), "navy").save(reference)
    frozen = FrozenAsset.approve(reference, "TEST fixture", "TEST")
    sheet = packet / "sheet.png"
    contact_sheet((frozen,), sheet)
    Manifest.freeze(
        (frozen,), FrozenAsset.approve(sheet, "TEST fixture", "TEST"),
        "TEST fixture", "TEST",
    ).save(packet / "manifest.json")
    compile_storyboard(
        "TEST-FACADE", FrozenAsset.approve(audio, "TEST fixture", "TEST"),
        [{"scene_id": "R001", "start": 0, "end": 2,
          "image_prompt": "céu", "action": "céu"}],
    ).save(packet / "compiled.json")
    (packet / "policy.json").write_text(json.dumps({
        "schema": 1, "version": "compiled-test-internal-v1", "mode": "TEST",
        "episode_cap_usd": "1.00", "month_cap_usd": "1.00",
        "experiment_cap_usd": "1.00", "allowed_providers": ["test-local-fixture"],
        "publication": {"enabled": False},
    }), encoding="utf-8")
    plan = packet / "plan.json"
    plan.write_text(json.dumps(_plan(episode_id="TEST-FACADE")), encoding="utf-8")
    workspace = tmp_path / "w"
    home = tmp_path / "h"
    home.mkdir()
    env = {key: os.environ[key] for key in (
        "PATH", "SystemRoot", "SYSTEMROOT", "WINDIR", "COMSPEC", "ComSpec",
        "PATHEXT", "TMPDIR", "TMP", "TEMP",
    ) if key in os.environ}
    env.update(
        HOME=str(home), USERPROFILE=str(home), APPDATA=str(home),
        LOCALAPPDATA=str(home), HERMES_HOME=str(home), HF_HOME=str(home),
        PYTHONPATH="", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
        STUDIO_EPISODES_DIR=str(tmp_path / "episodes"),
    )

    def cli(*arguments: str) -> dict:
        completed = subprocess.run(
            [sys.executable, "-B", "-X", "utf8", str(wrapper),
             "--cli", "autonomous", *arguments],
            cwd=root, env=env, capture_output=True, check=False,
        )
        assert completed.returncode == 0, completed.stderr or completed.stdout
        return json.loads(completed.stdout)

    started = cli("start", "--workspace", str(workspace), "--compiled-input", str(packet), "--publication-plan-file", str(plan))
    request_id = started["request_id"]
    assert cli("enqueue", "--workspace", str(workspace), "--request-id", request_id)["status"] == "PENDING"
    assert cli("run", "--once", "--workspace", str(workspace))["state"] == "WAITING_THUMBNAIL_APPROVAL"
    assert cli("approve", "--workspace", str(workspace), "--request-id", request_id, "--kind", "thumbnail", "--test-approval")["state"] == "WAITING_VIDEO_APPROVAL"
    assert cli("run", "--once", "--workspace", str(workspace))["state"] == "WAITING_VIDEO_APPROVAL"
    published = cli("approve", "--workspace", str(workspace), "--request-id", request_id, "--kind", "video", "--test-approval", "--approve-and-publish")
    assert published["state"] == "PUBLISHED"
    assert published["publication"]["video_id"].startswith("TEST-")
    assert published["publication"]["post_readback"]["result"]["video_id"] == published["publication"]["video_id"]
