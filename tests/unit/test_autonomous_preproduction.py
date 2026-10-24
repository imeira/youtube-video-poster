"""Offline pre-production requests fail closed without inventing episode facts."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest


def test_policy_requires_explicit_test_mode_preserves_it_and_binds_digest():
    from src.hybrid.autonomous import AutonomousPolicy, PolicyError

    document = {
        "schema": 1,
        "version": "v1",
        "episode_cap_usd": "1",
        "month_cap_usd": "1",
        "experiment_cap_usd": "1",
        "allowed_providers": ["test-local-fixture"],
        "publication": {"enabled": False},
    }
    with pytest.raises(PolicyError, match="mode"):
        AutonomousPolicy.from_dict(document)

    parsed = AutonomousPolicy.from_dict({**document, "mode": "TEST"})
    assert parsed.mode == "TEST"
    assert parsed.digest == sha256(
        b'{"allowed_providers":["test-local-fixture"],"episode_cap_usd":"1","experiment_cap_usd":"1","mode":"TEST","month_cap_usd":"1","publication":{"enabled":false},"schema":1,"version":"v1"}'
    ).hexdigest()


def test_compiled_snapshot_rejects_symlink_root_ancestor_and_packet_file(tmp_path):
    from src.hybrid.autonomous_cli import _snapshot_compiled_input, start_compiled

    source = tmp_path / "packet"
    source.mkdir()
    destination = tmp_path / "snapshot"
    root_link = tmp_path / "packet-link"
    root_link.symlink_to(source, target_is_directory=True)
    with pytest.raises(ValueError, match="reparse"):
        _snapshot_compiled_input(root_link, destination)

    ancestor = tmp_path / "real-ancestor"
    ancestor.mkdir()
    nested = ancestor / "packet"
    nested.mkdir()
    ancestor_link = tmp_path / "ancestor-link"
    ancestor_link.symlink_to(ancestor, target_is_directory=True)
    with pytest.raises(ValueError, match="reparse"):
        _snapshot_compiled_input(ancestor_link / "packet", destination)

    source.mkdir(exist_ok=True)
    for name in ("compiled.json", "manifest.json", "policy.json"):
        (source / name).write_text("{}", encoding="utf-8")
    (source / "manifest.json").unlink()
    (source / "manifest.json").symlink_to(source / "compiled.json")
    with pytest.raises(ValueError, match="reparse"):
        _snapshot_compiled_input(source, destination)
    with pytest.raises(ValueError, match="reparse"):
        start_compiled(tmp_path / "workspace", source, None)


def test_offline_preproduction_persists_literal_prompt_and_explicit_dependencies(tmp_path):
    from src.hybrid.preproduction import load_preproduction, start_preproduction

    raw = b"Quero um video infantil sobre uma passagem biblica ainda nao especificada.\r\n"
    created = start_preproduction(tmp_path, raw)
    assert created["kind"] == "preproduction-request"
    assert created["compiled"] is False
    assert created["budget_usd"] is None
    assert created["plan_proposal"] is None
    assert created["selection_proposal"]["executable"] is False
    assert created["dependencies"] == [
        "SOURCE_REQUIRED",
        "PLANNING_REQUIRED",
        "ASSETS_REQUIRED",
        "AUTHORIZATION_REQUIRED",
    ]
    assert Path(created["prompt_path"]).read_bytes() == raw
    assert created["prompt_sha256"] == sha256(raw).hexdigest()

    loaded = load_preproduction(tmp_path, created["request_id"])
    assert loaded == created
    with pytest.raises(ValueError, match="already exists"):
        start_preproduction(tmp_path, raw)


def test_cli_preproduce_status_and_resume_are_offline_and_hash_bound(tmp_path):
    from tests.unit.test_autonomous_cli import run_cli

    prompt = tmp_path / "prompt.txt"
    raw = b"Ideia aberta, sem fonte nem assets.\n"
    prompt.write_bytes(raw)
    workspace = tmp_path / "workspace"
    code, created = run_cli("preproduce", "--workspace", str(workspace), "--prompt-file", str(prompt))
    assert code == 0, created
    assert created["status"] == "BLOCKED"
    assert created["mode"] == "PREPRODUCTION"
    code, status = run_cli("status", "--workspace", str(workspace), "--request-id", created["request_id"])
    assert code == 0 and status == created
    code, resumed = run_cli("resume", "--workspace", str(workspace), "--request-id", created["request_id"])
    assert code == 0 and resumed == created

    package = workspace / "PREPRODUCTION" / created["request_id"] / "request.json"
    document = json.loads(package.read_text(encoding="utf-8"))
    document["prompt_sha256"] = "0" * 64
    package.write_text(json.dumps(document), encoding="utf-8")
    code, blocked = run_cli("status", "--workspace", str(workspace), "--request-id", created["request_id"])
    assert code == 1 and blocked["status"] == "BLOCKED"
