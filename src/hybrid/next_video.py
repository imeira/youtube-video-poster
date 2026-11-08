"""Public generic TEST/offline facade for the durable next-video factory.

This module deliberately composes the existing compiled runner and worker.  It
never constructs a second runner, executor, provider, or publication route.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from hashlib import sha256
from pathlib import Path

from src.hybrid.autonomous import _atomic_json
from src.hybrid.autonomous_cli import (
    _compiled_root,
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


def _windows_path(value: Path | str) -> str:
    """Normalize a path spelling solely for the pre-open Windows root policy."""
    return str(value).replace("\\", "/").rstrip("/").casefold()


def _is_production_path(value: Path | str) -> bool:
    protected = "c:/hermesstudio/production"
    candidate = _windows_path(value)
    return candidate == protected or candidate.startswith(protected + "/")


def _workspace(value: Path | str) -> Path:
    """Deny production spellings before resolve and resolved aliases before use."""
    if _is_production_path(value):
        raise ValueError("next-video TEST facade rejects the production root")
    # Resolve is required to detect a native junction alias, but only after the
    # literal Windows production root and every descendant spelling was denied.
    workspace = Path(value).resolve()
    if _is_production_path(workspace):
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
    started = start_compiled(workspace, Path(packet), None, provider_concurrency=concurrency)
    request_id = _request_id(started["request_id"])
    root, adapter, _document = _load_compiled(workspace, request_id, provider_concurrency=concurrency)
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


def _runner_status_from_queue(metadata: dict, queue: dict) -> dict:
    """Project runner state only from the hash-validated writer snapshot.

    ``runner.json`` is deliberately not opened here: it is mutable caller state
    and cannot establish a status authority for a read-only CLI.
    """
    if queue.get("snapshot") != "CURRENT" or queue.get("status") == "UNAVAILABLE":
        return {"status": "UNVERIFIED", "snapshot": "STALE"}
    if queue.get("episode_id") != metadata["episode_id"] or queue.get("kind") != "compiled":
        return {"status": "UNVERIFIED", "snapshot": "STALE"}
    snapshot_hash = queue.get("snapshot_sha256")
    generation = queue.get("generated_at_utc")
    fence = queue.get("fencing_token")
    state = queue.get("state")
    if (not isinstance(snapshot_hash, str) or len(snapshot_hash) != 64
            or not isinstance(generation, str) or not isinstance(state, str)
            or fence is not None and type(fence) is not int):
        return {"status": "UNVERIFIED", "snapshot": "STALE"}
    identity = sha256(
        (metadata["request_id"] + "\0" + metadata["episode_id"] + "\0" + metadata["compiled_sha256"]).encode("utf-8")
    ).hexdigest()
    return {
        "status": "VERIFIED_LOCAL_STATUS_ONLY", "snapshot": "CURRENT",
        "snapshot_sha256": snapshot_hash, "generation": generation,
        "identity_sha256": identity, "fencing_token": fence, "state": state,
    }


def status(workspace: Path, request_id: str) -> dict:
    workspace, request_id = _workspace(workspace), _request_id(request_id)
    metadata = _metadata(workspace, request_id)
    queue = EpisodeTaskQueue.read_status(_queue_path(workspace), metadata["episode_id"])
    runner = _runner_status_from_queue(metadata, queue)
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
    _root, adapter, document = _load_compiled(
        workspace, request_id, provider_concurrency=metadata["concurrency"]
    )
    if adapter.runner.episode_id != metadata["episode_id"] or adapter.pipeline.episode.checksum != metadata["compiled_sha256"]:
        raise ValueError("next-video metadata no longer binds the compiled runner")
    # Fresh-process resume is read-only but must never report a stale cached QA
    # pass after the exact completed bytes have changed.
    adapter.validate_cached_qa()
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
