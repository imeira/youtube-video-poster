import os
import subprocess
import threading
from pathlib import Path

import pytest


def test_try_lock_rejects_simulated_reparse_ancestor_before_mkdir_or_open(tmp_path, monkeypatch):
    from src.hybrid import locks

    lock_path = tmp_path / "junction" / "nested" / ".database.sqlite.init.lock"
    mkdir_calls, open_calls = [], []
    monkeypatch.setattr(
        locks,
        "_is_reparse_point",
        lambda candidate: Path(candidate) == tmp_path / "junction",
    )
    monkeypatch.setattr(os, "makedirs", lambda *args, **kwargs: mkdir_calls.append(args))
    monkeypatch.setattr(os, "open", lambda *args, **kwargs: open_calls.append(args))

    with pytest.raises(ValueError, match="reparse"):
        locks.try_lock(lock_path)

    assert mkdir_calls == []
    assert open_calls == []


def test_try_lock_rechecks_simulated_reparse_after_mkdir_before_open(tmp_path, monkeypatch):
    from src.hybrid import locks

    lock_path = tmp_path / "created" / ".database.sqlite.init.lock"
    created = False
    open_calls = []
    original_makedirs = os.makedirs

    def is_reparse(candidate):
        return created and Path(candidate) == lock_path.parent

    def makedirs(*args, **kwargs):
        nonlocal created
        original_makedirs(*args, **kwargs)
        created = True

    monkeypatch.setattr(locks, "_is_reparse_point", is_reparse)
    monkeypatch.setattr(os, "makedirs", makedirs)
    monkeypatch.setattr(os, "open", lambda *args, **kwargs: open_calls.append(args))

    with pytest.raises(ValueError, match="reparse"):
        locks.try_lock(lock_path)

    assert open_calls == []


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions are native reparse points")
def test_try_lock_rejects_native_junction_without_creating_outside_lock(tmp_path):
    from src.hybrid.locks import try_lock

    target = tmp_path / "outside"
    target.mkdir()
    junction = tmp_path / "junction"
    result = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(target)],
        check=False,
        capture_output=True,
    )
    if result.returncode:
        pytest.skip("native junction unavailable")
    outside_lock = target / "nested" / ".database.sqlite.init.lock"

    with pytest.raises(ValueError, match="reparse"):
        try_lock(junction / "nested" / ".database.sqlite.init.lock")

    assert not outside_lock.exists()
    assert not (target / "nested").exists()


def test_try_lock_preserves_relative_path_compatibility(tmp_path, monkeypatch):
    from src.hybrid.locks import try_lock

    monkeypatch.chdir(tmp_path)
    fd = try_lock("relative.lock")
    assert fd is not None
    os.close(fd)
    assert (tmp_path / "relative.lock").is_file()


def test_regular_file_lock_times_out_then_releases(tmp_path):
    from src.hybrid.locks import file_lock

    lock_path = tmp_path / "regular.lock"
    entered = threading.Event()
    release = threading.Event()

    def hold_lock():
        with file_lock(lock_path):
            entered.set()
            assert release.wait(5)

    holder = threading.Thread(target=hold_lock)
    holder.start()
    assert entered.wait(5)
    try:
        with pytest.raises(TimeoutError, match="timed out"), file_lock(lock_path, timeout=0.05):
            raise AssertionError("unexpected lock acquisition")
    finally:
        release.set()
        holder.join(5)
    assert not holder.is_alive()

    with file_lock(lock_path, timeout=0.5):
        pass
