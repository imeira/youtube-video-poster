"""Fail-closed external-audio boundary and a byte-only TEST fixture importer.

`import_test_audio_segment` receives an immutable byte snapshot from trusted
application composition.  It deliberately has no path/source-file parameter and
no CLI route.  Its cache root is likewise trusted local composition, not a
remote receipt or a CLI/payload value.  This binds replay to that configured
root and namespace; it does not claim to defend a local attacker controlling
that trusted root.
"""
from __future__ import annotations

import io
import json
import os
import re
import wave
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.hybrid.locks import reject_reparse_path

_MAX_TEST_WAV_BYTES = 16 * 1024 * 1024
_NAMESPACE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_SEGMENT_ID = re.compile(r"S[0-9]{3,}")
_WORD = re.compile(r"[\wÀ-ÿ]+(?:[-'][\wÀ-ÿ]+)*", re.UNICODE)
_BLOCKED_REPORT = {
    "status": "EXTERNAL_AUDIO_SYNTHESIS_BLOCKED",
    "capability": "UNQUALIFIED",
    "reason": "trust_and_transport_recovery_not_qualified",
}


def _tokens(text: str) -> list[str]:
    return [token.casefold() for token in _WORD.findall(text)]


def _measure_non_silent_wav(audio_bytes: bytes) -> float:
    """Decode exactly the supplied snapshot; accept only bounded PCM16 WAV."""
    if not isinstance(audio_bytes, bytes) or not audio_bytes:
        raise ValueError("TEST audio bytes are required")
    if len(audio_bytes) > _MAX_TEST_WAV_BYTES:
        raise ValueError("TEST audio size exceeds the closed limit")
    try:
        with wave.open(io.BytesIO(audio_bytes), "rb") as decoded:
            frames = decoded.getnframes()
            rate = decoded.getframerate()
            if (
                decoded.getcomptype() != "NONE"
                or decoded.getsampwidth() != 2
                or decoded.getnchannels() not in {1, 2}
                or not 8_000 <= rate <= 48_000
                or frames <= 0
            ):
                raise ValueError("TEST WAV format is not accepted")
            pcm = decoded.readframes(frames)
    except (wave.Error, EOFError) as error:
        raise ValueError("TEST fixture is not a decodable WAV") from error
    if len(pcm) != frames * 2 * decoded.getnchannels():
        raise ValueError("TEST WAV frame data is incomplete")
    if not any(pcm[index : index + 2] != b"\0\0" for index in range(0, len(pcm), 2)):
        raise ValueError("TEST WAV is silent and cannot be narration evidence")
    return frames / rate


def _validate_actual_boundaries(
    text: str, boundaries: list[dict[str, Any]], duration_s: float
) -> None:
    if not isinstance(boundaries, list) or not boundaries:
        raise ValueError("actual WordBoundary events receipt is required")
    actual: list[str] = []
    previous = 0.0
    for event in boundaries:
        if not isinstance(event, dict) or set(event) != {"word", "start", "end"}:
            raise ValueError("actual WordBoundary event schema is required")
        try:
            start, end = float(event["start"]), float(event["end"])
        except (TypeError, ValueError) as error:
            raise ValueError("actual WordBoundary event timing is invalid") from error
        if start < previous or end <= start or end > duration_s:
            raise ValueError("actual WordBoundary events must be ordered and measured")
        previous = end
        actual.extend(_tokens(str(event["word"])))
    if actual != _tokens(text):
        raise ValueError("actual WordBoundary events do not match text")


def _digest(receipt: dict[str, Any]) -> str:
    payload = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    return sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _write_exclusive(path: Path, payload: bytes) -> None:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(fd)


def _read_cache_bytes(path: Path, label: str) -> bytes:
    path = reject_reparse_path(path)
    if not path.is_file():
        raise ValueError(f"TEST cache {label} is incomplete")
    return path.read_bytes()


def _expected_receipt(
    *,
    trusted_cache_root: Path,
    namespace: str,
    segment_id: str,
    text: str,
    audio_bytes: bytes,
    duration_s: float,
    word_boundaries: list[dict[str, Any]],
) -> dict[str, Any]:
    audio_sha = sha256(audio_bytes).hexdigest()
    root_sha = sha256(str(trusted_cache_root.resolve()).encode("utf-8")).hexdigest()
    input_sha = sha256(
        json.dumps(
            {
                "segment_id": segment_id,
                "text": text,
                "audio_sha256": audio_sha,
                "cache_root_sha256": root_sha,
                "cache_namespace": namespace,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    receipt = {
        "schema": "test-audio-segment-receipt-v2",
        "mode": "TEST",
        "segment_id": segment_id,
        "input_sha256": input_sha,
        "audio_sha256": audio_sha,
        "audio_file": f"{segment_id}-{input_sha[:16]}.wav",
        "duration_s": duration_s,
        "word_boundaries": word_boundaries,
        "boundary_origin": "TEST_FIXTURE_WORDBOUNDARY",
        "voice_qualification": "NOT_QUALIFIED",
        "cache_root_sha256": root_sha,
        "cache_namespace": namespace,
    }
    receipt["receipt_sha256"] = _digest(receipt)
    return receipt


def import_test_audio_segment(
    *,
    cache_root: Path,
    namespace: str,
    segment_id: str,
    text: str,
    audio_bytes: bytes,
    media_type: str,
    word_boundaries: list[dict[str, Any]],
    is_closing: bool = False,
) -> dict[str, Any]:
    """Cache a TEST WAV snapshot under a trusted composition-owned root.

    No input path is accepted or opened.  Closing/reflection voice qualification
    is intentionally unavailable: its only possible receipt value is
    ``NOT_QUALIFIED``.
    """
    if media_type != "audio/wav":
        raise ValueError("TEST import accepts only the audio/wav media type")
    if not _NAMESPACE.fullmatch(namespace):
        raise ValueError("TEST cache namespace is invalid")
    if not _SEGMENT_ID.fullmatch(segment_id):
        raise ValueError("TEST segment id is invalid")
    if is_closing:
        raise ValueError("TEST fixtures cannot be used for a reflection closing")
    duration_s = _measure_non_silent_wav(audio_bytes)
    _validate_actual_boundaries(text, word_boundaries, duration_s)

    trusted_cache_root = reject_reparse_path(Path(cache_root))
    if not trusted_cache_root.is_dir():
        raise ValueError("trusted TEST cache root must already exist as a directory")
    namespace_root = trusted_cache_root / namespace
    if namespace_root.exists():
        namespace_root = reject_reparse_path(namespace_root)
    else:
        namespace_root.mkdir()
    receipt = _expected_receipt(
        trusted_cache_root=trusted_cache_root,
        namespace=namespace,
        segment_id=segment_id,
        text=text,
        audio_bytes=audio_bytes,
        duration_s=duration_s,
        word_boundaries=word_boundaries,
    )
    audio_path = namespace_root / receipt["audio_file"]
    receipt_path = namespace_root / f"{segment_id}-{receipt['input_sha256'][:16]}.receipt.json"
    prior_receipts = list(namespace_root.glob(f"{segment_id}-*.receipt.json"))
    if prior_receipts and receipt_path not in prior_receipts:
        raise ValueError("TEST cache receipt does not match the expected root/namespace")
    if audio_path.exists() or receipt_path.exists():
        if not (audio_path.exists() and receipt_path.exists()):
            raise ValueError("TEST cache is incomplete")
        try:
            cached_receipt = json.loads(_read_cache_bytes(receipt_path, "receipt").decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("TEST cache receipt is invalid") from error
        if not isinstance(cached_receipt, dict) or cached_receipt.get("receipt_sha256") != _digest(cached_receipt):
            raise ValueError("TEST cache receipt digest mismatch")
        if cached_receipt != receipt:
            raise ValueError("TEST cache receipt does not match the expected contract")
        if sha256(_read_cache_bytes(audio_path, "audio")).hexdigest() != receipt["audio_sha256"]:
            raise ValueError("TEST cache audio hash mismatch")
        return cached_receipt
    _write_exclusive(audio_path, audio_bytes)
    _write_exclusive(
        receipt_path,
        json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(),
    )
    return receipt


def preflight_audio_execution(
    workspace: Path,
    request_id: str,
    review_path: Path | None = None,
    authorization_path: Path | None = None,
    *,
    provider_factory: Any = None,
) -> dict[str, str]:
    """Read-only capability report. Caller files are intentionally not read."""
    del workspace, request_id, review_path, authorization_path, provider_factory
    return dict(_BLOCKED_REPORT)


def execute_authorized_audio(
    workspace: Path,
    request_id: str,
    review_path: Path | None = None,
    authorization_path: Path | None = None,
    *,
    provider_factory: Any = None,
) -> dict[str, Any]:
    """Fail before path resolution, state writes, or provider construction."""
    del workspace, request_id, review_path, authorization_path, provider_factory
    raise RuntimeError(
        "EXTERNAL_AUDIO_SYNTHESIS_BLOCKED: trust and transport recovery are not qualified"
    )
