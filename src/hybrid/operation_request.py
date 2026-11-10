"""Assemble and verify a non-dispatchable, source-bound operation request.

This module is intentionally upstream of provider and executor construction.  It
records an operator's immutable planning inputs and reports the exact missing
LIVE authority without treating a self-hash as an issuer receipt.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
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


# v2 takes only bytes and parsed values.
_V2_SCHEMA = "offline-production-operation-request/v2"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


def _raw_digest(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _strict_bytes_json(raw: object, label: str) -> dict[str, Any]:
    if not isinstance(raw, bytes):
        raise OperationRequestError(f"{label} must be UTF-8 JSON bytes")
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OperationRequestError(f"{label} must be strict JSON") from error
    if not isinstance(parsed, dict):
        raise OperationRequestError(f"{label} must be a JSON object")
    return parsed


def _v2_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise OperationRequestError(f"{label} must be a lowercase SHA-256")
    return value


def _v2_git_sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise OperationRequestError(f"{label} must be a full lowercase Git SHA")
    return value


def _v2_duration(value: object) -> str:
    if type(value) not in {int, float, str}:
        raise OperationRequestError("duration_seconds must be a finite positive decimal")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise OperationRequestError("duration_seconds must be a finite positive decimal") from error
    if not result.is_finite() or result <= 0:
        raise OperationRequestError("duration_seconds must be a finite positive decimal")
    return format(result, "f")


@dataclass(frozen=True)
class CanonicalEditorialBundle:
    """Validated editorial bytes and hashes; not authority and never a path."""

    audio_duration_seconds: str
    caption_cue_count: int
    beat_count: int
    shot_count: int
    narration_input_sha256: str
    timeline_sha256: str
    asset_requirements_sha256: str
    subtitles_sha256: str
    parent_manifest_sha256: str

    def artifact_hashes(self) -> dict[str, str]:
        return {
            "timeline.json": self.timeline_sha256,
            "asset_requirements.json": self.asset_requirements_sha256,
            "subtitles.srt": self.subtitles_sha256,
            "parent_manifest.json": self.parent_manifest_sha256,
        }


def parse_canonical_editorial_bundle(
    *, timeline_bytes: bytes, asset_requirements_bytes: bytes, subtitles_bytes: bytes, parent_manifest_bytes: bytes
) -> CanonicalEditorialBundle:
    """Parse supplied bytes only; protected root checks belong to the caller boundary."""
    timeline = _strict_bytes_json(timeline_bytes, "timeline")
    assets = _strict_bytes_json(asset_requirements_bytes, "asset_requirements")
    parent = _strict_bytes_json(parent_manifest_bytes, "parent_manifest")
    if timeline.get("schema") != "jose_postproduction_timeline/v1":
        raise OperationRequestError("timeline schema is invalid")
    if assets.get("schema") != "jose_asset_requirements/v1":
        raise OperationRequestError("asset_requirements schema is invalid")
    if parent.get("schema") != "jose_postproduction_parent_manifest/v1":
        raise OperationRequestError("parent_manifest schema is invalid")
    output_hashes = parent.get("output_hashes")
    if not isinstance(output_hashes, dict):
        raise OperationRequestError("parent manifest output hashes are required")
    if output_hashes.get("timeline.json") != _raw_digest(timeline_bytes):
        raise OperationRequestError("timeline hash does not match parent manifest")
    if output_hashes.get("asset_requirements.json") != _raw_digest(asset_requirements_bytes):
        raise OperationRequestError("asset_requirements hash does not match parent manifest")
    if output_hashes.get("subtitles.srt") != _raw_digest(subtitles_bytes):
        raise OperationRequestError("subtitles hash does not match parent manifest")
    counts, parent_counts = timeline.get("counts"), parent.get("counts")
    beats, requirements = timeline.get("beats"), assets.get("asset_requirements")
    audio, source_hashes = timeline.get("audio_bindings"), parent.get("source_hashes")
    if not all(isinstance(value, dict) for value in (counts, parent_counts, audio, source_hashes)):
        raise OperationRequestError("canonical bundle counts and narration bindings are required")
    if not isinstance(beats, list) or not isinstance(requirements, list):
        raise OperationRequestError("canonical bundle beats and asset requirements are required")
    captions = _count(counts.get("captions"), "caption count")
    if not isinstance(subtitles_bytes, bytes) or len(re.findall(rb"(?m)^\d+\r?$", subtitles_bytes)) != captions:
        raise OperationRequestError("caption count does not match subtitles")
    beat_count = _count(counts.get("semantic_beats"), "beat count")
    shot_count = _count(counts.get("shot_needs"), "shot count")
    if len(beats) != beat_count:
        raise OperationRequestError("beat count does not match timeline beats")
    if any(not isinstance(beat, dict) or not isinstance(beat.get("shots"), list) for beat in beats):
        raise OperationRequestError("timeline beat identities and shots are required")
    if sum(len(beat["shots"]) for beat in beats) != shot_count:
        raise OperationRequestError("shot count does not match timeline shots")
    if len(requirements) != shot_count:
        raise OperationRequestError("asset requirement count does not match timeline shots")
    required_parent = {"captions": captions, "semantic_beats": beat_count, "shot_needs": shot_count,
                       "asset_requirements_missing": len(requirements)}
    if any(parent_counts.get(key) != value for key, value in required_parent.items()):
        raise OperationRequestError("parent manifest counts do not match canonical timeline")
    narration_sha = _v2_sha(audio.get("approved_input_sha256"), "timeline narration hash")
    if source_hashes.get("approved_input_sha256") != narration_sha:
        raise OperationRequestError("parent manifest narration hash does not match timeline")
    return CanonicalEditorialBundle(
        audio_duration_seconds=_v2_duration(timeline.get("duration_seconds")), caption_cue_count=captions,
        beat_count=beat_count, shot_count=shot_count, narration_input_sha256=narration_sha,
        timeline_sha256=_raw_digest(timeline_bytes), asset_requirements_sha256=_raw_digest(asset_requirements_bytes),
        subtitles_sha256=_raw_digest(subtitles_bytes),
        parent_manifest_sha256=_raw_digest(parent_manifest_bytes),
    )


@dataclass(frozen=True)
class _V2Request:
    operation_id: str
    episode_id: str
    title: str
    base_revision: str
    code_revision: str
    canonical_bundle: CanonicalEditorialBundle
    voice_publication_license: str

    def request(self) -> dict[str, Any]:
        return {
            "schema": _V2_SCHEMA, "operation_id": self.operation_id, "episode_id": self.episode_id, "title": self.title,
            "source": {"repository": _REPOSITORY, "base_revision": self.base_revision, "code_revision": self.code_revision,
                       "narration_input_sha256": self.canonical_bundle.narration_input_sha256,
                       "editorial_artifact_hashes": self.canonical_bundle.artifact_hashes()},
            "editorial_inventory": {"audio_duration_seconds": self.canonical_bundle.audio_duration_seconds,
                "caption_cue_count": self.canonical_bundle.caption_cue_count, "beat_count": self.canonical_bundle.beat_count,
                "shot_count": self.canonical_bundle.shot_count, "image_quota": None},
            "provider_route": {"endpoint": "blackforestlabs/flux-3/text-to-image", "status": "UNQUALIFIED"},
            "voice_publication_license": self.voice_publication_license, "paid_generation": False, "dispatch_permitted": False,
            "human_gates": ["INDEPENDENT_VIDEO_QA_REQUIRED", "INDEPENDENT_THUMBNAIL_QA_REQUIRED"],
            "blocked": {"trusted_issuer_verifier": "UNPROVISIONED", "provider_quote": "MISSING",
                "provider_billing_readback": "MISSING", "provider_license_provenance": "MISSING",
                "voice_publication_license": self.voice_publication_license},
        }

    def canonical_bytes(self) -> bytes:
        request = self.request()
        return _canonical({"request": request, "request_sha256": _sha(request)})


def assemble_operation_request(
    *, operation_id: object, episode_id: object, title: object, base_revision: object,
    code_revision: object, canonical_bundle: object, voice_publication_license: object,
) -> _V2Request:
    """Assemble using typed keyword-only fields; caller counts cannot be supplied."""
    if not isinstance(canonical_bundle, CanonicalEditorialBundle):
        raise OperationRequestError("validated canonical editorial bundle is required")
    if title != "José e sua túnica especial":
        raise OperationRequestError("title must exactly preserve the approved José title")
    if voice_publication_license != "UNKNOWN":
        raise OperationRequestError("voice_publication_license must remain UNKNOWN until evidence exists")
    return _V2Request(_identifier(operation_id, "operation_id"), _identifier(episode_id, "episode_id"), title,
        _v2_git_sha(base_revision, "base_revision"), _v2_git_sha(code_revision, "code_revision"), canonical_bundle,
        voice_publication_license)


def preflight_operation_request(envelope: object, *, canonical_bundle: object, expected_code_revision: object) -> dict[str, Any]:
    """Validate a self-hashed request against canonical bytes; grants no authority."""
    if not isinstance(envelope, dict) or set(envelope) != {"request", "request_sha256"}:
        raise OperationRequestError("closed operation request envelope is required")
    request = envelope.get("request")
    if not isinstance(request, dict) or envelope.get("request_sha256") != _sha(request):
        raise OperationRequestError("operation request immutable hash mismatch")
    if not isinstance(canonical_bundle, CanonicalEditorialBundle):
        raise OperationRequestError("validated canonical editorial bundle is required")
    code_revision = _v2_git_sha(expected_code_revision, "expected_code_revision")
    source = request.get("source")
    if not isinstance(source, dict) or source.get("code_revision") != code_revision:
        raise OperationRequestError("operation request code revision does not match expected revision")
    expected = assemble_operation_request(operation_id=request.get("operation_id"), episode_id=request.get("episode_id"),
        title=request.get("title"), base_revision=source.get("base_revision"), code_revision=code_revision,
        canonical_bundle=canonical_bundle, voice_publication_license=request.get("voice_publication_license")).request()
    if request != expected:
        raise OperationRequestError("closed operation request fields are invalid")
    return {"status": "PREPRODUCTION_READY", "dispatch_permitted": False, "paid_generation": False,
        "request_sha256": envelope["request_sha256"], "request": expected, "blocked": expected["blocked"],
        "next_required_action": "PROVISION_FIXED_TRUSTED_ISSUER_VERIFIER"}


def main(argv: list[str] | None = None) -> int:
    """No path-based CLI exists: callers must use the protected artifact boundary."""
    del argv
    print(json.dumps({"status": "BLOCKED", "dispatch_permitted": False,
                      "error": "path-based preflight is unsupported; pass a validated canonical bundle"}, sort_keys=True))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
