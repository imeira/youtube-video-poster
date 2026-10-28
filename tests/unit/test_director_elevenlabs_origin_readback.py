"""Director-only TEST facade for existing ElevenLabs origin readback."""
from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


class ThreeGetFixture:
    """Explicit TEST fixture; it cannot represent provider authority."""

    def __init__(self, audio: bytes):
        self.audio = audio
        self.paths: list[str] = []

    async def __call__(self, binding, quarantine: Path):
        from src.hybrid.elevenlabs_origin_readback import readback_existing_tts_origin

        class Transport:
            authority = "API_KEY_AUTHENTICATED_GET"  # spoofing must not matter

            async def get_json(inner_self, *, path: str):
                self.paths.append(path)
                if path.startswith("/v1/history/"):
                    return {
                        "history_item_id": binding.history_item_id,
                        "request_id": None,
                        "source": "TTS",
                        "voice_id": binding.expected_voice_id,
                        "model_id": "eleven_multilingual_v2",
                        "text": "literal script",
                        "output_format": "pcm_44100",
                        "date_unix": 123,
                    }
                return {
                    "voice_id": binding.expected_voice_id,
                    "category": "premade",
                    "permission_on_resource": "read",
                    "is_owner": False,
                    "verified_languages": [],
                }

            async def get_bytes(inner_self, *, path: str):
                self.paths.append(path)
                return self.audio

        return await readback_existing_tts_origin(binding, Transport(), quarantine)


def _records(tmp_path: Path, *, audio: bytes = b"exact-pcm-candidate"):
    evidence = tmp_path / "configured-evidence"
    evidence.mkdir(parents=True)
    source_sha = hashlib.sha256(b"source").hexdigest()
    script_sha = hashlib.sha256(b"literal script").hexdigest()
    audio_sha = hashlib.sha256(audio).hexdigest()
    candidate = {
        "schema": "elevenlabs-origin-readback-candidate-fixture-v1",
        "mode": "TEST",
        "candidate_class": "TEST",
        "request_id": "request_001",
        "source_sha256": source_sha,
        "script_sha256": script_sha,
        "audio_sha256": audio_sha,
        "provider_id": "elevenlabs",
        "voice_id": "voice_abc",
    }
    candidate["receipt_sha256"] = _sha(candidate)
    candidate_path = evidence / "candidate-receipt.json"
    candidate_path.write_bytes(_canonical(candidate))
    submission = {
        "schema": "elevenlabs-history-submission-receipt-fixture-v1",
        "mode": "TEST",
        "request_id": "request_001",
        "provider": "elevenlabs",
        "history_item_id": "hist_abc",
        "source_sha256": source_sha,
        "script_sha256": script_sha,
        "audio_sha256": audio_sha,
        "voice_id": "voice_abc",
        "candidate_receipt_sha256": candidate["receipt_sha256"],
    }
    submission["receipt_sha256"] = _sha(submission)
    submission_path = evidence / "submission-receipt.json"
    submission_path.write_bytes(_canonical(submission))
    config = SimpleNamespace(raw={"external_trust_root": {"evidence_root": str(evidence)}})
    return config, candidate_path, submission_path, audio


def _director():
    from src.agents.director import DirectorAgent

    return DirectorAgent.__new__(DirectorAgent)


def test_director_e2e_reads_exact_metadata_voice_audio_from_test_submission_receipt(tmp_path: Path):
    config, candidate_path, submission_path, audio = _records(tmp_path)
    service = ThreeGetFixture(audio)

    result = asyncio.run(
        _director().readback_elevenlabs_origin_from_submission(
            candidate_receipt_path=candidate_path,
            submission_receipt_path=submission_path,
            app_config=config,
            readback_service=service,
        )
    )

    assert service.paths == [
        "/v1/history/hist_abc",
        "/v1/voices/voice_abc",
        "/v1/history/hist_abc/audio",
    ]
    assert result["status"] == "TTS_REQUIRED"
    assert result["qualification_status"] == "NOT_QUALIFIED"
    assert result["authenticated_readback"] == "TEST_NOT_AUTHORITY"
    assert result["candidate_receipt_sha256"] == json.loads(candidate_path.read_text())["receipt_sha256"]
    assert result["submission_receipt_sha256"] == json.loads(submission_path.read_text())["receipt_sha256"]
    assert result["operations"] == {
        "provider_dispatch": "BLOCKED", "render": "BLOCKED", "telegram": "BLOCKED", "publication": "BLOCKED",
    }


@pytest.mark.parametrize("mutation, match", [
    ("wrong-source", "submission receipt does not bind immutable candidate"),
    ("wrong-script", "submission receipt does not bind immutable candidate"),
    ("wrong-voice", "submission receipt does not bind immutable candidate"),
    ("missing-history", "history_item_id"),
    ("mutated-receipt", "receipt_sha256"),
    ("existing-target", "hist_abc.audio"),
    ("mp3-not-pcm", "audio hash"),
])
def test_director_rejects_unbound_or_nonexact_readback_before_or_at_existing_service_barrier(tmp_path: Path, mutation: str, match: str):
    config, candidate_path, submission_path, audio = _records(tmp_path)
    submission = json.loads(submission_path.read_text())
    if mutation == "wrong-source":
        submission["source_sha256"] = "0" * 64
    elif mutation == "wrong-script":
        submission["script_sha256"] = "0" * 64
    elif mutation == "wrong-voice":
        submission["voice_id"] = "voice_wrong"
    elif mutation == "missing-history":
        submission.pop("history_item_id")
    elif mutation == "mutated-receipt":
        submission["history_item_id"] = "hist_mutated"
    if mutation in {"wrong-source", "wrong-script", "wrong-voice", "missing-history"}:
        submission["receipt_sha256"] = _sha({key: value for key, value in submission.items() if key != "receipt_sha256"})
    submission_path.write_bytes(_canonical(submission))
    service = ThreeGetFixture(b"raw-mp3-not-the-candidate" if mutation == "mp3-not-pcm" else audio)
    if mutation == "existing-target":
        (tmp_path / "configured-evidence" / "elevenlabs-origin-readback").mkdir()
        (tmp_path / "configured-evidence" / "elevenlabs-origin-readback" / "hist_abc.audio").write_bytes(b"occupied")

    with pytest.raises((ValueError, FileExistsError), match=match):
        asyncio.run(
            _director().readback_elevenlabs_origin_from_submission(
                candidate_receipt_path=candidate_path,
                submission_receipt_path=submission_path,
                app_config=config,
                readback_service=service,
            )
        )
    if mutation not in {"existing-target", "mp3-not-pcm"}:
        assert service.paths == []


def test_director_blocks_missing_receipt_or_production_mode_without_constructing_injected_service(tmp_path: Path):
    config, candidate_path, submission_path, audio = _records(tmp_path)
    service = ThreeGetFixture(audio)
    submission_path.unlink()
    with pytest.raises(ValueError, match="missing or invalid durable submission receipt"):
        asyncio.run(_director().readback_elevenlabs_origin_from_submission(
            candidate_receipt_path=candidate_path, submission_receipt_path=submission_path,
            app_config=config, readback_service=service,
        ))
    assert service.paths == []

    with pytest.raises(ValueError, match="configured evidence root"):
        asyncio.run(_director().readback_elevenlabs_origin_from_submission(
            candidate_receipt_path=candidate_path, submission_receipt_path=candidate_path,
            app_config=SimpleNamespace(raw={}), readback_service=service,
        ))
    assert service.paths == []

    _config, _candidate, production_submission, _audio = _records(tmp_path / "production")
    payload = json.loads(production_submission.read_text())
    payload["mode"] = "PRODUCTION"
    payload["receipt_sha256"] = _sha({key: value for key, value in payload.items() if key != "receipt_sha256"})
    production_submission.write_bytes(_canonical(payload))
    with pytest.raises(ValueError, match="real submission receipt producer is unavailable"):
        asyncio.run(_director().readback_elevenlabs_origin_from_submission(
            candidate_receipt_path=_candidate, submission_receipt_path=production_submission,
            app_config=_config, readback_service=service,
        ))


@pytest.mark.parametrize("forgery", [
    "old-result-self-hash",
    "stale-other-source",
    "swapped-audio",
    "receipt-path-escape",
    "mutated-receipt",
])
def test_director_rejects_forged_materialized_origin_readback_without_returning_canonical_state(
    tmp_path: Path, forgery: str,
):
    """Callback claims and self-hashes are integrity data, never authority."""
    config, candidate_path, submission_path, audio = _records(tmp_path)

    class ForgedService:
        async def __call__(self, binding, quarantine: Path):
            origin = await ThreeGetFixture(audio)(binding, quarantine)
            receipt = dict(origin.receipt)
            if forgery == "old-result-self-hash":
                receipt["receipt_sha256"] = "old-canonical-origin-for-different-source"
            elif forgery == "stale-other-source":
                receipt["source"] = "OTHER"
                receipt["receipt_sha256"] = _sha({key: value for key, value in receipt.items() if key != "receipt_sha256"})
                origin.receipt_path.write_bytes(_canonical(receipt))
            elif forgery == "swapped-audio":
                receipt["audio_sha256"] = "0" * 64
                receipt["receipt_sha256"] = _sha({key: value for key, value in receipt.items() if key != "receipt_sha256"})
                origin.receipt_path.write_bytes(_canonical(receipt))
            elif forgery == "receipt-path-escape":
                return SimpleNamespace(
                    audio_path=origin.audio_path,
                    receipt_path=tmp_path / "outside-receipt.json",
                    receipt=receipt,
                )
            elif forgery == "mutated-receipt":
                receipt["voice_id"] = "voice_mutated"
                receipt["receipt_sha256"] = _sha({key: value for key, value in receipt.items() if key != "receipt_sha256"})
                origin.receipt_path.write_bytes(_canonical(receipt))
            return SimpleNamespace(
                audio_path=origin.audio_path,
                receipt_path=origin.receipt_path,
                receipt=receipt,
            )

    with pytest.raises(ValueError):
        asyncio.run(
            _director().readback_elevenlabs_origin_from_submission(
                candidate_receipt_path=candidate_path,
                submission_receipt_path=submission_path,
                app_config=config,
                readback_service=ForgedService(),
            )
        )
