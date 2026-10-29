from __future__ import annotations

import asyncio
import hashlib
import math
import os
import subprocess
import sys
import wave
from pathlib import Path

import pytest

from src.providers.tts.azure_speech_stock_tts import (
    AzureSpeechStockTTSProvider,
    OfflineAzureSpeechFixture,
)

VOICE = "pt-BR-ThalitaNeural"
TEXT = "Olá, Jacó. Deus está conosco."
LICENSE = {
    "license_kind": "AZURE_STOCK_NEURAL_VOICE",
    "voice": VOICE,
    "license_status": "PROVIDER_TERMS_REQUIRED",
    "allowed_use": "production_narration",
}


def _wav_bytes() -> bytes:
    path = Path(__file__).parent / ".azure-fixture.wav"
    try:
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(8_000)
            samples = bytearray()
            for index in range(8_000):
                sample = int(4_000 * math.sin(index / 8))
                samples.extend(sample.to_bytes(2, "little", signed=True))
            audio.writeframes(bytes(samples))
        return path.read_bytes()
    finally:
        path.unlink(missing_ok=True)


def _fixture() -> OfflineAzureSpeechFixture:
    return OfflineAzureSpeechFixture(
        audio_bytes=_wav_bytes(),
        transcript=TEXT,
        word_boundaries=[
            {"text": "Olá", "offset": 0, "duration": 2_000_000},
            {"text": "Jacó", "offset": 2_000_000, "duration": 2_000_000},
            {"text": "Deus", "offset": 4_000_000, "duration": 2_000_000},
            {"text": "está", "offset": 6_000_000, "duration": 2_000_000},
            {"text": "conosco", "offset": 8_000_000, "duration": 2_000_000},
        ],
        license_metadata=LICENSE,
    )


@pytest.mark.asyncio
async def test_test_fixture_captures_exact_word_boundaries_audio_hash_and_license_receipt(tmp_path: Path):
    calls = 0

    def forbidden_sdk_factory():
        nonlocal calls
        calls += 1
        raise AssertionError("TEST must not construct an Azure SDK client")

    provider = AzureSpeechStockTTSProvider(
        mode="TEST", offline_fixture=_fixture(), sdk_factory=forbidden_sdk_factory,
    )
    result = await provider.synthesize(TEXT, output_path=tmp_path / "narration.wav")

    receipt = result.metadata["boundary_receipt"]
    assert calls == 0
    assert result.metadata["voice"] == VOICE
    assert result.metadata["transcript"] == TEXT
    assert receipt["text"] == TEXT
    assert receipt["word_boundaries"] == [
        {"word": "Olá", "offset_100ns": 0, "duration_100ns": 2_000_000, "start": 0.0, "end": 0.2},
        {"word": "Jacó", "offset_100ns": 2_000_000, "duration_100ns": 2_000_000, "start": 0.2, "end": 0.4},
        {"word": "Deus", "offset_100ns": 4_000_000, "duration_100ns": 2_000_000, "start": 0.4, "end": 0.6},
        {"word": "está", "offset_100ns": 6_000_000, "duration_100ns": 2_000_000, "start": 0.6, "end": 0.8},
        {"word": "conosco", "offset_100ns": 8_000_000, "duration_100ns": 2_000_000, "start": 0.8, "end": 1.0},
    ]
    assert receipt["audio_sha256"] == hashlib.sha256(Path(result.audio_path).read_bytes()).hexdigest()
    assert result.duration_seconds == 1.0
    assert result.metadata["license"] == LICENSE


@pytest.mark.asyncio
async def test_live_is_explicitly_blocked_before_sdk_factory_or_output_write(tmp_path: Path):
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        raise AssertionError("must not construct SDK before admission")

    output = tmp_path / "new" / "narration.wav"
    provider = AzureSpeechStockTTSProvider(mode="LIVE", sdk_factory=factory)
    with pytest.raises(PermissionError, match="LIVE.*BLOCKED"):
        await provider.synthesize(TEXT, output_path=output, operation_id="jaco-001", operation_cap_usd="0.01")
    assert calls == 0
    assert not output.parent.exists()


@pytest.mark.asyncio
async def test_live_block_redacts_configured_secret_before_factory(monkeypatch, tmp_path: Path):
    secret = "not-for-logs-azure-secret"
    monkeypatch.setenv("AZURE_SPEECH_KEY", secret)
    provider = AzureSpeechStockTTSProvider(mode="LIVE")
    with pytest.raises(PermissionError, match="LIVE.*BLOCKED") as error:
        await provider.synthesize(TEXT, output_path=tmp_path / "narration.wav", operation_id="jaco-001", operation_cap_usd="0.01")
    assert secret not in str(error.value)


@pytest.mark.asyncio
async def test_live_fresh_provider_and_process_replays_are_blocked_with_zero_sdk_calls(tmp_path: Path):
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        raise AssertionError("LIVE must block before SDK construction")

    kwargs = {"output_path": tmp_path / "narration.wav", "operation_id": "jaco-001", "operation_cap_usd": "0.01"}
    for provider in (AzureSpeechStockTTSProvider(mode="LIVE", sdk_factory=factory), AzureSpeechStockTTSProvider(mode="LIVE", sdk_factory=factory)):
        with pytest.raises(PermissionError, match="LIVE.*BLOCKED"):
            await provider.synthesize(TEXT, **kwargs)
    script = (
        "import asyncio\n"
        "from pathlib import Path\n"
        "from src.providers.tts.azure_speech_stock_tts import AzureSpeechStockTTSProvider\n"
        "asyncio.run(AzureSpeechStockTTSProvider(mode='LIVE').synthesize("
        "'x', output_path=Path('x.wav'), operation_id='jaco-001', operation_cap_usd='0.01'))\n"
    )
    replay = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-c", script],
        check=False,
        cwd=Path.cwd(),
        text=True,
        capture_output=True,
    )
    assert replay.returncode != 0
    assert "LIVE BLOCKED" in replay.stderr
    assert calls == 0


@pytest.mark.asyncio
async def test_live_rejects_changed_operation_arguments_before_factory(tmp_path: Path):
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        raise AssertionError("must not run")

    provider = AzureSpeechStockTTSProvider(mode="LIVE", sdk_factory=factory)
    with pytest.raises(PermissionError, match="LIVE.*BLOCKED"):
        await provider.synthesize("different immutable text", output_path=tmp_path / "narration.wav", operation_id="jaco-001", operation_cap_usd="0.02")
    assert calls == 0


@pytest.mark.asyncio
async def test_test_mode_rejects_reparse_output_path_before_any_write(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = tmp_path / "linked"
    try:
        os.symlink(outside, linked, target_is_directory=True)
    except (NotImplementedError, OSError):
        pytest.skip("symlinks unavailable")
    provider = AzureSpeechStockTTSProvider(mode="TEST", offline_fixture=_fixture())
    with pytest.raises(ValueError, match="reparse"):
        await provider.synthesize(TEXT, output_path=linked / "narration.wav")
    assert not list(outside.iterdir())


def test_test_mode_cannot_be_reconfigured_to_live():
    provider = AzureSpeechStockTTSProvider(mode="TEST", offline_fixture=_fixture())
    with pytest.raises(PermissionError, match="TEST"):
        provider.enable_live()
