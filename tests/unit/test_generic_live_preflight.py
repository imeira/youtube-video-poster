"""Acceptance tests for the generic preflight trust-domain boundary."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _write(root: Path, name: str, value: dict) -> str:
    raw = _canonical(value)
    (root / name).write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def _fixture(root: Path, *, mode: str = "TEST") -> str:
    """Closed, non-secret TEST-domain fixture; it is not a LIVE trust root."""
    operation_id = f"{mode}-GENERIC-001"
    episode_id = "TEST-EPISODE-001"
    payload = {"prompt": "A child-safe illustrated scene with warm daylight.", "width": 1024, "height": 1024}
    payload_sha = _write(root, "payload.json", payload)
    consent = {"schema": "canonical-consent/v1", "operation_id": operation_id, "episode_id": episode_id, "scope": "IMAGE_GENERATION", "status": "ACTIVE"}
    consent_sha = _write(root, "consent.json", consent)
    financial = {"schema": "standing-financial-policy/v1", "operation_id": operation_id, "episode_id": episode_id, "consent_sha256": consent_sha, "currency": "USD", "maximum_usd": "0.048", "status": "ACTIVE"}
    financial_sha = _write(root, "financial.json", financial)
    quote = {"schema": "provider-finite-quote/v1", "provider": "TEST_PROVIDER", "endpoint": "https://provider.test/v1/images", "model": "test-image-v1", "payload_sha256": payload_sha, "currency": "USD", "maximum_usd": "0.048", "expires_at_epoch": 4102444800}
    quote_sha = _write(root, "quote.json", quote)
    license_doc = {"schema": "license-provenance/v1", "provider": "TEST_PROVIDER", "model": "test-image-v1", "license": "TEST-COMMERCIAL", "payload_sha256": payload_sha, "status": "VALID"}
    license_sha = _write(root, "license.json", license_doc)
    review = {"schema": "local-process-review-receipt/v1", "operation_id": operation_id, "verdict": "PASS", "consent_sha256": consent_sha, "financial_sha256": financial_sha, "quote_sha256": quote_sha, "license_sha256": license_sha, "payload_sha256": payload_sha, "reviewer": "LOCAL_PROCESS_REVIEW_TEST"}
    review_sha = _write(root, "review.json", review)
    packet = {"schema": "generic-live-operation-packet/v1", "operation_id": operation_id, "mode": mode, "episode_id": episode_id, "consent": {"path": "consent.json", "sha256": consent_sha}, "financial_policy": {"path": "financial.json", "sha256": financial_sha}, "independent_review": {"path": "review.json", "sha256": review_sha}, "quote": {"path": "quote.json", "sha256": quote_sha}, "license": {"path": "license.json", "sha256": license_sha}, "payload": {"path": "payload.json", "sha256": payload_sha}}
    _write(root, f"operations/{operation_id}.json", packet)
    return operation_id


def _verify(root: Path, operation_id: str):
    from src.hybrid.generic_live_preflight import GenericLivePreflightVerifier
    return GenericLivePreflightVerifier.for_test(root).verify(operation_id, now=1_700_000_000)


def test_for_test_rejects_live_packet_before_reading_any_evidence(tmp_path, monkeypatch):
    (tmp_path / "operations").mkdir()
    operation_id = _fixture(tmp_path, mode="LIVE")
    from src.hybrid.generic_live_preflight import GenericLivePreflightVerifier

    def evidence_must_not_be_read(path):
        if str(path).endswith(f"operations\\{operation_id}.json"):
            return original_read_bytes(path)
        raise AssertionError("TEST verifier read LIVE supporting evidence")

    original_read_bytes = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", evidence_must_not_be_read)

    with pytest.raises(PermissionError, match="TEST packet"):
        GenericLivePreflightVerifier.for_test(tmp_path).verify(operation_id, now=1_700_000_000)


def test_for_test_positive_fixture_has_test_only_structural_label(tmp_path):
    (tmp_path / "operations").mkdir()
    operation_id = _fixture(tmp_path)

    result = _verify(tmp_path, operation_id)

    assert result["status"] == "VERIFIED"
    assert result["domain"] == "TEST"
    assert result["evidence_mode"] == "TEST"
    assert result["verification"] == "STRUCTURAL_TEST_FIXTURE_ONLY"
    assert result["authority"] == "NONE"
    assert result["dispatch_permitted"] is False


def test_for_test_has_no_public_flag_that_can_flip_its_domain(tmp_path):
    from src.hybrid.generic_live_preflight import GenericLivePreflightVerifier

    with pytest.raises(TypeError):
        GenericLivePreflightVerifier.for_test(tmp_path, domain="LIVE")


def test_public_constructor_cannot_supply_live_root(tmp_path):
    from src.hybrid.generic_live_preflight import GenericLivePreflightVerifier

    with pytest.raises(TypeError):
        GenericLivePreflightVerifier(_root=tmp_path, _domain="LIVE")


def test_full_rebound_of_all_fixture_hashes_cannot_turn_forged_test_evidence_valid(tmp_path):
    (tmp_path / "operations").mkdir()
    operation_id = _fixture(tmp_path, mode="TEST")
    consent_path = tmp_path / "consent.json"
    consent = json.loads(consent_path.read_text())
    consent["status"] = "FORGED"
    consent_sha = _write(tmp_path, "consent.json", consent)
    financial = json.loads((tmp_path / "financial.json").read_text())
    financial["consent_sha256"] = consent_sha
    financial_sha = _write(tmp_path, "financial.json", financial)
    review = json.loads((tmp_path / "review.json").read_text())
    review["consent_sha256"] = consent_sha
    review["financial_sha256"] = financial_sha
    review_sha = _write(tmp_path, "review.json", review)
    packet_path = tmp_path / "operations" / f"{operation_id}.json"
    packet = json.loads(packet_path.read_text())
    packet["consent"]["sha256"] = consent_sha
    packet["financial_policy"]["sha256"] = financial_sha
    packet["independent_review"]["sha256"] = review_sha
    packet_path.write_bytes(_canonical(packet))

    with pytest.raises(PermissionError, match="canonical consent"):
        _verify(tmp_path, operation_id)


def test_production_readiness_never_falls_back_to_legacy_readiness(monkeypatch):
    import src.hybrid.qualified_episode_operation as qualified
    from src.hybrid.generic_live_composition import GenericLiveCompositionRegistry

    def legacy_must_not_run(*_args, **_kwargs):
        raise AssertionError("legacy readiness fallback was invoked")

    monkeypatch.setattr(qualified, "qualified_operation_readiness", legacy_must_not_run)
    report = GenericLiveCompositionRegistry.production().readiness("LIVE-GENERIC-001")

    assert report == {
        "operation_id": "LIVE-GENERIC-001",
        "status": "BLOCKED",
        "live_enabled": False,
        "reason": "generic canonical binding is not provisioned",
    }


def test_unprovisioned_generic_readiness_never_calls_jaco_or_reads_production_root(monkeypatch):
    from pathlib import Path as ConcretePath

    from src.hybrid import jaco_style_remediation_operation as jaco
    from src.hybrid import young_identity_model_sheet_production as model_sheet
    from src.hybrid.generic_live_composition import GenericLiveCompositionRegistry

    def jaco_must_not_run(*_args, **_kwargs):
        raise AssertionError("Jacó authority validator was called")

    monkeypatch.setattr(jaco.OperationAdmissionFactory, "_authority", jaco_must_not_run)
    monkeypatch.setattr(jaco.OperationAdmissionFactory, "_canonical_ledger_binding", jaco_must_not_run)
    monkeypatch.setattr(jaco.OperationAdmissionFactory, "_qualification", jaco_must_not_run)
    monkeypatch.setattr(model_sheet, "_canonical_consent", jaco_must_not_run)

    protected_reads: list[ConcretePath] = []
    original_read_bytes = ConcretePath.read_bytes

    def guard_production_open(path: ConcretePath):
        if str(path).replace("\\", "/").startswith("C:/HermesStudio/protected/"):
            protected_reads.append(path)
            raise AssertionError("production root was opened")
        return original_read_bytes(path)

    monkeypatch.setattr(ConcretePath, "read_bytes", guard_production_open)

    report = GenericLiveCompositionRegistry.production().readiness("LIVE-GENERIC-001")

    assert report["status"] == "BLOCKED"
    assert report["reason"] == "generic canonical binding is not provisioned"
    assert protected_reads == []


def test_rejects_malformed_id_and_never_dispatches(tmp_path):
    from src.hybrid.generic_live_preflight import GenericLivePreflightVerifier
    verifier = GenericLivePreflightVerifier.for_test(tmp_path)
    with pytest.raises(PermissionError, match="literal"):
        verifier.verify("../TEST-GENERIC-001", now=1_700_000_000)
