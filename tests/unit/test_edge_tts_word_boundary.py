from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.audio import AudioAgent
from src.providers.base import TTSResult
from src.providers.tts.edge_tts_provider import EdgeTTSProvider


class _FakeCommunicate:
    boundary_requested = None

    def __init__(self, text, voice, *, rate, pitch, boundary):
        self.text = text
        _FakeCommunicate.boundary_requested = boundary

    async def stream(self):
        yield {"type": "audio", "data": b"ID3fake"}
        words = [
            ("Era", 0, 2_000_000),
            ("uma", 2_500_000, 2_000_000),
            ("vez.", 5_000_000, 2_500_000),
            ("Outra", 8_000_000, 2_000_000),
            ("frase.", 11_000_000, 3_000_000),
        ]
        for text, offset, duration in words:
            yield {
                "type": "WordBoundary",
                "text": text,
                "offset": offset,
                "duration": duration,
            }


@pytest.mark.asyncio
async def test_synthesize_requests_word_boundaries_and_derives_sentence_windows(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    tts = EdgeTTSProvider()
    tts._edge_tts = SimpleNamespace(Communicate=_FakeCommunicate)
    monkeypatch.setattr(tts, "_get_duration", lambda _: 1.4)

    result = await tts.synthesize("Era uma vez. Outra frase.")

    assert _FakeCommunicate.boundary_requested == "WordBoundary"
    assert [word["word"] for word in result.word_timestamps] == [
        "Era", "uma", "vez.", "Outra", "frase."
    ]
    assert result.sentence_timestamps == [
        {"start": 0.0, "end": 0.75, "duration": 0.75, "text": "Era uma vez."},
        {"start": 0.8, "end": 1.4, "duration": 0.6, "text": "Outra frase."},
    ]


@pytest.mark.asyncio
async def test_audio_agent_prefers_direct_word_boundaries_over_whisper(monkeypatch, tmp_path: Path):
    audio_file = tmp_path / "source.mp3"
    audio_file.write_bytes(b"ID3fake")

    class FakeProvider:
        def __init__(self, **kwargs):
            pass

        async def synthesize(self, **kwargs):
            return TTSResult(
                success=True,
                audio_path=str(audio_file),
                duration_seconds=1.0,
                sentence_timestamps=[
                    {"start": 0.0, "end": 1.0, "duration": 1.0, "text": "Olá mundo."}
                ],
                word_timestamps=[
                    {"start": 0.0, "end": 0.4, "word": "Olá"},
                    {"start": 0.5, "end": 1.0, "word": "mundo."},
                ],
            )

        async def word_align(self, *args, **kwargs):
            raise AssertionError("Whisper must not run when Edge WordBoundary data exists")

    monkeypatch.setattr("src.agents.audio.EdgeTTSProvider", FakeProvider)

    result = await AudioAgent().run(
        episode_id="EP2",
        narration="Olá mundo.",
        audio_dir=str(tmp_path / "audio"),
    )

    assert result.success
    assert result.data["word_timestamps"] == [
        {"start": 0.0, "end": 0.4, "word": "Olá"},
        {"start": 0.5, "end": 1.0, "word": "mundo."},
    ]
