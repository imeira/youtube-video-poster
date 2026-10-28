"""Immutable, explicitly non-promotable audio evidence imports."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from tests.unit.test_real_audio_candidate_import import _candidate_inputs, _canonical


def _sign(path: Path, body: dict, private: Ed25519PrivateKey) -> None:
    path.write_bytes(
        _canonical(
            {
                **body,
                "signature_b64": base64.b64encode(
                    private.sign(_canonical(body))
                ).decode(),
            }
        )
    )


def _production_config(tmp_path: Path, keys: dict[str, Ed25519PrivateKey]):
    root = {
        "schema": 1,
        "kind": "EXTERNAL_SIGNER_TRUST_ROOT",
        "environment": "PRODUCTION",
        "algorithm": "Ed25519",
        "version": "test-root-v1",
        "keys": [],
        "revoked_signers": [],
    }
    for name, private in keys.items():
        root["keys"].append(
            {
                "signer_id": name,
                "role": "LICENSE",
                "scopes": ["TTS:LICENSE"],
                "public_key_b64": base64.b64encode(
                    private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
                ).decode(),
                "not_before": 100,
                "not_after": 9999,
            }
        )
    root_path = tmp_path / "production-root.json"
    root_path.write_bytes(_canonical(root))
    return SimpleNamespace(
        raw={
            "external_trust_root": {
                "root_path": str(root_path),
                "root_sha256": sha256(root_path.read_bytes()).hexdigest(),
                "evidence_root": str(tmp_path),
                "environment": "PRODUCTION",
            }
        }
    )


def _external_audio_import(tmp_path: Path, app_config, keys):
    from src.hybrid.audio_preproduction import import_real_audio_candidate

    created, revision, audio, timing_path, hashes = _candidate_inputs(tmp_path)
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    timing.update(
        {
            "mode": "PREPRODUCTION",
            "candidate_class": "EXTERNAL",
            "origin": "PROVIDER_WORDBOUNDARY",
            "provider_id": "external.tts",
            "voice_id": "voice-prod-1",
        }
    )
    timing_path.write_bytes(_canonical(timing))
    hashes["timing_sha256"] = sha256(timing_path.read_bytes()).hexdigest()
    license_path = tmp_path / "license-production.json"
    _sign(
        license_path,
        {
            "schema": "tts-license-declaration-v1",
            "mode": "PREPRODUCTION",
            **hashes,
            "provider_id": "external.tts",
            "voice_id": "voice-prod-1",
            "license_source": "external-contract-1",
            "signer_id": "license",
            "role": "LICENSE",
            "scope": "TTS:LICENSE",
            "issued_at": 200,
            "expires_at": 500,
            "nonce": "license-1",
        },
        keys["license"],
    )
    import_real_audio_candidate(
        tmp_path,
        created["request_id"],
        audio,
        timing_path,
        license_path,
        app_config=app_config,
        clock=lambda: 300,
    )
    receipt = json.loads(
        (revision / "audio-import" / "receipt.json").read_text(encoding="utf-8")
    )
    return created, revision, receipt, None


def _inputs(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "origin_declaration": tmp_path / "origin.json",
        "editorial_review": tmp_path / "editorial.json",
        "acoustic_review": tmp_path / "acoustic.json",
    }
    for name, path in paths.items():
        path.write_bytes(
            _canonical(
                {
                    "schema": "external-audio-declaration-v1",
                    "kind": name,
                    "signed_claim": True,
                }
            )
        )
    return paths


def _prepared(tmp_path: Path):
    keys = {name: Ed25519PrivateKey.generate() for name in ("license", "origin", "qa")}
    config = _production_config(tmp_path, keys)
    created, revision, receipt, _ = _external_audio_import(tmp_path, config, keys)
    return config, created, revision, receipt, _inputs(tmp_path)


def _canonical_mutations(revision: Path) -> dict[str, bytes]:
    names = (
        "request.json",
        "stages.json",
        "qa/script-qa.json",
        "storyboard/scenes.json",
        "timeline/timeline.json",
    )
    result = {}
    for name in names:
        path = (
            revision.parent.parent / name if name == "request.json" else revision / name
        )
        if path.exists():
            result[name] = path.read_bytes()
    return result


def test_imports_closed_attested_non_promotable_bundle_without_promotion(tmp_path):
    from src.hybrid.audio_qualification_evidence import (
        import_audio_qualification_evidence,
    )

    config, created, revision, receipt, evidence = _prepared(tmp_path)
    before = _canonical_mutations(revision)
    result = import_audio_qualification_evidence(
        tmp_path,
        created["request_id"],
        app_config=config,
        clock=lambda: 300,
        **evidence,
    )

    assert result["status"] == "TTS_REQUIRED"
    assert result["qualification_status"] == "NOT_QUALIFIED"
    assert result["provenance"] == "UNAUTHENTICATED"
    assert result["attestation_status"] == "ATTESTED_NON_PROMOTABLE"
    assert (revision / "audio-qualification-evidence" / "receipt.json").is_file()
    assert _canonical_mutations(revision) == before
    assert (
        json.loads(
            (revision / "audio-import" / "receipt.json").read_text(encoding="utf-8")
        )
        == receipt
    )


@pytest.mark.parametrize(
    "name",
    ["origin.json", "editorial.json", "acoustic.json", "index.json", "receipt.json"],
)
def test_replay_rejects_tampered_bundle_file_or_index_or_receipt(tmp_path, name):
    from src.hybrid.audio_qualification_evidence import (
        import_audio_qualification_evidence,
        verify_audio_qualification_evidence_import,
    )

    config, created, revision, _receipt, evidence = _prepared(tmp_path)
    import_audio_qualification_evidence(
        tmp_path,
        created["request_id"],
        app_config=config,
        clock=lambda: 300,
        **evidence,
    )
    target = revision / "audio-qualification-evidence" / name
    target.write_bytes(target.read_bytes() + b" ")
    with pytest.raises(ValueError):
        verify_audio_qualification_evidence_import(
            revision / "audio-qualification-evidence",
            app_config=config,
            clock=lambda: 300,
        )


def test_forged_valid_production_root_declaration_stays_blocked_and_changes_no_canonical_state(
    tmp_path,
):
    from src.agents.director import DirectorAgent

    config, created, revision, _receipt, evidence = _prepared(tmp_path)
    before = _canonical_mutations(revision)
    result = DirectorAgent.__new__(DirectorAgent).import_audio_qualification_evidence(
        tmp_path,
        created["request_id"],
        app_config=config,
        clock=lambda: 300,
        **evidence,
    )
    assert result["status"] == "TTS_REQUIRED"
    assert result["provenance"] == "UNAUTHENTICATED"
    assert _canonical_mutations(revision) == before


def test_failed_publication_leaves_no_partial_bundle(tmp_path, monkeypatch):
    import src.hybrid.audio_qualification_evidence as subject

    config, created, revision, _receipt, evidence = _prepared(tmp_path)
    monkeypatch.setattr(
        subject.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("injected rename failure")),
    )
    with pytest.raises(OSError, match="injected"):
        subject.import_audio_qualification_evidence(
            tmp_path,
            created["request_id"],
            app_config=config,
            clock=lambda: 300,
            **evidence,
        )
    assert not (revision / "audio-qualification-evidence").exists()
    assert (revision / ".audio-qualification-evidence-staging").exists()


def test_source_change_before_locked_publication_fails(tmp_path):
    from src.hybrid.audio_qualification_evidence import (
        import_audio_qualification_evidence,
    )

    config, created, revision, _receipt, evidence = _prepared(tmp_path)
    source = revision / "inputs" / "source.json"
    source.write_bytes(source.read_bytes() + b" ")
    with pytest.raises(
        ValueError, match="source-bound execution changed|source binding changed"
    ):
        import_audio_qualification_evidence(
            tmp_path,
            created["request_id"],
            app_config=config,
            clock=lambda: 300,
            **evidence,
        )
    assert not (revision / "audio-qualification-evidence").exists()


def test_rejects_symlinked_declared_evidence_when_supported(tmp_path):
    from src.hybrid.audio_qualification_evidence import (
        import_audio_qualification_evidence,
    )

    config, created, revision, _receipt, evidence = _prepared(tmp_path)
    linked = tmp_path / "origin-link.json"
    try:
        linked.symlink_to(evidence["origin_declaration"])
    except (NotImplementedError, OSError):
        pytest.skip("symlink creation is unavailable on this Windows runner")
    evidence["origin_declaration"] = linked
    with pytest.raises(ValueError, match="symlink|reparse"):
        import_audio_qualification_evidence(
            tmp_path,
            created["request_id"],
            app_config=config,
            clock=lambda: 300,
            **evidence,
        )
    assert not (revision / "audio-qualification-evidence").exists()


def test_two_processes_publish_one_idempotent_bundle(tmp_path):
    config, created, revision, _receipt, evidence = _prepared(tmp_path)
    payload = {
        "workspace": str(tmp_path),
        "request_id": created["request_id"],
        "config": config.raw,
        "evidence": {key: str(value) for key, value in evidence.items()},
    }
    script = (
        "import json, sys\n"
        "from types import SimpleNamespace\n"
        "from src.hybrid.audio_qualification_evidence import import_audio_qualification_evidence\n"
        "p=json.loads(sys.argv[1])\n"
        "r=import_audio_qualification_evidence(p['workspace'],p['request_id'],app_config=SimpleNamespace(raw=p['config']),clock=lambda:300,**p['evidence'])\n"
        "print(r['receipt_sha256'])\n"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).parents[2])
    commands = [sys.executable, "-c", script, json.dumps(payload)]
    first = subprocess.Popen(
        commands,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    second = subprocess.Popen(
        commands,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    first_out, first_error = first.communicate(timeout=30)
    second_out, second_error = second.communicate(timeout=30)
    assert first.returncode == second.returncode == 0, (first_error, second_error)
    assert first_out.strip() == second_out.strip()
    assert (revision / "audio-qualification-evidence").is_dir()
    assert not (revision / ".audio-qualification-evidence-staging").exists()
