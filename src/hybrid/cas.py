"""Workspace-independent content-addressed storage."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
from collections.abc import Iterable, Mapping
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.hybrid.artifacts import atomic_json, sha256


def _normalized(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("CAS parameters must be finite")
        normalized = value.normalize()
        return format(normalized, "f")
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("CAS parameters must be finite")
        return _normalized(Decimal(str(value)))
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("CAS parameter keys must be strings")
        return {key: _normalized(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalized(item) for item in value]
    raise TypeError(f"unsupported CAS parameter type: {type(value).__name__}")


def _bytes_sha256(value: bytes | bytearray | memoryview | Path | str) -> str:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return hashlib.sha256(bytes(value)).hexdigest()
    return sha256(Path(value))


def content_key(
    inputs: Iterable[bytes | bytearray | memoryview | Path | str],
    parameters: Mapping[str, Any],
) -> str:
    """Hash input bytes and canonical parameters, never input path names."""
    payload = {
        "input_sha256": [_bytes_sha256(item) for item in inputs],
        "parameters": _normalized(parameters),
        "schema": 1,
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _replace_local_paths(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            key: _replace_local_paths(item)
            for key, item in value.items()
            if key not in {"episode", "compilation", "reviewer"}
        }
    if isinstance(value, (list, tuple)):
        return [_replace_local_paths(item) for item in value]
    if isinstance(value, (str, Path)):
        candidate = Path(value)
        if candidate.is_file():
            return {"content_sha256": sha256(candidate)}
    return value


def job_content_key(job: Any) -> str:
    """Derive a provider-work key without workspace or reviewer identities."""
    return content_key(
        (asset.path for asset in job.manifest.assets),
        {
            "endpoint": job.endpoint,
            "mode": job.mode,
            "payload": _replace_local_paths(job.payload),
        },
    )


class ContentAddressedStore:
    """Atomic immutable object store keyed by :func:`content_key`."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.metadata = self.root / "metadata"
        self.objects.mkdir(parents=True, exist_ok=True)
        self.metadata.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _validate_key(key: str) -> None:
        if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
            raise ValueError("CAS key must be lowercase SHA-256")

    def object_path(self, key: str) -> Path:
        self._validate_key(key)
        return self.objects / key[:2] / key

    def metadata_path(self, key: str) -> Path:
        self._validate_key(key)
        return self.metadata / key[:2] / f"{key}.json"

    def get(self, key: str) -> Path | None:
        object_path = self.object_path(key)
        metadata_path = self.metadata_path(key)
        if not object_path.is_file() or not metadata_path.is_file():
            return None
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata != {
            "key": key,
            "object_sha256": sha256(object_path),
            "schema": 1,
        }:
            raise ValueError("CAS object metadata mismatch")
        return object_path

    def put(self, key: str, source: Path) -> Path:
        source = Path(source)
        if not source.is_file():
            raise ValueError("CAS source must be a file")
        target = self.object_path(key)
        metadata_path = self.metadata_path(key)
        existing = self.get(key)
        if existing is not None:
            if sha256(existing) != sha256(source):
                raise ValueError("CAS key collision with different output bytes")
            return existing

        target.parent.mkdir(parents=True, exist_ok=True)
        metadata_path.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if sha256(target) != sha256(source):
                raise ValueError("CAS key collision with different output bytes")
            atomic_json(
                metadata_path,
                {"key": key, "object_sha256": sha256(target), "schema": 1},
            )
            return target
        fd, temporary_name = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary)
            try:
                os.link(temporary, target)
            except FileExistsError:
                if sha256(target) != sha256(temporary):
                    raise ValueError("CAS key collision with different output bytes")
        finally:
            temporary.unlink(missing_ok=True)
        atomic_json(
            metadata_path,
            {"key": key, "object_sha256": sha256(target), "schema": 1},
        )
        return target

    def materialize(self, key: str, destination: Path) -> bool:
        source = self.get(key)
        if source is None:
            return False
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if sha256(destination) != sha256(source):
                raise ValueError("CAS destination contains different bytes")
            return True
        fd, temporary_name = tempfile.mkstemp(dir=destination.parent, suffix=".tmp")
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return True
