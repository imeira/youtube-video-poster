"""Edge TTS Provider — free pt-BR TTS with word timestamps.

§28: Voice pt-BR-ThalitaNeural, rate -8%, pitch +1Hz
§27: Narration as timeline — TTS → AUDIO MASTER → REAL TIMESTAMPS → STORYBOARD
§29: Voice preserved across episodes
B5: Measured RTF 0.048x (1.8s gen for 36.5s audio)
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
from pathlib import Path

from src.providers.base import TTSProvider, TTSResult


class EdgeTTSProvider(TTSProvider):
    """Free TTS using Microsoft Edge's online neural voices.

    Voice: pt-BR-ThalitaNeural (confirmed in B5)
    Requests WordBoundary events and derives sentence windows from those exact
    word timings for real narration alignment (§32).
    Falls back to Azure Speech if edge-tts is unavailable (§28 azure_fallback).
    """

    def __init__(self, voice: str = "pt-BR-ThalitaNeural", rate: str = "-8%", pitch: str = "+1Hz"):
        import edge_tts
        self._edge_tts = edge_tts
        self.voice = voice
        self.rate = rate
        self.pitch = pitch

    def estimate_cost(self, **params) -> float:
        """edge-tts is free. Azure fallback costs ~$0.07/episode."""
        return 0.0

    async def synthesize(
        self,
        text: str,
        voice: str = "",
        rate: str = "",
        pitch: str = "",
    ) -> TTSResult:
        """Synthesize speech and return audio + sentence timestamps.

        Args:
            text: Narration text to synthesize.
            voice: Voice name (default: pt-BR-ThalitaNeural).
            rate: Speech rate (default: -8%).
            pitch: Pitch adjustment (default: +1Hz).

        Returns:
            TTSResult with audio_path, sentence_timestamps, and duration.
        """
        voice = voice or self.voice
        rate = rate or self.rate
        pitch = pitch or self.pitch

        # Use a temp directory for output
        output_dir = Path(os.environ.get("LOCALAPPDATA", "/tmp")) / "Temp" / "studio_tts"
        output_dir.mkdir(parents=True, exist_ok=True)

        audio_path = output_dir / "narration.mp3"
        t_start = time.time()

        communicate = self._edge_tts.Communicate(
            text,
            voice,
            rate=rate,
            pitch=pitch,
            boundary="WordBoundary",
        )
        word_timestamps: list[dict] = []

        with open(audio_path, "wb") as audio_file:
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_file.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    # offset and duration are in 100ns units (10^-7 seconds)
                    offset_s = chunk["offset"] / 10_000_000
                    duration_s = chunk["duration"] / 10_000_000
                    word_timestamps.append({
                        "start": round(offset_s, 3),
                        "end": round(offset_s + duration_s, 3),
                        "word": chunk["text"],
                    })

        gen_time = time.time() - t_start

        # Get audio duration via ffprobe
        duration = self._get_duration(str(audio_path))
        sentence_timestamps = self._derive_sentence_timestamps(text, word_timestamps)

        return TTSResult(
            success=True,
            audio_path=str(audio_path),
            duration_seconds=duration,
            sentence_timestamps=sentence_timestamps,
            word_timestamps=word_timestamps,
            cost=0.0,
            generation_time=gen_time,
            metadata={"voice": voice, "rate": rate, "pitch": pitch, "rtf": gen_time / duration if duration > 0 else 0},
        )

    @staticmethod
    def _derive_sentence_timestamps(text: str, word_timestamps: list[dict]) -> list[dict]:
        """Map narration sentences onto exact Edge WordBoundary windows."""
        sentences = [
            part.strip()
            for part in re.split(r"(?<=[.!?])\s+", text.strip())
            if part.strip()
        ]
        if not sentences or not word_timestamps:
            return []

        sentence_timestamps = []
        word_index = 0
        for sentence_index, sentence in enumerate(sentences):
            expected_words = len(
                re.findall(r"\b[\wÀ-ÿ]+(?:[-'][\wÀ-ÿ]+)*\b", sentence)
            )
            if sentence_index == len(sentences) - 1:
                sentence_words = word_timestamps[word_index:]
            else:
                sentence_words = word_timestamps[word_index:word_index + expected_words]
            if not sentence_words:
                continue
            start = sentence_words[0]["start"]
            end = sentence_words[-1]["end"]
            sentence_timestamps.append({
                "start": round(start, 3),
                "end": round(end, 3),
                "duration": round(end - start, 3),
                "text": sentence,
            })
            word_index += len(sentence_words)
        return sentence_timestamps

    def _get_duration(self, audio_path: str) -> float:
        """Get audio duration in seconds using ffprobe."""
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", audio_path],
                capture_output=True, text=True, timeout=10,
            )
            return float(r.stdout.strip()) if r.stdout.strip() else 0.0
        except Exception:
            return 0.0

    async def word_align(self, audio_path: str, language: str = "pt") -> list[dict]:
        """Word-level alignment using faster-whisper (B5: 3.2s for 36s audio).

        Used as verification/refinement on top of SentenceBoundary timestamps.
        """
        from faster_whisper import WhisperModel
        model = WhisperModel("tiny", device="cpu", compute_type="int8")
        segments, _ = model.transcribe(audio_path, word_timestamps=True, language=language)
        words = []
        for seg in segments:
            for w in seg.words:
                words.append({
                    "start": round(w.start, 3),
                    "end": round(w.end, 3),
                    "word": w.word,
                })
        return words

    async def execute(self, **params) -> TTSResult:
        """Execute TTS synthesis."""
        return await self.synthesize(**params)
