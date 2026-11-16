"""Guard-only regressions; no project modules or protected I/O."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

MODULE = Path(__file__).resolve().parents[2] / "scripts" / "run_offline_tests.py"


def _guard_module():
    spec = importlib.util.spec_from_file_location("guard_parent_probe", MODULE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("path", [
    r"\HermesStudio\production\inert.json",
    r"\HermesStudio\episodes\inert.json",
    r"\Users\meira\Documents\HermesStudio\requests\inert.json",
])
def test_current_drive_rooted_alias_is_denied_before_metadata_backend(path, monkeypatch):
    guard = _guard_module()
    seen = []
    blocked = False
    with monkeypatch.context() as patch:
        patch.setattr(os, "getcwd", lambda: r"C:\owned-inert")
        patch.setattr(os, "stat", lambda value, *args, **kwargs: seen.append(value))
        restore = guard.install_guard()
        try:
            try:
                os.stat(path)
            except RuntimeError as error:
                blocked = str(error) == "Protected production directory"
        finally:
            restore()
    assert blocked, "root-relative alias reached the inert metadata backend"
    assert seen == []


def _hook_snapshot():
    import builtins
    import io
    import socket

    import aiohttp

    owners = [
        (builtins, "open"), (io, "open"),
        *((os, name) for name in (
            "open", "listdir", "scandir", "stat", "lstat", "access",
        )),
        *((socket.socket, name) for name in ("connect", "connect_ex", "bind", "sendto")),
        (socket, "create_connection"), (socket, "getaddrinfo"),
        (aiohttp.ClientSession, "_request"),
    ]
    if hasattr(socket.socket, "sendmsg"):
        owners.append((socket.socket, "sendmsg"))
    return [(owner, name, getattr(owner, name)) for owner, name in owners]


def test_spawn_bootstrap_keeps_guard_installed_without_starting_pytest(monkeypatch):
    import sys

    source = MODULE.read_bytes()
    denied = r"C:\HermesStudio\production\inert-spawn.json"
    seen = []
    blocked = False
    project_before = {name for name in sys.modules if name == "src" or name.startswith("src.")}
    pytest_calls = []
    namespace = {"__name__": "__mp_main__", "__file__": str(MODULE)}
    with monkeypatch.context() as patch:
        patch.setattr(pytest, "main", lambda *args, **kwargs: pytest_calls.append((args, kwargs)))
        original_stat = os.stat

        def stat(path, *args, **kwargs):
            if str(path) == denied:
                seen.append(path)
                return object()
            return original_stat(path, *args, **kwargs)

        patch.setattr(os, "stat", stat)
        hooks = _hook_snapshot()
        environment = dict(os.environ)
        try:
            # Execute the exact wrapper bytes in the Windows spawn module role.
            exec(compile(source, str(MODULE), "exec"), namespace)  # noqa: S102
            try:
                os.stat(denied)
            except RuntimeError as error:
                blocked = str(error) == "Protected production directory"
        finally:
            for owner, name, original in reversed(hooks):
                setattr(owner, name, original)
            os.environ.clear()
            os.environ.update(environment)
    assert blocked, "spawn role returned without enforcing the protected-root guard"
    assert seen == []
    assert {name for name in sys.modules if name == "src" or name.startswith("src.")} == project_before
    assert pytest_calls == []


def test_scratch_root_is_rejected_before_directory_backends(monkeypatch):
    guard = _guard_module()
    seen = []
    blocked = False
    with monkeypatch.context() as patch:
        patch.setenv("TMPDIR", "C:/HermesStudio/production")
        patch.setattr(Path, "mkdir", lambda *args, **kwargs: seen.append("mkdir"))
        patch.setattr(guard.tempfile, "TemporaryDirectory", lambda *args, **kwargs: seen.append("tempdir"))
        try:
            guard._scratch_directory()
        except RuntimeError as error:
            blocked = str(error) == "Protected production directory"
    assert blocked, "protected scratch root reached an inert directory backend"
    assert seen == []


@pytest.mark.parametrize("allowed", [True, False])
def test_http_keyword_route_preserves_loopback_and_denies_external(allowed, monkeypatch):
    import asyncio

    import aiohttp

    guard = _guard_module()
    seen = []
    timeout = object()
    url = "http://127.0.0.1/inert" if allowed else "https://example.test/inert"

    async def backend(session, method, **kwargs):
        seen.append((method, kwargs))
        return "inert-result"

    async def operation():
        await asyncio.sleep(0)
        return await aiohttp.ClientSession._request(
            None, method="GET", str_or_url=url, timeout=timeout,
        )

    with monkeypatch.context() as patch:
        patch.setattr(aiohttp.ClientSession, "_request", backend)
        restore_guard = guard.install_guard()
        try:
            restore_http = guard._block_aiohttp()
            try:
                if allowed:
                    assert asyncio.run(operation()) == "inert-result"
                else:
                    with pytest.raises(RuntimeError, match="External HTTP disabled"):
                        asyncio.run(operation())
            finally:
                restore_http()
        finally:
            restore_guard()
    if allowed:
        assert len(seen) == 1
        assert seen[0][0] == "GET"
        assert set(seen[0][1]) == {"str_or_url", "timeout"}
        assert seen[0][1]["str_or_url"] is url
        assert seen[0][1]["timeout"] is timeout
    else:
        assert seen == []


@pytest.mark.parametrize("fail_during_install", [False, True])
def test_all_hook_identities_restore_on_success_and_partial_install_failure(fail_during_install, monkeypatch):
    guard = _guard_module()
    hooks = _hook_snapshot()
    originals = {(id(owner), name): original for owner, name, original in hooks}
    installed = []
    restored = []

    def setter(owner, name, value):
        key = (id(owner), name)
        is_restore = value is originals[key]
        if fail_during_install and owner is os and name == "lstat" and not is_restore:
            raise RuntimeError("inert partial-install failure")
        setattr(owner, name, value)
        (restored if is_restore else installed).append(key)

    with monkeypatch.context() as patch:
        patch.setattr(guard, "setattr", setter, raising=False)
        if fail_during_install:
            with pytest.raises(RuntimeError, match="inert partial-install failure"):
                guard.install_guard()
        else:
            restore = guard.install_guard()
            restore()
    assert installed
    assert restored == list(reversed(installed))
    assert all(getattr(owner, name) is original for owner, name, original in hooks)


def test_real_local_socketpair_survives_the_guard():
    import socket

    guard = _guard_module()
    restore = guard.install_guard()
    try:
        left, right = socket.socketpair()
        with left, right:
            left.settimeout(2)
            right.settimeout(2)
            left.sendall(b"TEST loopback")
            assert right.recv(32) == b"TEST loopback"
    finally:
        restore()


@pytest.mark.parametrize("with_flags", [False, True])
@pytest.mark.parametrize("denied_host", ["198.51.100.55", "example.test", b"127.\xff0.0.1"])
def test_udp_sendto_denies_before_spy_and_preserves_allowed_signature(with_flags, denied_host, monkeypatch):
    import socket

    guard = _guard_module()
    calls = []

    def backend(sock, data, *arguments):
        calls.append((sock, data, arguments))
        return len(data)

    monkeypatch.setattr(socket.socket, "sendto", backend)
    payload = bytearray(b"TEST datagram")
    local = ("127.0.0.1", 9)
    external = (denied_host, 9)
    allowed_args = (0, local) if with_flags else (local,)
    denied_args = (0, external) if with_flags else (external,)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        restore = guard.install_guard()
        try:
            assert sock.sendto(payload, *allowed_args) == len(payload)
            with pytest.raises(RuntimeError, match="Network disabled"):
                sock.sendto(payload, *denied_args)
        finally:
            restore()
    assert len(calls) == 1
    assert calls[0][0] is sock and calls[0][1] is payload
    assert len(calls[0][2]) == len(allowed_args)
    assert all(got is expected for got, expected in zip(calls[0][2], allowed_args, strict=True))


@pytest.mark.parametrize("explicit_address", [True, False])
def test_optional_sendmsg_checks_destination_without_changing_arguments(explicit_address, monkeypatch):
    import socket

    guard = _guard_module()
    calls = []
    peer = [("127.0.0.1", 9)]

    def backend(sock, buffers, *arguments, **keywords):
        calls.append((sock, buffers, arguments, keywords))
        return 4

    # Windows lacks sendmsg; an inert backend exercises its optional wrapper.
    monkeypatch.setattr(socket.socket, "sendmsg", backend, raising=False)
    monkeypatch.setattr(socket.socket, "getpeername", lambda sock: peer[0])
    buffers = [b"TEST"]
    ancillary = []
    allowed_args = (ancillary, 0, peer[0]) if explicit_address else ()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        restore = guard.install_guard()
        try:
            assert sock.sendmsg(buffers, *allowed_args) == 4
            peer[0] = ("198.51.100.55", 9)
            denied_args = (ancillary, 0, peer[0]) if explicit_address else ()
            with pytest.raises(RuntimeError, match="Network disabled"):
                sock.sendmsg(buffers, *denied_args)
        finally:
            restore()
    assert len(calls) == 1
    assert calls[0][0] is sock and calls[0][1] is buffers
    assert len(calls[0][2]) == len(allowed_args)
    assert all(got is expected for got, expected in zip(calls[0][2], allowed_args, strict=True))
    assert calls[0][3] == {}


def test_disabled_datagram_enforcement_fails_the_same_inert_check(monkeypatch):
    import socket

    guard = _guard_module()
    calls = []
    monkeypatch.setattr(socket.socket, "sendto", lambda sock, data, address: calls.append(address) or len(data))
    external = ("198.51.100.55", 9)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        def must_reject():
            with pytest.raises(RuntimeError, match="Network disabled"):
                sock.sendto(b"TEST inert mutation", external)

        restore = guard.install_guard()
        try:
            must_reject()
        finally:
            restore()
        assert calls == []
        restore = guard.install_guard(enforce=False)
        try:
            with pytest.raises(pytest.fail.Exception, match="DID NOT RAISE"):
                must_reject()
        finally:
            restore()
    assert calls == [external]


def test_real_udp_loopback_survives_the_guard():
    import socket

    guard = _guard_module()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
        restore = guard.install_guard()
        try:
            receiver.settimeout(2)
            sender.settimeout(2)
            receiver.bind(("127.0.0.1", 0))
            payload = b"TEST local datagram"
            assert sender.sendto(payload, receiver.getsockname()) == len(payload)
            received, address = receiver.recvfrom(1024)
            assert received == payload
            assert address[0] == "127.0.0.1"
        finally:
            restore()
