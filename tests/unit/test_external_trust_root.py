from __future__ import annotations

import base64
import json
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


@pytest.fixture
def signed_fixture(tmp_path):
    private_key = Ed25519PrivateKey.generate()
    public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    root = {
        "schema": 1,
        "kind": "EXTERNAL_SIGNER_TRUST_ROOT",
        "environment": "TEST",
        "algorithm": "Ed25519",
        "version": "test-root-v1",
        "keys": [{
            "signer_id": "test-qa-1",
            "role": "QA",
            "scopes": ["EVIDENCE:QA"],
            "public_key_b64": base64.b64encode(public_key).decode("ascii"),
            "not_before": 4_102_443_000,
            "not_after": 4_102_445_000,
        }],
        "revoked_signers": [],
    }
    root_path = tmp_path / "external-trust-root.json"
    root_path.write_bytes(canonical(root))
    payload = {
        "schema": 1,
        "kind": "SIGNED_QA_EVIDENCE",
        "version": "evidence-v1",
        "signer_id": "test-qa-1",
        "role": "QA",
        "scope": "EVIDENCE:QA",
        "request_id": "request-1",
        "episode": "SC001",
        "source_sha256": "a" * 64,
        "assets_sha256": "b" * 64,
        "policy_sha256": "c" * 64,
        "issued_at": 4_102_444_000,
        "expires_at": 4_102_444_120,
        "nonce": "test-nonce-0001",
    }
    packet = {**payload, "signature_b64": base64.b64encode(private_key.sign(canonical(payload))).decode("ascii")}
    expected = {
        "request_id": "request-1", "episode": "SC001", "source_sha256": "a" * 64,
        "assets_sha256": "b" * 64, "policy_sha256": "c" * 64,
    }
    return root_path, packet, expected


def test_verifies_external_ed25519_qa_evidence_but_marks_test_root_non_live(signed_fixture):
    from src.hybrid.external_trust_root import ExternalTrustRootVerifier

    root_path, packet, expected = signed_fixture
    result = ExternalTrustRootVerifier(root_path, clock=lambda: 4_102_444_010).verify(packet, expected=expected)

    assert result.signer_id == "test-qa-1"
    assert result.role == "QA"
    assert result.live_authority is False


@pytest.mark.parametrize(
    ("mutate", "error"),
    [
        (lambda packet, _root: packet.__setitem__("nonce", "tampered-nonce"), "signature"),
        (lambda packet, _root: packet.__setitem__("signature_b64", "A" * 88), "signature"),
        (lambda packet, _root: packet.__setitem__("signer_id", "unknown"), "unknown signer"),
        (lambda packet, _root: packet.__setitem__("role", "FINANCE"), "role"),
        (lambda packet, _root: packet.__setitem__("scope", "FINANCE:SETTLE"), "scope"),
        (lambda packet, _root: packet.__setitem__("expires_at", 4_102_444_000), "expired"),
    ],
)
def test_rejects_tampered_or_untrusted_signed_evidence(signed_fixture, mutate, error):
    from src.hybrid.external_trust_root import (
        ExternalTrustRootVerifier,
        TrustRootVerificationError,
    )

    root_path, packet, expected = signed_fixture
    mutate(packet, root_path)
    with pytest.raises(TrustRootVerificationError, match=error):
        ExternalTrustRootVerifier(root_path, clock=lambda: 4_102_444_010).verify(packet, expected=expected)


def test_rejects_wrong_role_scope_expiry_and_revocation_even_when_resigned(signed_fixture):
    from src.hybrid.external_trust_root import (
        ExternalTrustRootVerifier,
        TrustRootVerificationError,
    )

    root_path, packet, expected = signed_fixture
    root = json.loads(root_path.read_text(encoding="utf-8"))
    private_key = Ed25519PrivateKey.generate()
    root["keys"][0]["public_key_b64"] = base64.b64encode(
        private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    ).decode("ascii")

    def resign(changes: dict) -> dict:
        candidate = deepcopy(packet)
        candidate.update(changes)
        body = {key: value for key, value in candidate.items() if key != "signature_b64"}
        candidate["signature_b64"] = base64.b64encode(private_key.sign(canonical(body))).decode("ascii")
        return candidate

    root_path.write_bytes(canonical(root))
    for changes, message in [
        ({"role": "FINANCE"}, "role"),
        ({"scope": "FINANCE:SETTLE"}, "scope"),
        ({"expires_at": 4_102_444_000}, "expired"),
    ]:
        with pytest.raises(TrustRootVerificationError, match=message):
            ExternalTrustRootVerifier(root_path, clock=lambda: 4_102_444_010).verify(resign(changes), expected=expected)

    root["revoked_signers"] = ["test-qa-1"]
    root_path.write_bytes(canonical(root))
    with pytest.raises(TrustRootVerificationError, match="revoked"):
        ExternalTrustRootVerifier(root_path, clock=lambda: 4_102_444_010).verify(resign({}), expected=expected)


def test_rejects_replay_missing_config_and_packet_supplied_key(signed_fixture, tmp_path):
    from src.hybrid.external_trust_root import (
        EvidenceReplayGuard,
        ExternalTrustRootVerifier,
        TrustRootVerificationError,
    )

    root_path, packet, expected = signed_fixture
    verifier = ExternalTrustRootVerifier(root_path, clock=lambda: 4_102_444_010)
    replay = EvidenceReplayGuard()
    verifier.verify(packet, expected=expected, replay_guard=replay)
    with pytest.raises(TrustRootVerificationError, match="replay"):
        verifier.verify(packet, expected=expected, replay_guard=replay)
    with pytest.raises(TrustRootVerificationError, match="missing"):
        ExternalTrustRootVerifier(tmp_path / "absent.json", clock=lambda: 4_102_444_010)

    forged = deepcopy(packet)
    forged["public_key_b64"] = "A" * 44
    with pytest.raises(TrustRootVerificationError, match="closed"):
        ExternalTrustRootVerifier(root_path, clock=lambda: 4_102_444_010).verify(forged, expected=expected)


def test_rejects_duplicate_json_keys_at_every_depth_before_root_parse(signed_fixture):
    from src.hybrid.external_trust_root import (
        ExternalTrustRootVerifier,
        TrustRootVerificationError,
    )

    root_path, _packet, _expected = signed_fixture
    root_path.write_text(
        '{"schema":1,"kind":"EXTERNAL_SIGNER_TRUST_ROOT","environment":"TEST",'
        '"algorithm":"Ed25519","version":"test-root-v1","keys":[{"signer_id":"one",'
        '"signer_id":"two"}],"revoked_signers":[]}',
        encoding="utf-8",
    )

    with pytest.raises(TrustRootVerificationError, match="duplicate"):
        ExternalTrustRootVerifier(root_path, clock=lambda: 4_102_444_010)


def test_rejects_boolean_schema_values_in_root_and_evidence(signed_fixture):
    from src.hybrid.external_trust_root import (
        ExternalTrustRootVerifier,
        TrustRootVerificationError,
    )

    root_path, packet, expected = signed_fixture
    root = json.loads(root_path.read_text(encoding="utf-8"))
    root["schema"] = True
    root_path.write_bytes(canonical(root))
    with pytest.raises(TrustRootVerificationError, match="schema"):
        ExternalTrustRootVerifier(root_path, clock=lambda: 4_102_444_010)

    root["schema"] = 1
    root_path.write_bytes(canonical(root))
    packet["schema"] = True
    with pytest.raises(TrustRootVerificationError, match="schema"):
        ExternalTrustRootVerifier(root_path, clock=lambda: 4_102_444_010).verify(packet, expected=expected)
