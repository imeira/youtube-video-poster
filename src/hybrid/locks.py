"""OS-released byte locks: bounded workers survive process crashes without leases."""

import asyncio
import errno
import os
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path


def _is_reparse_point(path: Path) -> bool:
    """Treat Windows reparse points and POSIX symlinks as unsafe indirection."""
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return False
    attributes = getattr(status, "st_file_attributes", 0)
    return path.is_symlink() or bool(attributes & 0x400)


def reject_reparse_path(path: Path) -> Path:
    """Reject a lexical path or existing ancestor before it is resolved or read."""
    lexical = Path(os.path.abspath(os.fspath(path)))
    for candidate in (lexical, *lexical.parents):
        if _is_reparse_point(candidate):
            raise ValueError("local input rejects symlink or reparse-point paths")
    return lexical


def try_lock(path):
    """Create and acquire a lock only through a lexical reparse-safe path."""
    path = os.fspath(reject_reparse_path(Path(path)))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # A directory can be swapped for a reparse point while mkdir runs; reject
    # that race before the lock file is opened or created.
    path = os.fspath(reject_reparse_path(Path(path)))
    if os.name == "nt":
        path = os.path.abspath(path)
        if not path.startswith("\\\\?\\"):
            path = (
                ("\\\\?\\UNC\\" + path[2:])
                if path.startswith("\\\\")
                else ("\\\\?\\" + path)
            )
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        os.lseek(fd, 0, os.SEEK_SET)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError as error:
        os.close(fd)
        if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
            raise
        return None


@contextmanager
def file_lock(path, *, timeout=None):
    """Acquire one crash-released interprocess lock without re-entering it."""
    if timeout is not None and (not isinstance(timeout, (int, float)) or timeout <= 0):
        raise ValueError("file lock timeout must be a positive number of seconds")
    fd = None
    deadline = None
    if timeout is not None:
        import time
        deadline = time.monotonic() + timeout
    try:
        while fd is None:
            fd = try_lock(path)
            if fd is None:
                import time

                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out acquiring file lock: {path}")
                time.sleep(0.02)
        yield
    finally:
        if fd is not None:
            os.close(fd)


@asynccontextmanager
async def file_slot(paths):
    fd = None
    try:
        while fd is None:
            for path in paths:
                fd = try_lock(path)
                if fd is not None:
                    break
            if fd is None:
                await asyncio.sleep(0.02)
        yield
    finally:
        if fd is not None:
            os.close(fd)
