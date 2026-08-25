"""Tests for Edge TTS Provider (§28, B5).

Tests use a short text and verify:
- Audio file is generated
- Sentence timestamps are returned
- Duration is correct
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.providers.tts.edge_tts_provider import EdgeTTSProvider


class TestEdgeTTSProvider:
    """B5: TTS with timestamps."""

    @pytest.mark.asyncio
    async def test_synthesize_basic(self):
        """Generate speech and verify audio + timestamps."""
        tts = EdgeTTSProvider()
        result = await tts.synthesize(
            text="Era uma vez um jovem pastor chamado Davi.",
            voice="pt-BR-ThalitaNeural",
            rate="-8%",
            pitch="+1Hz",
        )
        assert result.success
        assert os.path.exists(result.audio_path)
        assert result.duration_seconds > 0
        assert len(result.sentence_timestamps) > 0
        # First sentence should start near 0
        assert result.sentence_timestamps[0]["start"] < 1.0

    @pytest.mark.asyncio
    async def test_cost_is_zero(self):
        """edge-tts is free."""
        tts = EdgeTTSProvider()
        assert tts.estimate_cost() == 0.0

    def test_default_voice(self):
        """Default voice should be ThalitaNeural (§28)."""
        tts = EdgeTTSProvider()
        assert tts.voice == "pt-BR-ThalitaNeural"
        assert tts.rate == "-8%"
        assert tts.pitch == "+1Hz"
