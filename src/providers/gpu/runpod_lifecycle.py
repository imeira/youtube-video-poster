"""Durable RunPod pod ownership and exactly-once termination.

No SDK or network client is constructed here. A deployment supplies the
transport; the default application path therefore cannot allocate a pod.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol, TypeVar

from src.hybrid.artifacts import atomic_json


OWNERSHIP_TAG_KEY = "HERMES_OWNER_SHA256"
_TAG = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
T = TypeVar("T")


def valid_ownership_tag(value: object) -> bool:
    return isinstance(value, str) and _TAG.fullmatch(value) is not None


def pod_ownership_tag(pod: dict[str, Any]) -> object:
    """Read the exact owner marker from API tags or RunPod SDK env data."""
    tags = pod.get("tags")
    if isinstance(tags, dict) and OWNERSHIP_TAG_KEY in tags:
        return tags[OWNERSHIP_TAG_KEY]
    env = pod.get("env")
    if isinstance(env, dict):
        return env.get(OWNERSHIP_TAG_KEY)
    if isinstance(env, list):
        matches = [
            item.get("value") if isinstance(item, dict) else item.split("=", 1)[1]
            for item in env
            if (
                isinstance(item, dict) and item.get("key") == OWNERSHIP_TAG_KEY
            ) or (
                isinstance(item, str) and item.startswith(f"{OWNERSHIP_TAG_KEY}=")
            )
        ]
        if len(matches) == 1:
            return matches[0]
    return None


def ownership_tag(namespace: str, episode_id: str, revision_id: str) -> str:
    """Create a cryptographic, path-independent ownership scope identifier."""
    values = (namespace, episode_id, revision_id)
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("ownership namespace, episode, and revision are required")
    canonical = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PodTransport(Protocol):
    async def create_pod(self, spec: dict[str, Any]) -> dict[str, Any]: ...
    async def terminate_pod(self, pod_id: str) -> None: ...
    async def list_pods(self) -> list[dict[str, Any]]: ...


class RunPodPodLifecycle:
    """Own pods by an exact strong tag and claim each termination once."""

    def __init__(self, *, transport: PodTransport, owner_tag: str, state_dir: Path | str) -> None:
        if not valid_ownership_tag(owner_tag):
            raise ValueError("owner tag must be a SHA-256 identifier")
        self.transport = transport
        self.owner_tag = owner_tag
        self.state_dir = Path(state_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, pod_id: str) -> Path:
        if not isinstance(pod_id, str) or not _ID.fullmatch(pod_id):
            raise ValueError("invalid RunPod pod ID")
        return self.state_dir / f"{pod_id}.json"

    def _load(self, pod_id: str) -> dict[str, Any] | None:
        path = self._path(pod_id)
        if not path.exists():
            return None
        state = json.loads(path.read_text(encoding="utf-8"))
        if state.get("pod_id") != pod_id or state.get("owner_tag") != self.owner_tag:
            raise ValueError("pod lifecycle checkpoint identity mismatch")
        return state

    def _save(self, state: dict[str, Any]) -> None:
        atomic_json(self._path(state["pod_id"]), state)

    def _claim_path(self, kind: str, identifier: str) -> Path:
        return self.state_dir / f".{kind}-{identifier}.claim"

    def _claim_once(self, kind: str, identifier: str) -> bool:
        path = self._claim_path(kind, identifier)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            return False
        claim = {"owner_tag": self.owner_tag, "kind": kind, "identifier": identifier}
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(claim, stream, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return True

    def _release_claim(self, kind: str, identifier: str) -> None:
        path = self._claim_path(kind, identifier)
        try:
            claim = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        expected = {"owner_tag": self.owner_tag, "kind": kind, "identifier": identifier}
        if claim != expected:
            raise ValueError("refusing to remove foreign lifecycle claim")
        path.unlink()

    async def run(
        self,
        pod_spec: dict[str, Any],
        operation: Callable[[str], Awaitable[T]],
    ) -> T:
        """Create one owned pod and terminate it in every exit path."""
        if not isinstance(pod_spec, dict):
            raise ValueError("pod specification must be a mapping")
        spec = deepcopy(pod_spec)
        tags = spec.setdefault("tags", {})
        if not isinstance(tags, dict):
            raise ValueError("pod tags must be a mapping")
        existing = tags.get(OWNERSHIP_TAG_KEY)
        if existing is not None and existing != self.owner_tag:
            raise ValueError("pod specification has a conflicting ownership tag")
        tags[OWNERSHIP_TAG_KEY] = self.owner_tag
        if not self._claim_once("create", self.owner_tag):
            raise RuntimeError("pod creation already claimed; recover or reconcile instead")
        created = await self.transport.create_pod(spec)
        pod_id = created.get("id") if isinstance(created, dict) else None
        if not isinstance(pod_id, str) or not _ID.fullmatch(pod_id):
            raise ValueError("RunPod create response has invalid pod ID")
        if self._load(pod_id) is not None:
            raise RuntimeError("pod ID already has a lifecycle checkpoint")
        try:
            self._save(
                {
                    "schema_version": 1,
                    "pod_id": pod_id,
                    "owner_tag": self.owner_tag,
                    "terminate_claimed": False,
                    "terminated": False,
                }
            )
        except BaseException:
            if self._claim_once("terminate", pod_id):
                await self.transport.terminate_pod(pod_id)
            raise
        try:
            return await operation(pod_id)
        finally:
            await asyncio.shield(self.terminate_once(pod_id))

    async def terminate_once(self, pod_id: str) -> bool:
        """Claim termination durably before making its one external call."""
        state = self._load(pod_id)
        if state is None:
            raise ValueError("refusing to terminate an unowned pod")
        if state.get("terminate_claimed") or not self._claim_once("terminate", pod_id):
            return False
        state["terminate_claimed"] = True
        self._save(state)
        try:
            await self.transport.terminate_pod(pod_id)
        except BaseException as error:
            state["termination_error"] = type(error).__name__
            self._save(state)
            raise
        state["terminated"] = True
        self._save(state)
        return True

    async def cleanup_orphans(self) -> list[str]:
        """Terminate active pods whose ownership tag is an exact match."""
        terminated: list[str] = []
        for pod in await self.transport.list_pods():
            if not isinstance(pod, dict):
                continue
            if pod_ownership_tag(pod) != self.owner_tag:
                continue
            status = pod.get("status", pod.get("desiredStatus", ""))
            if status in {"EXITED", "TERMINATED"}:
                continue
            pod_id = pod.get("id")
            if not isinstance(pod_id, str) or not _ID.fullmatch(pod_id):
                continue
            state = self._load(pod_id)
            if state is None:
                self._save(
                    {
                        "schema_version": 1,
                        "pod_id": pod_id,
                        "owner_tag": self.owner_tag,
                        "terminate_claimed": False,
                        "terminated": False,
                        "adopted_orphan": True,
                    }
                )
            elif state.get("terminate_claimed") and not state.get("terminated"):
                state["terminate_claimed"] = False
                state["reconciled_active_orphan"] = True
                self._save(state)
                self._release_claim("terminate", pod_id)
            if await self.terminate_once(pod_id):
                terminated.append(pod_id)
        return terminated
