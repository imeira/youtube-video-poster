"""Content-addressed manifests for safe render cache reuse."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(
    input_paths: Iterable[Path], timeline: list[dict], render_config: dict
) -> dict:
    """Build a deterministic manifest for all render inputs and parameters."""
    files = [
        {"path": str(path.resolve()), "sha256": _sha256(path)}
        for path in sorted((Path(path) for path in input_paths), key=lambda item: str(item.resolve()))
    ]
    payload = {
        "schema_version": 1,
        "files": files,
        "timeline": timeline,
        "render_config": render_config,
    }
    payload["fingerprint"] = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return payload


def manifest_matches(
    manifest_path: Path,
    input_paths: Iterable[Path],
    timeline: list[dict],
    render_config: dict,
) -> bool:
    """Return True only when a saved manifest exactly matches current inputs."""
    path = Path(manifest_path)
    if not path.is_file():
        return False
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        current = build_manifest(input_paths, timeline, render_config)
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return saved.get("fingerprint") == current["fingerprint"]


def write_manifest_atomic(manifest_path: Path, manifest: dict) -> None:
    """Persist a manifest atomically so interruptions cannot leave partial JSON."""
    path = Path(manifest_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)
