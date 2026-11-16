"""Exercise the delivered wrapper through an actual multiprocessing spawn.

The only protected-path operation has an inert backend below the installed hook.
If that hook is absent, the child never invokes the protected-path operation.
Run this coverage control through scripts/run_offline_tests.py, not bare pytest.
"""
from __future__ import annotations

import hashlib
import multiprocessing
import os
import sys
import tempfile
from pathlib import Path

WRAPPER = Path(__file__).resolve().parents[2] / "scripts" / "run_offline_tests.py"


def _observe_spawn_guard(connection, expected_wrapper):
    import asyncio
    import builtins
    import io
    import socket

    main = sys.modules["__main__"]
    main_file = getattr(main, "__file__", "")
    hooks = [builtins.open, io.open, os.open, os.stat, os.lstat, os.access,
             os.listdir, os.scandir, socket.socket.connect, socket.socket.connect_ex,
             socket.socket.bind, socket.socket.sendto, socket.create_connection,
             socket.getaddrinfo]
    observed = {
        "pid": os.getpid(), "main_name": main.__name__, "main_file": main_file,
        "all_hooks_from_spawn_module": all(getattr(hook, "__module__", None) == "__mp_main__" for hook in hooks),
        "probe_invoked": False, "blocked": False, "backend_calls": None,
    }
    try:
        code = getattr(os.stat, "__code__", None)
        closure = getattr(os.stat, "__closure__", None)
        if (not observed["all_hooks_from_spawn_module"] or code is None or closure is None
                or "original_stat" not in code.co_freevars
                or Path(main_file) != Path(expected_wrapper)):
            connection.send(observed)
            return

        cell = closure[code.co_freevars.index("original_stat")]
        original = cell.cell_contents
        calls = []

        def inert_metadata(*args, **kwargs):
            calls.append((args, kwargs))
            return object()

        # Only the captured backend changes; the installed predicate is intact.
        cell.cell_contents = inert_metadata
        try:
            observed["probe_invoked"] = True
            try:
                os.stat(r"C:\HermesStudio\production\inert-spawn-control.json")
            except RuntimeError as error:
                observed["blocked"] = str(error) == "Protected production directory"
        finally:
            cell.cell_contents = original
        observed["backend_calls"] = len(calls)

        async def loopback_control():
            await asyncio.sleep(0)
            return True

        observed.update(
            wrapper_sha256=hashlib.sha256(Path(main_file).read_bytes()).hexdigest(),
            environment={name: os.environ.get(name) for name in ("TMPDIR", "TMP", "TEMP", "HERMES_HOME")},
            tempfile_root=tempfile.gettempdir(),
            local_asyncio=asyncio.run(loopback_control()),
            project_modules=[name for name in sys.modules if name == "src" or name.startswith("src.")],
        )
        connection.send(observed)
    finally:
        connection.close()


def test_actual_spawn_installs_guard_before_target_and_preserves_owned_environment():
    expected_hash = hashlib.sha256(WRAPPER.read_bytes()).hexdigest()
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_observe_spawn_guard, args=(sender, str(WRAPPER)))
    process.start()
    sender.close()
    try:
        assert receiver.poll(30), "spawn control did not return its bounded observation"
        observed = receiver.recv()
    finally:
        process.join(5)
        if process.is_alive():
            process.terminate()
            process.join(5)
        receiver.close()
    assert process.exitcode == 0
    assert observed["pid"] != os.getpid()
    assert observed["main_name"] == "__mp_main__"
    assert observed["all_hooks_from_spawn_module"]
    assert observed["probe_invoked"] and observed["blocked"]
    assert observed["backend_calls"] == 0
    assert observed["wrapper_sha256"] == expected_hash
    assert observed["local_asyncio"]
    assert observed["project_modules"] == []
    assert observed["environment"] == {name: os.environ.get(name) for name in ("TMPDIR", "TMP", "TEMP", "HERMES_HOME")}
    assert Path(observed["tempfile_root"]) == Path(observed["environment"]["TMPDIR"])
    print("SPAWN_CONTROL", observed)
