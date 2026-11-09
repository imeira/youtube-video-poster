"""Protected qualified episode operations are loaded from a fixed root only."""
from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import os
import shutil
import sqlite3
import subprocess
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
    mode: str = "TEST",
    provider_kind: str = "TEST_CONTROLLED_TRANSPORT",
) -> str:
    payload = {"prompt": "fixture only"}
    payload_path = root / "payload.json"
    payload_sha = _write(payload_path, payload)
    operation = {
        "schema": "qualified-episode-operation/v1",
        "operation_id": operation_id,
        "episode_id": episode_id,
        "mode": mode,
        "executor_database": "state/executor.sqlite3",
        "ledger_database": ledger_database,
        "ledger_policy_sha256": ledger_policy_sha256,
        "endpoint": "fixture-controlled-provider",
        "scene": "TEST-S001",
        "category": "first",
        "input_kind": "TEXT_TO_IMAGE",
        "payload": {"path": "payload.json", "sha256": payload_sha},
        "maximum_usd": "0.01",
        "provider_kind": provider_kind,
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


def test_generic_storage_boundary_rejects_substituted_executor_or_ledger_before_effects(monkeypatch, tmp_path):
    """The fixed document, not a caller's object graph, owns both SQLite paths."""
    import src.hybrid.qualified_episode_operation as subject
    from src.hybrid.execution import Executor
    from src.hybrid.planner import Config
    from src.hybrid.standing_budget import StandingBudgetLedger

    root = tmp_path / "protected"
    _operation(root, operation_id="TEST-OWNER-001")
    monkeypatch.setattr(subject, "_PROTECTED_ROOT", root)
    owner = subject.build_qualified_episode_operation("TEST-OWNER-001")
    attacker_db = root / "attacker" / "executor.sqlite3"
    attacker_ledger_path = root / "attacker" / "ledger.sqlite3"
    attacker_ledger = StandingBudgetLedger(
        attacker_ledger_path, policy_sha256="a" * 64,
        episode_limit_usd="0.01", month_limit_usd="0.01", experiment_limit_usd="0.01", mode="TEST",
    )
    wrong_executor = Executor(attacker_db, Config(limit=Decimal("0.01"), concurrency=1), standing_ledger=owner.ledger)
    wrong_executor_before = attacker_db.read_bytes()
    with pytest.raises(subject.QualifiedEpisodeOperationError, match="storage"):
        wrong_executor.prepare_intent(owner.job)
    assert wrong_executor.inspect(owner.job.request_id) is None
    assert attacker_db.read_bytes() == wrong_executor_before

    both_wrong_db = root / "attacker" / "both.sqlite3"
    both_wrong = Executor(both_wrong_db, Config(limit=Decimal("0.01"), concurrency=1), standing_ledger=attacker_ledger)
    both_wrong_before = both_wrong_db.read_bytes()
    with pytest.raises(subject.QualifiedEpisodeOperationError, match="storage"):
        both_wrong.prepare_intent(owner.job)
    assert both_wrong.inspect(owner.job.request_id) is None
    assert both_wrong_db.read_bytes() == both_wrong_before

    owner.executor.prepare_intent(owner.job)
    owner_executor_before = owner.executor.database.read_bytes()
    owner_ledger_before = owner.ledger.path.read_bytes()
    attacker_ledger_before = attacker_ledger.path.read_bytes()
    wrong_ledger_executor = Executor(owner.executor.database, Config(limit=Decimal("0.01"), concurrency=1), standing_ledger=attacker_ledger)
    with pytest.raises(subject.QualifiedEpisodeOperationError, match="storage"):
        wrong_ledger_executor.bind_prepared_generic_admission(owner.job, operation_id="TEST-OWNER-001")
    assert owner.executor.inspect(owner.job.request_id).get("generic_admission_marker") is None
    assert owner.executor.database.read_bytes() == owner_executor_before
    assert owner.ledger.path.read_bytes() == owner_ledger_before
    assert attacker_ledger.path.read_bytes() == attacker_ledger_before

    copied_db = root / "attacker" / "copied-executor.sqlite3"
    with sqlite3.connect(owner.executor.database) as database:
        database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    source_bytes = owner.executor.database.read_bytes()
    shutil.copyfile(owner.executor.database, copied_db)
    assert copied_db.read_bytes() == source_bytes
    copied_executor = Executor(copied_db, Config(limit=Decimal("0.01"), concurrency=1), standing_ledger=owner.ledger)
    copied_before = copied_db.read_bytes()
    with pytest.raises(subject.QualifiedEpisodeOperationError, match="storage"):
        copied_executor.bind_prepared_generic_admission(owner.job, operation_id="TEST-OWNER-001")
    assert copied_executor.inspect(owner.job.request_id).get("generic_admission_marker") is None
    assert copied_db.read_bytes() == copied_before

    owner.ledger.reserve(
        owner.job.request_id, owner.document["episode_id"], owner.job.endpoint, owner.job.cost,
        settlement_mode="FAL_READBACK_V2", auth_scope="TEST_LOCAL_ONLY",
        provider_request_id="UNASSIGNED", authority_mode="TEST",
    )
    owner.executor.bind_prepared_generic_admission(owner.job, operation_id="TEST-OWNER-001")
    owner.executor._checkpoint_provider_binding(owner.job.request_id, "TEST-remote")
    with sqlite3.connect(owner.executor.database) as database:
        database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copyfile(owner.executor.database, copied_db)
    copied_recovery = Executor(copied_db, Config(limit=Decimal("0.01"), concurrency=1), standing_ledger=owner.ledger)
    journal_before = (root / "state" / "transport-journal.json").read_bytes() if (root / "state" / "transport-journal.json").exists() else b""
    with pytest.raises(subject.QualifiedEpisodeOperationError, match="storage"):
        asyncio.run(copied_recovery.run(owner.job, owner.provider))
    assert ((root / "state" / "transport-journal.json").read_bytes() if (root / "state" / "transport-journal.json").exists() else b"") == journal_before


def test_public_complete_recovery_rejects_copied_executor_before_state_read(monkeypatch, tmp_path):
    """A public constructor cannot turn a copied completed SQLite DB into authority."""
    import src.hybrid.qualified_episode_operation as subject
    from src.hybrid.execution import Executor
    from src.hybrid.planner import Config

    root = tmp_path / "protected"
    _operation(root, operation_id="TEST-OWNER-001")
    monkeypatch.setattr(subject, "_PROTECTED_ROOT", root)
    owner = subject.build_qualified_episode_operation("TEST-OWNER-001")
    assert asyncio.run(owner.execute())["status"] == "COMPLETE"
    with sqlite3.connect(owner.executor.database) as database:
        database.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    copied_db = root / "attacker" / "copied-complete.sqlite3"
    copied_db.parent.mkdir()
    shutil.copyfile(owner.executor.database, copied_db)
    copied_executor = Executor(
        copied_db, Config(limit=Decimal("0.01"), concurrency=1), standing_ledger=owner.ledger
    )
    copied_executor.inspect = lambda _request_id: pytest.fail("copied state was read")
    caller_composed = subject.QualifiedEpisodeOperation(
        owner.operation_id, owner.document, owner.root, copied_executor,
        owner.ledger, owner.job, owner.provider,
    )
    journal_before = (root / "state" / "transport-journal.json").read_bytes()

    with pytest.raises(subject.QualifiedEpisodeOperationError, match="storage"):
        asyncio.run(caller_composed.recover())

    assert (root / "state" / "transport-journal.json").read_bytes() == journal_before


def test_public_complete_recovery_revalidates_receipt_artifact_without_transport(monkeypatch, tmp_path):
    """Canonical COMPLETE replay hashes the bound artifact instead of trusting status."""
    import src.hybrid.qualified_episode_operation as subject

    root = tmp_path / "protected"
    _operation(root, operation_id="TEST-OWNER-001")
    monkeypatch.setattr(subject, "_PROTECTED_ROOT", root)
    operation = subject.build_qualified_episode_operation("TEST-OWNER-001")
    completed = asyncio.run(operation.execute())
    Path(completed["result"]).write_bytes(b"substituted bytes")
    journal_before = (root / "state" / "transport-journal.json").read_bytes()

    with pytest.raises(ValueError, match="receipt result hash mismatch"):
        asyncio.run(operation.recover())

    assert (root / "state" / "transport-journal.json").read_bytes() == journal_before


def test_public_operation_accepts_same_canonical_relative_root_alias(monkeypatch, tmp_path):
    """A spelling alias is safe only when it resolves to the fixed protected root."""
    import src.hybrid.qualified_episode_operation as subject

    root = tmp_path / "protected"
    _operation(root, operation_id="TEST-OWNER-001")
    monkeypatch.setattr(subject, "_PROTECTED_ROOT", root)
    operation = subject.build_qualified_episode_operation("TEST-OWNER-001")
    operation.root = root.parent / root.name / ".." / root.name

    assert asyncio.run(operation.execute())["status"] == "COMPLETE"
    assert asyncio.run(operation.recover())["status"] == "COMPLETE"
    assert json.loads((root / "state" / "transport-journal.json").read_text()) == {"gets": 0, "posts": 1}


@pytest.mark.skipif(os.name != "nt", reason="native Windows junction boundary")
def test_loader_rejects_native_junction_before_opening_state_storage(monkeypatch, tmp_path):
    """Windows junctions are reparse points, not safe canonical storage aliases."""
    import src.hybrid.qualified_episode_operation as subject

    root = tmp_path / "protected"
    target = tmp_path / "junction-target"
    _operation(root, operation_id="TEST-OWNER-001")
    (root / "state").mkdir()
    (root / "state").rmdir()
    target.mkdir()
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(root / "state"), str(target)],
        check=False, capture_output=True,
    )
    assert created.returncode == 0, created.stderr.decode(errors="replace")
    monkeypatch.setattr(subject, "_PROTECTED_ROOT", root)
    opened = 0
    original_read_bytes = Path.read_bytes

    def audited_read_bytes(path: Path):
        nonlocal opened
        if target in path.parents or path == target:
            opened += 1
            raise AssertionError("junction target was opened")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", audited_read_bytes)
    with pytest.raises(subject.QualifiedEpisodeOperationError, match="reparse"):
        subject.load_qualified_operation("TEST-OWNER-001")

    assert opened == 0
    assert list(target.iterdir()) == []


def test_generic_live_operation_is_denied_before_reservation_or_transport(monkeypatch, tmp_path):
    import src.hybrid.qualified_episode_operation as subject

    root = tmp_path / "protected"
    _operation(
        root, operation_id="TEST-LIVE-001", mode="LIVE",
        provider_kind="FAL_FLUX_IMAGE_EDIT",
    )
    monkeypatch.setattr(subject, "_PROTECTED_ROOT", root)
    with pytest.raises(subject.QualifiedEpisodeOperationError, match="unprovisioned"):
        subject.build_qualified_episode_operation("TEST-LIVE-001")
    assert not (root / "state").exists()
    assert not (root / "quarantine").exists()
