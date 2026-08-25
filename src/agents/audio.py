"""Audio Agent — generates TTS narration + word timestamps (§27-28).

Responsibility: TTS synthesis + timestamp alignment
Input: narration text
Output: audio/narration.wav + sentence_timestamps + word_timestamps
Constraints: ThalitaNeural, rate -8%, pitch +1Hz (§28); preserve voice across episodes (§29)
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from src.agents.base import BaseAgent, AgentResult
from src.providers.tts.edge_tts_provider import EdgeTTSProvider


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

        tts = EdgeTTSProvider(voice=voice, rate=rate, pitch=pitch)

        # Generate speech + sentence timestamps
        result = await tts.synthesize(text=narration, voice=voice, rate=rate, pitch=pitch)

        if not result.success:
            return AgentResult(success=False, error=f"TTS failed: {result.error}")

        # Word-level alignment with faster-whisper
        word_timestamps = []
        try:
            word_timestamps = await tts.word_align(result.audio_path, language="pt")
        except Exception as e:
            # Word alignment is a refinement — sentence timestamps are sufficient
            pass

        # Copy audio to episode dir if specified
        audio_path = result.audio_path
        if audio_dir:
            Path(audio_dir).mkdir(parents=True, exist_ok=True)
            target = Path(audio_dir) / "narration.mp3"
            import shutil
            shutil.copy2(audio_path, target)
            audio_path = str(target)

        return AgentResult(
            success=True,
            data={
                "audio_path": audio_path,
                "duration_s": result.duration_seconds,
                "sentence_timestamps": result.sentence_timestamps,
                "word_timestamps": word_timestamps,
                "rtf": result.metadata.get("rtf", 0),
            },
            next_state="STORYBOARDING",
        )
