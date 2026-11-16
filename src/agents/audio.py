"""Audio Agent — generates TTS narration + word timestamps (§27-28).

Responsibility: TTS synthesis + timestamp alignment
Input: narration text
Output: audio/narration.wav + sentence_timestamps + word_timestamps
Constraints: ThalitaNeural, rate -8%, pitch +1Hz (§28); preserve voice across episodes (§29)
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import math
import os
import shutil
from pathlib import Path

from src.agents.base import AgentResult, BaseAgent
from src.hybrid.artifacts import atomic_json, digest, sha256
from src.hybrid.locks import file_slot
from src.path_guard import reject_reparse_path
from src.providers.tts import edge_tts_provider
from src.providers.tts.edge_tts_provider import EdgeTTSProvider


def _validate_candidate_data(data):
    fields = {"audio_path", "duration_s", "sentence_timestamps", "word_timestamps", "rtf"}
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError("closed audio candidate data schema required")
    duration = data["duration_s"]
    if (type(duration) not in {int, float} or not math.isfinite(duration) or duration <= 0
            or not isinstance(data["audio_path"], str) or not data["audio_path"]
            or not isinstance(data["word_timestamps"], list)
            or not isinstance(data["sentence_timestamps"], list)):
        raise ValueError("invalid candidate duration, path or timestamp lists")
    # Structural and semantic validation of timestamp events
    for kind, items, text_key in (("word", data["word_timestamps"], "word"), ("sentence", data["sentence_timestamps"], "text")):
        last_start = 0.0
        for item in items:
            if not isinstance(item, dict) or not {"start", "end", text_key}.issubset(set(item)):
                raise ValueError(f"invalid {kind} timestamp entry schema")
            start, end, text = item["start"], item["end"], item[text_key]
            if (type(start) not in {int, float} or not math.isfinite(start)
                    or type(end) not in {int, float} or not math.isfinite(end)
                    or not isinstance(text, str)):
                raise ValueError(f"invalid {kind} timestamp scalar types")
            if not (0.0 <= start <= end <= duration + 0.1):
                raise ValueError(f"{kind} timestamps out of bounds: {start} -> {end} (duration={duration})")
            if start < last_start - 0.001:
                raise ValueError(f"{kind} timestamps are out of chronological order: {start} < {last_start}")
            last_start = start


class AudioAgent(BaseAgent):
    """Generates TTS narration with timestamps (§27-28)."""

    def __init__(self):
        super().__init__(name="VoiceDirector")

    async def run(
        self,
        episode_id: str,
        narration: str = "",
        audio_dir: str = "",
        voice: str = "pt-BR-ThalitaNeural",
        rate: str = "-8%",
        pitch: str = "+1Hz",
        **kwargs,
    ) -> AgentResult:
        """Generate TTS and align timestamps.

        §27: SCRIPT → TTS → AUDIO → REAL TIMESTAMPS → STORYBOARD
        """
        if not narration:
            return AgentResult(success=False, error="No narration text provided")

        try:
            provider_version = importlib.metadata.version("edge-tts")
        except importlib.metadata.PackageNotFoundError:
            provider_version = "not-installed"
        key = digest({
            "schema": "audio-candidate-cache/v1", "episode_id": episode_id,
            "narration_sha256": hashlib.sha256(narration.encode("utf-8")).hexdigest(),
            "voice": voice, "rate": rate, "pitch": pitch,
            "provider": f"{EdgeTTSProvider.__module__}.{EdgeTTSProvider.__qualname__}",
            "provider_version": provider_version,
            "provider_source_sha256": sha256(edge_tts_provider.__file__),
            "algorithm_sha256": sha256(__file__),
        })
        cache = Path(audio_dir) / ".audio-cache" / f"{key}.json" if audio_dir else None
        arguments = {"key": key, "cache": cache, "narration": narration, "audio_dir": audio_dir,
                     "voice": voice, "rate": rate, "pitch": pitch}
        if cache is not None:
            async with file_slot([cache.with_suffix(".lock")]):
                return await self._candidate(**arguments)
        return await self._candidate(**arguments)

    async def _candidate(self, *, key, cache, narration, audio_dir, voice, rate, pitch):
        if cache is not None:
            try:
                reject_reparse_path(cache)
            except (OSError, ValueError) as error:
                return AgentResult(success=False, error=f"Invalid audio candidate cache: {error}")
        if cache is not None and cache.exists():
            try:
                receipt = json.loads(cache.read_text(encoding="utf-8"))
                checksum = receipt.pop("receipt_sha256")
                _validate_candidate_data(receipt["data"])
                suffix = Path(receipt["data"]["audio_path"]).suffix
                if suffix not in {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac"}:
                    raise ValueError("invalid candidate audio suffix")
                expected = str(cache.with_suffix(suffix))
                if (set(receipt) != {"key", "audio_sha256", "data"}
                        or receipt["key"] != key or checksum != digest(receipt)
                        or receipt["data"]["audio_path"] != expected
                        or sha256(reject_reparse_path(Path(expected))) != receipt["audio_sha256"]):
                    raise ValueError("audio candidate cache binding mismatch")
                return AgentResult(
                    success=True, data={**receipt["data"], "cache_hit": True},
                    next_state="STORYBOARDING",
                )
            except (OSError, ValueError, KeyError, TypeError) as error:
                return AgentResult(success=False, error=f"Invalid audio candidate cache: {error}")

        if cache is not None and any(
            path.name != cache.with_suffix(".lock").name
            for path in cache.parent.glob(f"{key}.*")
        ):
            return AgentResult(success=False, error="Incomplete audio candidate cache requires reconciliation")

        tts = EdgeTTSProvider(voice=voice, rate=rate, pitch=pitch)

        # Generate speech + sentence timestamps
        output = {"output_path": cache.with_suffix(".source.mp3")} if cache is not None else {}
        result = await tts.synthesize(text=narration, voice=voice, rate=rate, pitch=pitch, **output)

        if not result.success:
            return AgentResult(success=False, error=f"TTS failed: {result.error}")

        # Edge WordBoundary is authoritative. Whisper is only a fallback for
        # providers or edge-tts versions that return no direct word events.
        word_timestamps = result.word_timestamps
        if not word_timestamps:
            try:
                word_timestamps = await tts.word_align(result.audio_path, language="pt")
            except (ImportError, OSError, RuntimeError, ValueError) as error:
                # Sentence timestamps remain a valid fallback.
                logging.getLogger(__name__).warning("Optional word alignment unavailable: %s", type(error).__name__)

        audio_path = result.audio_path
        data = {
            "audio_path": audio_path,
            "duration_s": result.duration_seconds,
            "sentence_timestamps": result.sentence_timestamps,
            "word_timestamps": word_timestamps,
            "rtf": result.metadata.get("rtf", 0),
        }
        # Retain distinct candidates; no cache entry can replace prior narration.
        if cache is not None:
            try:
                _validate_candidate_data(data)
            except ValueError as error:
                return AgentResult(success=False, error=f"Invalid audio cache candidate: {error}")
            source = reject_reparse_path(Path(audio_path))
            expected_sha256 = sha256(source)
            target = cache.with_suffix(source.suffix.lower())
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open("rb") as incoming, target.open("xb") as outgoing:
                shutil.copyfileobj(incoming, outgoing)
                outgoing.flush()
                os.fsync(outgoing.fileno())
            if sha256(source) != expected_sha256 or sha256(target) != expected_sha256:
                return AgentResult(success=False, error="Audio cache candidate changed during copy; retained for reconciliation")
            audio_path = str(target)
            # Keep the initial legacy filename without ever replacing it.
            try:
                with target.open("rb") as incoming, (Path(audio_dir) / "narration.mp3").open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
            except FileExistsError:
                pass

        data["audio_path"] = audio_path
        if cache is not None:
            receipt = {"key": key, "audio_sha256": sha256(audio_path), "data": data}
            atomic_json(cache, {**receipt, "receipt_sha256": digest(receipt)})
        return AgentResult(
            success=True, data={**data, "cache_hit": False},
            next_state="STORYBOARDING",
        )
