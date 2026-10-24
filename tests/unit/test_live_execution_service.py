from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.execution import Job


def live_job(tmp_path: Path) -> Job:
    reference = tmp_path / "reference.png"
    Image.new("RGB", (64, 36), "green").save(reference)
    asset = FrozenAsset.approve(reference, "identity-review", "LIVE")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze(
        (asset,), FrozenAsset.approve(sheet, "assets-review", "LIVE"),
        "assets-review", "LIVE",
    )
    return Job(
        "SC001", "first", "LIVE", "fal-ai/flux-2/klein/9b/edit",
        {"prompt": "Calm child-safe scene", "image_urls": [str(reference)],
         "image_size": {"width": 64, "height": 36}}, manifest, Decimal("0.02"),
    )


def policy() -> dict:
    return {
        "schema": 2, "version": "external-live-v1", "mode": "LIVE",
        "episode_cap_usd": "1.00", "month_cap_usd": "2.00",
        "experiment_cap_usd": "0.50", "allowed_adapters": ["fal_flux"],
        "publication": {"enabled": False},
    }


def persisted_qa(path: Path, job: Job, kind: str, *, evidence: dict | None = None) -> Path:
    path.write_text(json.dumps({
        "schema": 1, "kind": kind, "approved": True,
        "request_id": job.request_id, "manifest_sha256": job.manifest.checksum,
        "reviewer": f"independent-{kind}",
        "evidence": evidence or {"method": "external-review", "result": "PASS"},
    }), encoding="utf-8")
    return path


class QuoteResolver:
    def __init__(self, *, valid_until: float = 4_102_444_120):
        self.valid_until = valid_until

    def quote(self, job: Job) -> dict:
        return {
            "endpoint": job.endpoint, "amount": str(job.cost),
            "valid_until": self.valid_until,
            "evidence": {"source": "authenticated-billing-export", "currency": "USD"},
        }


class ForbiddenExecutor:
    def __init__(self):
        self.calls = 0

    async def run(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("disabled service must never invoke executor")


class ForbiddenLedger:
    def __init__(self):
        self.calls = []
        self.binding = {}

    def __getattr__(self, name):
        def forbidden(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError("disabled service must never mutate the ledger")
        return forbidden


def service(tmp_path: Path, *, resolver: QuoteResolver | None = None):
    from src.hybrid.live_execution import LiveExecutionPolicy, LiveExecutionService

    trust_root = tmp_path / "external-trust-root"
    trust_root.mkdir()
    trust_root.joinpath("live-trust-root.json").write_text(json.dumps({
        "schema": 1, "kind": "LIVE_TRUST_ROOT", "issuer": "external-operator",
        "verification": "UNAVAILABLE",
    }), encoding="utf-8")
    parsed = LiveExecutionPolicy.from_dict(policy())
    executor, ledger = ForbiddenExecutor(), ForbiddenLedger()
    return (
        LiveExecutionService(
            executor=executor, ledger=ledger, policy=parsed,
            quote_resolver=resolver or QuoteResolver(), trust_root=trust_root,
            clock=lambda: 4_102_444_000,
        ), executor, ledger, trust_root,
    )


def test_preflight_is_write_once_and_explicitly_not_qualified(tmp_path):
    service_, _executor, _ledger, trust_root = service(tmp_path)
    job = live_job(tmp_path)
    identity = persisted_qa(trust_root / "identity.json", job, "IDENTITY_QA")
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")
    receipt_path = tmp_path / "preflight.json"

    qualification = service_.preflight(
        job, adapter="fal_flux", identity_qa_path=identity, assets_qa_path=assets,
        receipt_path=receipt_path,
    )

    assert qualification.execution_state == "BLOCKED_NOT_QUALIFIED"
    assert qualification.authorization is None and qualification.price is None
    persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert persisted["execution_state"] == "BLOCKED_NOT_QUALIFIED"
    assert persisted["trust_root_verification"] == "UNAVAILABLE"
    assert "authenticated-billing-export" in json.dumps(persisted)
    assert service_.preflight(
        job, adapter="fal_flux", identity_qa_path=identity, assets_qa_path=assets,
        receipt_path=receipt_path,
    ).document_sha256 == qualification.document_sha256
    receipt_path.write_text('{"different": true}', encoding="utf-8")
    with pytest.raises(ValueError, match="write-once"):
        service_.preflight(
            job, adapter="fal_flux", identity_qa_path=identity, assets_qa_path=assets,
            receipt_path=receipt_path,
        )


def test_dispatch_is_hard_blocked_before_executor_or_ledger_even_after_preflight(tmp_path):
    service_, executor, ledger, trust_root = service(tmp_path)
    job = live_job(tmp_path)
    identity = persisted_qa(trust_root / "identity.json", job, "IDENTITY_QA")
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")
    receipt_path = tmp_path / "preflight.json"
    service_.preflight(job, adapter="fal_flux", identity_qa_path=identity,
                       assets_qa_path=assets, receipt_path=receipt_path)

    with pytest.raises(PermissionError, match="disabled until external trust"):
        asyncio.run(service_.dispatch(job, preflight_path=receipt_path))
    # Replays after any hypothetical reserve/submit/receipt/settle crash cannot
    # enter a recovery or resubmit branch: this service has no such branch.
    with pytest.raises(PermissionError, match="disabled until external trust"):
        asyncio.run(service_.dispatch(job, preflight_path=receipt_path))

    assert executor.calls == 0
    assert ledger.calls == []


def test_package_exposes_no_live_factory_or_test_transport_selector():
    from src.hybrid import live_execution

    assert not hasattr(live_execution, "LiveAdapterFactory")


def test_dispatch_revalidates_preflight_hash_before_hard_block(tmp_path):
    service_, executor, _ledger, trust_root = service(tmp_path)
    job = live_job(tmp_path)
    identity = persisted_qa(trust_root / "identity.json", job, "IDENTITY_QA")
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")
    receipt_path = tmp_path / "preflight.json"
    service_.preflight(job, adapter="fal_flux", identity_qa_path=identity,
                       assets_qa_path=assets, receipt_path=receipt_path)
    receipt_path.write_text('{"tampered":true}', encoding="utf-8")

    with pytest.raises(ValueError, match="preflight receipt hash mismatch"):
        asyncio.run(service_.dispatch(job, preflight_path=receipt_path))
    assert executor.calls == 0


def test_inline_self_attested_qa_and_bearer_evidence_are_rejected(tmp_path):
    service_, _executor, _ledger, trust_root = service(tmp_path)
    job = live_job(tmp_path)
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")

    with pytest.raises(ValueError, match="persisted"):
        service_.preflight(job, adapter="fal_flux", identity_qa_path={},
                           assets_qa_path=assets, receipt_path=tmp_path / "p.json")

    identity = persisted_qa(
        trust_root / "identity.json", job, "IDENTITY_QA",
        evidence={"method": "external-review", "note": "Bearer secret-value"},
    )
    with pytest.raises(ValueError, match="sensitive"):
        service_.preflight(job, adapter="fal_flux", identity_qa_path=identity,
                           assets_qa_path=assets, receipt_path=tmp_path / "p.json")


def test_closed_policy_and_quote_expiry_window_reject_before_preflight_write(tmp_path):
    from src.hybrid.live_execution import LiveExecutionPolicy

    extra = policy() | {"bypass_live_block": True}
    with pytest.raises(ValueError, match="closed"):
        LiveExecutionPolicy.from_dict(extra)

    service_, _executor, _ledger, trust_root = service(
        tmp_path, resolver=QuoteResolver(valid_until=4_102_444_800)
    )
    job = live_job(tmp_path)
    identity = persisted_qa(trust_root / "identity.json", job, "IDENTITY_QA")
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")
    with pytest.raises(ValueError, match="maximum window"):
        service_.preflight(job, adapter="fal_flux", identity_qa_path=identity,
                           assets_qa_path=assets, receipt_path=tmp_path / "p.json")
    assert not (tmp_path / "p.json").exists()