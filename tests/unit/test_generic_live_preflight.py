"""SDD acceptance for the bounded generic preflight verifier."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _write(root: Path, name: str, value: dict) -> str:
    raw = _canonical(value)
    (root / name).write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _fixture(root: Path) -> str:
    """All non-secret static TEST evidence; pins are derived before loader use."""
    payload = {"prompt": "A child-safe illustrated scene with warm daylight.", "width": 1024, "height": 1024}
    payload_sha = _write(root, "payload.json", payload)
    consent = {"schema": "canonical-consent/v1", "operation_id": "TEST-GENERIC-001", "episode_id": "TEST-EPISODE-001", "scope": "IMAGE_GENERATION", "status": "ACTIVE"}
    consent_sha = _write(root, "consent.json", consent)
    financial = {"schema": "standing-financial-policy/v1", "operation_id": "TEST-GENERIC-001", "episode_id": "TEST-EPISODE-001", "consent_sha256": consent_sha, "currency": "USD", "maximum_usd": "0.048", "status": "ACTIVE"}
    financial_sha = _write(root, "financial.json", financial)
    quote = {"schema": "provider-finite-quote/v1", "provider": "TEST_PROVIDER", "endpoint": "https://provider.test/v1/images", "model": "test-image-v1", "payload_sha256": payload_sha, "currency": "USD", "maximum_usd": "0.048", "expires_at_epoch": 4102444800}
    quote_sha = _write(root, "quote.json", quote)
    license_doc = {"schema": "license-provenance/v1", "provider": "TEST_PROVIDER", "model": "test-image-v1", "license": "TEST-COMMERCIAL", "payload_sha256": payload_sha, "status": "VALID"}
    license_sha = _write(root, "license.json", license_doc)
    review = {"schema": "local-process-review-receipt/v1", "operation_id": "TEST-GENERIC-001", "verdict": "PASS", "consent_sha256": consent_sha, "financial_sha256": financial_sha, "quote_sha256": quote_sha, "license_sha256": license_sha, "payload_sha256": payload_sha, "reviewer": "LOCAL_PROCESS_REVIEW_TEST"}
    review_sha = _write(root, "review.json", review)
    packet = {"schema": "generic-live-operation-packet/v1", "operation_id": "TEST-GENERIC-001", "mode": "TEST", "episode_id": "TEST-EPISODE-001", "consent": {"path": "consent.json", "sha256": consent_sha}, "financial_policy": {"path": "financial.json", "sha256": financial_sha}, "independent_review": {"path": "review.json", "sha256": review_sha}, "quote": {"path": "quote.json", "sha256": quote_sha}, "license": {"path": "license.json", "sha256": license_sha}, "payload": {"path": "payload.json", "sha256": payload_sha}}
    _write(root, "operations/TEST-GENERIC-001.json", packet)
    return "TEST-GENERIC-001"


def _verify(root: Path, operation_id: str):
    from src.hybrid.generic_live_preflight import GenericLivePreflightVerifier
    return GenericLivePreflightVerifier.for_test(root).verify(operation_id, now=1_700_000_000)


def test_canonical_test_packet_returns_verified_bound_preflight_not_authority(tmp_path):
    (tmp_path / "operations").mkdir()
    operation_id = _fixture(tmp_path)
    result = _verify(tmp_path, operation_id)
    assert result["status"] == "VERIFIED_BOUND_PREFLIGHT"
    assert result["mode"] == "TEST"
    assert result["authority"] == "NONE"
    assert result["dispatch_permitted"] is False
    assert result["bindings"]["operation_id"] == operation_id


@pytest.mark.parametrize("mutation,reason", [
    (("consent.json", "status", "FORGED"), "canonical consent"),
    (("review.json", "reviewer", "producer"), "independent review"),
    (("quote.json", "expires_at_epoch", 1), "quote expired"),
    (("financial.json", "operation_id", "TEST-OTHER-001"), "financial policy"),
    (("license.json", "payload_sha256", "0" * 64), "license provenance"),
    (("quote.json", "payload_sha256", "f" * 64), "quote"),
])
def test_rejects_forged_or_mismatched_bound_evidence(tmp_path, mutation, reason):
    (tmp_path / "operations").mkdir()
    operation_id = _fixture(tmp_path)
    filename, field, value = mutation
    path = tmp_path / filename
    doc = json.loads(path.read_text())
    doc[field] = value
    path.write_bytes(_canonical(doc))
    # Rebind only quote mutations so expiry/wire semantics—not merely its pin—
    # are exercised. Other forged files must fail their immutable pin first.
    if filename == "quote.json":
        packet_path = tmp_path / "operations" / f"{operation_id}.json"
        packet = json.loads(packet_path.read_text())
        packet["quote"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        packet_path.write_bytes(_canonical(packet))
    with pytest.raises(PermissionError, match=reason):
        _verify(tmp_path, operation_id)


def test_rejects_malformed_id_and_never_dispatches(tmp_path):
    from src.hybrid.generic_live_preflight import GenericLivePreflightVerifier
    verifier = GenericLivePreflightVerifier.for_test(tmp_path)
    with pytest.raises(PermissionError, match="literal"):
        verifier.verify("../TEST-GENERIC-001", now=1_700_000_000)
