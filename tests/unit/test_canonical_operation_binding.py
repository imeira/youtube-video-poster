"""Tests for read-only canonical mandate and financial bindings."""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _write(path: Path, value: dict) -> str:
    raw = _canonical(value)
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _fixture(tmp_path: Path):
    from src.hybrid.canonical_operation_binding import CanonicalOperationBinding

    assistant = "canonical assistant context"
    user = "I authorize the exact bounded operation."
    transcript = tmp_path / "canonical-state.sqlite3"
    db = sqlite3.connect(transcript)
    db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)")
    db.executemany(
        "INSERT INTO messages VALUES (?, ?, ?, ?)",
        [(17, "session-immutable", "assistant", assistant), (18, "session-immutable", "user", user)],
    )
    db.commit(); db.close()

    policy = {
        "schema": "generic-canonical-operation-policy/v1",
        "operation_id": "TEST-CANONICAL-001",
        "episode_id": "TEST-EPISODE-001",
        "operation_scope": "IMAGE_GENERATION",
        "canonical_consent": {
            "schema": "canonical-transcript-provenance/v1",
            "session_id": "session-immutable",
            "adjacent_assistant_message_id": 17,
            "user_message_id": 18,
            "assistant_message_sha256": hashlib.sha256(assistant.encode()).hexdigest(),
            "user_message_sha256": hashlib.sha256(user.encode()).hexdigest(),
        },
    }
    policy_path = tmp_path / "operation-policy.json"
    policy_sha = _write(policy_path, policy)
    financial = {
        "schema": "generic-canonical-financial-policy/v1",
        "operation_id": "TEST-CANONICAL-001",
        "episode_id": "TEST-EPISODE-001",
        "operation_scope": "IMAGE_GENERATION",
        "currency": "USD",
        "maximum_usd": "0.048",
        "status": "ACTIVE",
    }
    financial_path = tmp_path / "financial-policy.json"
    financial_sha = _write(financial_path, financial)
    ledger = tmp_path / "ledger.sqlite3"
    db = sqlite3.connect(ledger)
    db.execute("CREATE TABLE policy (id INTEGER PRIMARY KEY, binding TEXT NOT NULL)")
    db.execute("INSERT INTO policy VALUES (1, ?)", (json.dumps({"financial_policy_sha256": financial_sha}),))
    db.commit(); db.close()
    binding = CanonicalOperationBinding(
        operation_id="TEST-CANONICAL-001", root=tmp_path,
        authority_name="operation-policy.json", authority_sha256=policy_sha,
        financial_name="financial-policy.json", financial_sha256=financial_sha,
        ledger_name="ledger.sqlite3", transcript_path=transcript,
        operation_scope="IMAGE_GENERATION",
        fixture_mode="TEST_OFFLINE_FROZEN",
        expected_transcript_sha256=hashlib.sha256(transcript.read_bytes()).hexdigest(),
        expected_ledger_sha256=hashlib.sha256(ledger.read_bytes()).hexdigest(),
    )
    return binding, transcript, policy, financial


def test_verifies_source_owned_transcript_and_financial_ledger_read_only(tmp_path):
    from src.hybrid.canonical_operation_binding import (
        verify_canonical_operation_binding,
    )

    binding, transcript, _policy, _financial = _fixture(tmp_path)
    before = transcript.read_bytes(), (tmp_path / "ledger.sqlite3").read_bytes()

    evidence = verify_canonical_operation_binding(binding, transcript_path=transcript)

    assert evidence["operation_id"] == "TEST-CANONICAL-001"
    assert evidence["operation_scope"] == "IMAGE_GENERATION"
    assert evidence["operation_policy_sha256"] != evidence["financial_policy_sha256"]
    assert evidence["canonical_message_ids"] == {"assistant": 17, "user": 18}
    assert before == (transcript.read_bytes(), (tmp_path / "ledger.sqlite3").read_bytes())
    assert not list(tmp_path.glob("*.sqlite3-wal"))
    assert not list(tmp_path.glob("*.sqlite3-shm"))


def test_public_binding_verifier_labels_fixture_evidence_test_only(tmp_path):
    from src.hybrid.canonical_operation_binding import (
        verify_canonical_operation_binding,
    )

    binding, transcript, _policy, _financial = _fixture(tmp_path)

    evidence = verify_canonical_operation_binding(binding, transcript_path=transcript)

    assert evidence["status"] == "TEST_BOUND_EVIDENCE"
    assert evidence["authority"] == "NONE"
    assert evidence["live_authority"] is False


def test_requires_a_pinned_offline_test_fixture_manifest_before_any_source_read(tmp_path, monkeypatch):
    """Arbitrary filesystem paths are not a canonical or LIVE capture interface."""
    from src.hybrid.canonical_operation_binding import (
        CanonicalOperationBinding,
        verify_canonical_operation_binding,
    )

    binding, transcript, _policy, _financial = _fixture(tmp_path)
    opened: list[object] = []
    monkeypatch.setattr(os, "open", lambda *args, **kwargs: opened.append(args) or 0)
    unbound = CanonicalOperationBinding(
        operation_id=binding.operation_id,
        root=binding.root,
        authority_name=binding.authority_name,
        authority_sha256=binding.authority_sha256,
        financial_name=binding.financial_name,
        financial_sha256=binding.financial_sha256,
        ledger_name=binding.ledger_name,
        transcript_path=transcript,
        operation_scope=binding.operation_scope,
        fixture_mode="UNBOUND",
        expected_transcript_sha256="0" * 64,
        expected_ledger_sha256="0" * 64,
    )

    with pytest.raises(PermissionError, match="offline TEST fixture"):
        verify_canonical_operation_binding(unbound, transcript_path=transcript)
    assert opened == []


@pytest.mark.skipif(sys.platform != "win32", reason="native junction behavior is Windows-only")
def test_windows_junction_source_is_rejected_before_target_bytes_are_read(tmp_path):
    """A real mklink /J ancestor must not be followed by the capture boundary."""
    from src.hybrid.canonical_operation_binding import (
        CanonicalOperationBinding,
        verify_canonical_operation_binding,
    )

    target = tmp_path / "target"
    target.mkdir()
    binding, transcript, _policy, _financial = _fixture(target)
    junction = tmp_path / "junction"
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        check=False,
        capture_output=True,
    )
    if completed.returncode:
        pytest.skip(f"mklink /J unavailable: {completed.stderr or completed.stdout}")
    redirected = CanonicalOperationBinding(
        operation_id=binding.operation_id,
        root=junction,
        authority_name=binding.authority_name,
        authority_sha256=binding.authority_sha256,
        financial_name=binding.financial_name,
        financial_sha256=binding.financial_sha256,
        ledger_name=binding.ledger_name,
        transcript_path=junction / transcript.name,
        operation_scope=binding.operation_scope,
        fixture_mode="TEST_OFFLINE_FROZEN",
        expected_transcript_sha256=hashlib.sha256(transcript.read_bytes()).hexdigest(),
        expected_ledger_sha256=hashlib.sha256((target / "ledger.sqlite3").read_bytes()).hexdigest(),
    )

    with pytest.raises(PermissionError, match="reparse|unavailable"):
        verify_canonical_operation_binding(redirected, transcript_path=redirected.transcript_path)


@pytest.mark.parametrize("database_name", ["canonical-state.sqlite3", "ledger.sqlite3"])
def test_rejects_any_present_wal_or_shm_before_reading_either_database(tmp_path, database_name):
    from src.hybrid.canonical_operation_binding import (
        verify_canonical_operation_binding,
    )

    binding, transcript, _policy, _financial = _fixture(tmp_path)
    database = tmp_path / database_name
    sidecar = database.with_name(database.name + "-wal")
    sidecar.write_bytes(b"present WAL means the source snapshot is not immutable")

    with pytest.raises(PermissionError, match="WAL|sidecar"):
        verify_canonical_operation_binding(binding, transcript_path=transcript)


@pytest.mark.parametrize("database_name", ["canonical-state.sqlite3", "ledger.sqlite3"])
def test_rejects_committed_wal_revocation_for_each_canonical_database(tmp_path, database_name):
    from src.hybrid.canonical_operation_binding import (
        verify_canonical_operation_binding,
    )

    binding, transcript, _policy, _financial = _fixture(tmp_path)
    database_path = tmp_path / database_name
    writer = sqlite3.connect(database_path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0].lower() == "wal"
        if database_name == "canonical-state.sqlite3":
            writer.execute("UPDATE messages SET content = 'REVOKED' WHERE id = 18")
        else:
            writer.execute("UPDATE policy SET binding = ? WHERE id = 1", ("{}",))
        writer.commit()
        assert database_path.with_name(database_path.name + "-wal").exists()
        with pytest.raises(PermissionError, match="WAL|sidecar"):
            verify_canonical_operation_binding(binding, transcript_path=transcript)
    finally:
        writer.close()


def test_rejects_fabricated_packet_even_when_every_claim_hash_is_rebound(tmp_path):
    from src.hybrid.canonical_operation_binding import (
        CanonicalOperationBinding,
        verify_canonical_operation_binding,
    )

    binding, transcript, policy, _financial = _fixture(tmp_path)
    policy["canonical_consent"]["user_message_sha256"] = hashlib.sha256(b"fabricated consent" ).hexdigest()
    forged_sha = _write(tmp_path / "operation-policy.json", policy)
    forged = CanonicalOperationBinding(
        operation_id=binding.operation_id, root=binding.root, authority_name=binding.authority_name,
        authority_sha256=forged_sha, financial_name=binding.financial_name,
        financial_sha256=binding.financial_sha256, ledger_name=binding.ledger_name,
        transcript_path=binding.transcript_path,
        operation_scope=binding.operation_scope,
        fixture_mode=binding.fixture_mode,
        expected_transcript_sha256=binding.expected_transcript_sha256,
        expected_ledger_sha256=binding.expected_ledger_sha256,
        )

    with pytest.raises(PermissionError, match="transcript"):
        verify_canonical_operation_binding(forged, transcript_path=transcript)


def test_rejects_foreign_scope_before_financial_admission(tmp_path):
    from src.hybrid.canonical_operation_binding import (
        CanonicalOperationBinding,
        verify_canonical_operation_binding,
    )

    binding, transcript, policy, _financial = _fixture(tmp_path)
    policy["operation_scope"] = "FOREIGN_SCOPE"
    policy_sha = _write(tmp_path / "operation-policy.json", policy)
    foreign = CanonicalOperationBinding(
        operation_id=binding.operation_id, root=binding.root, authority_name=binding.authority_name,
        authority_sha256=policy_sha, financial_name=binding.financial_name,
        financial_sha256=binding.financial_sha256, ledger_name=binding.ledger_name,
        transcript_path=binding.transcript_path,
        operation_scope=binding.operation_scope,
        fixture_mode=binding.fixture_mode,
        expected_transcript_sha256=binding.expected_transcript_sha256,
        expected_ledger_sha256=binding.expected_ledger_sha256,
        )

    with pytest.raises(PermissionError, match="scope"):
        verify_canonical_operation_binding(foreign, transcript_path=transcript)


def test_public_live_preflight_rejects_caller_constructed_binding_without_issuer_receipt(tmp_path, monkeypatch):
    from src.hybrid import generic_live_canonical_config as config
    from src.hybrid.generic_live_preflight import GenericLivePreflightVerifier

    binding, _transcript, _policy, _financial = _fixture(tmp_path)
    monkeypatch.setattr(config, "GENERIC_CANONICAL_BINDINGS", {binding.operation_id: binding})

    with pytest.raises(PermissionError, match="issuer.*unprovisioned"):
        GenericLivePreflightVerifier.production().verify(binding.operation_id, now=0)
