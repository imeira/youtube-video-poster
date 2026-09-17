from __future__ import annotations

from decimal import Decimal

import pytest
from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.execution import Job
from src.hybrid.preflight import LivePreflightIssuer


@pytest.fixture
def live_job(tmp_path):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (64, 64), "green").save(reference)
    frozen = FrozenAsset.approve(reference, "reviewer", "LIVE")
    sheet = tmp_path / "sheet.png"
    contact_sheet((frozen,), sheet)
    manifest = Manifest.freeze((frozen,), FrozenAsset.approve(sheet, "reviewer", "LIVE"), "reviewer", "LIVE")
    return Job("SC001", "first", "LIVE", "fal-ai/flux-2/klein/9b/edit", {"prompt": "test"}, manifest, Decimal("0.02"))


class PriceResolver:
    def __init__(self, amount: Decimal):
        self.amount = amount
        self.calls: list[str] = []

    def quote(self, job: Job):
        self.calls.append(job.request_id)
        return {"endpoint": job.endpoint, "amount": self.amount, "evidence": "official-price-v1"}


def test_live_preflight_issues_exact_authority_and_persists_sanitized_receipt(tmp_path, live_job):
    resolver = PriceResolver(live_job.cost)
    issuer = LivePreflightIssuer(tmp_path / "costs.json", hard_limit=Decimal(6), price_resolver=resolver)

    result = issuer.issue([live_job], reviewer="budget-qa", receipt_path=tmp_path / "preflight.json")

    assert set(result.authorizations) == {live_job.request_id}
    assert result.prices[live_job.request_id].amount == live_job.cost
    assert resolver.calls == [live_job.request_id]
    receipt = (tmp_path / "preflight.json").read_text(encoding="utf-8")
    assert "official-price-v1" in receipt
    assert "FAL_KEY" not in receipt


def test_live_preflight_blocks_bad_quote_or_budget_overrun(tmp_path, live_job):
    wrong = LivePreflightIssuer(
        tmp_path / "costs.json", hard_limit=Decimal(6), price_resolver=PriceResolver(Decimal("0.01"))
    )
    with pytest.raises(ValueError, match="exact job cost"):
        wrong.issue([live_job], reviewer="budget-qa", receipt_path=tmp_path / "wrong.json")

    blocked = LivePreflightIssuer(
        tmp_path / "costs.json", hard_limit=Decimal("0.01"), price_resolver=PriceResolver(live_job.cost)
    )
    with pytest.raises(RuntimeError, match="budget"):
        blocked.issue([live_job], reviewer="budget-qa", receipt_path=tmp_path / "blocked.json")
