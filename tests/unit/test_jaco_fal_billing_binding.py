from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest

REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
LOCAL_ID = "7f19aab26d71464a9bc257f4f566d2c6cb642aab0bc55864bad8dcbaeab0ae47"
REMOTE_ID = "01a1b229-3ac4-78f2-a320-73ee43fc8bc2"
ENDPOINT = "blackforestlabs/flux-3/text-to-image"
PAYLOAD_SHA = "e" * 64


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _write(path: Path, document: dict) -> str:
    path.write_bytes(_canonical(document))
    return sha256(path.read_bytes()).hexdigest()


def _contract(tmp_path: Path):
    mandate = {
        "schema": "JACO_USER_EXECUTION_MANDATE/2", "request_id": REQUEST_ID,
        "revision": "818556d97edb648f", "endpoint": ENDPOINT,
        "payload_sha256": PAYLOAD_SHA, "maximum_usd": "0.048", "call_limit": 1,
        "budget_cap_usd": "10.00", "scope": "EVALUATION_GENERATION_ONLY",
        "release_authorization": "NOT_AUTHORIZED",
        "license_status": "EVALUATION_ONLY_CONDITIONALLY_SUPPORTABLE",
    }
    mandate_sha = _write(tmp_path / "user-mandate.json", mandate)
    authorization = {
        "schema": 1, "kind": "JACO_FLUX3_FINAL_ADMISSION_AUTHORIZATION",
        "version": f"mandate-provenance-v1-{mandate_sha[:12]}", "request_id": REQUEST_ID,
        "endpoint": ENDPOINT, "payload_sha256": PAYLOAD_SHA, "maximum_usd": "0.048",
        "call_limit": 1,
        "user_authorization_evidence": "USER_AUTHORIZED_LOCAL_QA_AND_ONE_ALTERNATIVE_IMAGE_EVAL_WITHIN_USD10",
    }
    authorization_sha = _write(tmp_path / "final-admission-authorization.json", authorization)
    policy = {
        "schema": "JACO_FLUX3_LOCAL_OPERATOR_POLICY/1",
        "trust_derivation": "CANONICAL_USER_TRANSCRIPT_PLUS_TRUSTED_LOCAL_OPERATOR",
        "mandate_sha256": mandate_sha, "provenance_sha256": "d" * 64,
        "source_evidence": {"financial_record": "f" * 64, "official_contract_review": "e" * 64,
                            "packet": "a" * 64, "get_only_preflight": "b" * 64},
    }
    _write(tmp_path / "parent-provisioned-policy.json", policy)
    receipt = {
        "schema": 1, "kind": "JACO_FLUX3_FINAL_ADMISSION", "request_id": REQUEST_ID,
        "endpoint": ENDPOINT, "reserved_usd": "0.048", "call_limit": 1,
        "packet_sha256": "a" * 64, "review_sha256": "b" * 64,
        "authorization_sha256": authorization_sha, "quote_sha256": "c" * 64,
        "authorization_version": authorization["version"], "validated_at_epoch": 1,
    }
    receipt["receipt_sha256"] = sha256(_canonical(receipt)).hexdigest()
    execution = tmp_path / "execution"
    execution.mkdir()
    _write(execution / "final-admission-receipt.json", receipt)
    scope = f"JACO_FLUX3_FINAL_ADMISSION:{mandate_sha}"
    binding = {"schema": 2, "local_request_id": LOCAL_ID,
               "provider_request_id": REMOTE_ID, "endpoint_id": ENDPOINT,
               "auth_scope": scope, "authority_mode": "LIVE"}
    executor_receipt = {
        "receipt_schema": 2, "request_id": LOCAL_ID, "endpoint": ENDPOINT,
        "mode": "LIVE", "authorization": {"request_id": LOCAL_ID},
        "input_contract": {
            "schema": 1,
            "input_kind": "TEXT_TO_IMAGE",
            "payload_sha256": PAYLOAD_SHA,
        },
        "final_admission_marker": {
            "schema": 2, "kind": "JACO_FLUX3_FINAL_ADMISSION_MARKER",
            "receipt_path": str((execution / "final-admission-receipt.json").resolve()),
            "receipt_sha256": receipt["receipt_sha256"], "request_id": LOCAL_ID,
            "endpoint": ENDPOINT, "payload_sha256": PAYLOAD_SHA,
            "input_kind": "TEXT_TO_IMAGE", "auth_scope": scope,
            "reserved_usd": "0.048", "trust_identity": "FIXED_PRODUCTION_COMPOSITION",
        },
    }
    claim = {"request_id": LOCAL_ID, "episode_id": "JACO_GENESIS28", "provider": ENDPOINT,
             "reserved_usd": "0.048000", "state": "RESERVED", "actual_usd": None,
             "settlement_mode": "FAL_READBACK_V2", "auth_scope": scope,
             "provider_request_id": REMOTE_ID, "authority_mode": "LIVE"}
    return executor_receipt, binding, claim, tmp_path, execution


def test_jaco_contract_verifier_rejects_forged_identity_before_readback(tmp_path):
    from src.hybrid.billing_reconciler import (
        JacoFinalAdmissionContractVerifier,
        SettlementDenied,
    )

    receipt, binding, claim, root, execution = _contract(tmp_path)
    verifier = JacoFinalAdmissionContractVerifier(root=root, execution_root=execution)
    verifier.verify(receipt, binding, claim)

    forged = deepcopy(receipt)
    forged["final_admission_marker"]["payload_sha256"] = "f" * 64
    with pytest.raises(SettlementDenied, match="final-admission"):
        verifier.verify(forged, binding, claim)


def test_jaco_contract_verifier_rejects_test_as_live_and_wrong_receipt_or_remote(tmp_path):
    from src.hybrid.billing_reconciler import (
        JacoFinalAdmissionContractVerifier,
        SettlementDenied,
    )

    receipt, binding, claim, root, execution = _contract(tmp_path)
    verifier = JacoFinalAdmissionContractVerifier(root=root, execution_root=execution)
    for mutate in (
        lambda r, b, c: r.__setitem__("mode", "TEST"),
        lambda r, b, c: r["final_admission_marker"].__setitem__("receipt_sha256", "0" * 64),
        lambda r, b, c: b.__setitem__("provider_request_id", "wrong-remote"),
        lambda r, b, c: b.__setitem__("auth_scope", "JACO_FLUX3_FINAL_ADMISSION:wrong"),
    ):
        r, b, c = deepcopy(receipt), deepcopy(binding), deepcopy(claim)
        mutate(r, b, c)
        with pytest.raises(SettlementDenied, match="final-admission"):
            verifier.verify(r, b, c)


@pytest.mark.parametrize(
    ("field", "value"),
    (("schema", 2), ("input_kind", "IMAGE_EDIT"), ("payload_sha256", "f" * 64)),
)
def test_jaco_contract_verifier_rejects_forged_input_contract_before_readback(
    tmp_path, field, value,
):
    from src.hybrid.billing_reconciler import (
        JacoFinalAdmissionContractVerifier,
        SettlementDenied,
    )

    receipt, binding, claim, root, execution = _contract(tmp_path)
    receipt["input_contract"][field] = value
    verifier = JacoFinalAdmissionContractVerifier(root=root, execution_root=execution)

    with pytest.raises(SettlementDenied, match="final-admission"):
        verifier.verify(receipt, binding, claim)


def test_jaco_contract_verifier_rejects_wrong_claim_currency_amount_and_settlement_identity(tmp_path):
    from src.hybrid.billing_reconciler import (
        JacoFinalAdmissionContractVerifier,
        SettlementDenied,
    )

    receipt, binding, claim, root, execution = _contract(tmp_path)
    verifier = JacoFinalAdmissionContractVerifier(root=root, execution_root=execution)
    for key, value in (("reserved_usd", "0.047000"), ("provider", "other/endpoint"),
                       ("authority_mode", "TEST")):
        bad_claim = deepcopy(claim)
        bad_claim[key] = value
        with pytest.raises(SettlementDenied, match="final-admission"):
            verifier.verify(receipt, binding, bad_claim)


def test_jaco_contract_verifier_admits_only_the_typed_style_remediation_successor(tmp_path):
    """The successor must not fall through to the generic LIVE binding path."""
    from src.hybrid.billing_reconciler import JacoFinalAdmissionContractVerifier

    operation = "JACO_GEN28_STYLE_REMEDIATION_001"
    local_id = "27ef6a66be109984c208daa6e053e4a9d4f51a0202b46d624ea964336f74d052"
    remote_id = "01a1b4a6-65e9-7e31-815f-1726452a3693"
    policy_sha = "4" * 64
    financial_sha = "9" * 64
    payload_sha = "a" * 64
    authority = {
        "schema": "jaco-derived-operation-authority/v1",
        "status": "READY_FOR_INDEPENDENT_PARENT_REVIEW_NO_RESERVATION_NO_POST",
        "operation_id": operation, "episode_id": "JACO_GENESIS28",
        "policy_sha256": policy_sha, "preflight_sha256": "p" * 64,
        "canonical_ledger": {"financial_policy_sha256": financial_sha},
    }
    authority_sha = _write(tmp_path / f"{operation}.authority.v2.json", authority)
    qualification = {
        "schema": "jaco-protected-deployment-qualification/v2", "status": "QUALIFIED",
        "operation_id": operation, "episode_id": "JACO_GENESIS28", "mode": "LIVE",
        "authorization": "STANDING_USER_AUTHORIZATION_PROTECTED_POLICY",
        "policy_sha256": policy_sha, "operation_policy_sha256": policy_sha,
        "financial_policy_sha256": financial_sha, "preflight_sha256": "p" * 64,
    }
    qualification_sha = _write(tmp_path / f"{operation}.qualification.v2.json", qualification)
    policy = {"schema": "jaco-standing-episode-operation-policy/v1", "episode_id": "JACO_GENESIS28",
              "canonical_consent": {"session_id": "canonical", "consent_message_id": 1, "prompt_message_id": 0}}
    policy_sha = _write(tmp_path / "JACO_GENESIS28_STANDING_OPERATION_POLICY.v1.json", policy)
    financial = {"schema": "jaco-factory-financial-reconciliation/1", "currency": "USD"}
    financial_sha = _write(tmp_path / "financial-record-source.json", financial)
    authority["policy_sha256"] = policy_sha
    authority["canonical_ledger"]["financial_policy_sha256"] = financial_sha
    authority_sha = _write(tmp_path / f"{operation}.authority.v2.json", authority)
    qualification.update({"policy_sha256": policy_sha, "operation_policy_sha256": policy_sha,
                          "financial_policy_sha256": financial_sha})
    qualification_sha = _write(tmp_path / f"{operation}.qualification.v2.json", qualification)
    execution = tmp_path / "execution"; execution.mkdir()
    marker = {
        "schema": 2, "kind": "JACO_STYLE_REMEDIATION_QUALIFICATION_MARKER",
        "qualification_sha256": qualification_sha, "request_id": local_id, "endpoint": ENDPOINT,
        "payload_sha256": payload_sha, "input_kind": "TEXT_TO_IMAGE",
        "auth_scope": f"{operation}:{policy_sha}", "reserved_usd": "0.048",
        "trust_identity": "PROTECTED_DEPLOYMENT_QUALIFICATION",
    }
    receipt = {"receipt_schema": 2, "request_id": local_id, "scene": operation,
               "endpoint": ENDPOINT, "mode": "LIVE",
               "input_contract": {"schema": 1, "input_kind": "TEXT_TO_IMAGE", "payload_sha256": payload_sha},
               "final_admission_marker": marker}
    binding = {"schema": 2, "local_request_id": local_id, "provider_request_id": remote_id,
               "endpoint_id": ENDPOINT, "auth_scope": marker["auth_scope"], "authority_mode": "LIVE"}
    claim = {"request_id": local_id, "episode_id": "JACO_GENESIS28", "provider": ENDPOINT,
             "reserved_usd": "0.048000", "state": "RESERVED", "actual_usd": None,
             "settlement_mode": "FAL_READBACK_V2", "auth_scope": marker["auth_scope"],
             "provider_request_id": remote_id, "authority_mode": "LIVE", "currency": "USD"}

    verifier = JacoFinalAdmissionContractVerifier(
        root=tmp_path, execution_root=execution,
        style_authority_path=tmp_path / f"{operation}.authority.v2.json",
        style_qualification_path=tmp_path / f"{operation}.qualification.v2.json",
        style_policy_path=tmp_path / "JACO_GENESIS28_STANDING_OPERATION_POLICY.v1.json",
        financial_source_path=tmp_path / "financial-record-source.json",
        style_authority_sha256=authority_sha,
    )
    assert verifier.matches_scope(marker["auth_scope"])
    assert not verifier.matches_scope(f"{operation}:{'f' * 64}")
    verifier.verify(receipt, binding, claim)

    for mutation in (
        lambda r, b, c: r.__setitem__("mode", "TEST"),
        lambda r, b, c: b.__setitem__("provider_request_id", "wrong-remote"),
        lambda r, b, c: r["input_contract"].__setitem__("payload_sha256", "f" * 64),
        lambda r, b, c: c.__setitem__("reserved_usd", "0.047000"),
        lambda r, b, c: c.__setitem__("currency", "EUR"),
    ):
        r, b, c = deepcopy(receipt), deepcopy(binding), deepcopy(claim)
        mutation(r, b, c)
        with pytest.raises(Exception, match="final-admission"):
            verifier.verify(r, b, c)
