"""Protected qualified episode operations are loaded from a fixed root only."""
from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import os
from decimal import Decimal
from pathlib import Path

import pytest

from src.hybrid.execution import Job


def _write(path: Path, value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _operation(
    root: Path,
    *,
    operation_id: str = "TEST-NEXT-VIDEO-001",
    episode_id: str = "TEST-EPISODE-001",
    ledger_database: str = "state/ledger.sqlite3",
    ledger_policy_sha256: str = "a" * 64,
) -> str:
    payload = {"prompt": "fixture only"}
    payload_path = root / "payload.json"
    payload_sha = _write(payload_path, payload)
    operation = {
        "schema": "qualified-episode-operation/v1",
        "operation_id": operation_id,
        "episode_id": episode_id,
        "mode": "TEST",
        "executor_database": "state/executor.sqlite3",
        "ledger_database": ledger_database,
        "ledger_policy_sha256": ledger_policy_sha256,
        "endpoint": "fixture-controlled-provider",
        "scene": "TEST-S001",
        "category": "first",
        "input_kind": "TEXT_TO_IMAGE",
        "payload": {"path": "payload.json", "sha256": payload_sha},
        "maximum_usd": "0.01",
        "provider_kind": "TEST_CONTROLLED_TRANSPORT",
    }
    return _write(root / "operations" / f"{operation_id}.json", operation)


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


def test_foreign_closed_operation_cannot_bind_or_recover_owner_job(monkeypatch, tmp_path):
    """A second valid document is not authority for the first operation's job."""
    import src.hybrid.qualified_episode_operation as subject

    root = tmp_path / "protected"
    _operation(root, operation_id="TEST-OWNER-001")
    _operation(root, operation_id="TEST-FOREIGN-002")
    monkeypatch.setattr(subject, "_PROTECTED_ROOT", root)
    operation = subject.build_qualified_episode_operation("TEST-OWNER-001")
    operation.executor.prepare_intent(operation.job)
    operation.ledger.reserve(
        operation.job.request_id,
        operation.document["episode_id"],
        operation.job.endpoint,
        operation.job.cost,
        settlement_mode="FAL_READBACK_V2",
        auth_scope="TEST_LOCAL_ONLY",
        provider_request_id="UNASSIGNED",
        authority_mode="TEST",
    )
    prepared = operation.executor.inspect(operation.job.request_id)
    claim = operation.ledger.claim(operation.job.request_id)

    with pytest.raises(subject.QualifiedEpisodeOperationError, match="origin"):
        operation.executor.bind_prepared_generic_admission(
            operation.job, operation_id="TEST-FOREIGN-002"
        )
    assert operation.executor.inspect(operation.job.request_id) == prepared
    assert operation.ledger.claim(operation.job.request_id) == claim
    assert not (root / "state" / "transport-journal.json").exists()

    operation.executor.bind_prepared_generic_admission(
        operation.job, operation_id="TEST-OWNER-001"
    )
    operation.executor._checkpoint_provider_binding(
        operation.job.request_id, "TEST-remote"
    )
    with operation.executor._db() as db:
        row = operation.executor.inspect(operation.job.request_id)
        row["generic_admission_marker"]["operation_id"] = "TEST-FOREIGN-002"
        db.execute(
            "UPDATE jobs SET data=? WHERE id=?",
            (json.dumps(row, sort_keys=True), operation.job.request_id),
        )
    tampered = operation.executor.inspect(operation.job.request_id)
    claim = operation.ledger.claim(operation.job.request_id)

    with pytest.raises(subject.QualifiedEpisodeOperationError, match="origin"):
        asyncio.run(operation.executor.run(operation.job, operation.provider))
    assert operation.executor.inspect(operation.job.request_id) == tampered
    assert operation.ledger.claim(operation.job.request_id) == claim
    assert not (root / "state" / "transport-journal.json").exists()


def test_generic_operation_origin_is_intrinsic_before_foreign_bind_or_claim(monkeypatch, tmp_path):
    """Distinct valid documents cannot collide before either ledger is touched."""
    import src.hybrid.qualified_episode_operation as subject

    root = tmp_path / "protected"
    _operation(
        root, operation_id="TEST-OWNER-001", episode_id="TEST-EPISODE-A",
        ledger_database="state/owner-ledger.sqlite3", ledger_policy_sha256="a" * 64,
    )
    _operation(
        root, operation_id="TEST-FOREIGN-002", episode_id="TEST-EPISODE-B",
        ledger_database="state/foreign-ledger.sqlite3", ledger_policy_sha256="b" * 64,
    )
    monkeypatch.setattr(subject, "_PROTECTED_ROOT", root)
    owner = subject.build_qualified_episode_operation("TEST-OWNER-001")
    foreign = subject.build_qualified_episode_operation("TEST-FOREIGN-002")

    assert owner.job.request_id != foreign.job.request_id
    prepared = owner.executor.prepare_intent(owner.job)
    assert prepared["generic_operation_id"] == "TEST-OWNER-001"
    executor_before = owner.executor.inspect(owner.job.request_id)
    owner_ledger_before = owner.ledger.exposure()
    foreign_ledger_before = foreign.ledger.exposure()

    with pytest.raises(subject.QualifiedEpisodeOperationError, match="origin"):
        owner.executor.bind_prepared_generic_admission(
            owner.job, operation_id="TEST-FOREIGN-002"
        )

    assert owner.executor.inspect(owner.job.request_id) == executor_before
    assert owner.ledger.exposure() == owner_ledger_before
    assert foreign.ledger.exposure() == foreign_ledger_before
    assert not (root / "state" / "transport-journal.json").exists()


def test_legacy_job_request_identity_is_unchanged():
    legacy = Job(
        "TEST-S001", "first", "TEST", "fixture-controlled-provider",
        {"prompt": "fixture only"}, None, Decimal("0.01"),
        input_kind="TEXT_TO_IMAGE",
    )
    assert legacy.request_id == "4a25277ab08449334b22381c394198a009c615ce983932a1fda86b9fe24718c3"
