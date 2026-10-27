"""Local WAV candidate ingestion is evidence-only and never clears TTS_REQUIRED."""
from __future__ import annotations

import base64
import io
import json
import math
import os
import wave
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as rendered:
        rendered.setnchannels(1)
        rendered.setsampwidth(2)
        rendered.setframerate(8_000)
        rendered.writeframes(b"".join(
            int(9000 * math.sin(2 * math.pi * 440 * frame / 8_000)).to_bytes(2, "little", signed=True)
            for frame in range(16_000)
        ))
    return output.getvalue()


def _prepared_request(tmp_path: Path):
    from tests.unit.test_real_preproduction_handoff import _prepare
    return _prepare(tmp_path)


def _configured_root(tmp_path: Path, private: Ed25519PrivateKey):
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    root = {
        "schema": 1, "kind": "EXTERNAL_SIGNER_TRUST_ROOT", "environment": "TEST",
        "algorithm": "Ed25519", "version": "test-tts-root-v1",
        "keys": [{"signer_id": "license-source", "role": "LICENSE", "scopes": ["TTS:LICENSE"],
                  "public_key_b64": base64.b64encode(public).decode(), "not_before": 100, "not_after": 9_999}],
        "revoked_signers": [],
    }
    root_path = tmp_path / "root.json"
    root_path.write_bytes(_canonical(root))
    return SimpleNamespace(raw={"external_trust_root": {
        "root_path": str(root_path), "root_sha256": sha256(root_path.read_bytes()).hexdigest(),
        "evidence_root": str(tmp_path), "environment": "TEST",
    }})


def _candidate_inputs(tmp_path: Path):
    created, _source, revision, _review = _prepared_request(tmp_path)
    script = json.loads((revision / "script" / "script.json").read_text(encoding="utf-8"))
    text = " ".join(segment["narration"] for segment in script["segments"])
    audio = _wav()
    audio_path = tmp_path / "candidate.wav"
    audio_path.write_bytes(audio)
    boundaries = []
    cursor = 0.0
    for word in text.replace(".", "").split():
        boundaries.append({"word": word, "start": cursor, "end": cursor + 0.1})
        cursor += 0.1
    hashes = {
        "source_sha256": sha256((revision / "inputs" / "source.json").read_bytes()).hexdigest(),
        "script_sha256": sha256((revision / "script" / "script.json").read_bytes()).hexdigest(),
        "audio_sha256": sha256(audio).hexdigest(),
    }
    timing = {"schema": "provider-word-boundary-receipt-v1", "mode": "TEST", "candidate_class": "TEST",
              "origin": "FIXTURE_WORDBOUNDARY", **hashes, "provider_id": "provider.example",
              "voice_id": "voice-approved-1", "word_boundaries": boundaries}
    timing_path = tmp_path / "timing.json"
    timing_path.write_bytes(_canonical(timing))
    hashes["timing_sha256"] = sha256(timing_path.read_bytes()).hexdigest()
    return created, revision, audio_path, timing_path, hashes


def _license(path: Path, hashes: dict, private: Ed25519PrivateKey, *, expires_at=500):
    body = {"schema": "tts-license-declaration-v1", "mode": "TEST", **hashes,
            "provider_id": "provider.example", "voice_id": "voice-approved-1", "license_source": "authorized-source-1",
            "signer_id": "license-source", "role": "LICENSE", "scope": "TTS:LICENSE", "issued_at": 200,
            "expires_at": expires_at, "nonce": "license-nonce-1"}
    path.write_bytes(_canonical({**body, "signature_b64": base64.b64encode(private.sign(_canonical(body))).decode()}))


def test_imports_explicit_test_candidate_but_never_calls_fixture_audio_real_aligned_or_qa_qualified(tmp_path):
    from src.agents.director import DirectorAgent

    created, revision, audio, timing, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)

    result = DirectorAgent.__new__(DirectorAgent).import_preproduction_audio_candidate(
        tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300,
    )

    assert result["status"] == "TTS_REQUIRED"
    assert result["qualification_status"] == "NOT_QUALIFIED"
    assert result["operations"] == {"provider_dispatch": "BLOCKED", "render": "BLOCKED", "telegram": "BLOCKED", "publication": "BLOCKED"}
    receipt = json.loads((revision / "audio-import" / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["integrity"] == "VERIFIED"
    assert receipt["cryptographic_verification"] == "VERIFIED"
    assert receipt["configuration_validation"] == "EXTERNAL_CONFIGURATION_VALIDATED"
    assert receipt["provenance"] == "UNAUTHENTICATED"
    assert receipt["candidate_class"] == "TEST"
    assert receipt["alignment_verification"] == "NOT_VERIFIED"
    assert receipt["qa_qualification"] == "NOT_QUALIFIED"
    assert "REAL" not in json.dumps(receipt)
    assert '"ALIGNED"' not in json.dumps(receipt)
    assert receipt["authorization_assertion"] == "SOURCE_AUTHORIZATION_DECLARED_NOT_LEGAL_PROOF"
    assert DirectorAgent.__new__(DirectorAgent).import_preproduction_audio_candidate(
        tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300,
    ) == result


@pytest.mark.parametrize("mutate, error", [
    (lambda timing, license: timing.__setitem__("voice_id", "wrong-voice"), "binding"),
    (lambda timing, license: timing["word_boundaries"].__setitem__(0, {"word": "inventado", "start": 0.0, "end": 0.1}), "do not match"),
    (lambda timing, license: license.__setitem__("source_sha256", "0" * 64), "binding"),
    (lambda timing, license: license.__setitem__("expires_at", 300), "expired"),
])
def test_rejects_wrong_voice_tampered_source_expired_license_and_synthetic_words_before_materialization(tmp_path, mutate, error):
    from src.hybrid.audio_preproduction import import_real_audio_candidate

    created, revision, audio, timing_path, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    license = json.loads(license_path.read_text(encoding="utf-8"))
    mutate(timing, license)
    timing_path.write_bytes(_canonical(timing))
    if error != "expired":
        body = {key: value for key, value in license.items() if key != "signature_b64"}
        license = {**body, "signature_b64": base64.b64encode(private.sign(_canonical(body))).decode()}
    license_path.write_bytes(_canonical(license))

    with pytest.raises(ValueError, match=error):
        import_real_audio_candidate(tmp_path, created["request_id"], audio, timing_path, license_path, app_config=app_config, clock=lambda: 300)
    assert not (revision / "audio-import").exists()


def test_replay_rejects_tampered_materialized_audio_before_returning_handoff(tmp_path):
    from src.hybrid.audio_preproduction import import_real_audio_candidate

    created, revision, audio, timing, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    import_real_audio_candidate(tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300)
    (revision / "audio-import" / "audio.wav").write_bytes(b"tampered")

    with pytest.raises(ValueError, match="materialized"):
        import_real_audio_candidate(tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300)


@pytest.mark.parametrize("mutate, error", [
    (lambda timing: timing["word_boundaries"].__setitem__(1, {"word": timing["word_boundaries"][1]["word"], "start": 0.0, "end": 0.1}), "ordered"),
    (lambda timing: timing["word_boundaries"].__setitem__(0, {"word": timing["word_boundaries"][0]["word"], "start": float("inf"), "end": 0.1}), "JSON non-finite"),
])
def test_rejects_nonfinite_or_overlapping_timing_as_structural_contract_errors(tmp_path, mutate, error):
    from src.hybrid.audio_preproduction import import_real_audio_candidate

    created, revision, audio, timing_path, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    mutate(timing)
    timing_path.write_bytes(_canonical(timing))

    with pytest.raises(ValueError, match=error):
        import_real_audio_candidate(tmp_path, created["request_id"], audio, timing_path, license_path, app_config=app_config, clock=lambda: 300)
    assert not (revision / "audio-import").exists()


def test_rejects_changed_active_source_before_any_audio_import_mutation(tmp_path):
    from src.hybrid.audio_preproduction import import_real_audio_candidate

    created, revision, audio, timing, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    (revision / "inputs" / "source.json").write_text('{"changed":true}', encoding="utf-8")

    with pytest.raises(ValueError, match="source binding"):
        import_real_audio_candidate(tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300)
    assert not (revision / "audio-import").exists()


def test_replay_rejects_duplicate_json_and_extra_packet_file_before_returning(tmp_path):
    from src.hybrid.audio_preproduction import import_real_audio_candidate

    created, revision, audio, timing, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    import_real_audio_candidate(tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300)
    (revision / "audio-import" / "extra.txt").write_text("tamper", encoding="utf-8")

    with pytest.raises(ValueError, match="missing or extra"):
        import_real_audio_candidate(tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300)


@pytest.mark.parametrize("field, value", [
    ("role", "LORENA_CLOSING"),
    ("locale", "en-US"),
    ("alignment_verification", "ALIGNED"),
])
def test_rejects_unbound_role_locale_or_alignment_claim_before_promotion(tmp_path, field, value):
    from src.hybrid.audio_preproduction import import_real_audio_candidate

    created, revision, audio, timing_path, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    timing[field] = value
    timing_path.write_bytes(_canonical(timing))

    with pytest.raises(ValueError, match="closed schema"):
        import_real_audio_candidate(tmp_path, created["request_id"], audio, timing_path, license_path, app_config=app_config, clock=lambda: 300)
    assert not (revision / "audio-import").exists()


def test_replay_rejects_duplicate_json_keys_before_index_or_handoff_return(tmp_path):
    from src.hybrid.audio_preproduction import import_real_audio_candidate

    created, revision, audio, timing, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    import_real_audio_candidate(tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300)
    (revision / "audio-import" / "timing.json").write_bytes(b'{"mode":"TEST","mode":"PREPRODUCTION"}')

    with pytest.raises(ValueError, match="materialized audio import evidence"):
        import_real_audio_candidate(tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300)


def test_rejects_reparse_timing_evidence_before_any_audio_import_mutation(tmp_path):
    from src.hybrid.audio_preproduction import import_real_audio_candidate

    created, revision, audio, timing_path, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    target = tmp_path / "timing-target.json"
    timing_path.replace(target)
    try:
        os.symlink(target, timing_path)
    except OSError as error:
        pytest.skip(f"symlink privilege unavailable: {error}")

    with pytest.raises(ValueError, match="symlink or reparse"):
        import_real_audio_candidate(tmp_path, created["request_id"], audio, timing_path, license_path, app_config=app_config, clock=lambda: 300)
    assert not (revision / "audio-import").exists()


def test_rejects_source_changed_after_initial_check_without_packet_or_staging(tmp_path, monkeypatch):
    from src.hybrid.audio_preproduction import import_real_audio_candidate
    from src.hybrid.external_trust_root import ExternalTrustRootVerifier

    created, revision, audio, timing, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    original = ExternalTrustRootVerifier.from_app_config.__func__

    def mutate_source(cls, *args, **kwargs):
        (revision / "inputs" / "source.json").write_bytes(b'{"changed_after_initial_check":true}')
        return original(cls, *args, **kwargs)

    monkeypatch.setattr(ExternalTrustRootVerifier, "from_app_config", classmethod(mutate_source))
    with pytest.raises(ValueError, match="source.*changed"):
        import_real_audio_candidate(tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300)
    assert not (revision / "audio-import").exists()
    assert not (revision / ".audio-import-staging").exists()


def test_interrupted_materialized_staging_is_preserved_and_blocks_retry_without_rewrite(tmp_path, monkeypatch):
    from src.hybrid import audio_preproduction
    from src.hybrid.audio_preproduction import import_real_audio_candidate

    created, revision, audio, timing, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    original_write = audio_preproduction._write_exclusive
    writes = []

    def fail_after_first_artifact(path, raw):
        writes.append((path.name, sha256(raw).hexdigest()))
        if len(writes) == 2:
            raise RuntimeError("simulated write interruption")
        return original_write(path, raw)

    monkeypatch.setattr(audio_preproduction, "_write_exclusive", fail_after_first_artifact)
    with pytest.raises(RuntimeError, match="simulated write interruption"):
        import_real_audio_candidate(tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300)

    staging = revision / ".audio-import-staging"
    materialized = staging / "audio.wav"
    assert materialized.read_bytes() == audio.read_bytes()
    assert sha256(materialized.read_bytes()).hexdigest() == hashes["audio_sha256"]
    before_retry = {item.name: item.read_bytes() for item in staging.iterdir()}
    assert not (revision / "audio-import").exists()

    with pytest.raises(ValueError, match="staging requires audited recovery"):
        import_real_audio_candidate(tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300)

    assert writes == [("audio.wav", hashes["audio_sha256"]), ("timing.json", sha256(timing.read_bytes()).hexdigest())]
    assert {item.name: item.read_bytes() for item in staging.iterdir()} == before_retry
    assert not (revision / "audio-import").exists()


def test_existing_interrupted_staging_blocks_retry_and_leaves_published_packet_unchanged(tmp_path):
    from src.hybrid.audio_preproduction import import_real_audio_candidate

    created, revision, audio, timing, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    import_real_audio_candidate(tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300)
    packet = revision / "audio-import"
    packet_before_retry = {item.name: item.read_bytes() for item in packet.iterdir()}
    staging = revision / ".audio-import-staging"
    staging.mkdir()
    (staging / "audio.wav").write_bytes(b"interrupted-artifact")

    with pytest.raises(ValueError, match="staging requires audited recovery"):
        import_real_audio_candidate(tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300)

    assert {item.name: item.read_bytes() for item in packet.iterdir()} == packet_before_retry
    assert (staging / "audio.wav").read_bytes() == b"interrupted-artifact"


def test_replay_rejects_forged_promotable_receipt_even_with_recomputed_hash(tmp_path):
    from src.hybrid.audio_preproduction import (
        _candidate_digest,
        import_real_audio_candidate,
        verify_real_audio_candidate_import,
    )

    created, revision, audio, timing, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    import_real_audio_candidate(tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300)
    receipt_path = revision / "audio-import" / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt.update({"provenance": "EXTERNAL_PROVENANCED", "qa_qualification": "QUALIFIED", "qualification_status": "QUALIFIED", "status": "QA_APPROVED"})
    receipt["receipt_sha256"] = _candidate_digest({key: value for key, value in receipt.items() if key != "receipt_sha256"})
    receipt_path.write_bytes(_canonical(receipt))

    with pytest.raises(ValueError, match="receipt contract"):
        verify_real_audio_candidate_import(revision / "audio-import", app_config=app_config, clock=lambda: 300)


@pytest.mark.parametrize("mutate", [
    lambda receipt: receipt.__setitem__("unexpected", "field"),
    lambda receipt: receipt.__setitem__("status", True),
    lambda receipt: receipt["operations"].__setitem__("publication", "ALLOWED"),
])
def test_replay_rejects_unknown_bool_or_unblocked_receipt_contract_fields(tmp_path, mutate):
    from src.hybrid.audio_preproduction import (
        _candidate_digest,
        import_real_audio_candidate,
        verify_real_audio_candidate_import,
    )

    created, revision, audio, timing, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    import_real_audio_candidate(tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300)
    receipt_path = revision / "audio-import" / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    mutate(receipt)
    receipt["receipt_sha256"] = _candidate_digest({key: value for key, value in receipt.items() if key != "receipt_sha256"})
    receipt_path.write_bytes(_canonical(receipt))

    with pytest.raises(ValueError, match="receipt contract"):
        verify_real_audio_candidate_import(revision / "audio-import", app_config=app_config, clock=lambda: 300)


def test_valid_packet_replays_after_the_receipt_contract_is_revalidated(tmp_path):
    from src.hybrid.audio_preproduction import (
        import_real_audio_candidate,
        verify_real_audio_candidate_import,
    )

    created, revision, audio, timing, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    result = import_real_audio_candidate(tmp_path, created["request_id"], audio, timing, license_path, app_config=app_config, clock=lambda: 300)
    audio.unlink()
    timing.unlink()
    license_path.unlink()
    (revision / "inputs" / "source.json").unlink()
    (revision / "script" / "script.json").unlink()

    replayed = verify_real_audio_candidate_import(revision / "audio-import", app_config=app_config, clock=lambda: 300)
    assert replayed["receipt_sha256"] == result["receipt_sha256"]


def _rehash_materialized_packet(packet: Path) -> None:
    """Model an attacker who can recompute every local integrity hash."""
    from src.hybrid.audio_preproduction import _candidate_digest

    index_path = packet / "index.json"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    for entry in index["entries"]:
        raw = (packet / entry["path"]).read_bytes()
        entry["sha256"] = sha256(raw).hexdigest()
        entry["size"] = len(raw)
    index_path.write_bytes(_canonical(index))
    receipt_path = packet / "receipt.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["timing_sha256"] = sha256((packet / "timing.json").read_bytes()).hexdigest()
    receipt["license_sha256"] = sha256((packet / "license.json").read_bytes()).hexdigest()
    receipt["index_sha256"] = _candidate_digest(index)
    receipt["receipt_sha256"] = _candidate_digest({key: value for key, value in receipt.items() if key != "receipt_sha256"})
    receipt_path.write_bytes(_canonical(receipt))


def test_replay_rejects_forged_timing_origin_after_all_local_hashes_are_recomputed(tmp_path):
    from src.hybrid.audio_preproduction import (
        import_real_audio_candidate,
        verify_real_audio_candidate_import,
    )

    created, revision, audio, timing_path, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    import_real_audio_candidate(tmp_path, created["request_id"], audio, timing_path, license_path, app_config=app_config, clock=lambda: 300)
    packet = revision / "audio-import"
    timing = json.loads((packet / "timing.json").read_text(encoding="utf-8"))
    timing.update({"mode": "PREPRODUCTION", "candidate_class": "EXTERNAL", "origin": "PROVIDER_WORDBOUNDARY"})
    (packet / "timing.json").write_bytes(_canonical(timing))
    _rehash_materialized_packet(packet)

    with pytest.raises(ValueError, match="timing.*binding|license.*binding"):
        verify_real_audio_candidate_import(packet, app_config=app_config, clock=lambda: 300)


@pytest.mark.parametrize("mutate", [
    lambda license: license.__setitem__("scope", "TTS:WRONG_SCOPE"),
    lambda license: license.__setitem__("signature_b64", base64.b64encode(b"x" * 64).decode()),
])
def test_replay_rejects_forged_license_scope_or_signature_after_all_local_hashes_are_recomputed(tmp_path, mutate):
    from src.hybrid.audio_preproduction import (
        import_real_audio_candidate,
        verify_real_audio_candidate_import,
    )

    created, revision, audio, timing_path, hashes = _candidate_inputs(tmp_path)
    private = Ed25519PrivateKey.generate()
    app_config = _configured_root(tmp_path, private)
    license_path = tmp_path / "license.json"
    _license(license_path, hashes, private)
    import_real_audio_candidate(tmp_path, created["request_id"], audio, timing_path, license_path, app_config=app_config, clock=lambda: 300)
    packet = revision / "audio-import"
    license = json.loads((packet / "license.json").read_text(encoding="utf-8"))
    mutate(license)
    (packet / "license.json").write_bytes(_canonical(license))
    _rehash_materialized_packet(packet)

    with pytest.raises(ValueError, match="scope|signature"):
        verify_real_audio_candidate_import(packet, app_config=app_config, clock=lambda: 300)
