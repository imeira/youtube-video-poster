"""Materialize declared audio-review evidence without granting promotion authority.

A bundle is an immutable audit handoff, not provider authentication, QA approval,
or a preproduction transition.  Its receipt is deliberately permanent:
``TTS_REQUIRED``, ``NOT_QUALIFIED``, and ``UNAUTHENTICATED``.
"""

from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.hybrid.audio_preproduction import (
    _active_audio_import_snapshot,
    _assert_active_audio_import_snapshot,
    _candidate_digest,
    _write_exclusive,
    verify_real_audio_candidate_import,
)
from src.hybrid.external_trust_root import (
    TrustRootVerificationError,
    load_strict_json_snapshot,
)
from src.hybrid.locks import file_lock, reject_reparse_path

_BUNDLE = "audio-qualification-evidence"
_STAGING = ".audio-qualification-evidence-staging"
_BLOCKED = {
    "provider_dispatch": "BLOCKED",
    "render": "BLOCKED",
    "telegram": "BLOCKED",
    "publication": "BLOCKED",
}
_FILES = {"origin.json", "editorial.json", "acoustic.json"}


def _durable_directory(path: Path) -> None:
    """Flush directory metadata where the operating system provides an FD."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _strict_document(path: Path, label: str) -> bytes:
    """Take one reparse-safe strict-JSON snapshot; its claim remains unauthenticated."""
    try:
        document, _digest, raw = load_strict_json_snapshot(
            path, label, include_bytes=True
        )
    except TrustRootVerificationError as error:
        raise ValueError(str(error)) from error
    if not isinstance(document, dict) or not document:
        raise ValueError(f"{label} must be a nonempty JSON object")
    return raw


def _receipt(
    candidate: dict[str, Any], request_id: str, index: dict[str, Any]
) -> dict[str, Any]:
    result = {
        "schema": "audio-qualification-evidence-import-receipt-v1",
        "request_id": request_id,
        "audio_import_receipt_sha256": candidate["receipt_sha256"],
        "source_sha256": candidate["source_sha256"],
        "script_sha256": candidate["script_sha256"],
        "audio_sha256": candidate["audio_sha256"],
        "timing_sha256": candidate["timing_sha256"],
        "license_sha256": candidate["license_sha256"],
        "provider_id": candidate["provider_id"],
        "voice_id": candidate["voice_id"],
        "attestation_status": "ATTESTED_NON_PROMOTABLE",
        "provenance": "UNAUTHENTICATED",
        "qualification_status": "NOT_QUALIFIED",
        "status": "TTS_REQUIRED",
        "operations": dict(_BLOCKED),
        "index_sha256": _candidate_digest(index),
    }
    result["receipt_sha256"] = _candidate_digest(result)
    return result


def _load_json(
    path: Path, label: str, packet: Path
) -> tuple[dict[str, Any], str, bytes]:
    try:
        return load_strict_json_snapshot(
            path, label, contained_by=packet, include_bytes=True
        )
    except TrustRootVerificationError as error:
        raise ValueError(f"audio qualification evidence {label} is invalid") from error


def verify_audio_qualification_evidence_import(
    packet_path: Path, *, app_config: object, clock
) -> dict[str, Any]:
    """Replay only materialized bytes; never reopen declared origin evidence."""
    packet = reject_reparse_path(Path(packet_path))
    if not packet.is_dir() or {item.name for item in packet.iterdir()} != (
        _FILES | {"index.json", "receipt.json"}
    ):
        raise ValueError("audio qualification evidence has missing or extra files")
    receipt, _receipt_digest, receipt_raw = _load_json(
        packet / "receipt.json", "receipt", packet
    )
    expected_fields = {
        "schema",
        "request_id",
        "audio_import_receipt_sha256",
        "source_sha256",
        "script_sha256",
        "audio_sha256",
        "timing_sha256",
        "license_sha256",
        "provider_id",
        "voice_id",
        "attestation_status",
        "provenance",
        "qualification_status",
        "status",
        "operations",
        "index_sha256",
        "receipt_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected_fields:
        raise ValueError("audio qualification evidence receipt schema is invalid")
    if (
        receipt.get("schema") != "audio-qualification-evidence-import-receipt-v1"
        or receipt.get("attestation_status") != "ATTESTED_NON_PROMOTABLE"
        or receipt.get("provenance") != "UNAUTHENTICATED"
        or receipt.get("qualification_status") != "NOT_QUALIFIED"
        or receipt.get("status") != "TTS_REQUIRED"
        or receipt.get("operations") != _BLOCKED
        or receipt.get("receipt_sha256")
        != _candidate_digest(
            {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        )
        or receipt_raw
        != json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode()
    ):
        raise ValueError(
            "audio qualification evidence receipt is not permanently blocked"
        )
    index, _index_digest, index_raw = _load_json(packet / "index.json", "index", packet)
    if (
        receipt["index_sha256"] != _candidate_digest(index)
        or index.get("schema") != "audio-qualification-evidence-import-index-v1"
        or index_raw
        != json.dumps(index, sort_keys=True, separators=(",", ":")).encode()
    ):
        raise ValueError("audio qualification evidence index mismatch")
    entries = index.get("entries")
    if (
        not isinstance(entries, list)
        or len(entries) != len(_FILES)
        or any(
            not isinstance(item, dict) or set(item) != {"path", "sha256", "size"}
            for item in entries
        )
    ):
        raise ValueError("audio qualification evidence index schema is invalid")
    indexed = {item["path"]: item for item in entries}
    if set(indexed) != _FILES or len(indexed) != len(entries):
        raise ValueError("audio qualification evidence index entries are invalid")
    for name in _FILES:
        document, digest, raw = _load_json(packet / name, name, packet)
        if (
            not isinstance(document, dict)
            or not document
            or indexed[name].get("sha256") != digest
            or indexed[name].get("size") != len(raw)
        ):
            raise ValueError("audio qualification evidence bytes do not match index")
    # The candidate verifier rechecks materialized WAV, timing/license signatures,
    # expiry, and configured-root constraints.  It grants no provenance here.
    candidate_packet = reject_reparse_path(packet.parent / "audio-import")
    candidate = verify_real_audio_candidate_import(
        candidate_packet, app_config=app_config, clock=clock
    )
    for field in (
        "request_id",
        "source_sha256",
        "script_sha256",
        "audio_sha256",
        "timing_sha256",
        "license_sha256",
        "provider_id",
        "voice_id",
    ):
        if receipt.get(field) != candidate.get(field):
            raise ValueError("audio qualification evidence candidate binding mismatch")
    if receipt["audio_import_receipt_sha256"] != candidate["receipt_sha256"]:
        raise ValueError("audio qualification evidence candidate receipt mismatch")
    return receipt


def import_audio_qualification_evidence(
    workspace: Path,
    request_id: str,
    *,
    origin_declaration: Path | None = None,
    editorial_review: Path | None = None,
    acoustic_review: Path | None = None,
    app_config: object,
    clock,
) -> dict[str, Any]:
    """Publish an idempotent non-promotable evidence bundle under the parent lock."""
    if (
        origin_declaration is None
        or editorial_review is None
        or acoustic_review is None
    ):
        raise ValueError(
            "origin declaration, editorial review and acoustic review are required"
        )
    # Snapshot all caller files before taking the request lock.  They are copied
    # as declarations only; no caller-controlled root can authenticate a provider.
    files = {
        "origin.json": _strict_document(origin_declaration, "origin declaration"),
        "editorial.json": _strict_document(editorial_review, "editorial review"),
        "acoustic.json": _strict_document(acoustic_review, "acoustic review"),
    }
    root = reject_reparse_path(
        Path(workspace).absolute() / "PREPRODUCTION" / request_id
    )
    with file_lock(root / ".advance.lock"):
        _request, revision, _source_raw, _script_raw, _narration, snapshot = (
            _active_audio_import_snapshot(workspace, request_id)
        )
        revision = reject_reparse_path(revision)
        candidate = verify_real_audio_candidate_import(
            revision / "audio-import", app_config=app_config, clock=clock
        )
        index = {
            "schema": "audio-qualification-evidence-import-index-v1",
            "entries": [
                {"path": name, "sha256": sha256(raw).hexdigest(), "size": len(raw)}
                for name, raw in sorted(files.items())
            ],
        }
        receipt = _receipt(candidate, request_id, index)
        packet = reject_reparse_path(revision / _BUNDLE)
        staging = reject_reparse_path(revision / _STAGING)
        if staging.exists():
            raise ValueError(
                "audio qualification evidence staging requires audited recovery"
            )
        if packet.exists():
            previous = verify_audio_qualification_evidence_import(
                packet, app_config=app_config, clock=clock
            )
            if previous != receipt:
                raise ValueError(
                    "audio qualification evidence conflicts with changed immutable evidence"
                )
        else:
            staging.mkdir()
            try:
                for name, raw in files.items():
                    _write_exclusive(staging / name, raw)
                _write_exclusive(
                    staging / "index.json",
                    json.dumps(index, sort_keys=True, separators=(",", ":")).encode(),
                )
                _write_exclusive(
                    staging / "receipt.json",
                    json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode(),
                )
                _durable_directory(staging)
                _assert_active_audio_import_snapshot(workspace, request_id, snapshot)
                os.replace(staging, packet)
                _durable_directory(revision)
            except Exception:
                if staging.exists() and not any(staging.iterdir()):
                    staging.rmdir()
                raise
            verify_audio_qualification_evidence_import(
                packet, app_config=app_config, clock=clock
            )
    return {
        "status": "TTS_REQUIRED",
        "qualification_status": "NOT_QUALIFIED",
        "provenance": "UNAUTHENTICATED",
        "attestation_status": "ATTESTED_NON_PROMOTABLE",
        "packet_path": str(packet),
        "receipt_sha256": receipt["receipt_sha256"],
        "operations": dict(_BLOCKED),
    }
