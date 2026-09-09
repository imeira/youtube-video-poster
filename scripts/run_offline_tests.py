"""Run pytest with forbidden asset paths, secret files and network blocked."""

import os
import shutil
import socket
import sys
import tempfile
from pathlib import Path

_socketpair_active = False
_socketpair = socket.socketpair
PROTECTED_EPISODES = Path("C:/HermesStudio/episodes").resolve(strict=False)


def local_socketpair(*args, **kwargs):
    # Windows asyncio uses a loopback socketpair for its internal wakeup pipe.
    global _socketpair_active
    _socketpair_active = True
    try:
        return _socketpair(*args, **kwargs)
    finally:
        _socketpair_active = False


def is_protected_path(value):
    try:
        candidate = Path(os.fsdecode(value)).resolve(strict=False)
    except (OSError, TypeError, ValueError):
        return False
    return candidate == PROTECTED_EPISODES or PROTECTED_EPISODES in candidate.parents


def audit(event, args):
    if (
        event in {"socket.connect", "socket.getaddrinfo", "socket.bind"}
        and not _socketpair_active
    ):
        raise RuntimeError("Network disabled for offline validation")
    if event in {"open", "os.listdir", "os.scandir"} and args:
        value = args[0]
        if isinstance(value, (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(value)).resolve(strict=False)
            if is_protected_path(path):
                raise RuntimeError("Protected episode directory")
            if path.name.lower() == ".env" or path.name.lower().startswith(".env."):
                raise RuntimeError("Secret file access disabled")


class OfflinePolicy:
    def pytest_sessionstart(self, session):
        # Legacy Director tests construct Telegram without injected credentials.
        # Suppress the legacy secret-file lookup; this is not a live adapter.
        import src.telegram.approval_gate as gate

        gate._read_env_var = lambda name: ""
        # aiohttp may use c-ares DNS, which bypasses Python socket audit hooks.
        import aiohttp

        async def denied(*args, **kwargs):
            raise RuntimeError("External HTTP disabled for offline validation")

        aiohttp.ClientSession._request = denied

    def pytest_collection_modifyitems(self, items):
        import pytest

        for item in items:
            if item.name in {"test_synthesize_basic", "test_generate_lcm_image"}:
                item.add_marker(
                    pytest.mark.skip(
                        reason="legacy integration requires network/model download"
                    )
                )
            if not shutil.which("ffmpeg") and (
                "test_images" in item.fixturenames or "test_image" in item.fixturenames
            ):
                item.add_marker(
                    pytest.mark.skip(reason="real ffmpeg executable unavailable")
                )


if __name__ == "__main__":
    os.environ["PYTHONPATH"] = ""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    with tempfile.TemporaryDirectory(prefix="hybrid-offline-") as root:
        os.environ["STUDIO_EPISODES_DIR"] = root
        socket.socketpair = local_socketpair
        sys.addaudithook(audit)
        import pytest

        raise SystemExit(pytest.main(sys.argv[1:], plugins=[OfflinePolicy()]))
