"""GET-only ElevenLabs History origin readback.

This module deliberately has no synthesis, listing, discovery, retry-submit, CLI, or
state-transition surface.  It materializes exact provider bytes into quarantine and
returns permanently non-promotable evidence for a later, explicit consumer.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol

_BASE_ORIGIN = "https://api.elevenlabs.io"
_MAX_JSON_BYTES = 1_048_576
_MAX_AUDIO_BYTES = 64 * 1_024 * 1_024
_TIMEOUT_SECONDS = 15
_ID = re.compile(r"[A-Za-z0-9_-]{1,256}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BLOCKED = {
    "provider_dispatch": "BLOCKED",
    "render": "BLOCKED",
    "telegram": "BLOCKED",
    "publication": "BLOCKED",
}
_WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT = 0x0400
_QUARANTINE_LOCKS: dict[str, asyncio.Lock] = {}


class ElevenLabsReadbackTransport(Protocol):
    """Credential-owning boundary.  The service exposes only fixed GET paths."""

    authority: str

    async def get_json(self, *, path: str) -> Mapping[str, object]: ...

    async def get_bytes(self, *, path: str) -> bytes: ...


@dataclass(frozen=True)
class ElevenLabsHistoryBinding:
    request_id: str
    history_item_id: str
    expected_script_sha256: str
    expected_voice_id: str
    expected_provider: Literal["elevenlabs"]
    expected_source: Literal["TTS"]
    expected_audio_sha256: str | None


@dataclass(frozen=True)
class OriginReadbackReceipt:
    """Materialized, non-promotable bytes and their closed receipt projection."""

    audio_path: Path
    receipt_path: Path
    receipt: dict[str, object]


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _opaque_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{label} must be a bounded opaque identifier")
    return value


def _nonempty_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 16_384:
        raise ValueError(f"{label} must be a bounded nonempty string")
    return value


def _validate_binding(binding: ElevenLabsHistoryBinding) -> None:
    _opaque_id(binding.request_id, "request_id")
    _opaque_id(binding.history_item_id, "history_item_id")
    _opaque_id(binding.expected_voice_id, "expected_voice_id")
    if binding.expected_provider != "elevenlabs" or binding.expected_source != "TTS":
        raise ValueError("ElevenLabs history binding has an unsupported provider or source")
    if not isinstance(binding.expected_script_sha256, str) or not _SHA256.fullmatch(
        binding.expected_script_sha256
    ):
        raise ValueError("expected_script_sha256 must be a lowercase SHA-256")
    if binding.expected_audio_sha256 is not None and (
        not isinstance(binding.expected_audio_sha256, str)
        or not _SHA256.fullmatch(binding.expected_audio_sha256)
    ):
        raise ValueError("expected_audio_sha256 must be null or a lowercase SHA-256")


def _history_path(history_item_id: str) -> str:
    return f"/v1/history/{_opaque_id(history_item_id, 'history_item_id')}"


def _voice_path(voice_id: str) -> str:
    return f"/v1/voices/{_opaque_id(voice_id, 'voice_id')}"


def _audio_path(history_item_id: str) -> str:
    return f"{_history_path(history_item_id)}/audio"


def _validate_history(metadata: Mapping[str, object], binding: ElevenLabsHistoryBinding) -> dict[str, object]:
    if not isinstance(metadata, Mapping):
        raise TypeError("history metadata must be an object")
    if metadata.get("history_item_id") != binding.history_item_id:
        raise ValueError("history metadata history_item_id does not match binding")
    if metadata.get("source") != "TTS":
        raise ValueError("history metadata source is not TTS")
    if metadata.get("voice_id") != binding.expected_voice_id:
        raise ValueError("history metadata voice_id does not match binding")
    text = _nonempty_string(metadata.get("text"), "history text")
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != binding.expected_script_sha256:
        raise ValueError("history text hash does not match binding")
    date_unix = metadata.get("date_unix")
    if isinstance(date_unix, bool) or not isinstance(date_unix, (int, float)) or not math.isfinite(date_unix) or date_unix <= 0:
        raise ValueError("history date_unix must be finite and positive")
    request_id = metadata.get("request_id")
    if not isinstance(request_id, str):
        request_id = None
    elif len(request_id) > 256:
        raise ValueError("provider request_id exceeds the bounded receipt schema")
    return {
        "history_item_id": binding.history_item_id,
        "request_id": request_id,
        "source": "TTS",
        "voice_id": binding.expected_voice_id,
        "model_id": _nonempty_string(metadata.get("model_id"), "history model_id"),
        "text": text,
        "output_format": _nonempty_string(metadata.get("output_format"), "history output_format"),
        "date_unix": date_unix,
    }


def _json_value(value: object, *, depth: int = 0) -> object:
    """Allow only bounded JSON data before including provider metadata in a hash."""
    if depth > 8:
        raise ValueError("voice metadata nesting exceeds bound")
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("voice metadata has non-finite number")
        return value
    if isinstance(value, str):
        if len(value) > 4_096:
            raise ValueError("voice metadata string exceeds bound")
        return value
    if isinstance(value, list) and len(value) <= 64:
        return [_json_value(item, depth=depth + 1) for item in value]
    if isinstance(value, Mapping) and len(value) <= 64 and all(isinstance(key, str) and len(key) <= 128 for key in value):
        return {key: _json_value(item, depth=depth + 1) for key, item in value.items()}
    raise ValueError("voice metadata has an unsupported value")


def _voice_projection(metadata: Mapping[str, object], expected_voice_id: str) -> dict[str, object]:
    if not isinstance(metadata, Mapping) or metadata.get("voice_id") != expected_voice_id:
        raise ValueError("voice metadata voice_id does not match binding")
    category = _nonempty_string(metadata.get("category"), "voice category")
    permission = metadata.get("permission_on_resource")
    if not isinstance(permission, (str, bool)) and permission is not None:
        raise ValueError("voice permission_on_resource has unknown shape")
    is_owner = metadata.get("is_owner")
    if not isinstance(is_owner, bool):
        raise TypeError("voice is_owner must be a boolean")
    verified_languages = metadata.get("verified_languages")
    if verified_languages is not None and not isinstance(verified_languages, list):
        raise ValueError("voice verified_languages has unknown shape")
    return {
        "voice_id": expected_voice_id,
        "category": category,
        "permission_on_resource": permission,
        "is_owner": is_owner,
        "verified_languages": _json_value(verified_languages),
    }


def _is_reparse_point(path: Path) -> bool:
    """Detect symlinks and Windows junctions without resolving them."""
    try:
        status = os.lstat(path)
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(
        getattr(status, "st_file_attributes", 0) & _WINDOWS_FILE_ATTRIBUTE_REPARSE_POINT
    )


def _absolute_quarantine(quarantine: Path) -> Path:
    root = Path(quarantine)
    if not root.is_absolute():
        raise ValueError("quarantine root must be absolute")
    # Do not resolve(): it follows precisely the reparse points this boundary rejects.
    return root.absolute()


def _safe_directory(root: Path) -> None:
    """Create only ordinary directories after checking every existing ancestor."""
    root = _absolute_quarantine(root)
    for directory in reversed((root, *root.parents)):
        if _is_reparse_point(directory):
            raise ValueError("quarantine path contains a symlink or reparse point")
        if not os.path.lexists(directory):
            os.mkdir(directory)
        if _is_reparse_point(directory):
            raise ValueError("quarantine path contains a symlink or reparse point")
        if not directory.is_dir():
            raise ValueError("quarantine ancestor is not a directory")


def _safe_target(root: Path, path: Path, *, must_be_absent: bool) -> None:
    root = _absolute_quarantine(root)
    if not path.is_absolute() or path.parent != root:
        raise ValueError("quarantine target is outside its anchored root")
    _safe_directory(root)
    if _is_reparse_point(path):
        raise ValueError("quarantine target is a symlink or reparse point")
    if must_be_absent and os.path.lexists(path):
        raise FileExistsError(path)


def _pre_network_target(root: Path, path: Path) -> None:
    """Reject a reparse escape or existing destination without creating directories."""
    root = _absolute_quarantine(root)
    if not path.is_absolute() or path.parent != root:
        raise ValueError("quarantine target is outside its anchored root")
    for directory in reversed((root, *root.parents)):
        if os.path.lexists(directory) and _is_reparse_point(directory):
            raise ValueError("quarantine path contains a symlink or reparse point")
    if os.path.lexists(root):
        if not root.is_dir():
            raise ValueError("quarantine root is not a directory")
        if _is_reparse_point(path):
            raise ValueError("quarantine target is a symlink or reparse point")
        if os.path.lexists(path):
            raise FileExistsError(path)


def _write_exclusive(root: Path, path: Path, data: bytes) -> None:
    _safe_target(root, path, must_be_absent=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _read_safe_bytes(root: Path, path: Path) -> bytes:
    _safe_target(root, path, must_be_absent=False)
    if not path.is_file():
        raise ValueError("quarantine target is not a regular file")
    return path.read_bytes()


def _quarantine_lock(root: Path) -> asyncio.Lock:
    return _QUARANTINE_LOCKS.setdefault(os.path.normcase(os.fspath(root)), asyncio.Lock())


async def readback_existing_tts_origin(
    binding: ElevenLabsHistoryBinding,
    transport: ElevenLabsReadbackTransport,
    quarantine: Path,
) -> OriginReadbackReceipt:
    """Read injected fixture bytes; injected transports are permanently TEST-only."""
    return await _readback(
        binding,
        quarantine,
        transport_factory=lambda: transport,
        authenticated_readback="TEST_NOT_AUTHORITY",
        provenance="TEST_EXTERNAL_READBACK_FIXTURE",
    )


async def readback_existing_tts_origin_from_elevenlabs(
    binding: ElevenLabsHistoryBinding,
    quarantine: Path,
    *,
    credential_supplier: Callable[[], str],
) -> OriginReadbackReceipt:
    """Production-only factory path; credentials are unreachable before the barrier."""
    return await _readback(
        binding,
        quarantine,
        transport_factory=lambda: ElevenLabsProductionTransport(
            credential_supplier=credential_supplier
        ),
        authenticated_readback="API_KEY_AUTHENTICATED_GET",
        provenance="EXTERNAL_API_READBACK",
    )


async def _readback(
    binding: ElevenLabsHistoryBinding,
    quarantine: Path,
    *,
    transport_factory: Callable[[], ElevenLabsReadbackTransport],
    authenticated_readback: str,
    provenance: str,
) -> OriginReadbackReceipt:
    """Run the existing-target barrier before a factory, credential, or GET."""
    _validate_binding(binding)
    root = _absolute_quarantine(quarantine)
    audio_path = root / f"{binding.history_item_id}.audio"
    receipt_path = root / f"{binding.history_item_id}.receipt.json"
    claim_path = root / f"{binding.history_item_id}.claim.json"
    async with _quarantine_lock(root):
        # These guards are intentionally before a claim, factory, credential, or GET.
        # The durable exclusive claim then coordinates independent processes; it is
        # never released after a failure because that outcome is ambiguous.
        _pre_network_target(root, audio_path)
        _pre_network_target(root, receipt_path)
        _pre_network_target(root, claim_path)
        _write_exclusive(
            root,
            claim_path,
            _canonical(
                {
                    "schema": "elevenlabs-history-origin-readback-claim-v1",
                    "history_item_id": binding.history_item_id,
                    "request_id": binding.request_id,
                    "state": "CLAIMED",
                }
            ),
        )
        transport = transport_factory()
        history = _validate_history(
            await transport.get_json(path=_history_path(binding.history_item_id)), binding
        )
        voice = _voice_projection(
            await transport.get_json(path=_voice_path(binding.expected_voice_id)), binding.expected_voice_id
        )
        audio = await transport.get_bytes(path=_audio_path(binding.history_item_id))
        if not isinstance(audio, bytes) or not audio or len(audio) > _MAX_AUDIO_BYTES:
            raise ValueError("audio response must be bounded nonempty bytes")
        _write_exclusive(root, audio_path, audio)
        audio_sha256 = hashlib.sha256(_read_safe_bytes(root, audio_path)).hexdigest()
        if audio_sha256 != hashlib.sha256(audio).hexdigest():
            raise ValueError("quarantined audio bytes changed before hashing")
        if binding.expected_audio_sha256 is not None and audio_sha256 != binding.expected_audio_sha256:
            raise ValueError("downloaded audio hash does not match binding")
        receipt: dict[str, object] = {
            "schema": "elevenlabs-history-origin-readback-v1",
            "request_id": binding.request_id,
            "provider": "elevenlabs",
            "history_item_id": binding.history_item_id,
            "provider_request_id": history["request_id"],
            "source": "TTS",
            "voice_id": binding.expected_voice_id,
            "model_id": history["model_id"],
            "date_unix": history["date_unix"],
            "output_format": history["output_format"],
            "returned_text_sha256": hashlib.sha256(history["text"].encode("utf-8")).hexdigest(),
            "history_metadata_sha256": _digest(history),
            "voice_metadata_sha256": _digest(voice),
            "audio_sha256": audio_sha256,
            "audio_bytes": len(audio),
            "provider_output_hash": None,
            "authenticated_readback": authenticated_readback,
            "origin_provenance": provenance,
            "voice_access_observation": "OBSERVED_NOT_LEGAL_PROOF",
            "commercial_license": "NOT_VERIFIED",
            "acoustic_alignment": "NOT_VERIFIED",
            "qualification_status": "NOT_QUALIFIED",
            "status": "TTS_REQUIRED",
            "operations": dict(_BLOCKED),
        }
        receipt["receipt_sha256"] = _digest(receipt)
        _write_exclusive(root, receipt_path, _canonical(receipt))
        return OriginReadbackReceipt(audio_path=audio_path, receipt_path=receipt_path, receipt=receipt)


class ElevenLabsProductionTransport:
    """Concrete HTTPS-only, no-proxy, no-redirect GET transport.

    The credential supplier is invoked only after a fixed allowlisted path passes
    validation.  There is no URL, method, header, proxy, or redirect input surface.
    """

    authority = "API_KEY_AUTHENTICATED_GET"

    def __init__(self, *, credential_supplier: Callable[[], str]):
        if not callable(credential_supplier):
            raise TypeError("credential_supplier must be callable")
        self._credential_supplier = credential_supplier

    async def get_json(self, *, path: str) -> Mapping[str, object]:
        body = await self._get(path, limit=_MAX_JSON_BYTES)
        try:
            value = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=self._no_duplicate_object,
                parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON")),
            )
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            raise ValueError("provider returned malformed JSON") from error
        if not isinstance(value, dict):
            raise TypeError("provider JSON response must be an object")
        return value

    async def get_bytes(self, *, path: str) -> bytes:
        return await self._get(path, limit=_MAX_AUDIO_BYTES)

    @staticmethod
    def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    @staticmethod
    def _allowlisted(path: str) -> bool:
        return bool(
            re.fullmatch(r"/v1/history/[A-Za-z0-9_-]{1,256}(?:/audio)?", path)
            or re.fullmatch(r"/v1/voices/[A-Za-z0-9_-]{1,256}", path)
        )

    async def _get(self, path: str, *, limit: int) -> bytes:
        if not isinstance(path, str) or not self._allowlisted(path):
            raise ValueError("path is not an allowlisted ElevenLabs GET endpoint")
        credential = self._credential_supplier()
        if not isinstance(credential, str) or not credential or len(credential) > 4_096:
            raise ValueError("configured ElevenLabs credential is unavailable")
        import aiohttp

        timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout, trust_env=False) as session,
                session.get(
                    f"{_BASE_ORIGIN}{path}",
                    headers={"xi-api-key": credential},
                    allow_redirects=False,
                    proxy=None,
                ) as response,
            ):
                if response.status < 200 or response.status >= 300:
                    raise ValueError(f"ElevenLabs GET failed with HTTP {response.status}")
                length = response.content_length
                if length is not None and length > limit:
                    raise ValueError("ElevenLabs response exceeds configured bound")
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.content.iter_chunked(65_536):
                    total += len(chunk)
                    if total > limit:
                        raise ValueError("ElevenLabs response exceeds configured bound")
                    chunks.append(chunk)
                return b"".join(chunks)
        except (TimeoutError, OSError) as error:
            raise ValueError("ElevenLabs GET transport failure") from error
