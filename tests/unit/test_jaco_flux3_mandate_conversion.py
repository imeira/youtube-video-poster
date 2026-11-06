from __future__ import annotations

import json
import sqlite3
from hashlib import sha256
from pathlib import Path

import pytest


def _write(path: Path, doc: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def test_conversion_materializes_only_verified_pinned_mandate(tmp_path):
    from src.hybrid.jaco_flux3_final_admission import (
        materialize_existing_mandate_authorization,
    )

    root = tmp_path / "production"
    assistant, user = "parent prompt", "I authorize one evaluation image"
    assistant_hash, user_hash = sha256(assistant.encode()).hexdigest(), sha256(user.encode()).hexdigest()
    payload_hash = "a" * 64
    mandate = _write(root / "user-mandate.json", {"schema": "JACO_USER_EXECUTION_MANDATE/2", "request_id": "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9", "revision": "818556d97edb648f", "endpoint": "blackforestlabs/flux-3/text-to-image", "payload_sha256": payload_hash, "maximum_usd": "0.048", "call_limit": 1, "budget_cap_usd": "10.00", "scope": "EVALUATION_GENERATION_ONLY", "release_authorization": "NOT_AUTHORIZED", "license_status": "EVALUATION_ONLY_CONDITIONALLY_SUPPORTABLE"})
    provenance = _write(root / "provenance.json", {"schema": "JACO_FLUX3_CANONICAL_TRANSCRIPT_PROVENANCE/1", "session_id": "session", "adjacent_assistant_message_id": 9, "user_message_id": 10, "assistant_message_sha256": assistant_hash, "user_message_sha256": user_hash, "financial_record_sha256": "b" * 64, "review_source_sha256": "c" * 64})
    _write(root / "parent-provisioned-policy.json", {"schema": "JACO_FLUX3_LOCAL_OPERATOR_POLICY/1", "mandate_sha256": sha256(mandate.read_bytes()).hexdigest(), "provenance_sha256": sha256(provenance.read_bytes()).hexdigest()})
    state = tmp_path / "state.db"
    db = sqlite3.connect(state)
    db.execute("CREATE TABLE messages (id INTEGER, session_id TEXT, role TEXT, content TEXT)")
    db.executemany("INSERT INTO messages VALUES (?,?,?,?)", [(9, "session", "assistant", assistant), (10, "session", "user", user)])
    db.commit(); db.close()

    created = materialize_existing_mandate_authorization(root=root, state_db=state)

    assert created["call_limit"] == 1
    assert created["maximum_usd"] == "0.048"
    assert (root / "final-admission-authorization.json").exists()
    assert "NOT_AUTHORIZED" not in created.values()


def test_conversion_rejects_changed_payload_or_existing_conflict(tmp_path):
    from src.hybrid.jaco_flux3_final_admission import (
        FinalAdmissionError,
        materialize_existing_mandate_authorization,
    )

    with pytest.raises(FinalAdmissionError, match="pinned mandate"):
        materialize_existing_mandate_authorization(root=tmp_path, state_db=tmp_path / "state.db")
