"""GET-only authenticated billing reconciliation for the completed model sheet.

This companion deliberately has no reserve, submit, image, or authority-writing
path.  It reopens the protected v3 qualification and the one durable request
before resolving credentials, then delegates the single billing readback to the
existing typed FAL settlement service.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from src.hybrid.billing_reconciler import (
    FalLedgerSettlementService,
    ModelSheetV3BillingVerifier,
)
from src.hybrid.execution import Executor
from src.hybrid.fal_billing import AiohttpFalBillingClient, FalBillingConsumer
from src.hybrid.jaco_flux3_production import resolve_default_fal_credential
from src.hybrid.planner import Config
from src.hybrid.standing_budget import StandingBudgetLedger
from src.hybrid.young_identity_model_sheet_production import (
    ENDPOINT,
    OPERATION_ID,
    ProductionAdmissionError,
    YoungIdentityModelSheetProduction,
)

LOCAL_REQUEST_ID = "c81bec2124005d306d2f843ac7b5d5c747feb254d090610582d79099f0535cc1"
REMOTE_REQUEST_ID = "01a1b7bf-ef29-77c1-ad38-5214baa197de"


def _utc_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an ISO-8601 UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != UTC.utcoffset(parsed):
        raise argparse.ArgumentTypeError("must be an ISO-8601 UTC timestamp")
    return parsed


def _protected_components() -> tuple[StandingBudgetLedger, Executor, ModelSheetV3BillingVerifier]:
    """Open only the installed qualified operation; never mutate it here."""
    factory = YoungIdentityModelSheetProduction()
    authority, _policy, _preflight = factory._authority()
    qualification = factory._qualification(authority)
    if qualification["mode"] != "LIVE":
        raise ProductionAdmissionError("production billing requires LIVE qualification")
    ledger_path, financial_sha, _financial = factory._ledger_binding(authority)
    execution = factory.root / "execution" / OPERATION_ID
    if factory._execution_phase(execution) != "RECOVERY":
        raise ProductionAdmissionError("durable model-sheet execution receipt is unavailable")
    verifier = ModelSheetV3BillingVerifier(
        root=factory.root,
        execution_root=execution,
        authority_path=factory.authority_path,
        policy_path=Path(authority["policy_path"]),
        preflight_path=Path(authority["preflight_path"]),
        qualification_path=Path(authority["deployment_qualification"]["path"]),
        financial_source_path=factory.root / "financial-record-source.json",
        authority_sha256=sha256(factory.authority_path.read_bytes()).hexdigest(),
        execution_code_sha256=qualification["execution_code_sha256"],
    )
    ledger = StandingBudgetLedger(
        ledger_path,
        policy_sha256=financial_sha,
        episode_limit_usd="10",
        month_limit_usd="10",
        experiment_limit_usd="10",
        mode="LIVE",
    )
    executor = Executor(
        execution / "executor.sqlite3", Config(limit=Decimal(10)), standing_ledger=ledger,
    )
    receipt = executor.inspect(LOCAL_REQUEST_ID)
    binding = receipt.get("settlement_binding") if isinstance(receipt, dict) else None
    if not isinstance(binding, dict) or binding.get("provider_request_id") != REMOTE_REQUEST_ID:
        raise ProductionAdmissionError("durable model-sheet provider receipt binding changed")
    if binding.get("endpoint_id") != ENDPOINT:
        raise ProductionAdmissionError("durable model-sheet endpoint binding changed")
    return ledger, executor, verifier


def settlement_service(consumer: FalBillingConsumer) -> FalLedgerSettlementService:
    """Compose the existing typed service from fixed, read-only production state."""
    ledger, executor, verifier = _protected_components()
    return FalLedgerSettlementService(
        ledger, executor, consumer, jaco_final_admission_verifier=verifier,
    )


async def reconcile(*, start: datetime, end: datetime) -> dict:
    """Resolve a credential only after the full durable contract is validated."""
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
        raise ProductionAdmissionError("--reconcile with exact --start and --end is required")
    print(json.dumps(asyncio.run(reconcile(start=args.start, end=args.end)), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
