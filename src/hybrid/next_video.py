"""Public generic TEST/offline facade for the durable next-video factory.

This module deliberately composes the existing compiled runner and worker.  It
never constructs a second runner, executor, provider, or publication route.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import ntpath
import os
import re
from hashlib import sha256
from pathlib import Path

from src.hybrid.autonomous import _atomic_json
from src.hybrid.autonomous_cli import (
    _compiled_root,
    _fixture_worker_handler,
    _load_compiled,
    _serve_test_host,
    _start_checkpoint,
    start_compiled,
    validate_compiled_gate_receipts,
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
    """Return one supported lexical Windows spelling before any filesystem I/O."""
    raw = str(value)
    slash = raw.replace("/", "\\")
    # Device namespaces and UNC shares have semantics this TEST-only local
    # facade does not support. Reject rather than treating an alias as ordinary.
    if slash.startswith(("\\\\?\\", "\\\\.\\", "\\\\")):
        raise ValueError("unsupported Windows path form")
    drive, tail = ntpath.splitdrive(slash)
    if drive and not tail.startswith("\\"):
        raise ValueError("unsupported Windows path form")
    if drive or re.match(r"^[A-Za-z]:", slash):
        normalized = ntpath.normpath(slash)
        return normalized.replace("\\", "/").rstrip("/").casefold()
    return raw.replace("\\", "/").rstrip("/").casefold()


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


def _deny_terminal_publication_state(workspace: Path, request_id: str, document: dict | None) -> None:
    """Reject publication attempts; admit only hash-bound TEST gate completion."""
    if not isinstance(document, dict):
        raise TypeError("compiled runner state is missing or invalid")
    if document.get("state") in {"PUBLISHED", "PUBLISH_INTENT"}:
        raise ValueError("terminal publication state is not executable in next-video TEST mode")
    if document.get("state") != "READY_TO_PUBLISH":
        return
    media, gates = document.get("media"), document.get("gates")
    if not isinstance(media, dict) or not isinstance(gates, dict):
        raise TypeError("ready TEST terminal lacks durable gate evidence")
    for kind in ("thumbnail", "video"):
        artifact, gate = media.get(kind), gates.get(kind)
        if not isinstance(artifact, dict) or not isinstance(gate, dict):
            raise TypeError("ready TEST terminal lacks durable gate evidence")
        path, digest = artifact.get("path"), artifact.get("sha256")
        if (not isinstance(path, str) or not isinstance(digest, str) or len(digest) != 64
                or gate.get("approved") is not True or gate.get("artifact_sha256") != digest
                or not isinstance(gate.get("reviewer"), str) or not gate["reviewer"].strip()):
            raise ValueError("ready TEST terminal lacks durable gate evidence")
        try:
            if sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise ValueError("ready TEST terminal gate bytes do not match")
        except OSError as error:
            raise ValueError("ready TEST terminal gate artifact is unavailable") from error
    validate_compiled_gate_receipts(workspace, request_id, document)


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
    queued: dict = {}

    def persist_public_admission(root: Path, adapter, _document: dict) -> None:
        request_id = _request_id(json.loads((root / "compiled-harness.json").read_text(encoding="utf-8"))["request_id"])
        metadata = {
            "schema": _SCHEMA, "mode": "TEST", "request_id": request_id,
            "episode_id": adapter.runner.episode_id, "concurrency": concurrency,
            "compiled_sha256": adapter.pipeline.episode.checksum,
            "policy_sha256": adapter.runner.policy.digest,
        }
        metadata_path = root / "next-video.json"
        if metadata_path.exists():
            if _metadata(workspace, request_id) != metadata:
                raise ValueError("next-video metadata does not bind this start request")
        else:
            _atomic_json(metadata_path, metadata)
        _start_checkpoint("after_metadata")
        queued.update(EpisodeTaskQueue(_queue_path(workspace)).enqueue(adapter.runner.episode_id, str(workspace), "compiled"))
        _start_checkpoint("after_queue")

    # The request's non-reentrant admission lock spans snapshot, runner,
    # public metadata, queue row, and READY intent; no public start can observe
    # or complete only a prefix of this saga.
    started = start_compiled(workspace, Path(packet), None, provider_concurrency=concurrency, on_admission=persist_public_admission)
    request_id = _request_id(started["request_id"])
    metadata = _metadata(workspace, request_id)
    return {
        "mode": "TEST", "request_id": request_id, "episode_id": metadata["episode_id"],
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
    _deny_terminal_publication_state(workspace, request_id, document)
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
    # Validate mutable runner state before opening or claiming the queue. A
    # forged terminal marker must not become a DONE/TERMINAL queue projection.
    _root, adapter, document = _load_compiled(
        workspace, request_id, provider_concurrency=persisted
    )
    adapter.validate_cached_qa()
    _deny_terminal_publication_state(workspace, request_id, document)
    if adapter.runner.episode_id != metadata["episode_id"] or adapter.pipeline.episode.checksum != metadata["compiled_sha256"]:
        raise ValueError("next-video metadata no longer binds the compiled runner")
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
