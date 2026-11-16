"""Contract tests for the bounded offline Python guard."""

from __future__ import annotations

import importlib.util
import io
import os
import socket
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[2] / "scripts" / "run_offline_tests.py"


def _guard_module():
    spec = importlib.util.spec_from_file_location("offline_factory_guard", MODULE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _installed(guard, **kwargs):
    """Install a guard and always undo its process-global hooks."""
    restore = guard.install_guard(**kwargs)
    try:
        yield
    finally:
        restore()


@pytest.mark.parametrize(
    ("target", "blocked"),
    [
        (r"C:\HermesStudio\episodes\fixture\clip.mp4", True),
        (r"C:\HermesStudio\production\policy.json", True),
        (r"C:\Users\meira\Documents\HermesStudio\requests\job.json", True),
        (r"C:\scratch\fixture\auth.json", True),
        (r"C:\scratch\fixture\provider-credentials.toml", True),
        (r"C:\Users\meira\AppData\Local\hermes\cache\scratch\ok.txt", False),
    ],
)
def test_guard_classifies_inert_paths_lexically_before_backend(target: str, blocked: bool, monkeypatch):
    guard = _guard_module()
    seen: list[str] = []

    def backend(path, *args, **kwargs):
        seen.append(str(path))
        return io.BytesIO(b"TEST inert stream")

    monkeypatch.setattr(guard.builtins, "open", backend)
    for _ in _installed(guard):
        if blocked:
            with pytest.raises(RuntimeError, match="Protected|Secret"), open(target, "rb"):
                pass
            assert seen == []
        else:
            with open(target, "rb") as stream:
                assert stream.read() == b"TEST inert stream"
            assert seen == [target]


def test_guard_blocks_external_socket_and_keeps_loopback_asyncio(monkeypatch):
    guard = _guard_module()
    seen = []

    def connect(self, address):
        seen.append(address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    sock = socket.socket()
    for _ in _installed(guard):
        with pytest.raises(RuntimeError, match="Network disabled"):
            sock.connect(("198.51.100.7", 443))
        sock.connect(("127.0.0.1", 1))
        assert seen == [("127.0.0.1", 1)]


def _must_reject_protected_open():
    with (
        pytest.raises(RuntimeError, match="Protected"),
        open(r"C:\HermesStudio\episodes\never-probe.txt", "rb"),
    ):
        pass


def test_enforcement_disabled_fails_the_same_protection_predicate(monkeypatch):
    guard = _guard_module()
    seen = []

    def backend(path, *args, **kwargs):
        seen.append(str(path))
        return io.BytesIO(b"TEST inert stream")

    monkeypatch.setattr(guard.builtins, "open", backend)
    for _ in _installed(guard):
        _must_reject_protected_open()
    for _ in _installed(guard, enforce=False):
        with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
            _must_reject_protected_open()
    assert seen == [r"C:\HermesStudio\episodes\never-probe.txt"]


def test_pathlib_io_signature_is_checked_before_its_backend(monkeypatch):
    guard = _guard_module()
    seen = []

    def backend(path, mode="r", *args, **kwargs):
        seen.append((str(path), mode))
        return io.BytesIO(b"TEST inert stream")

    monkeypatch.setattr(io, "open", backend)
    for _ in _installed(guard):
        with (
            pytest.raises(RuntimeError, match="Protected"),
            Path(r"C:\HermesStudio\production\inert.json").open("rb"),
        ):
            pass
        assert seen == []


@pytest.mark.parametrize(
    "alias",
    [
        r"C:/HermesStudio/episodes/fixture/../clip.mp4",
        r"\\?\C:\HermesStudio\production\nested\..\policy.json",
        r"\\.\C:\Users\meira\Documents\HermesStudio\requests\job.json",
    ],
)
def test_guard_rejects_windows_aliases_without_touching_a_backend(alias, monkeypatch):
    guard = _guard_module()
    seen = []
    monkeypatch.setattr(guard.os, "stat", lambda value, *args, **kwargs: seen.append(value))
    for _ in _installed(guard):
        with pytest.raises(RuntimeError, match="Protected"):
            os.stat(alias)
    assert seen == []


def test_guard_covers_file_metadata_apis_and_restores_in_reverse(monkeypatch):
    guard = _guard_module()
    original_stat, original_lstat, original_access = os.stat, os.lstat, os.access
    seen = []
    monkeypatch.setattr(os, "stat", lambda value, *args, **kwargs: seen.append(("stat", value, args, kwargs)) or "stat")
    monkeypatch.setattr(os, "lstat", lambda value, *args, **kwargs: seen.append(("lstat", value, args, kwargs)) or "lstat")
    monkeypatch.setattr(os, "access", lambda value, *args, **kwargs: seen.append(("access", value, args, kwargs)) or True)
    restore = guard.install_guard()
    try:
        for operation in (os.stat, os.lstat, os.access):
            with pytest.raises(RuntimeError, match="Protected"):
                operation(r"C:\HermesStudio\episodes\inert.txt", 0)
        assert seen == []
        assert os.stat(r"C:\safe\inert.txt", follow_symlinks=False) == "stat"
        assert seen == [("stat", r"C:\safe\inert.txt", (), {"follow_symlinks": False})]
    finally:
        restore()
    assert os.stat is not original_stat
    assert os.lstat is not original_lstat
    assert os.access is not original_access
    assert os.stat(r"C:\safe\after-restore.txt") == "stat"


def test_guard_denies_connect_ex_and_create_connection_before_inert_spies(monkeypatch):
    guard = _guard_module()
    seen = []

    def connect_ex(sock, address):
        seen.append(("connect_ex", address))
        return 0

    def create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, *, all_errors=False):
        seen.append(("create_connection", address, timeout, source_address, all_errors))
        return object()

    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "create_connection", create_connection)
    sock = socket.socket()
    for _ in _installed(guard):
        with pytest.raises(RuntimeError, match="Network disabled"):
            sock.connect_ex(("198.51.100.9", 443))
        with pytest.raises(RuntimeError, match="Network disabled"):
            socket.create_connection(("example.test", 443), timeout=3, source_address=("127.0.0.1", 0), all_errors=True)
        assert sock.connect_ex(("127.0.0.1", 1)) == 0
        assert socket.create_connection(("localhost", 2), timeout=4, source_address=("127.0.0.1", 0), all_errors=True)
    assert seen == [("connect_ex", ("127.0.0.1", 1)), ("create_connection", ("localhost", 2), 4, ("127.0.0.1", 0), True)]
