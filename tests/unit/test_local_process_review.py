from __future__ import annotations

import json
import time
from hashlib import sha256
from pathlib import Path

import pytest

REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
ENDPOINT = "blackforestlabs/flux-3/text-to-image"


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def _inputs(tmp_path: Path, *, quote_expiry: int | None = None) -> dict[str, Path]:
    quote = _write_json(tmp_path / "quote.json", {"endpoint_id": ENDPOINT, "currency": "USD", "maximum_usd": "0.048", "quote_id": "quoted-1", "valid_until_epoch": quote_expiry if quote_expiry is not None else int(time.time()) + 120})
    openapi = _write_json(tmp_path / "openapi.json", {"endpoint": ENDPOINT, "output_format": "png", "resolution": "1k"})
    finance = _write_json(tmp_path / "finance.json", {
        "schema": "jaco-factory-financial-reconciliation/1", "request_hash": REQUEST_ID,
        "revision": "818556d97edb648f", "currency": "USD", "total_episode_cap": "10.00",
        "status": "APPROVED_USD10", "approval_scope": "FINANCIAL_ONLY", "authorizes_generation": False,
    })
    payload = {
        "prompt": "Genesis 28 Jacó and Isaque, child-safe canonical establishing image; no text, no modern objects.",
        "aspect_ratio": "4:3", "resolution": "1k", "output_format": "png",
        "enable_prompt_expansion": False, "safety_tolerance": 2,
    }
    packet = {
        "schema": "jaco-fal-flux3-successor-benchmark/3", "kind": "BOUNDED_FAL_FLUX3_CANARY_PREGEN_PACKET",
        "bindings": {"request_id": REQUEST_ID, "endpoint": ENDPOINT,
                     "payload_sha256": sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
                     "authenticated_price_quote": {"sha256": _sha(quote), "bytes": quote.stat().st_size},
                     "openapi_snapshot": {"sha256": _sha(openapi), "bytes": openapi.stat().st_size},
                     "financial_approval": {"sha256": _sha(finance), "status": "APPROVED_USD10", "total_episode_cap_usd": "10.00"}},
        "candidate": {"call_limit": 1, "single_output": True, "reference_inputs": [], "payload": payload},
        "cost": {"approved_canary_maximum_usd": "0.048", "current_authenticated_quote_maximum_usd": "0.048",
                 "actual_billed_amount": "UNKNOWN_UNTIL_PROVIDER_RECEIPT_OR_BILLING_RECONCILIATION"},
    }
    packet_path = _write_json(tmp_path / "packet.json", packet)
    request = _write_json(tmp_path / "request.json", {
        "schema": "jaco-fal-flux3-independent-pre-generation-review-request/2",
        "kind": "INDEPENDENT_PREGEN_REVIEW_REQUEST", "packet": {"sha256": _sha(packet_path)},
        "required_exact_bindings": {"request_id": REQUEST_ID, "endpoint": ENDPOINT,
                                    "payload_sha256": packet["bindings"]["payload_sha256"],
                                    "approved_canary_maximum_usd": "0.048", "successor_packet_sha256": _sha(packet_path)},
        "prohibitions": ["NO_FAL_PAID_POST", "NO_RESERVATION", "NO_GATEWAY_OR_OTHER_EP_WRITE", "NO_TEST_AS_LIVE"],
    })
    return {"packet": packet_path, "request": request, "quote": quote, "openapi": openapi, "finance": finance}


def _policy(tmp_path: Path, *, request_sha: str | None = None, expires_at: int | None = None) -> Path:
    from src.hybrid.local_process_review import build_local_process_review_policy

    paths = _inputs(tmp_path, quote_expiry=expires_at)
    return build_local_process_review_policy(
        root=tmp_path, packet_path=paths["packet"], review_request_path=paths["request"],
        quote_path=paths["quote"], openapi_path=paths["openapi"], financial_record_path=paths["finance"],
        output_path=tmp_path / "review-output.json", request_id=REQUEST_ID,
        review_request_sha256=request_sha or _sha(paths["request"]),
    )


def test_local_process_reviewer_produces_readback_verified_pass_without_provider_secrets(tmp_path, monkeypatch):
    from src.hybrid.local_process_review import run_local_process_review

    monkeypatch.setenv("FAL_KEY", "must-not-reach-reviewer")
    policy = _policy(tmp_path)
    result = run_local_process_review(policy)

    assert result["verdict"] == "PASS"
    assert result["admission_state"] == "READY_FOR_PARENT_VERIFICATION_ONLY"
    assert result["isolation"]["provider_secrets_available"] is False
    assert result["isolation"]["same_host_threat_model"] == "NOT_HOSTILE_CODE_ISOLATION"
    assert result["prohibitions"] == ["NO_FAL_PAID_POST", "NO_RESERVATION", "NO_GATEWAY_OR_OTHER_EP_WRITE", "NO_TEST_AS_LIVE"]


def test_local_process_reviewer_rejects_wrong_review_request_hash_before_policy_write(tmp_path):
    from src.hybrid.local_process_review import LocalProcessReviewError

    with pytest.raises(LocalProcessReviewError, match="review request hash"):
        _policy(tmp_path, request_sha="0" * 64)
    assert not (tmp_path / "local-process-review-policy.json").exists()
    assert not (tmp_path / "review-output.json").exists()


def test_local_process_reviewer_rejects_expired_quote_before_spawn(tmp_path):
    from src.hybrid.local_process_review import (
        LocalProcessReviewError,
        run_local_process_review,
    )

    policy = _policy(tmp_path, expires_at=int(time.time()) - 1)
    with pytest.raises(LocalProcessReviewError, match="quote expiry"):
        run_local_process_review(policy)
    assert not (tmp_path / "review-output.json").exists()


def test_local_process_reviewer_requires_provider_issued_expiry_not_a_local_ttl(tmp_path):
    from src.hybrid.local_process_review import (
        LocalProcessReviewError,
        build_local_process_review_policy,
    )

    paths = _inputs(tmp_path)
    quote = json.loads(paths["quote"].read_text(encoding="utf-8"))
    quote.pop("valid_until_epoch")
    _write_json(paths["quote"], quote)

    with pytest.raises(LocalProcessReviewError, match="provider-issued quote expiry"):
        build_local_process_review_policy(
            root=tmp_path, packet_path=paths["packet"], review_request_path=paths["request"],
            quote_path=paths["quote"], openapi_path=paths["openapi"], financial_record_path=paths["finance"],
            output_path=tmp_path / "review-output.json", request_id=REQUEST_ID,
            review_request_sha256=_sha(paths["request"]),
        )


def test_local_process_reviewer_replay_is_write_once(tmp_path):
    from src.hybrid.local_process_review import (
        LocalProcessReviewError,
        run_local_process_review,
    )

    policy = _policy(tmp_path)
    run_local_process_review(policy)
    with pytest.raises(LocalProcessReviewError, match="write-once"):
        run_local_process_review(policy)


def test_local_process_reviewer_rejects_reparse_input_and_does_not_spawn(tmp_path):
    from src.hybrid.local_process_review import (
        LocalProcessReviewError,
        run_local_process_review,
    )

    policy = _policy(tmp_path)
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    (tmp_path / "quote.json").unlink()
    (tmp_path / "quote.json").symlink_to(target)
    with pytest.raises(LocalProcessReviewError, match="reparse"):
        run_local_process_review(policy)
    assert not (tmp_path / "review-output.json").exists()
