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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows native handle boundary")
def test_windows_reader_never_opens_junction_target_after_publisher_exit(tmp_path, monkeypatch):
    """A pinned parent must make the final capability open relative to that handle."""
    import ctypes
    import subprocess

    import src.hybrid.canonical_operation_binding as binding

    root, moved, target = tmp_path / "publisher", tmp_path / "moved", tmp_path / "target"
    root.mkdir()
    target.mkdir()
    capability, attacker_capability = root / "capability", target / "capability"
    capability.write_bytes(b"publisher-capability")
    attacker_capability.write_bytes(b"attacker-capability")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    original_create, original_read = kernel32.CreateFileW, kernel32.ReadFile
    original_information = kernel32.GetFileInformationByHandle
    original_information.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    original_information.restype = ctypes.c_int
    original_final = kernel32.GetFinalPathNameByHandleW
    original_final.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32]
    original_final.restype = ctypes.c_uint32
    invalid = ctypes.c_void_p(-1).value
    counters = {"target_opens": 0, "target_reads": 0}
    target_handles: set[int] = set()
    swapped = False
    inspected_paths = []

    def final_path(handle):
        size = original_final(handle, None, 0, 0)
        buffer = ctypes.create_unicode_buffer(size + 1)
        assert original_final(handle, buffer, len(buffer), 0)
        return buffer.value.removeprefix("\\\\?\\").casefold()

    def counted_create(path, *args):
        handle = original_create(path, *args)
        if handle != invalid and final_path(handle).startswith(str(target).casefold()):
            counters["target_opens"] += 1
            target_handles.add(handle)
        return handle

    def counted_read(handle, *args):
        if handle in target_handles:
            counters["target_reads"] += 1
        return original_read(handle, *args)

    def swap_after_pinning(handle, info):
        nonlocal swapped
        result = original_information(handle, info)
        observed_path = final_path(handle)
        inspected_paths.append(observed_path)
        if not swapped and result and observed_path.rstrip("\\").endswith("\\publisher"):
            assert kernel32.MoveFileExW(str(root), str(moved), 0)
            linked = subprocess.run(
                ["cmd.exe", "/c", "mklink", "/J", str(root), str(target)],
                check=False,
                capture_output=True,
            )
            assert linked.returncode == 0, linked.stderr.decode(errors="replace")
            swapped = True
        return result

    class KernelProxy:
        pass

    proxy = KernelProxy()
    for name in ("CloseHandle", "GetFinalPathNameByHandleW"):
        setattr(proxy, name, getattr(kernel32, name))
    proxy.CreateFileW = counted_create
    proxy.ReadFile = counted_read
    proxy.GetFileInformationByHandle = swap_after_pinning
    original_windll = binding.ctypes.WinDLL
    monkeypatch.setattr(
        binding.ctypes,
        "WinDLL",
        lambda name, **kwargs: proxy if name.lower() == "kernel32" else original_windll(name, **kwargs),
    )

    with pytest.raises(binding.CanonicalOperationBindingError) as error:
        binding._stable_bytes(
            capability,
            "post-exit capability",
            share_delete_with_pinned_publisher=True,
        )
    assert swapped, (str(error.value), inspected_paths)
    assert counters == {"target_opens": 0, "target_reads": 0}

    # Positive control: the counters observe a real target handle and ReadFile.
    handle = proxy.CreateFileW(str(attacker_capability), 0x80000000, 0x3, None, 3, 0, None)
    try:
        buffer, count = ctypes.create_string_buffer(64), ctypes.c_uint32()
        assert proxy.ReadFile(handle, buffer, len(buffer), ctypes.byref(count), None)
        assert count.value == len(b"attacker-capability")
    finally:
        kernel32.CloseHandle(handle)
    assert counters == {"target_opens": 1, "target_reads": 1}
