"""Assemble and verify a non-dispatchable, source-bound operation request.

This module is intentionally upstream of provider and executor construction.  It
records an operator's immutable planning inputs and reports the exact missing
LIVE authority without treating a self-hash as an issuer receipt.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

_SCHEMA = "offline-production-operation-request/v1"
_REPOSITORY = "https://github.com/imeira/youtube-video-poster.git"
_SHA = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_IDENTIFIER = re.compile(r"[A-Z][A-Z0-9-]{2,159}\Z", re.ASCII)


class OperationRequestError(ValueError):
    """The offline request is malformed, altered, or not source-compatible."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise OperationRequestError(f"{label} must be a literal ASCII identifier")
    return value


def _count(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise OperationRequestError(f"{label} must be a positive integer")
    return value


def _duration(value: object) -> str:
    if not isinstance(value, str):
        raise OperationRequestError("audio_duration_seconds must be a decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as error:
        raise OperationRequestError("audio_duration_seconds must be a decimal string") from error
    if not parsed.is_finite() or parsed <= 0:
        raise OperationRequestError("audio_duration_seconds must be positive")
    return value


@dataclass(frozen=True)
class OfflineOperationRequest:
    operation_id: str
    episode_id: str
    title: str
    source_repository: str
    source_revision: str
    audio_duration_seconds: str
    timeline_count: int
    caption_cue_count: int
    beat_count: int
    shot_count: int
    voice_publication_license: str

    def request(self) -> dict[str, Any]:
        return {
            "schema": _SCHEMA,
            "operation_id": self.operation_id,
            "episode_id": self.episode_id,
            "title": self.title,
            "source": {"repository": self.source_repository, "revision": self.source_revision},
            "editorial_inventory": {
                "audio_duration_seconds": self.audio_duration_seconds,
                "timeline_count": self.timeline_count,
                "caption_cue_count": self.caption_cue_count,
                "beat_count": self.beat_count,
                "shot_count": self.shot_count,
                "image_quota": None,
            },
            "provider_route": {
                "endpoint": "blackforestlabs/flux-3/text-to-image",
                "status": "UNQUALIFIED",
            },
            "voice_publication_license": self.voice_publication_license,
            "paid_generation": False,
            "dispatch_permitted": False,
            "human_gates": [
                "INDEPENDENT_VIDEO_QA_REQUIRED",
                "INDEPENDENT_THUMBNAIL_QA_REQUIRED",
            ],
            "blocked": {
                "trusted_issuer_verifier": "UNPROVISIONED",
                "provider_quote": "MISSING",
                "provider_billing_readback": "MISSING",
                "provider_license_provenance": "MISSING",
                "voice_publication_license": self.voice_publication_license,
            },
        }

    def canonical_bytes(self) -> bytes:
        request = self.request()
        return _canonical({"request": request, "request_sha256": _sha(request)})


def assemble_operation_request(descriptor: object) -> OfflineOperationRequest:
    """Create a typed offline request; no provider, credential, or executor is used."""
    required = {
        "operation_id", "episode_id", "title", "source_repository", "source_revision",
        "audio_duration_seconds", "timeline_count", "caption_cue_count", "beat_count",
        "shot_count", "voice_publication_license",
    }
    if not isinstance(descriptor, dict):
        raise OperationRequestError("closed operation descriptor is required")
    if "image_quota" in descriptor:
        raise OperationRequestError("image_quota is not implied by shot_count")
    if set(descriptor) != required:
        raise OperationRequestError("closed operation descriptor is required")
    if descriptor["source_repository"] != _REPOSITORY:
        raise OperationRequestError("source_repository is not the canonical remote")
    if not isinstance(descriptor["source_revision"], str) or not _SHA.fullmatch(descriptor["source_revision"]):
        raise OperationRequestError("source_revision must be a full lowercase Git SHA")
    if not isinstance(descriptor["title"], str) or not descriptor["title"].strip():
        raise OperationRequestError("title is required")
    if descriptor["voice_publication_license"] != "UNKNOWN":
        raise OperationRequestError("voice_publication_license must remain UNKNOWN until evidence exists")
    return OfflineOperationRequest(
        operation_id=_identifier(descriptor["operation_id"], "operation_id"),
        episode_id=_identifier(descriptor["episode_id"], "episode_id"),
        title=descriptor["title"],
        source_repository=descriptor["source_repository"],
        source_revision=descriptor["source_revision"],
        audio_duration_seconds=_duration(descriptor["audio_duration_seconds"]),
        timeline_count=_count(descriptor["timeline_count"], "timeline_count"),
        caption_cue_count=_count(descriptor["caption_cue_count"], "caption_cue_count"),
        beat_count=_count(descriptor["beat_count"], "beat_count"),
        shot_count=_count(descriptor["shot_count"], "shot_count"),
        voice_publication_license=descriptor["voice_publication_license"],
    )


def _read_strict_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OperationRequestError("operation request is unavailable strict JSON") from error
    if not isinstance(value, dict):
        raise OperationRequestError("operation request must be an object")
    return value


def preflight_operation_request(path: Path | str, *, expected_source_revision: str) -> dict[str, Any]:
    """Verify immutable planning bytes and report non-promotable readiness."""
    if not isinstance(expected_source_revision, str) or not _SHA.fullmatch(expected_source_revision):
        raise OperationRequestError("expected_source_revision must be a full lowercase Git SHA")
    document = _read_strict_json(Path(path))
    if set(document) != {"request", "request_sha256"} or not isinstance(document.get("request"), dict):
        raise OperationRequestError("closed operation request envelope is required")
    request = document["request"]
    if document.get("request_sha256") != _sha(request):
        raise OperationRequestError("operation request immutable hash mismatch")
    source = request.get("source")
    if not isinstance(source, dict) or source != {"repository": _REPOSITORY, "revision": expected_source_revision}:
        raise OperationRequestError("operation request source binding does not match expected revision")
    editorial = request.get("editorial_inventory")
    required_request = {
        "schema", "operation_id", "episode_id", "title", "source", "editorial_inventory",
        "provider_route", "voice_publication_license", "paid_generation", "dispatch_permitted",
        "human_gates", "blocked",
    }
    if set(request) != required_request or request.get("schema") != _SCHEMA:
        raise OperationRequestError("closed operation request is required")
    descriptor = {
        "operation_id": request["operation_id"], "episode_id": request["episode_id"],
        "title": request["title"], "source_repository": source["repository"],
        "source_revision": source["revision"],
        "audio_duration_seconds": editorial.get("audio_duration_seconds") if isinstance(editorial, dict) else None,
        "timeline_count": editorial.get("timeline_count") if isinstance(editorial, dict) else None,
        "caption_cue_count": editorial.get("caption_cue_count") if isinstance(editorial, dict) else None,
        "beat_count": editorial.get("beat_count") if isinstance(editorial, dict) else None,
        "shot_count": editorial.get("shot_count") if isinstance(editorial, dict) else None,
        "voice_publication_license": request["voice_publication_license"],
    }
    assembled = assemble_operation_request(descriptor)
    if request != assembled.request():
        raise OperationRequestError("closed operation request fields are invalid")
    return {
        "status": "PREPRODUCTION_READY",
        "dispatch_permitted": False,
        "paid_generation": False,
        "request_sha256": document["request_sha256"],
        "request": assembled.request(),
        "blocked": assembled.request()["blocked"],
        "next_required_action": "PROVISION_FIXED_TRUSTED_ISSUER_VERIFIER",
    }


def write_operation_request(path: Path | str, descriptor: object) -> OfflineOperationRequest:
    """Atomically publish a new offline request; never overwrite an existing one."""
    request = assemble_operation_request(descriptor)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    try:
        with os.fdopen(os.open(destination, flags, 0o600), "wb", closefd=True) as stream:
            stream.write(request.canonical_bytes())
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as error:
        raise OperationRequestError("operation request already exists; create a successor revision") from error
    return request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("preflight",))
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--expected-source-revision", required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(preflight_operation_request(args.request, expected_source_revision=args.expected_source_revision), ensure_ascii=False, sort_keys=True))
        return 0
    except OperationRequestError as error:
        print(json.dumps({"status": "BLOCKED", "dispatch_permitted": False, "error": str(error)}, ensure_ascii=False, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
