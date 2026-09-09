"""OS-released byte locks: bounded workers survive process crashes without leases."""

import asyncio
import errno
import os
from contextlib import asynccontextmanager


def try_lock(path):
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
