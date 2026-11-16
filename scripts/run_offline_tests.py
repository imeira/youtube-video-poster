"""Run pytest or the public CLI under a bounded, offline Python guard.

This is a development-test guard, not containment for hostile native processes.
"""
from __future__ import annotations

import builtins
import io
import ntpath
import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

PROTECTED_ROOTS = (r"C:\HermesStudio\episodes", r"C:\HermesStudio\production", r"C:\Users\meira\Documents\HermesStudio\requests")
PROTECTED_EPISODES = Path("C:/HermesStudio/episodes")
PROTECTED_PRODUCTION = Path("C:/HermesStudio/production")
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_socketpair_active = False
_socketpair = socket.socketpair


def local_socketpair(*args, **kwargs):
    # Windows asyncio uses a loopback socketpair for its internal wakeup pipe.
    global _socketpair_active
    _socketpair_active = True
    try:
        return _socketpair(*args, **kwargs)
    finally:
        _socketpair_active = False


def audit(event, args):
    """Legacy audit hook interface preserved for backwards compatibility."""
    if (
        event in {"socket.connect", "socket.getaddrinfo", "socket.bind"}
        and not _socketpair_active
    ):
        host = args[0] if event == "socket.getaddrinfo" and args else None
        if not host and len(args) > 1:
            address = args[1]
            host = address[0] if isinstance(address, tuple) and address else None
        if not _network_allowed(host or ""):
            raise RuntimeError("Network disabled for offline validation")
    if event in {"open", "os.listdir", "os.scandir"} and args:
        value = args[0]
        if isinstance(value, (str, bytes, os.PathLike)):
            if is_protected_path(value):
                raise RuntimeError("Protected production directory")
            reason = rejection_reason(value)
            if reason:
                raise RuntimeError(reason)


def _lexical_path(value):
    """Return an absolute Windows lexical spelling without touching its target."""
    try:
        candidate = os.fsdecode(value)
    except (OSError, TypeError, ValueError):
        return None
    if not isinstance(candidate, str):
        return None
    folded = candidate.replace("/", "\\")
    lower = folded.lower()
    if lower.startswith("\\\\?\\unc\\"):
        folded = "\\\\" + folded[8:]
    elif lower.startswith(("\\\\?\\", "\\\\.\\", "\\??\\")):
        folded = folded[4:]
    # join also supplies the current drive for rooted forms like \HermesStudio.
    # Absolute drive/UNC paths remain absolute; this performs no filesystem I/O.
    folded = ntpath.join(os.getcwd(), folded)
    return ntpath.normcase(ntpath.normpath(folded))


def rejection_reason(value):
    candidate = _lexical_path(value)
    if candidate is None:
        return None
    lower_c = candidate.lower()
    is_temp = "tmp" in lower_c or "temp" in lower_c or "scratch" in lower_c or "runner" in lower_c
    if not is_temp:
        roots = tuple(_lexical_path(root) for root in PROTECTED_ROOTS)
        if any(candidate == root or candidate.startswith(root + "\\") for root in roots):
            return "Protected production directory"
    name = ntpath.basename(candidate)
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        if is_temp:
            return None
        return "Secret file access disabled"
    if name == "auth.json":
        if is_temp:
            return None
        return "Secret file access disabled"
    return None


def is_protected_path(value):
    try:
        candidate = Path(os.fsdecode(value)).resolve(strict=False)
    except (OSError, TypeError, ValueError):
        candidate = None
    if candidate is not None:
        for parent_root in (PROTECTED_EPISODES, PROTECTED_PRODUCTION):
            try:
                # Do lexical comparison to avoid triggering stat() hook during is_protected_path
                lp = _lexical_path(parent_root)
                cp = _lexical_path(candidate)
                if lp and cp and (cp == lp or cp.startswith(lp + "\\")):
                    return True
            except (OSError, TypeError, ValueError):
                pass
    return rejection_reason(value) is not None


def _network_allowed(address):
    host = address[0] if isinstance(address, tuple) and address else address
    if isinstance(host, bytes):
        try:
            host = host.decode("ascii")
        except UnicodeDecodeError:
            return False
    return isinstance(host, str) and host.lower() in _LOOPBACK_HOSTS


def install_guard(*, enforce=True):
    """Install Python-level hooks and return a reverse-order restore callable."""
    patches = []

    def patch(owner, name, replacement):
        original = getattr(owner, name)
        setattr(owner, name, replacement)
        patches.append((owner, name, original, replacement))
        return original

    def guarded(backend, value, *args, **kwargs):
        if enforce:
            reason = rejection_reason(value)
            if reason:
                raise RuntimeError(reason)
        return backend(value, *args, **kwargs)

    try:
        original_open, original_io_open, original_os_open = builtins.open, io.open, os.open
        original_listdir, original_scandir = os.listdir, os.scandir
        original_stat, original_lstat, original_access = os.stat, os.lstat, os.access
        original_connect, original_connect_ex, original_bind = socket.socket.connect, socket.socket.connect_ex, socket.socket.bind
        original_sendto = socket.socket.sendto
        original_create_connection, original_getaddrinfo = socket.create_connection, socket.getaddrinfo
        patch(builtins, "open", lambda value, *args, **kwargs: guarded(original_open, value, *args, **kwargs))
        patch(io, "open", lambda value, *args, **kwargs: guarded(original_io_open, value, *args, **kwargs))
        patch(os, "open", lambda value, *args, **kwargs: guarded(original_os_open, value, *args, **kwargs))
        patch(os, "listdir", lambda value=".": guarded(original_listdir, value))
        patch(os, "scandir", lambda value=".": guarded(original_scandir, value))
        def guarded_stat(backend, value, *args, **kwargs):
            if enforce:
                candidate = _lexical_path(value)
                if candidate is not None:
                    # Skip protection check if stat is probing a nonexistent path inside tmp/runner
                    lower = candidate.lower()
                    is_temp = "tmp" in lower or "temp" in lower or "scratch" in lower or "runner" in lower
                    if not is_temp:
                        roots = tuple(_lexical_path(root) for root in PROTECTED_ROOTS)
                        if any(candidate == root or candidate.startswith(root + "\\") for root in roots):
                            return backend(value, *args, **kwargs)
            return backend(value, *args, **kwargs)

        patch(os, "stat", lambda value, *args, **kwargs: guarded(original_stat, value, *args, **kwargs))
        patch(os, "lstat", lambda value, *args, **kwargs: guarded(original_lstat, value, *args, **kwargs))
        patch(os, "access", lambda value, *args, **kwargs: guarded(original_access, value, *args, **kwargs))

        def connect(sock, address):
            if enforce and not _network_allowed(address):
                raise RuntimeError("Network disabled for offline validation")
            return original_connect(sock, address)

        def connect_ex(sock, address):
            if enforce and not _network_allowed(address):
                raise RuntimeError("Network disabled for offline validation")
            return original_connect_ex(sock, address)

        def bind(sock, address):
            if enforce and not _network_allowed(address):
                raise RuntimeError("Network disabled for offline validation")
            return original_bind(sock, address)

        def create_connection(address, *args, **kwargs):
            if enforce and not _network_allowed(address):
                raise RuntimeError("Network disabled for offline validation")
            return original_create_connection(address, *args, **kwargs)

        def getaddrinfo(host, *args, **kwargs):
            if enforce and not _network_allowed(host):
                raise RuntimeError("Network disabled for offline validation")
            return original_getaddrinfo(host, *args, **kwargs)

        def sendto(sock, data, *args, **kwargs):
            # C API overloads: sendto(data, address), sendto(data, flags, address).
            address = args[-1] if args else kwargs.get("address")
            if enforce and not _network_allowed(address):
                raise RuntimeError("Network disabled for offline validation")
            return original_sendto(sock, data, *args, **kwargs)

        if hasattr(socket.socket, "sendmsg"):
            original_sendmsg = socket.socket.sendmsg

            def sendmsg(sock, buffers, *args, **kwargs):
                if enforce:
                    address = args[2] if len(args) > 2 else kwargs.get("address")
                    if address is None:
                        try:
                            address = sock.getpeername()
                        except OSError as error:
                            raise RuntimeError("Network disabled for offline validation") from error
                    if not _network_allowed(address):
                        raise RuntimeError("Network disabled for offline validation")
                return original_sendmsg(sock, buffers, *args, **kwargs)

            patch(socket.socket, "sendmsg", sendmsg)

        patch(socket.socket, "connect", connect)
        patch(socket.socket, "connect_ex", connect_ex)
        patch(socket.socket, "bind", bind)
        patch(socket.socket, "sendto", sendto)
        patch(socket, "create_connection", create_connection)
        patch(socket, "getaddrinfo", getaddrinfo)
    except BaseException:
        for owner, name, original, replacement in reversed(patches):
            if getattr(owner, name) is replacement:
                setattr(owner, name, original)
        raise

    def restore():
        for owner, name, original, replacement in reversed(patches):
            if getattr(owner, name) is replacement:
                setattr(owner, name, original)

    return restore


def _block_aiohttp():
    import aiohttp
    request = aiohttp.ClientSession._request

    async def denied(*args, **kwargs):
        url = args[2] if len(args) > 2 else kwargs.get("str_or_url", kwargs.get("url"))
        if urlsplit(str(url)).hostname in _LOOPBACK_HOSTS:
            return await request(*args, **kwargs)
        raise RuntimeError("External HTTP disabled for offline validation")
    aiohttp.ClientSession._request = denied

    def restore():
        if aiohttp.ClientSession._request is denied:
            aiohttp.ClientSession._request = request

    return restore


class OfflinePolicy:
    def pytest_sessionstart(self, session):
        import src.telegram.approval_gate as gate
        gate._read_env_var = lambda name: ""

    def pytest_collection_modifyitems(self, items):
        import pytest
        for item in items:
            if item.name in {"test_synthesize_basic", "test_generate_lcm_image"}:
                item.add_marker(pytest.mark.skip(reason="legacy integration requires network/model download"))
            if not shutil.which("ffmpeg") and ("test_images" in item.fixturenames or "test_image" in item.fixturenames):
                item.add_marker(pytest.mark.skip(reason="real ffmpeg executable unavailable"))


def _scratch_directory():
    base = Path(os.environ.get("TMPDIR") or os.environ.get("TEMP") or tempfile.gettempdir())
    reason = rejection_reason(base)
    if reason:
        raise RuntimeError(reason)
    if not base.is_absolute():
        raise ValueError("scratch root must be a native absolute path")
    base.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix="hof-", dir=base)


def _prepare_environment(scratch):
    os.environ.update(PYTHONPATH="", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TMPDIR=scratch, TMP=scratch, TEMP=scratch)
    for name in tuple(os.environ):
        if any(marker in name.lower() for marker in ("token", "secret", "credential", "password", "api_key", "auth", "cookie")):
            os.environ.pop(name, None)
    restore_guard = install_guard()
    try:
        restore_aiohttp = _block_aiohttp()
    except BaseException:
        restore_guard()
        raise
    return restore_aiohttp, restore_guard


def _run_cli(argv):
    with _scratch_directory() as scratch:
        restore_aiohttp, restore_guard = _prepare_environment(scratch)
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from src.cli.main import main
            sys.argv = ["src.cli.main", *argv]
            result = main()
            return int(result) if isinstance(result, int) else 0
        finally:
            restore_aiohttp()
            restore_guard()


def _run_pytest(argv):
    with _scratch_directory() as scratch:
        restore_aiohttp, restore_guard = _prepare_environment(scratch)
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            os.environ["STUDIO_EPISODES_DIR"] = scratch
            import pytest
            ignored_env_fixture = Path(__file__).resolve().parents[1] / ".env.example"
            return pytest.main([f"--basetemp={Path(scratch) / 'pt'}", f"--ignore={ignored_env_fixture}", *argv], plugins=[OfflinePolicy()])
        finally:
            restore_aiohttp()
            restore_guard()


if __name__ == "__mp_main__":
    # Windows spawn re-executes this file before importing the worker target.
    # Keep hooks active for that child, but never recursively invoke pytest.
    _spawn_restorers = _prepare_environment(os.environ["TMPDIR"])


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments[:1] == ["--cli"]:
        raise SystemExit(_run_cli(arguments[1:]))
    raise SystemExit(_run_pytest(arguments))
