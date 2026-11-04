"""Azure Speech stock-voice adapter with an offline, non-promotable TEST fixture.

LIVE is deliberately blocked before any output write, SDK factory construction,
credential read, or provider call because this slice has no durable authenticated
budget reservation/executor integration.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from src.hybrid.locks import reject_reparse_path
from src.providers.base import TTSProvider, TTSResult

VOICE = "pt-BR-ThalitaNeural"
_PROVIDER = "azure-speech"
_TICKS_PER_SECOND = 10_000_000
_LICENSE_KEYS = {"license_kind", "voice", "license_status", "allowed_use"}


class SubmissionUnknown(RuntimeError):
    """The provider may have accepted the request; reconcile before any successor."""


class AzureSpeechResponse(Protocol):
    word_boundaries: list[dict[str, Any]]
    transcript: str
    license_metadata: dict[str, str]


class AzureSpeechClient(Protocol):
    def synthesize(self, text: str, voice: str, output_path: Path) -> AzureSpeechResponse: ...


@dataclass(frozen=True)
class _FixtureResponse:
    word_boundaries: list[dict[str, Any]]
    transcript: str
    license_metadata: dict[str, str]


class OfflineAzureSpeechFixture:
    """Injectable, prebuilt WAV fixture used exclusively by TEST.

    The fixture represents a stock provider voice as metadata only; it does not
    claim a local recording is licensed.  Production readiness must separately
    verify the provider's then-current terms and regional endpoint availability.
    """
    def __init__(self, *, audio_bytes: bytes, transcript: str, word_boundaries: list[dict[str, Any]],
                 license_metadata: dict[str, str]) -> None:
        if not isinstance(audio_bytes, bytes) or not audio_bytes:
            raise ValueError("offline audio fixture bytes are required")
        if not isinstance(transcript, str) or not transcript:
            raise ValueError("offline fixture transcript is required")
        if type(word_boundaries) is not list or not word_boundaries:
            raise ValueError("offline fixture word boundaries are required")
        if type(license_metadata) is not dict or set(license_metadata) != _LICENSE_KEYS:
            raise ValueError("offline fixture requires closed stock-voice license metadata")
        if license_metadata.get("voice") != VOICE:
            raise ValueError("offline fixture license must bind pt-BR-ThalitaNeural")
        self.audio_bytes = audio_bytes
        self.transcript = transcript
        self.word_boundaries = [dict(item) for item in word_boundaries]
        self.license_metadata = dict(license_metadata)

    def synthesize(self, text: str, voice: str, output_path: Path) -> _FixtureResponse:
        if text != self.transcript or voice != VOICE:
            raise ValueError("offline fixture only serves its exact transcript and stock voice")
        output_path.write_bytes(self.audio_bytes)
        return _FixtureResponse(self.word_boundaries, self.transcript, self.license_metadata)


class _AzureSpeechSDKClient:
    """Minimal synchronous SDK wrapper; constructed only after LIVE admission."""
    def __init__(self) -> None:
        try:
            import azure.cognitiveservices.speech as speechsdk
        except ImportError as error:  # pragma: no cover - dependency intentionally optional
            raise RuntimeError("Azure Speech SDK is not installed; install azure-cognitiveservices-speech in the existing venv") from error
        endpoint = os.environ.get("AZURE_SPEECH_ENDPOINT")
        key = os.environ.get("AZURE_SPEECH_KEY")
        if not endpoint or not key:
            raise RuntimeError("Azure Speech endpoint availability and configured credentials are required")
        self._sdk, self._endpoint, self._key = speechsdk, endpoint, key

    def synthesize(self, text: str, voice: str, output_path: Path) -> AzureSpeechResponse:
        sdk = self._sdk
        config = sdk.SpeechConfig(endpoint=self._endpoint, subscription=self._key)
        config.speech_synthesis_voice_name = voice
        output = sdk.audio.AudioOutputConfig(filename=str(output_path))
        synthesizer = sdk.SpeechSynthesizer(speech_config=config, audio_config=output)
        boundaries: list[dict[str, Any]] = []

        def on_boundary(event: Any) -> None:
            boundaries.append({"text": event.text, "offset": event.audio_offset, "duration": event.duration})

        synthesizer.synthesis_word_boundary.connect(on_boundary)
        result = synthesizer.speak_text_async(text).get()
        if result.reason != sdk.ResultReason.SynthesizingAudioCompleted:
            raise RuntimeError("Azure Speech did not complete synthesis")
        return _FixtureResponse(boundaries, text, {
            "license_kind": "AZURE_STOCK_NEURAL_VOICE", "voice": voice,
            "license_status": "PROVIDER_TERMS_REQUIRED", "allowed_use": "production_narration",
        })


def _wav_duration_and_non_silent(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as audio:
            if audio.getnchannels() < 1 or audio.getsampwidth() != 2 or audio.getframerate() <= 0 or audio.getnframes() <= 0:
                raise ValueError("audio fixture must be a non-empty PCM16 WAV")
            frames = audio.readframes(audio.getnframes())
            if not any(frames):
                raise ValueError("audio fixture is silent")
            return audio.getnframes() / audio.getframerate()
    except (wave.Error, OSError) as error:
        raise ValueError("audio fixture must be a valid non-silent WAV") from error


def _boundaries(raw: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    if not raw:
        raise ValueError("actual Azure WordBoundary events are required")
    normalized: list[dict[str, Any]] = []
    for item in raw:
        if type(item) is not dict or set(item) != {"text", "offset", "duration"}:
            raise ValueError("WordBoundary event must use exact text/offset/duration fields")
        word, offset, duration = item["text"], item["offset"], item["duration"]
        if not isinstance(word, str) or not word or type(offset) is not int or type(duration) is not int or offset < 0 or duration <= 0:
            raise ValueError("WordBoundary event is invalid")
        normalized.append({"word": word, "offset_100ns": offset, "duration_100ns": duration,
                           "start": offset / _TICKS_PER_SECOND, "end": (offset + duration) / _TICKS_PER_SECOND})
    if "".join(part["word"] for part in normalized).casefold().replace(" ", "") == "":
        raise ValueError("WordBoundary transcript is empty")
    return normalized


class AzureSpeechStockTTSProvider(TTSProvider):
    """Offline TEST adapter; LIVE remains fail-closed until durably integrated."""
    def __init__(self, *, mode: str, offline_fixture: OfflineAzureSpeechFixture | None = None,
                 sdk_factory: Callable[[], AzureSpeechClient] | None = None,
                 voice: str = VOICE) -> None:
        if mode not in {"TEST", "LIVE"}:
            raise ValueError("Azure Speech mode must be TEST or LIVE")
        if voice != VOICE:
            raise ValueError("only the approved stock voice pt-BR-ThalitaNeural is supported")
        if mode == "TEST" and offline_fixture is None:
            raise ValueError("TEST requires an injectable offline fixture")
        self.mode, self.fixture = mode, offline_fixture
        self._sdk_factory = sdk_factory or _AzureSpeechSDKClient
        self.voice = voice

    def estimate_cost(self, **params: Any) -> float:
        """No local estimate is financial authority for a blocked LIVE adapter."""
        return 0.0

    def enable_live(self) -> None:
        if self.mode == "TEST":
            raise PermissionError("TEST adapter cannot be reconfigured to LIVE")
        raise PermissionError("LIVE mode is selected only by trusted composition")

    def reconcile(self, operation_id: str) -> dict[str, Any]:
        raise SubmissionUnknown(
            "Azure Speech LIVE is BLOCKED: no durable executor or authenticated provider readback is configured"
        )

    def _preflight(self, text: str, operation_id: str | None, operation_cap_usd: object | None) -> dict[str, Any]:
        if self.mode == "TEST":
            return {"state": "TEST_FIXTURE"}
        raise PermissionError(
            "Azure Speech LIVE BLOCKED: no existing durable executor can atomically reserve an immutable "
            "operation intent against authenticated budget authority before SDK construction"
        )

    async def synthesize(self, text: str, voice: str = "", rate: str = "", pitch: str = "", *,
                         output_path: str | Path, operation_id: str | None = None,
                         operation_cap_usd: object | None = None) -> TTSResult:
        if not isinstance(text, str) or not text.strip():
            raise ValueError("exact non-empty narration text is required")
        if voice and voice != self.voice:
            raise ValueError("requested voice differs from approved stock voice")
        target = reject_reparse_path(Path(output_path))
        intent = self._preflight(text, operation_id, operation_cap_usd)
        target.parent.mkdir(parents=True, exist_ok=True)
        target = reject_reparse_path(target)
        fd, staged_name = tempfile.mkstemp(prefix=".azure-speech-", suffix=".wav", dir=target.parent)
        os.close(fd)
        staged = Path(staged_name)
        try:
            if self.mode == "TEST":
                response = self.fixture.synthesize(text, self.voice, staged)  # type: ignore[union-attr]
            else:
                client = self._sdk_factory()
                response = client.synthesize(text, self.voice, staged)
            if response.transcript != text:
                raise ValueError("Azure Speech transcript does not bind the submitted text")
            duration = _wav_duration_and_non_silent(staged)
            words = _boundaries(response.word_boundaries, text)
            if type(response.license_metadata) is not dict or set(response.license_metadata) != _LICENSE_KEYS or response.license_metadata.get("voice") != self.voice:
                raise ValueError("Azure Speech stock-voice license metadata is invalid")
            os.replace(staged, target)
        except TimeoutError as error:
            raise SubmissionUnknown("Azure Speech submission outcome UNKNOWN; reconcile before any successor") from error
        finally:
            staged.unlink(missing_ok=True)
        audio_hash = hashlib.sha256(target.read_bytes()).hexdigest()
        receipt = {"provider": _PROVIDER, "voice": self.voice, "text": text, "audio_sha256": audio_hash,
                   "word_boundaries": words, "duration_seconds": duration}
        return TTSResult(success=True, audio_path=str(target), duration_seconds=duration,
                         word_timestamps=[{"start": item["start"], "end": item["end"], "word": item["word"]} for item in words],
                         cost=0.0, metadata={"provider": _PROVIDER, "voice": self.voice, "transcript": text,
                         "audio_sha256": audio_hash, "license": dict(response.license_metadata),
                         "boundary_receipt": receipt, "operation_receipt": dict(intent)})

    async def execute(self, **params) -> TTSResult:
        return await self.synthesize(**params)
