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
        "intent_input_sha256": PAYLOAD_SHA,
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
    ):
        r, b, c = deepcopy(receipt), deepcopy(binding), deepcopy(claim)
        mutate(r, b, c)
        with pytest.raises(SettlementDenied, match="final-admission"):
            verifier.verify(r, b, c)


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
