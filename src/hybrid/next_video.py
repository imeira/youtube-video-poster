"""Public generic TEST/offline facade for the durable next-video factory.

This module deliberately composes the existing compiled runner and worker.  It
never constructs a second runner, executor, provider, or publication route.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from src.hybrid.autonomous import _atomic_json
from src.hybrid.autonomous_cli import (
    _compiled_root,
    _compiled_status,
    _fixture_worker_handler,
    _load_compiled,
    _serve_test_host,
    start_compiled,
)
from src.hybrid.worker import DurableEpisodeWorker, EpisodeTaskQueue

_SCHEMA = "next-video-factory/v1"


def _request_id(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("request-id must be lowercase sha256")
    return value


def _concurrency(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 32:
        raise ValueError("concurrency must be an integer from 1 through 32")
    return value


def _workspace(value: Path | str) -> Path:
    """Reject the literal production root before any state or packet access."""
    workspace = Path(value).resolve()
    if workspace.as_posix().rstrip("/").casefold() == "c:/hermesstudio/production":
        raise ValueError("next-video TEST facade rejects the production root")
    return workspace


def _metadata_path(workspace: Path, request_id: str) -> Path:
    return _compiled_root(workspace, request_id) / "next-video.json"


def _metadata(workspace: Path, request_id: str) -> dict:
    path = _metadata_path(workspace, request_id)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("next-video durable metadata is missing or invalid") from error
    if set(document) != {"schema", "mode", "request_id", "episode_id", "concurrency", "compiled_sha256", "policy_sha256"}:
        raise ValueError("next-video durable metadata schema is closed")
    if document["schema"] != _SCHEMA or document["mode"] != "TEST" or document["request_id"] != request_id:
        raise ValueError("next-video durable metadata binding is invalid")
    if not isinstance(document["episode_id"], str) or not document["episode_id"]:
        raise ValueError("next-video metadata lacks episode identity")
    _concurrency(document["concurrency"])
    return document


def _queue_path(workspace: Path) -> Path:
    return workspace.resolve() / "TEST" / "worker.sqlite3"


def _assert_persisted_concurrency(metadata: dict, requested: int | None) -> int:
    persisted = _concurrency(metadata["concurrency"])
    if requested is not None and _concurrency(requested) != persisted:
        raise ValueError("resume/worker concurrency must equal the durable start concurrency")
    return persisted


def start(workspace: Path, packet: Path, *, mode: str, concurrency: int) -> dict:
    if mode != "TEST":
        raise ValueError("next-video is TEST/offline only; LIVE is not executable")
    concurrency = _concurrency(concurrency)
    workspace = _workspace(workspace)
    # Existing validation snapshots the packet and denies non-TEST/external
    # policy before any queue, executor submission, or worker construction.
    started = start_compiled(workspace, Path(packet), None)
    request_id = _request_id(started["request_id"])
    root, adapter, _document = _load_compiled(workspace, request_id)
    metadata = {
        "schema": _SCHEMA,
        "mode": "TEST",
        "request_id": request_id,
        "episode_id": adapter.runner.episode_id,
        "concurrency": concurrency,
        "compiled_sha256": adapter.pipeline.episode.checksum,
        "policy_sha256": adapter.runner.policy.digest,
    }
    _atomic_json(root / "next-video.json", metadata)
    queue = EpisodeTaskQueue(_queue_path(workspace))
    queued = queue.enqueue(adapter.runner.episode_id, str(workspace), "compiled")
    return {
        "mode": "TEST", "request_id": request_id, "episode_id": adapter.runner.episode_id,
        "concurrency": concurrency, "queued": {key: queued[key] for key in ("status", "next_action", "state")},
        "publication": "DISABLED",
        "note": "Local TEST/offline factory evidence only; no LIVE readiness or publication authority.",
    }


def status(workspace: Path, request_id: str) -> dict:
    workspace, request_id = _workspace(workspace), _request_id(request_id)
    metadata = _metadata(workspace, request_id)
    queue = EpisodeTaskQueue.read_status(_queue_path(workspace), metadata["episode_id"])
    runner = _compiled_status(_compiled_root(workspace, request_id), request_id)
    return {
        "mode": "TEST", "request_id": request_id, "episode_id": metadata["episode_id"],
        "concurrency": metadata["concurrency"], "queue": queue, "runner": runner,
        "publication": "DISABLED",
    }


def resume(workspace: Path, request_id: str, *, concurrency: int | None) -> dict:
    workspace, request_id = _workspace(workspace), _request_id(request_id)
    metadata = _metadata(workspace, request_id)
    _assert_persisted_concurrency(metadata, concurrency)
    # Re-open/re-hash the packet and runner in a fresh process but do not open
    # the queue or submit.  The fenced worker is the only mutation authority.
    _root, adapter, document = _load_compiled(workspace, request_id)
    if adapter.runner.episode_id != metadata["episode_id"] or adapter.pipeline.episode.checksum != metadata["compiled_sha256"]:
        raise ValueError("next-video metadata no longer binds the compiled runner")
    projected = EpisodeTaskQueue.read_status(_queue_path(workspace), metadata["episode_id"])
    return {
        "mode": "TEST", "request_id": request_id, "episode_id": metadata["episode_id"],
        "concurrency": metadata["concurrency"], "queue": projected,
        "runner_state": document["state"], "publication": "DISABLED",
    }


def worker(workspace: Path, request_id: str, *, once: bool, concurrency: int | None) -> dict:
    workspace, request_id = _workspace(workspace), _request_id(request_id)
    metadata = _metadata(workspace, request_id)
    persisted = _assert_persisted_concurrency(metadata, concurrency)
    queue = EpisodeTaskQueue(_queue_path(workspace))
    worker = DurableEpisodeWorker(queue, f"next-video-{os.getpid()}", _fixture_worker_handler)
    if once:
        result = asyncio.run(worker.run_once()) or {"status": "IDLE", "mode": "TEST"}
    else:
        try:
            result = asyncio.run(_serve_test_host(worker, concurrency=persisted))
        except KeyboardInterrupt:
            result = {"status": "STOPPED", "mode": "TEST"}
    return result | {"mode": "TEST", "request_id": request_id, "concurrency": persisted, "publication": "DISABLED"}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "resume", "status", "worker"))
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--compiled-input", type=Path)
    parser.add_argument("--request-id")
    parser.add_argument("--mode", default="TEST")
    parser.add_argument("--concurrency", type=int)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action == "start":
            if args.compiled_input is None or args.request_id is not None or args.once:
                parser.error("start requires --compiled-input and accepts neither --request-id nor --once")
            result = start(args.workspace, args.compiled_input, mode=args.mode, concurrency=1 if args.concurrency is None else args.concurrency)
        else:
            if args.compiled_input is not None or not args.request_id or args.mode != "TEST":
                parser.error("resume/status/worker require --request-id, reject --compiled-input, and are TEST-only")
            if args.action == "status":
                if args.concurrency is not None or args.once:
                    parser.error("status does not accept --concurrency or --once")
                result = status(args.workspace, args.request_id)
            elif args.action == "resume":
                if args.once:
                    parser.error("resume does not accept --once")
                result = resume(args.workspace, args.request_id, concurrency=args.concurrency)
            else:
                result = worker(args.workspace, args.request_id, once=args.once, concurrency=args.concurrency)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        print(json.dumps({"status": "BLOCKED", "mode": "TEST", "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
