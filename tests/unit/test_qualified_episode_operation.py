"""Protected qualified episode operations are loaded from a fixed root only."""
from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import os
from pathlib import Path

import pytest


def _write(path: Path, value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _operation(root: Path) -> str:
    payload = {"prompt": "fixture only"}
    payload_path = root / "payload.json"
    payload_sha = _write(payload_path, payload)
    operation = {
        "schema": "qualified-episode-operation/v1",
        "operation_id": "TEST-NEXT-VIDEO-001",
        "episode_id": "TEST-EPISODE-001",
        "mode": "TEST",
        "executor_database": "state/executor.sqlite3",
        "ledger_database": "state/ledger.sqlite3",
        "ledger_policy_sha256": "a" * 64,
        "endpoint": "fixture-controlled-provider",
        "scene": "TEST-S001",
        "category": "first",
        "input_kind": "TEXT_TO_IMAGE",
        "payload": {"path": "payload.json", "sha256": payload_sha},
        "maximum_usd": "0.01",
        "provider_kind": "TEST_CONTROLLED_TRANSPORT",
    }
    return _write(root / "operations" / "TEST-NEXT-VIDEO-001.json", operation)


def _fresh_process_worker(root_text: str, crash: bool, queue) -> None:
    """Create a new resolver/executor factory in each OS process."""
    import src.hybrid.qualified_episode_operation as subject
    subject._PROTECTED_ROOT = Path(root_text)
    if crash:
        os.environ["QUALIFIED_TEST_EXIT_AFTER_CHECKPOINT"] = "1"
        asyncio.run(subject.build_qualified_episode_operation("TEST-NEXT-VIDEO-001").execute())
    else:
        os.environ.pop("QUALIFIED_TEST_EXIT_AFTER_CHECKPOINT", None)
        result = asyncio.run(subject.build_qualified_episode_operation("TEST-NEXT-VIDEO-001").recover())
        queue.put(result["status"])


def test_fixed_loader_rejects_nonliteral_ids_before_any_path_lookup(monkeypatch, tmp_path):
    import src.hybrid.qualified_episode_operation as subject

    monkeypatch.setattr(subject, "_PROTECTED_ROOT", tmp_path)
    with pytest.raises(subject.QualifiedEpisodeOperationError, match="literal"):
        subject.load_qualified_operation("../TEST-NEXT-VIDEO-001")
    assert list(tmp_path.iterdir()) == []


def test_protected_test_operation_executes_once_and_fresh_resolver_recovers_without_post(monkeypatch, tmp_path):
    import src.hybrid.qualified_episode_operation as subject

    root = tmp_path / "protected"
    _operation(root)
    monkeypatch.setattr(subject, "_PROTECTED_ROOT", root)

    first = asyncio.run(subject.build_qualified_episode_operation("TEST-NEXT-VIDEO-001").execute())
    assert first["status"] == "COMPLETE"
    counts = json.loads((root / "state" / "transport-journal.json").read_text())
    assert counts == {"gets": 0, "posts": 1}

    second = asyncio.run(subject.build_qualified_episode_operation("TEST-NEXT-VIDEO-001").recover())
    assert second["status"] == "COMPLETE"
    assert json.loads((root / "state" / "transport-journal.json").read_text()) == {"gets": 0, "posts": 1}


def test_fresh_factory_recovers_checkpoint_once_without_second_post_or_reservation(tmp_path):
    root = tmp_path / "protected"
    _operation(root)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    crashed = context.Process(target=_fresh_process_worker, args=(str(root), True, queue))
    crashed.start(); crashed.join(20)
    assert crashed.exitcode == 97
    recovered = context.Process(target=_fresh_process_worker, args=(str(root), False, queue))
    recovered.start(); recovered.join(20)
    assert recovered.exitcode == 0
    assert queue.get(timeout=3) == "COMPLETE"
    assert json.loads((root / "state" / "transport-journal.json").read_text()) == {"gets": 1, "posts": 1}


def test_missing_protected_configuration_fails_closed_without_creating_root(monkeypatch, tmp_path):
    import src.hybrid.qualified_episode_operation as subject

    root = tmp_path / "missing-protected-root"
    monkeypatch.setattr(subject, "_PROTECTED_ROOT", root)
    with pytest.raises(subject.QualifiedEpisodeOperationError, match="protected operation"):
        subject.build_qualified_episode_operation("TEST-NEXT-VIDEO-001")
    assert not root.exists()
