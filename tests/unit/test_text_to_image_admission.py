from __future__ import annotations

import json
import os
import subprocess
import sys
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest

from src.hybrid.execution import Executor, Job
from src.hybrid.jaco_flux3_final_admission import (
    FinalAdmissionError,
    FinalAdmissionKernel,
)
from src.hybrid.planner import Config
from src.hybrid.standing_budget import StandingBudgetLedger

ENDPOINT = "blackforestlabs/flux-3/text-to-image"
PAYLOAD = {
    "prompt": "A child-safe Genesis establishing image",
    "aspect_ratio": "4:3",
    "resolution": "1k",
    "output_format": "png",
    "enable_prompt_expansion": False,
    "safety_tolerance": 2,
}


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _receipt() -> dict[str, str | int]:
    body: dict[str, str | int] = {
        "schema": 1,
        "kind": "JACO_FLUX3_FINAL_ADMISSION",
        "request_id": "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9",
        "endpoint": ENDPOINT,
        "reserved_usd": "0.048",
        "call_limit": 1,
        "packet_sha256": "a" * 64,
        "review_sha256": "b" * 64,
        "authorization_sha256": "c" * 64,
        "quote_sha256": "d" * 64,
        "authorization_version": "TEST",
        "validated_at_epoch": 1,
    }
    return {**body, "receipt_sha256": sha256(_canonical(body)).hexdigest()}


def _text_job(*, mode="TEST", manifest=None, input_kind="TEXT_TO_IMAGE") -> Job:
    return Job(
        "JACO_GEN28_CANONICAL_001",
        "first",
        mode,
        ENDPOINT,
        PAYLOAD,
        manifest,
        Decimal("0.048"),
        input_kind=input_kind,
    )


def _ledger(tmp_path: Path) -> StandingBudgetLedger:
    return StandingBudgetLedger(
        tmp_path / "ledger.sqlite3",
        policy_sha256="a" * 64,
        episode_limit_usd="10",
        month_limit_usd="10",
        experiment_limit_usd="10",
        mode="TEST",
    )


def _reference_manifest(tmp_path: Path):
    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet

    image = tmp_path / "reference.png"
    Image.new("RGB", (8, 8), "white").save(image)
    asset = FrozenAsset.approve(image, "test", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    return Manifest.freeze((asset,), FrozenAsset.approve(sheet, "test", "TEST"), "test", "TEST")


def test_text_and_edit_input_contracts_reject_fake_compatibility(tmp_path):
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(10)))

    with pytest.raises(ValueError, match="TEXT_TO_IMAGE.*reference"):
        executor.prepare_intent(_text_job(manifest=_reference_manifest(tmp_path)))
    with pytest.raises(ValueError, match="IMAGE_EDIT.*reference"):
        executor.prepare_intent(_text_job(input_kind="IMAGE_EDIT"))


@pytest.mark.asyncio
async def test_text_admission_rejects_live_receipt_and_cross_kind_marker_replay(tmp_path):
    ledger = _ledger(tmp_path)
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(10)), standing_ledger=ledger)

    class Provider:
        mode = "TEST"

        async def submit(self, *args):
            raise AssertionError("must not submit")

        async def recover(self, *args):
            raise AssertionError("must not recover")

    kernel = FinalAdmissionKernel(
        executor=executor, ledger=ledger, episode_id="JACO_GENESIS28", provider=Provider()
    )
    with pytest.raises(FinalAdmissionError, match="TEST canary"):
        await kernel.execute(_text_job(mode="LIVE"), _receipt())

    job = _text_job()
    row = executor.prepare_intent(job)
    ledger.reserve(
        job.request_id,
        "JACO_GENESIS28",
        ENDPOINT,
        job.cost,
        settlement_mode="FAL_READBACK_V2",
        auth_scope=row["settlement_binding"]["auth_scope"],
        provider_request_id="UNASSIGNED",
        authority_mode="TEST",
    )
    executor._change(
        job.request_id,
        final_admission_marker={
            "schema": 2,
            "kind": "JACO_FLUX3_FINAL_ADMISSION_MARKER",
            "receipt_path": "TEST_ONLY_IN_MEMORY_RECEIPT",
            "receipt_sha256": _receipt()["receipt_sha256"],
            "request_id": job.request_id,
            "endpoint": ENDPOINT,
            "payload_sha256": job.intent_input_sha256,
            "input_kind": "IMAGE_EDIT",
            "auth_scope": row["settlement_binding"]["auth_scope"],
            "reserved_usd": "0.048",
            "trust_identity": "TEST_ONLY_KERNEL",
        },
    )
    with pytest.raises(FinalAdmissionError, match="immutable"):
        await kernel.execute(job, _receipt())


def test_text_admission_fresh_process_recovers_only_by_get_without_second_reserve(tmp_path):
    """A reopened kernel sees the durable remote ID and calls recover, not submit."""
    script = tmp_path / "child.py"
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps(_receipt()), encoding="utf-8")
    script.write_text(
        '''
import asyncio, json, sys
from decimal import Decimal
from pathlib import Path
from src.hybrid.execution import Executor, Job, ProviderResult
from src.hybrid.jaco_flux3_final_admission import FinalAdmissionKernel
from src.hybrid.planner import Config
from src.hybrid.standing_budget import StandingBudgetLedger
root = Path(sys.argv[1]); action = sys.argv[2]
payload = {"prompt":"A child-safe Genesis establishing image","aspect_ratio":"4:3","resolution":"1k","output_format":"png","enable_prompt_expansion":False,"safety_tolerance":2}
job = Job("JACO_GEN28_CANONICAL_001", "first", "TEST", "blackforestlabs/flux-3/text-to-image", payload, None, Decimal("0.048"), input_kind="TEXT_TO_IMAGE")
ledger = StandingBudgetLedger(root / "ledger.sqlite3", policy_sha256="a" * 64, episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="TEST")
executor = Executor(root / "executor.sqlite3", Config(limit=Decimal(10)), standing_ledger=ledger)
counts_path = root / "counts.json"; counts = json.loads(counts_path.read_text()) if counts_path.exists() else {"posts": 0, "gets": 0}
class Transport:
    mode = "TEST"
    async def submit(self, job, request_id, checkpoint):
        counts["posts"] += 1; counts_path.write_text(json.dumps(counts)); checkpoint(provider_id="strict-remote-id"); raise TimeoutError("crash after remote id")
    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        assert provider_id == "strict-remote-id"; counts["gets"] += 1; counts_path.write_text(json.dumps(counts)); output = root / "recovered.bin"; output.write_bytes(b"GET-only recovery"); return ProviderResult(output, Decimal("0.048"))
kernel = FinalAdmissionKernel(executor=executor, ledger=ledger, episode_id="JACO_GENESIS28", provider=Transport())
async def main():
    try:
        result = await kernel.execute(job, json.loads((root / "receipt.json").read_text()))
    except TimeoutError:
        result = {"state": "CRASHED"}
    print(json.dumps(result, sort_keys=True))
asyncio.run(main())
''',
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(Path.cwd())}
    first = subprocess.run([sys.executable, str(script), str(tmp_path), "submit"], env=env, capture_output=True, text=True, check=True)
    assert json.loads(first.stdout)["state"] == "CRASHED"
    second = subprocess.run([sys.executable, str(script), str(tmp_path), "recover"], env=env, capture_output=True, text=True, check=True)
    assert json.loads(second.stdout)["status"] == "COMPLETE"
    assert json.loads((tmp_path / "counts.json").read_text()) == {"posts": 1, "gets": 1}

    ledger = _ledger(tmp_path)
    assert ledger.claim(_text_job().request_id)["state"] == "RESERVED"
    row = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(10))).inspect(
        _text_job().request_id
    )
    assert row["input_contract"] == {
        "schema": 1,
        "input_kind": "TEXT_TO_IMAGE",
        "payload_sha256": _text_job().intent_input_sha256,
    }
    assert row["final_admission_marker"]["auth_scope"] == "TEST_LOCAL_ONLY"
    assert row["final_admission_marker"]["reserved_usd"] == "0.048"
    with ledger._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_unknown_text_submission_without_remote_id_stays_quarantined(tmp_path):
    ledger = _ledger(tmp_path)
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(10)), standing_ledger=ledger)

    class Transport:
        mode = "TEST"
        submits = 0

        async def submit(self, job, request_id, checkpoint):
            self.submits += 1
            raise TimeoutError("unknown acceptance")

        async def recover(self, *args):
            raise AssertionError("no remote ID means no GET")

    transport = Transport()
    kernel = FinalAdmissionKernel(executor=executor, ledger=ledger, episode_id="JACO_GENESIS28", provider=transport)
    with pytest.raises(TimeoutError):
        await kernel.execute(_text_job(), _receipt())
    with pytest.raises(RuntimeError, match="reconciliation"):
        await kernel.execute(_text_job(), _receipt())
    assert transport.submits == 1
    assert executor.inspect(_text_job().request_id)["status"] == "SUBMITTING"
