from __future__ import annotations

from decimal import Decimal
from hashlib import sha256

import pytest


@pytest.mark.asyncio
async def test_adapter_executes_through_factory_composition_and_persists_executor_receipt(tmp_path):
    """PR53's adapter is an execution facade, not a second admission authority."""
    from src.hybrid.bounded_operation_admission import BoundedOperationAdmission
    from src.hybrid.execution import ProviderResult
    from src.hybrid.standing_budget import StandingBudgetLedger
    from tests.unit.test_jaco_style_remediation_operation import (
        _fixture_openapi,
        _fixture_quote,
        _qualified_test_authority,
    )

    authority = _qualified_test_authority(tmp_path)
    counters = {"reserve": 0, "submit": 0, "recover": 0}

    class Transport:
        mode = "TEST"

        def __init__(self, output, _schema):
            self.output = output

        async def submit(self, _job, _request_id, checkpoint):
            counters["submit"] += 1
            checkpoint(provider_id="adapter-remote-1", partial=self.output)
            self.output.write_bytes(b"submitted")
            return ProviderResult(self.output, Decimal("0.048"))

        async def recover(self, *_args):
            counters["recover"] += 1
            return ProviderResult(self.output, Decimal("0.048"))

    adapter = BoundedOperationAdmission._for_test(root=tmp_path, authority_path=authority)
    await adapter._execute_prepared(
        quote=_fixture_quote(), openapi=_fixture_openapi(), test_provider_factory=Transport,
    )

    financial_sha = sha256((tmp_path / "financial-record-source.json").read_bytes()).hexdigest()
    ledger = StandingBudgetLedger(tmp_path / "ledger" / "standing-budget.sqlite3", policy_sha256=financial_sha, episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="TEST")
    with ledger._connect() as db:
        counters["reserve"] = db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    assert counters == {"reserve": 1, "submit": 1, "recover": 0}
    execution = tmp_path / "execution" / "JACO_GEN28_STYLE_REMEDIATION_001"
    assert (execution / "executor.sqlite3").is_file()
    assert (execution / "provider-receipt.json").read_bytes() == b"submitted"


@pytest.mark.asyncio
async def test_public_adapter_cannot_inject_authority_or_bypass_live_qualification():
    from src.hybrid.bounded_operation_admission import BoundedOperationAdmission

    with pytest.raises(TypeError):
        BoundedOperationAdmission(root="untrusted")
