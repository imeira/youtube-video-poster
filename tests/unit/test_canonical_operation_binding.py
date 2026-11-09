"""Tests for read-only canonical mandate and financial bindings."""
from __future__ import annotations

import hashlib
import json
import sqlite3
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
    )

    with pytest.raises(PermissionError, match="scope"):
        verify_canonical_operation_binding(foreign, transcript_path=transcript)


def test_public_live_preflight_returns_only_binding_evidence_when_fixed_binding_is_provisioned(tmp_path, monkeypatch):
    from src.hybrid import generic_live_canonical_config as config
    from src.hybrid.generic_live_preflight import GenericLivePreflightVerifier

    binding, _transcript, _policy, _financial = _fixture(tmp_path)
    monkeypatch.setattr(config, "GENERIC_CANONICAL_BINDINGS", {binding.operation_id: binding})

    result = GenericLivePreflightVerifier.production().verify(binding.operation_id, now=0)

    assert result["status"] == "CANONICAL_BINDING_VERIFIED"
    assert result["live_enabled"] is False
    assert result["dispatch_permitted"] is False
    assert set(result["blocked"]) == {"quote", "independent_review", "license"}
    assert result["canonical_binding"]["operation_policy_sha256"] != result["canonical_binding"]["financial_policy_sha256"]
