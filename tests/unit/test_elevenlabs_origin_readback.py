"""Offline contract tests for GET-only ElevenLabs history origin readback."""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest


class FixtureTransport:
    """A protocol-only fixture: it is not provider authentication authority."""

    authority = "TEST_NOT_AUTHORITY"

    def __init__(self, history: dict[str, object], voice: dict[str, object], audio: bytes):
        self.history = history
        self.voice = voice
        self.audio = audio
        self.paths: list[str] = []

    async def get_json(self, *, path: str) -> dict[str, object]:
        self.paths.append(path)
        if path.startswith("/v1/history/"):
            return self.history
        if path.startswith("/v1/voices/"):
            return self.voice
        raise AssertionError("unexpected GET path")

    async def get_bytes(self, *, path: str) -> bytes:
        self.paths.append(path)
        assert path.endswith("/audio")
        return self.audio


class TrapTransport:
    authority = "TEST_NOT_AUTHORITY"

    def __init__(self):
        self.called = False

    async def get_json(self, *, path: str) -> dict[str, object]:
        self.called = True
        raise AssertionError("transport must not be called")

    async def get_bytes(self, *, path: str) -> bytes:
        self.called = True
        raise AssertionError("transport must not be called")


class SpoofedProductionAuthorityFixture(FixtureTransport):
    """A test double cannot mint production authority by setting a string."""

    authority = "API_KEY_AUTHENTICATED_GET"


def _binding(*, audio: bytes = b"RIFF-audio"):
    from src.hybrid.elevenlabs_origin_readback import ElevenLabsHistoryBinding

    return ElevenLabsHistoryBinding(
        request_id="local-request-001",
        history_item_id="hist_abc",
        expected_script_sha256=hashlib.sha256(b"literal script").hexdigest(),
        expected_voice_id="voice_abc",
        expected_provider="elevenlabs",
        expected_source="TTS",
        expected_audio_sha256=hashlib.sha256(audio).hexdigest(),
    )


def _transport(*, audio: bytes = b"RIFF-audio") -> FixtureTransport:
    return FixtureTransport(
        {
            "history_item_id": "hist_abc",
            "request_id": None,
            "source": "TTS",
            "voice_id": "voice_abc",
            "model_id": "eleven_multilingual_v2",
            "text": "literal script",
            "output_format": "mp3_44100_128",
            "date_unix": 123.5,
            "ignored_provider_secret": "must-not-persist",
        },
        {
            "voice_id": "voice_abc",
            "category": "premade",
            "permission_on_resource": "read",
            "is_owner": False,
            "verified_languages": [{"language": "pt", "model_id": "eleven_multilingual_v2"}],
            "token": "must-not-persist",
        },
        audio,
    )


def test_public_service_reads_exact_three_gets_writes_exclusive_audio_and_receipt(tmp_path: Path):
    from src.hybrid.elevenlabs_origin_readback import readback_existing_tts_origin

    audio = b"RIFF-audio"
    transport = _transport(audio=audio)
    result = asyncio.run(
        readback_existing_tts_origin(_binding(audio=audio), transport, tmp_path / "quarantine")
    )

    receipt = result.receipt
    assert transport.paths == [
        "/v1/history/hist_abc",
        "/v1/voices/voice_abc",
        "/v1/history/hist_abc/audio",
    ]
    assert result.audio_path.read_bytes() == audio
    assert result.audio_path.name == "hist_abc.audio"
    assert receipt["provider_request_id"] is None
    assert receipt["audio_sha256"] == hashlib.sha256(audio).hexdigest()
    assert receipt["authenticated_readback"] == "TEST_NOT_AUTHORITY"
    assert receipt["origin_provenance"] == "TEST_EXTERNAL_READBACK_FIXTURE"
    assert receipt["commercial_license"] == "NOT_VERIFIED"
    assert receipt["acoustic_alignment"] == "NOT_VERIFIED"
    assert receipt["qualification_status"] == "NOT_QUALIFIED"
    assert receipt["status"] == "TTS_REQUIRED"
    assert receipt["operations"] == {
        "provider_dispatch": "BLOCKED",
        "render": "BLOCKED",
        "telegram": "BLOCKED",
        "publication": "BLOCKED",
    }
    assert "ignored_provider_secret" not in json.dumps(receipt)
    assert "must-not-persist" not in json.dumps(receipt)
    expected_hash = hashlib.sha256(
        json.dumps(
            {key: value for key, value in receipt.items() if key != "receipt_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    assert receipt["receipt_sha256"] == expected_hash


def test_injected_fixture_spoofing_production_authority_stays_non_authoritative(tmp_path: Path):
    from src.hybrid.elevenlabs_origin_readback import readback_existing_tts_origin

    fixture = _transport()
    fixture.__class__ = SpoofedProductionAuthorityFixture
    result = asyncio.run(readback_existing_tts_origin(_binding(), fixture, tmp_path / "quarantine"))
    assert result.receipt["authenticated_readback"] == "TEST_NOT_AUTHORITY"
    assert result.receipt["origin_provenance"] == "TEST_EXTERNAL_READBACK_FIXTURE"


def test_invalid_history_id_rejects_before_transport_or_credentials(tmp_path: Path):
    from src.hybrid.elevenlabs_origin_readback import (
        ElevenLabsHistoryBinding,
        readback_existing_tts_origin,
    )

    trap = TrapTransport()
    binding = ElevenLabsHistoryBinding(
        request_id="local-request-001",
        history_item_id="../history-list",
        expected_script_sha256="a" * 64,
        expected_voice_id="voice_abc",
        expected_provider="elevenlabs",
        expected_source="TTS",
        expected_audio_sha256=None,
    )
    with pytest.raises(ValueError, match="history_item_id"):
        asyncio.run(readback_existing_tts_origin(binding, trap, tmp_path / "quarantine"))
    assert not trap.called
    assert not (tmp_path / "quarantine").exists()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.__setitem__("history_item_id", "other"),
        lambda payload: payload.__setitem__("source", "STS"),
        lambda payload: payload.__setitem__("voice_id", "other_voice"),
        lambda payload: payload.__setitem__("text", "normalized script"),
        lambda payload: payload.__setitem__("date_unix", float("inf")),
        lambda payload: payload.pop("model_id"),
        lambda payload: payload.pop("output_format"),
    ],
)
def test_history_binding_mismatch_fails_before_voice_or_audio(tmp_path: Path, mutate):
    from src.hybrid.elevenlabs_origin_readback import readback_existing_tts_origin

    transport = _transport()
    mutate(transport.history)
    with pytest.raises(ValueError):
        asyncio.run(readback_existing_tts_origin(_binding(), transport, tmp_path / "quarantine"))
    assert transport.paths == ["/v1/history/hist_abc"]
    assert (tmp_path / "quarantine" / "hist_abc.claim.json").exists()



def test_expected_audio_hash_mismatch_preserves_exact_quarantined_candidate(tmp_path: Path):
    from src.hybrid.elevenlabs_origin_readback import (
        ElevenLabsHistoryBinding,
        readback_existing_tts_origin,
    )

    transport = _transport(audio=b"returned-exact-bytes")
    binding = ElevenLabsHistoryBinding(
        request_id="local-request-001",
        history_item_id="hist_abc",
        expected_script_sha256=hashlib.sha256(b"literal script").hexdigest(),
        expected_voice_id="voice_abc",
        expected_provider="elevenlabs",
        expected_source="TTS",
        expected_audio_sha256=hashlib.sha256(b"different-expected-bytes").hexdigest(),
    )
    with pytest.raises(ValueError, match="audio hash"):
        asyncio.run(readback_existing_tts_origin(binding, transport, tmp_path / "quarantine"))
    assert (tmp_path / "quarantine" / "hist_abc.audio").read_bytes() == b"returned-exact-bytes"
    assert not (tmp_path / "quarantine" / "hist_abc.receipt.json").exists()


def test_voice_unknown_permission_and_preexisting_quarantine_fail_closed(tmp_path: Path):
    from src.hybrid.elevenlabs_origin_readback import readback_existing_tts_origin

    transport = _transport()
    transport.voice["permission_on_resource"] = {"unexpected": "object"}
    with pytest.raises(ValueError, match="voice"):
        asyncio.run(readback_existing_tts_origin(_binding(), transport, tmp_path / "quarantine"))
    assert transport.paths == ["/v1/history/hist_abc", "/v1/voices/voice_abc"]

    transport = _transport()
    target = tmp_path / "quarantine" / "hist_abc.audio"
    target.parent.mkdir(exist_ok=True)
    target.write_bytes(b"do-not-overwrite")
    with pytest.raises(FileExistsError):
        asyncio.run(readback_existing_tts_origin(_binding(), transport, target.parent))
    assert target.read_bytes() == b"do-not-overwrite"
    assert transport.paths == []


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_quarantine_reparse_escape_rejects_before_any_get(tmp_path: Path, kind: str):
    from src.hybrid.elevenlabs_origin_readback import readback_existing_tts_origin

    outside = tmp_path / "outside"
    outside.mkdir()
    quarantine = tmp_path / "quarantine"
    if kind == "symlink":
        try:
            quarantine.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            pytest.skip(f"symlink unavailable: {error}")
    else:
        completed = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(quarantine), str(outside)],
            capture_output=True,
            check=False,
        )
        if completed.returncode:
            pytest.skip(f"junction unavailable: {completed.stderr or completed.stdout}")
    transport = TrapTransport()
    with pytest.raises(ValueError, match="reparse|symlink"):
        asyncio.run(readback_existing_tts_origin(_binding(), transport, quarantine))
    assert not transport.called
    assert not (outside / "hist_abc.audio").exists()


def test_existing_target_barrier_runs_before_production_factory_or_get(tmp_path: Path, monkeypatch):
    import src.hybrid.elevenlabs_origin_readback as subject

    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    (quarantine / "hist_abc.audio").write_bytes(b"occupied")
    factory_calls = 0

    class UnexpectedProductionTransport:
        def __init__(self, *, credential_supplier):
            nonlocal factory_calls
            factory_calls += 1
            raise AssertionError("production factory must not run")

    monkeypatch.setattr(subject, "ElevenLabsProductionTransport", UnexpectedProductionTransport)
    with pytest.raises(FileExistsError):
        asyncio.run(
            subject.readback_existing_tts_origin_from_elevenlabs(
                _binding(), quarantine, credential_supplier=lambda: "not-read"
            )
        )
    assert factory_calls == 0


def test_repeated_call_stops_at_existing_target_before_any_additional_get(tmp_path: Path):
    from src.hybrid.elevenlabs_origin_readback import readback_existing_tts_origin

    transport = _transport()
    quarantine = tmp_path / "quarantine"
    asyncio.run(readback_existing_tts_origin(_binding(), transport, quarantine))
    with pytest.raises(FileExistsError):
        asyncio.run(readback_existing_tts_origin(_binding(), transport, quarantine))
    assert transport.paths == [
        "/v1/history/hist_abc",
        "/v1/voices/voice_abc",
        "/v1/history/hist_abc/audio",
    ]


def test_concurrent_same_quarantine_has_one_reader_and_never_overwrites(tmp_path: Path):
    from src.hybrid.elevenlabs_origin_readback import readback_existing_tts_origin

    transport = _transport()

    async def invoke():
        return await readback_existing_tts_origin(_binding(), transport, tmp_path / "quarantine")

    async def run():
        return await asyncio.gather(invoke(), invoke(), return_exceptions=True)

    results = asyncio.run(run())
    assert sum(isinstance(item, FileExistsError) for item in results) == 1
    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert transport.paths == [
        "/v1/history/hist_abc",
        "/v1/voices/voice_abc",
        "/v1/history/hist_abc/audio",
    ]
    assert (tmp_path / "quarantine" / "hist_abc.audio").read_bytes() == b"RIFF-audio"


def test_production_transport_validates_path_before_reading_credentials():
    from src.hybrid.elevenlabs_origin_readback import ElevenLabsProductionTransport

    credential_reads = 0

    def credential_supplier() -> str:
        nonlocal credential_reads
        credential_reads += 1
        return "never-read"

    transport = ElevenLabsProductionTransport(credential_supplier=credential_supplier)
    with pytest.raises(ValueError, match="allowlisted"):
        asyncio.run(transport.get_json(path="/v1/history/../../not-allowed"))
    assert credential_reads == 0


_SUBPROCESS_READER = r'''
import asyncio
import hashlib
import os
import sys
import time
from pathlib import Path

import src.hybrid.elevenlabs_origin_readback as subject

quarantine, audit, start, release, mode = map(Path, sys.argv[1:])

def record(value):
    with audit.open("a", encoding="utf-8") as handle:
        handle.write(value + "\n")
        handle.flush()
        os.fsync(handle.fileno())

class Transport:
    def __init__(self, *, credential_supplier):
        credential_supplier()
        if mode.name == "crash":
            os._exit(91)

    async def get_json(self, *, path):
        record("GET " + path)
        while not release.exists():
            time.sleep(0.01)
        if path.startswith("/v1/history/"):
            return {
                "history_item_id": "hist_abc", "request_id": None, "source": "TTS",
                "voice_id": "voice_abc", "model_id": "model", "text": "literal script",
                "output_format": "mp3", "date_unix": 1,
            }
        return {
            "voice_id": "voice_abc", "category": "premade",
            "permission_on_resource": "read", "is_owner": False,
            "verified_languages": [],
        }

    async def get_bytes(self, *, path):
        record("GET " + path)
        while not release.exists():
            time.sleep(0.01)
        return b"RIFF-audio"

subject.ElevenLabsProductionTransport = Transport
binding = subject.ElevenLabsHistoryBinding(
    request_id="local-request-001", history_item_id="hist_abc",
    expected_script_sha256=hashlib.sha256(b"literal script").hexdigest(),
    expected_voice_id="voice_abc", expected_provider="elevenlabs", expected_source="TTS",
    expected_audio_sha256=hashlib.sha256(b"RIFF-audio").hexdigest(),
)
record("ready")
while not start.exists():
    time.sleep(0.01)
asyncio.run(subject.readback_existing_tts_origin_from_elevenlabs(
    binding, quarantine, credential_supplier=lambda: record("credential") or "test-credential"
))
'''


def _spawn_reader(tmp_path: Path, mode: str) -> tuple[subprocess.Popen[str], Path, Path, Path]:
    audit = tmp_path / "audit.log"
    start = tmp_path / "start"
    release = tmp_path / "release"
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _SUBPROCESS_READER,
            str(tmp_path / "quarantine"),
            str(audit),
            str(start),
            str(release),
            mode,
        ],
        cwd=Path(__file__).parents[2],
        text=True,
    )
    return process, audit, start, release


def _wait_for(predicate, *, timeout: float = 5) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("subprocess synchronization timed out")
        time.sleep(0.02)


def _audit_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_two_processes_claim_before_credentials_or_gets(tmp_path: Path):
    first, audit, start, release = _spawn_reader(tmp_path, "normal")
    second, _, _, _ = _spawn_reader(tmp_path, "normal")
    try:
        _wait_for(lambda: _audit_lines(audit).count("ready") == 2)
        start.write_text("go", encoding="utf-8")
        _wait_for(lambda: "credential" in _audit_lines(audit))
        _wait_for(lambda: first.poll() is not None or second.poll() is not None)
        assert _audit_lines(audit) == ["ready", "ready", "credential", "GET /v1/history/hist_abc"]
    finally:
        release.write_text("release", encoding="utf-8")
        first.wait(timeout=5)
        second.wait(timeout=5)
    assert sorted([first.returncode, second.returncode]) == [0, 1]
    assert _audit_lines(audit) == [
        "ready",
        "ready",
        "credential",
        "GET /v1/history/hist_abc",
        "GET /v1/voices/voice_abc",
        "GET /v1/history/hist_abc/audio",
    ]


def test_crash_after_claim_blocks_automatic_credential_or_get_reuse(tmp_path: Path):
    crashed, audit, start, release = _spawn_reader(tmp_path, "crash")
    try:
        _wait_for(lambda: _audit_lines(audit) == ["ready"])
        start.write_text("go", encoding="utf-8")
        assert crashed.wait(timeout=5) == 91
    finally:
        release.write_text("release", encoding="utf-8")
    retry, _, _, _ = _spawn_reader(tmp_path, "normal")
    try:
        assert retry.wait(timeout=5) == 1
    finally:
        release.write_text("release", encoding="utf-8")
    assert _audit_lines(audit) == ["ready", "credential", "ready"]
