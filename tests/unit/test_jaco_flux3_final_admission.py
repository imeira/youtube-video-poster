from __future__ import annotations

import json
import time
from hashlib import sha256
from pathlib import Path

import pytest


def _write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _evidence(tmp_path: Path, *, price: str = "0.048", reviewed_at: int | None = None) -> dict[str, Path]:
    reviewed_at = int(time.time()) if reviewed_at is None else reviewed_at
    packet = _write(tmp_path / "packet.json", {"endpoint": "blackforestlabs/flux-3/text-to-image", "payload_sha256": "a" * 64, "maximum_usd": "0.048"})
    review = _write(tmp_path / "review.json", {"schema": 1, "kind": "LOCAL_PROCESS_REVIEW_OUTPUT", "verdict": "PASS", "admission_state": "READY_FOR_PARENT_VERIFICATION_ONLY", "request_id": "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9", "endpoint": "blackforestlabs/flux-3/text-to-image", "packet_sha256": _sha(packet), "reviewed_at_epoch": reviewed_at})
    authorization = _write(tmp_path / "authorization.json", {"schema": 1, "kind": "JACO_FLUX3_FINAL_ADMISSION_AUTHORIZATION", "version": "2026-11-06", "request_id": "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9", "endpoint": "blackforestlabs/flux-3/text-to-image", "payload_sha256": "a" * 64, "maximum_usd": "0.048", "call_limit": 1, "user_authorization_evidence": "USER_AUTHORIZED_LOCAL_QA_AND_ONE_ALTERNATIVE_IMAGE_EVAL_WITHIN_USD10"})
    price_doc = {"schema": "FAL_AUTHENTICATED_CATALOG_PRICE_SNAPSHOT/1", "endpoint_id": "blackforestlabs/flux-3/text-to-image", "currency": "USD", "maximum_usd": price, "unit": "megapixels", "unit_price_usd": price, "observed_at_epoch": int(time.time()), "provider_expiry": None, "price_validity": "CATALOG_PRICE_NOT_TRANSACTION_QUOTE"}
    quote = _write(tmp_path / "quote.json", price_doc)
    return {"packet": packet, "review": review, "authorization": authorization, "quote": quote}


def test_final_admission_binds_fresh_review_authorization_payload_and_price(tmp_path):
    from src.hybrid.jaco_flux3_final_admission import (
        prepare_execution_intent,
        validate_final_admission,
    )

    paths = _evidence(tmp_path)
    result = validate_final_admission(**paths)

    assert result["endpoint"] == "blackforestlabs/flux-3/text-to-image"
    assert result["reserved_usd"] == "0.048"
    assert result["authorization_sha256"] == _sha(paths["authorization"])
    assert result["review_sha256"] == _sha(paths["review"])
    intent = prepare_execution_intent(result, tmp_path / "execution-intent.json")
    assert intent["state"] == "PREPARED"
    assert intent["remote_request_id"] == "UNASSIGNED"


def test_final_admission_execution_intent_is_exclusive_and_replay_has_no_second_effect(tmp_path):
    from src.hybrid.jaco_flux3_final_admission import (
        FinalAdmissionError,
        prepare_execution_intent,
        validate_final_admission,
    )

    paths = _evidence(tmp_path)
    receipt = validate_final_admission(**paths)
    intent_path = tmp_path / "execution-intent.json"
    prepare_execution_intent(receipt, intent_path)
    with pytest.raises(FinalAdmissionError, match="already consumed"):
        prepare_execution_intent(receipt, intent_path)
    assert json.loads(intent_path.read_text(encoding="utf-8"))["state"] == "PREPARED"


@pytest.mark.parametrize("changed", ["price", "packet"])
def test_final_admission_fails_closed_before_reservation_on_changed_price_or_payload(tmp_path, changed):
    from src.hybrid.jaco_flux3_final_admission import (
        FinalAdmissionError,
        validate_final_admission,
    )

    paths = _evidence(tmp_path, price="0.047" if changed == "price" else "0.048")
    if changed == "packet":
        packet = json.loads(paths["packet"].read_text(encoding="utf-8"))
        packet["payload_sha256"] = "b" * 64
        _write(paths["packet"], packet)
    with pytest.raises(FinalAdmissionError):
        validate_final_admission(**paths)


@pytest.mark.asyncio
async def test_final_receipt_uses_existing_executor_ledger_kernel_and_recovers_without_second_submit(tmp_path):
    """The admission receipt is consumed by prepare/reserve/run, not a side file."""
    from decimal import Decimal

    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.execution import Executor, Job, ProviderResult
    from src.hybrid.jaco_flux3_final_admission import (
        FinalAdmissionKernel,
        validate_final_admission,
    )
    from src.hybrid.planner import Config
    from src.hybrid.standing_budget import StandingBudgetLedger

    paths = _evidence(tmp_path)
    reference = tmp_path / "reference.png"
    Image.new("RGB", (8, 8), "white").save(reference)
    asset = FrozenAsset.approve(reference, "test", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "test", "TEST"), "test", "TEST")
    job = Job("JACO_GEN28_CANONICAL_001", "first", "TEST", "blackforestlabs/flux-3/text-to-image",
              {"prompt": "A canonical Genesis image", "aspect_ratio": "4:3", "resolution": "1k",
               "output_format": "png", "enable_prompt_expansion": False, "safety_tolerance": 2},
              manifest, Decimal("0.048"))
    ledger = StandingBudgetLedger(tmp_path / "ledger.sqlite3", policy_sha256="a" * 64,
                                  episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="TEST")
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal("10")), standing_ledger=ledger)

    class Provider:
        mode = "TEST"
        submits = 0
        recovers = 0

        async def submit(self, submitted, request_id, checkpoint):
            self.submits += 1
            checkpoint(provider_id="fal-test-remote-1")
            raise TimeoutError("interrupted after durable provider id")

        async def recover(self, submitted, request_id, provider_id, partial, checkpoint):
            self.recovers += 1
            assert provider_id == "fal-test-remote-1"
            output = tmp_path / "recovered.png"
            Image.new("RGB", (8, 8), "white").save(output)
            return ProviderResult(output, Decimal("0.048"))

    kernel = FinalAdmissionKernel(executor=executor, ledger=ledger, episode_id="JACO_GENESIS28", provider=Provider())
    receipt = validate_final_admission(**paths)
    with pytest.raises(TimeoutError):
        await kernel.execute(job, receipt)
    completed = await kernel.execute(job, receipt)

    assert completed["status"] == "COMPLETE"
    assert kernel.provider.submits == 1
    assert kernel.provider.recovers == 1
    assert ledger.claim(job.request_id)["state"] == "RESERVED"
