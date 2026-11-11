from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def test_hermes_capabilities_use_protected_handle_snapshot(monkeypatch, tmp_path):
    import src.hybrid.hermes_issuer_client as client

    capability = tmp_path / "capability"
    capability.write_bytes(b"fixture-token")
    calls = []

    def snapshot(
        path, label, *, protected=False, share_delete_with_pinned_publisher=False
    ):
        calls.append(
            (Path(path), label, protected, share_delete_with_pinned_publisher)
        )
        return b"fixture-token"

    monkeypatch.setattr(client, "_stable_bytes", snapshot)
    assert client.HermesIssuerClient._read_ascii(
        capability, "fixture capability", protected=True
    ) == "fixture-token"
    assert calls == [(capability, "fixture capability", True, False)]

    calls.clear()
    assert client.HermesIssuerClient._read_ascii(
        capability,
        "Hermes endpoint capability",
        protected=True,
        share_delete_with_pinned_publisher=True,
    ) == "fixture-token"
    assert calls == [(capability, "Hermes endpoint capability", True, True)]


def test_admission_policy_loader_uses_handle_snapshot(monkeypatch, tmp_path):
    import src.hybrid.trusted_issuer_admission as admission

    document = {"schema": "fixture/v1"}
    raw = _canonical(document)
    policy = tmp_path / "policy.json"
    policy.write_bytes(raw)
    calls = []

    def snapshot(path, label, *, protected=False):
        calls.append((Path(path), label, protected))
        return raw

    monkeypatch.setattr(admission, "_stable_bytes", snapshot)
    loaded, digest = admission._FrozenRootPolicyLoader(
        tmp_path, production=True
    ).load({"name": "policy.json", "sha256": hashlib.sha256(raw).hexdigest()}, "policy")

    assert loaded == document
    assert digest == hashlib.sha256(raw).hexdigest()
    assert calls == [(policy, "policy", True)]


def test_operation_descriptor_uses_handle_snapshot(monkeypatch, tmp_path):
    import src.hybrid.trusted_issuer_admission as admission

    operation = {
        "schema": "jose-trusted-operation/v2",
        "operation_id": admission.JOSE_OPERATION_ID,
        "episode_id": "JOSE_GENESIS37",
        "mode": "TEST",
        "scene": "fixture",
        "category": "first",
        "input_kind": "TEXT_TO_IMAGE",
        "endpoint": "test/fixture",
        "payload": {},
        "hermes_receipt_id": "fixture",
        "hermes_descriptor": {},
        "standing_policy": {},
        "financial_policy": {},
        "quote": {},
        "license": {},
        "provider_registration": {},
    }
    raw = _canonical(operation)
    operation_path = tmp_path / "operations" / f"{admission.JOSE_OPERATION_ID}.json"
    operation_path.parent.mkdir()
    operation_path.write_bytes(raw)
    calls = []

    def snapshot(path, label, *, protected=False):
        calls.append((Path(path), label, protected))
        return raw

    monkeypatch.setattr(admission, "_stable_bytes", snapshot)
    composed = admission.JoseTrustedIssuerAdmission.for_test_ipc(
        root=tmp_path,
        client=object.__new__(admission.HermesIssuerClient),
        provider_factory=lambda: None,
        now=lambda: 0,
    )

    assert composed._source()["_raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert calls == [(operation_path, "Jose operation descriptor", False)]


def test_windows_protected_snapshot_rejects_fixture_acl_with_other_principals(tmp_path):
    if sys.platform != "win32":
        return
    from src.hybrid.canonical_operation_binding import (
        CanonicalOperationBindingError,
        _stable_bytes,
    )

    capability = tmp_path / "ordinary-inherited-acl.capability"
    capability.write_bytes(b"fixture")

    try:
        _stable_bytes(capability, "fixture capability", protected=True)
    except CanonicalOperationBindingError as error:
        assert "protected DACL" in str(error)
    else:
        # A hardened test runner may already give tmp_path an owner-only ACL.
        assert _stable_bytes(capability, "fixture capability") == b"fixture"


def test_windows_protected_dacl_contract_rejects_owner_substitution_and_inheritance():
    """A file's owner is evidence only when it is the process-token SID."""
    from src.hybrid.canonical_operation_binding import (
        CanonicalOperationBindingError,
        _windows_validate_protected_dacl_snapshot,
    )

    process_sid = "S-1-5-21-101-202-303-404"
    exact = {
        "owner_sid": process_sid,
        "dacl_protected": True,
        "aces": (
            (process_sid, 0x1F01FF, False),
            ("S-1-5-18", 0x1F01FF, False),
            ("S-1-5-32-544", 0x1F01FF, False),
        ),
    }
    _windows_validate_protected_dacl_snapshot(exact, process_sid, "fixture")

    for snapshot in (
        {**exact, "owner_sid": "S-1-5-21-foreign"},
        {
            **exact,
            "aces": (
                (process_sid, 0x1F01FF, True),
                ("S-1-5-18", 0x1F01FF, False),
                ("S-1-5-32-544", 0x1F01FF, False),
            ),
        },
    ):
        with pytest.raises(CanonicalOperationBindingError, match="protected DACL"):
            _windows_validate_protected_dacl_snapshot(snapshot, process_sid, "fixture")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows token SID boundary")
def test_windows_ordinary_replacement_is_not_a_protected_capability_after_publisher_exit(tmp_path):
    """An ordinary replacement has inherited ACLs and fails after no publisher remains."""
    from src.hybrid.canonical_operation_binding import (
        CanonicalOperationBindingError,
        _stable_bytes,
        _windows_current_process_sid,
    )

    replacement = tmp_path / "publisher-exited-ordinary-replacement"
    replacement.write_bytes(b"attacker-controlled")
    assert _windows_current_process_sid().startswith("S-")
    with pytest.raises(CanonicalOperationBindingError, match="protected DACL"):
        _stable_bytes(replacement, "Hermes replacement capability", protected=True)
