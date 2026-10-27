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
import math
import os
import re
import wave
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.hybrid.external_trust_root import (
    ExternalTrustRootVerifier,
    TrustRootVerificationError,
    load_strict_json_snapshot,
)
from src.hybrid.locks import file_lock, reject_reparse_path

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


_DIRECTOR_BLOCKED_OPERATIONS = {
    "provider_dispatch": "BLOCKED", "render": "BLOCKED", "telegram": "BLOCKED", "publication": "BLOCKED",
}
_IMPORT_RECEIPT_FIELDS = {
    "schema", "mode", "request_id", "source_sha256", "script_sha256", "audio_sha256", "duration_s",
    "timing_sha256", "license_sha256", "provider_id", "voice_id", "integrity", "cryptographic_verification",
    "configuration_validation", "provenance", "candidate_class", "alignment_verification", "qa_qualification",
    "authorization_assertion", "qualification_status", "status", "operations", "index_sha256", "receipt_sha256",
}
_IMPORT_RECEIPT_FIXED_VALUES = {
    "schema": "preproduction-audio-candidate-import-receipt-v1",
    "mode": "PREPRODUCTION",
    "integrity": "VERIFIED",
    "cryptographic_verification": "VERIFIED",
    "configuration_validation": "EXTERNAL_CONFIGURATION_VALIDATED",
    "provenance": "UNAUTHENTICATED",
    "alignment_verification": "NOT_VERIFIED",
    "qa_qualification": "NOT_QUALIFIED",
    "authorization_assertion": "SOURCE_AUTHORIZATION_DECLARED_NOT_LEGAL_PROOF",
    "qualification_status": "NOT_QUALIFIED",
    "status": "TTS_REQUIRED",
}


def _strict_wav_snapshot(path: Path) -> tuple[bytes, str, float]:
    candidate = reject_reparse_path(Path(path))
    if candidate.suffix.lower() != ".wav" or not candidate.is_file():
        raise ValueError("real audio candidate must be a regular WAV file")
    raw = candidate.read_bytes()
    return raw, sha256(raw).hexdigest(), _measure_non_silent_wav(raw)


def _structural_boundaries(text: str, boundaries: object, duration_s: float) -> None:
    """Check receipt structure only; timestamps do not prove acoustic alignment."""
    if not isinstance(boundaries, list) or not boundaries:
        raise ValueError("provider actual WordBoundary events are required")
    words: list[str] = []
    previous = 0.0
    for event in boundaries:
        if not isinstance(event, dict) or set(event) != {"word", "start", "end"}:
            raise ValueError("provider actual WordBoundary event schema is required")
        if not isinstance(event["word"], str):
            raise TypeError("provider actual WordBoundary word is invalid")
        if type(event["start"]) not in {int, float, Decimal} or type(event["end"]) not in {int, float, Decimal}:
            raise ValueError("provider actual WordBoundary timing is invalid")
        start, end = float(event["start"]), float(event["end"])
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError("provider WordBoundary timing must be finite")
        if start < previous or end <= start or end > duration_s:
            raise ValueError("provider WordBoundary timing must be ordered, non-overlapping, and within decoded audio")
        previous = end
        words.extend(_tokens(event["word"]))
    if words != _tokens(text):
        raise ValueError("provider actual WordBoundary events do not match source-bound script")


def _validate_timing_receipt(
    timing: object, *, expected: dict[str, str], duration_s: float, narration: str | None,
) -> tuple[str, str, str]:
    """Validate the closed timing schema and its immutable bindings.

    A replay has no permission to reopen origin files.  It can still revalidate
    every closed evidence field against the materialized receipt and the
    configured license verifier; the original narration-token comparison is
    performed only at ingestion while the active script snapshot is available.
    """
    required = {"schema", "mode", "candidate_class", "origin", *expected, "provider_id", "voice_id", "word_boundaries"}
    if not isinstance(timing, dict) or set(timing) != required:
        raise ValueError("provider WordBoundary receipt must use the closed schema")
    candidate_class = timing.get("candidate_class")
    if timing.get("schema") != "provider-word-boundary-receipt-v1" or candidate_class not in {"TEST", "EXTERNAL"}:
        raise ValueError("provider WordBoundary receipt candidate class is invalid")
    if candidate_class == "TEST":
        if timing.get("mode") != "TEST" or timing.get("origin") != "FIXTURE_WORDBOUNDARY" or timing.get("provider_id") != "provider.example":
            raise ValueError("TEST candidate must use explicit fixture evidence")
    elif timing.get("mode") != "PREPRODUCTION" or timing.get("origin") != "PROVIDER_WORDBOUNDARY":
        raise ValueError("external candidate provider receipt is invalid")
    if any(timing.get(key) != value for key, value in expected.items()):
        raise ValueError("provider WordBoundary receipt source/script/audio binding mismatch")
    if not isinstance(timing.get("provider_id"), str) or not timing["provider_id"] or not isinstance(timing.get("voice_id"), str) or not timing["voice_id"]:
        raise ValueError("provider/voice evidence is required")
    if narration is not None:
        _structural_boundaries(narration, timing["word_boundaries"], duration_s)
    return candidate_class, timing["provider_id"], timing["voice_id"]


def _candidate_digest(value: dict[str, Any]) -> str:
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _is_sha256(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _validate_import_receipt(receipt: object) -> dict[str, Any]:
    """Accept only the single permanently non-promotable handoff contract."""
    if not isinstance(receipt, dict) or set(receipt) != _IMPORT_RECEIPT_FIELDS:
        raise ValueError("materialized audio import receipt contract is invalid")
    if any(receipt.get(field) != value for field, value in _IMPORT_RECEIPT_FIXED_VALUES.items()):
        raise ValueError("materialized audio import receipt contract is invalid")
    if receipt.get("candidate_class") not in {"TEST", "EXTERNAL"}:
        raise ValueError("materialized audio import receipt contract is invalid")
    if receipt.get("operations") != _DIRECTOR_BLOCKED_OPERATIONS:
        raise ValueError("materialized audio import receipt contract is invalid")
    text_fields = {"request_id", "duration_s", "provider_id", "voice_id"}
    hash_fields = {"source_sha256", "script_sha256", "audio_sha256", "timing_sha256", "license_sha256", "index_sha256", "receipt_sha256"}
    if any(type(receipt.get(field)) is not str or not receipt[field] for field in text_fields):
        raise ValueError("materialized audio import receipt contract is invalid")
    if any(not _is_sha256(receipt.get(field)) for field in hash_fields):
        raise ValueError("materialized audio import receipt contract is invalid")
    try:
        duration = Decimal(receipt["duration_s"])
    except Exception as error:
        raise ValueError("materialized audio import receipt contract is invalid") from error
    if not duration.is_finite() or duration <= 0:
        raise ValueError("materialized audio import receipt contract is invalid")
    if receipt["receipt_sha256"] != _candidate_digest({key: value for key, value in receipt.items() if key != "receipt_sha256"}):
        raise ValueError("materialized audio import receipt contract is invalid")
    return receipt


def _active_audio_import_snapshot(workspace: Path, request_id: str) -> tuple[dict[str, Any], Path, bytes, bytes, str, tuple[str, str, str, str]]:
    """Capture request execution and source/script bytes for one import attempt."""
    from src.hybrid.preproduction import load_preproduction

    request = load_preproduction(workspace, request_id)
    execution = request.get("execution")
    if not isinstance(execution, dict) or execution.get("mode") != "PREPRODUCTION" or execution.get("paid_execution") != "BLOCKED":
        raise ValueError("real audio candidate requires blocked PREPRODUCTION request")
    source_binding = execution.get("source_sha256")
    active_revision = execution.get("active_revision")
    if not isinstance(source_binding, str) or active_revision != f"revisions/{source_binding[:16]}":
        raise ValueError("real audio candidate requires hash-bound active revision")
    revision = reject_reparse_path(Path(workspace).absolute() / "PREPRODUCTION" / request_id / active_revision)
    try:
        _source, source_sha, source_raw = load_strict_json_snapshot(revision / "inputs" / "source.json", "active source", include_bytes=True)
        script, _script_sha, script_raw = load_strict_json_snapshot(revision / "script" / "script.json", "active script", include_bytes=True)
    except TrustRootVerificationError as error:
        raise ValueError("active source-bound script is invalid") from error
    if source_sha != source_binding:
        raise ValueError("active source binding changed before audio import")
    if not isinstance(script.get("segments"), list) or not script["segments"]:
        raise ValueError("active source-bound script segments are required")
    narration = " ".join(
        segment["narration"] for segment in script["segments"]
        if isinstance(segment, dict) and isinstance(segment.get("narration"), str)
    )
    if not narration or len(narration.split()) == 0:
        raise ValueError("active source-bound script narration is required")
    execution_snapshot = _candidate_digest(execution)
    return request, revision, source_raw, script_raw, narration, (source_binding, active_revision, execution_snapshot, sha256(script_raw).hexdigest())


def _assert_active_audio_import_snapshot(workspace: Path, request_id: str, expected: tuple[str, str, str, str]) -> None:
    """Re-read source, execution and revision bindings while holding the import lock."""
    _request, _revision, _source_raw, _script_raw, _narration, actual = _active_audio_import_snapshot(workspace, request_id)
    if actual != expected:
        raise ValueError("source-bound execution changed during audio import")


def verify_real_audio_candidate_import(
    packet_path: Path, *, app_config: object, clock,
) -> dict[str, Any]:
    """Replay a packet only with its configured external verifier available."""
    packet = reject_reparse_path(Path(packet_path))
    expected_files = {"audio.wav", "timing.json", "license.json", "index.json", "receipt.json"}
    if not packet.is_dir() or {item.name for item in packet.iterdir()} != expected_files:
        raise ValueError("materialized audio import has missing or extra files")
    try:
        receipt, _receipt_sha, _receipt_raw = load_strict_json_snapshot(
            packet / "receipt.json", "materialized audio import receipt", include_bytes=True,
        )
        index, _index_sha, _index_raw = load_strict_json_snapshot(
            packet / "index.json", "materialized audio import index", include_bytes=True,
        )
    except TrustRootVerificationError as error:
        raise ValueError("materialized audio import receipt or index is invalid") from error
    receipt = _validate_import_receipt(receipt)
    if not isinstance(index, dict) or receipt.get("index_sha256") != _candidate_digest(index):
        raise ValueError("materialized audio import index mismatch")
    entries = index.get("entries")
    if index.get("schema") != "preproduction-audio-candidate-import-index-v1" or not isinstance(entries, list) or len(entries) != 3:
        raise ValueError("materialized audio import index schema is invalid")
    if any(not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"} for entry in entries):
        raise ValueError("materialized audio import index entries are invalid")
    indexed = {entry["path"]: entry for entry in entries}
    if set(indexed) != {"audio.wav", "timing.json", "license.json"}:
        raise ValueError("materialized audio import index entries are invalid")
    try:
        _audio_raw, audio_sha, duration_s = _strict_wav_snapshot(packet / "audio.wav")
    except ValueError as error:
        raise ValueError("materialized audio import audio contract mismatch") from error
    if indexed["audio.wav"].get("sha256") != audio_sha or indexed["audio.wav"].get("size") != len(_audio_raw):
        raise ValueError("materialized audio import bytes do not match index")
    evidence: dict[str, tuple[dict[str, Any], str, bytes]] = {}
    for name, label in (("timing.json", "materialized timing receipt"), ("license.json", "materialized license receipt")):
        try:
            parsed, digest, raw = load_strict_json_snapshot(packet / name, label, include_bytes=True)
        except TrustRootVerificationError as error:
            raise ValueError("materialized audio import evidence is invalid") from error
        entry = indexed[name]
        if not isinstance(parsed, dict) or entry.get("sha256") != digest or entry.get("size") != len(raw):
            raise ValueError("materialized audio import bytes do not match index")
        evidence[name] = (parsed, digest, raw)
    try:
        receipt_duration = float(receipt.get("duration_s", ""))
    except (TypeError, ValueError):
        raise ValueError("materialized audio import audio contract mismatch") from None
    if audio_sha != receipt.get("audio_sha256") or not math.isfinite(receipt_duration) or abs(duration_s - receipt_duration) > 0.000001:
        raise ValueError("materialized audio import audio contract mismatch")
    timing, timing_sha, _timing_raw = evidence["timing.json"]
    license_receipt, license_sha, _license_raw = evidence["license.json"]
    if timing_sha != receipt["timing_sha256"] or license_sha != receipt["license_sha256"]:
        raise ValueError("materialized audio import evidence hash mismatch")
    expected = {
        "source_sha256": receipt["source_sha256"], "script_sha256": receipt["script_sha256"], "audio_sha256": audio_sha,
    }
    candidate_class, provider_id, voice_id = _validate_timing_receipt(
        timing, expected=expected, duration_s=duration_s, narration=None,
    )
    if candidate_class != receipt["candidate_class"] or provider_id != receipt["provider_id"] or voice_id != receipt["voice_id"]:
        raise ValueError("materialized audio import timing binding mismatch")
    try:
        verifier = ExternalTrustRootVerifier.from_app_config(app_config, clock=clock)
        verified = verifier.verify_tts_license_declaration(
            license_receipt,
            expected={**expected, "timing_sha256": timing_sha, "provider_id": provider_id, "voice_id": voice_id},
        )
    except TrustRootVerificationError as error:
        raise ValueError(str(error)) from error
    if receipt["configuration_validation"] != verified.provenance:
        raise ValueError("materialized audio import verifier configuration mismatch")
    return receipt


def import_real_audio_candidate(
    workspace: Path,
    request_id: str,
    audio_path: Path,
    timing_path: Path,
    license_path: Path,
    *,
    app_config: object,
    clock,
) -> dict[str, Any]:
    """Materialize verified local WAV evidence without clearing any TTS gate.

    The license is a signed source declaration, not legal authorization proof.
    This function never constructs a provider and returns a permanently blocked
    PREPRODUCTION handoff for this slice.
    """
    _request, revision, source_raw, script_raw, narration, active_snapshot = _active_audio_import_snapshot(workspace, request_id)
    source_binding = active_snapshot[0]
    audio_raw, audio_sha, duration_s = _strict_wav_snapshot(audio_path)
    source_sha, script_sha = sha256(source_raw).hexdigest(), sha256(script_raw).hexdigest()
    if source_sha != source_binding:
        raise ValueError("active source binding changed before audio import")
    try:
        verifier = ExternalTrustRootVerifier.from_app_config(app_config, clock=clock)
        scope = verifier.trusted_configuration.evidence_root if verifier.trusted_configuration else None
        timing, timing_sha, timing_raw = load_strict_json_snapshot(
            timing_path, "provider WordBoundary receipt", contained_by=scope, include_bytes=True
        )
        license_receipt, license_sha, license_raw = load_strict_json_snapshot(
            license_path, "TTS license receipt", contained_by=scope, include_bytes=True
        )
    except TrustRootVerificationError as error:
        raise ValueError(str(error)) from error
    expected = {"source_sha256": source_sha, "script_sha256": script_sha, "audio_sha256": audio_sha}
    candidate_class, provider_id, voice_id = _validate_timing_receipt(
        timing, expected=expected, duration_s=duration_s, narration=narration,
    )
    try:
        verified = verifier.verify_tts_license_declaration(
            license_receipt,
            expected={**expected, "timing_sha256": timing_sha, "provider_id": provider_id, "voice_id": voice_id},
        )
    except TrustRootVerificationError as error:
        if "provider_id" in str(error) or "voice_id" in str(error):
            raise ValueError("provider/voice license binding mismatch") from error
        raise ValueError(str(error)) from error
    packet = reject_reparse_path(revision / "audio-import")
    receipt = {
        "schema": "preproduction-audio-candidate-import-receipt-v1", "mode": "PREPRODUCTION", "request_id": request_id,
        **expected, "duration_s": format(duration_s, ".9f"), "timing_sha256": timing_sha, "license_sha256": license_sha,
        "provider_id": provider_id, "voice_id": voice_id, "integrity": "VERIFIED",
        "cryptographic_verification": "VERIFIED", "configuration_validation": verified.provenance,
        "provenance": "UNAUTHENTICATED", "candidate_class": candidate_class,
        "alignment_verification": "NOT_VERIFIED", "qa_qualification": "NOT_QUALIFIED",
        "authorization_assertion": "SOURCE_AUTHORIZATION_DECLARED_NOT_LEGAL_PROOF",
        "qualification_status": "NOT_QUALIFIED", "status": "TTS_REQUIRED", "operations": _DIRECTOR_BLOCKED_OPERATIONS,
    }
    files = {"audio.wav": audio_raw, "timing.json": timing_raw, "license.json": license_raw}
    index = {"schema": "preproduction-audio-candidate-import-index-v1", "entries": [
        {"path": name, "sha256": sha256(raw).hexdigest(), "size": len(raw)} for name, raw in sorted(files.items())
    ]}
    receipt["index_sha256"] = _candidate_digest(index)
    receipt["receipt_sha256"] = _candidate_digest(receipt)
    _validate_import_receipt(receipt)
    with file_lock(revision / ".audio-import.lock"):
        _assert_active_audio_import_snapshot(workspace, request_id, active_snapshot)
        staging = revision / ".audio-import-staging"
        if staging.exists():
            raise ValueError("real audio import staging requires audited recovery")
        if packet.exists():
            prior = verify_real_audio_candidate_import(packet, app_config=app_config, clock=clock)
            if prior != receipt:
                raise ValueError("real audio import conflicts with changed immutable evidence")
        else:
            staging.mkdir()
            try:
                for name, raw in files.items():
                    _write_exclusive(staging / name, raw)
                _write_exclusive(staging / "index.json", json.dumps(index, sort_keys=True, separators=(",", ":")).encode())
                _write_exclusive(staging / "receipt.json", json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode())
                _assert_active_audio_import_snapshot(workspace, request_id, active_snapshot)
                os.replace(staging, packet)
            except Exception:
                if staging.exists() and not any(staging.iterdir()):
                    staging.rmdir()
                raise
            verify_real_audio_candidate_import(packet, app_config=app_config, clock=clock)
    return {"status": "TTS_REQUIRED", "qualification_status": "NOT_QUALIFIED", "packet_path": str(packet), "receipt_sha256": receipt["receipt_sha256"], "operations": dict(_DIRECTOR_BLOCKED_OPERATIONS)}
