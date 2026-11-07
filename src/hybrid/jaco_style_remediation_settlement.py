"""GET-only reconciliation for the one persisted Jacó style-remediation claim.

This is intentionally separate from the execution composition whose source hash
is pinned by the deployed qualification.  It has no reservation or provider
submission path.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from src.hybrid.billing_reconciler import (
    FalLedgerSettlementService,
    JacoFinalAdmissionContractVerifier,
)
from src.hybrid.execution import Executor
from src.hybrid.fal_billing import AiohttpFalBillingClient, FalBillingConsumer
from src.hybrid.jaco_flux3_production import resolve_default_fal_credential
from src.hybrid.jaco_style_remediation_operation import (
    OPERATION_ID,
    POLICY_PATH,
    OperationAdmissionError,
    OperationAdmissionFactory,
)
from src.hybrid.planner import Config
from src.hybrid.standing_budget import StandingBudgetLedger

LOCAL_REQUEST_ID = "27ef6a66be109984c208daa6e053e4a9d4f51a0202b46d624ea964336f74d052"
STYLE_AUTHORITY_SHA256 = "b7e0841a93ab2d112d7ddbfff8df2091854ccda061c7315ad8a269d654503cf9"


def _utc_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise argparse.ArgumentTypeError("must be an ISO-8601 UTC timestamp")
    return parsed


def _protected_components() -> tuple[StandingBudgetLedger, Executor, JacoFinalAdmissionContractVerifier]:
    """Reopen every protected binding before credential or transport setup."""
    factory = OperationAdmissionFactory()
    authority, _policy, _preflight = factory._authority()
    qualification = factory._qualification(authority)
    if qualification["mode"] != "LIVE":
        raise OperationAdmissionError("production billing requires LIVE qualification")
    ledger_path, financial_policy_sha, _financial = factory._canonical_ledger_binding(authority)
    execution = factory.root / "execution" / OPERATION_ID
    executor_path = execution / "executor.sqlite3"
    if not executor_path.is_file():
        raise OperationAdmissionError("protected successor executor receipt is unavailable")
    verifier = JacoFinalAdmissionContractVerifier(
        root=factory.root,
        execution_root=execution,
        style_authority_path=factory.authority_path,
        style_qualification_path=(
            factory.root / "standing-policy" / "JACO_GEN28_STYLE_REMEDIATION_001.qualification.v2.json"
        ),
        style_policy_path=factory.root / "standing-policy" / POLICY_PATH.name,
        financial_source_path=factory.root / "financial-record-source.json",
        style_authority_sha256=STYLE_AUTHORITY_SHA256,
        execution_code_path=Path(__import__(
            "src.hybrid.jaco_style_remediation_operation", fromlist=["__file__"]
        ).__file__),
    )
    ledger = StandingBudgetLedger(
        ledger_path,
        policy_sha256=financial_policy_sha,
        episode_limit_usd="10",
        month_limit_usd="10",
        experiment_limit_usd="10",
        mode="LIVE",
    )
    executor = Executor(executor_path, Config(limit=Decimal(10)), standing_ledger=ledger)
    return ledger, executor, verifier


def settlement_service(consumer: FalBillingConsumer) -> FalLedgerSettlementService:
    """Compose a GET-only settlement service from protected durable state."""
    ledger, executor, verifier = _protected_components()
    return FalLedgerSettlementService(
        ledger, executor, consumer, jaco_final_admission_verifier=verifier
    )


async def reconcile(*, start: datetime, end: datetime) -> dict:
    """Resolve the credential only after protected composition is complete."""
    ledger, executor, verifier = _protected_components()
    credential, _presence = resolve_default_fal_credential()
    service = FalLedgerSettlementService(
        ledger,
        executor,
        FalBillingConsumer(AiohttpFalBillingClient(credential)),
        jaco_final_admission_verifier=verifier,
    )
    return await service.reconcile(LOCAL_REQUEST_ID, start=start, end=end)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--start", type=_utc_timestamp)
    parser.add_argument("--end", type=_utc_timestamp)
    args = parser.parse_args(argv)
    if not args.reconcile or args.start is None or args.end is None:
        raise OperationAdmissionError("--reconcile with exact --start and --end is required")
    print(json.dumps(asyncio.run(reconcile(start=args.start, end=args.end)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
