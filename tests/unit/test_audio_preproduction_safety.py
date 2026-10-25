"""Regression tests for the fail-closed audio entrypoint and TEST importer."""

from __future__ import annotations

import inspect
import io
import json
import wave
from hashlib import sha256

import pytest


def _wav_bytes(*, silent: bool = False, frames: int = 8_000) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8_000)
        sample = b"\0\0" if silent else b"\x01\0"
        wav.writeframes(sample * frames)
    return output.getvalue()


def _boundaries(text: str = "texto") -> list[dict[str, object]]:
    return [{"word": text, "start": 0.0, "end": 1.0}]


def _state_bytes(root):
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }


def _import(cache_root, audio_bytes, **overrides):
    from src.hybrid.audio_preproduction import import_test_audio_segment

    arguments = {
        "cache_root": cache_root,
        "namespace": "unit-test-audio",
        "segment_id": "S001",
        "text": "texto",
        "audio_bytes": audio_bytes,
        "media_type": "audio/wav",
        "word_boundaries": _boundaries(),
        "is_closing": False,
    }
    arguments.update(overrides)
    return import_test_audio_segment(**arguments)


def test_forged_authorization_never_constructs_provider_or_mutates_state(tmp_path):
    from src.hybrid.audio_preproduction import execute_authorized_audio

    forged_review = tmp_path / "forged-review.json"
    forged_auth = tmp_path / "forged-auth.json"
    forged_review.write_text('{"verdict":"PASS"}', encoding="utf-8")
    forged_auth.write_text('{"authorized":true}', encoding="utf-8")
    before = _state_bytes(tmp_path)

    with pytest.raises(RuntimeError, match="EXTERNAL_AUDIO_SYNTHESIS_BLOCKED"):
        execute_authorized_audio(
            tmp_path, "a" * 64, forged_review, forged_auth,
            provider_factory=lambda: (_ for _ in ()).throw(AssertionError("constructed")),
        )
    assert _state_bytes(tmp_path) == before


def test_audio_preflight_is_read_only_capability_report_not_authorization(tmp_path):
    from src.hybrid.audio_preproduction import preflight_audio_execution

    before = _state_bytes(tmp_path)
    report = preflight_audio_execution(tmp_path, "a" * 64, tmp_path / "forged.json", tmp_path / "auth.json")
    assert report == {
        "status": "EXTERNAL_AUDIO_SYNTHESIS_BLOCKED",
        "capability": "UNQUALIFIED",
        "reason": "trust_and_transport_recovery_not_qualified",
    }
    assert _state_bytes(tmp_path) == before


def test_import_accepts_only_immutable_wav_bytes_and_binds_cache_root_and_namespace(tmp_path):
    root = tmp_path / "trusted-cache"
    root.mkdir()
    audio = _wav_bytes()

    receipt = _import(root, audio)

    assert receipt["mode"] == "TEST"
    assert receipt["voice_qualification"] == "NOT_QUALIFIED"
    assert receipt["audio_sha256"] == sha256(audio).hexdigest()
    assert receipt["cache_namespace"] == "unit-test-audio"
    assert receipt["cache_root_sha256"] == sha256(str(root.resolve()).encode("utf-8")).hexdigest()
    cached = root / "unit-test-audio" / receipt["audio_file"]
    assert cached.read_bytes() == audio
    assert _import(root, audio) == receipt


def test_import_rejects_source_path_legacy_argument_and_reflection_closing(tmp_path):
    from src.hybrid.audio_preproduction import import_test_audio_segment

    root = tmp_path / "trusted-cache"
    root.mkdir()
    assert "source_path" not in inspect.signature(import_test_audio_segment).parameters
    with pytest.raises(TypeError, match="source_path"):
        import_test_audio_segment(source_path=tmp_path / "fixture.wav")
    with pytest.raises(ValueError, match="closing"):
        _import(root, _wav_bytes(), is_closing=True)


@pytest.mark.parametrize("kind", ("silent", "invalid", "media_type", "size"))
def test_import_rejects_silence_invalid_type_and_size(tmp_path, kind):
    root = tmp_path / "trusted-cache"
    root.mkdir()

    audio_bytes = _wav_bytes()
    media_type = "audio/wav"
    message = {"silent": "silent", "invalid": "decodable WAV", "media_type": "media type", "size": "size"}[kind]
    if kind == "silent":
        audio_bytes = _wav_bytes(silent=True)
    elif kind == "invalid":
        audio_bytes = b"not a wav"
    elif kind == "media_type":
        media_type = "audio/mpeg"
    elif kind == "size":
        audio_bytes += b"x" * (16 * 1024 * 1024)

    with pytest.raises(ValueError, match=message):
        _import(root, audio_bytes, media_type=media_type)


def test_import_rejects_false_or_invented_word_boundary_receipt(tmp_path):
    root = tmp_path / "trusted-cache"
    root.mkdir()

    with pytest.raises(ValueError, match="actual WordBoundary"):
        _import(root, _wav_bytes(), word_boundaries=[])
    with pytest.raises(ValueError, match="do not match"):
        _import(root, _wav_bytes(), word_boundaries=[{"word": "inventado", "start": 0, "end": 1}])


def test_replay_rejects_tampered_and_recomputed_receipt_metadata(tmp_path):
    root = tmp_path / "trusted-cache"
    root.mkdir()
    audio = _wav_bytes()
    receipt = _import(root, audio)
    receipt_path = root / "unit-test-audio" / f"S001-{receipt['input_sha256'][:16]}.receipt.json"
    forged = dict(receipt)
    forged["voice_qualification"] = "QUALIFIED"
    forged["receipt_sha256"] = sha256(json.dumps(
        {key: value for key, value in forged.items() if key != "receipt_sha256"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()
    receipt_path.write_text(json.dumps(forged), encoding="utf-8")

    with pytest.raises(ValueError, match="expected contract"):
        _import(root, audio)


def test_replay_rejects_cache_copied_under_a_different_trusted_root(tmp_path):
    source_root = tmp_path / "trusted-a"
    source_root.mkdir()
    audio = _wav_bytes()
    receipt = _import(source_root, audio)
    copied_root = tmp_path / "trusted-b"
    copied_root.mkdir()
    copied_namespace = copied_root / "unit-test-audio"
    copied_namespace.mkdir()
    for path in (source_root / "unit-test-audio").iterdir():
        (copied_namespace / path.name).write_bytes(path.read_bytes())

    with pytest.raises(ValueError, match="root/namespace"):
        _import(copied_root, audio)
    assert receipt["cache_root_sha256"] != sha256(str(copied_root.resolve()).encode("utf-8")).hexdigest()
