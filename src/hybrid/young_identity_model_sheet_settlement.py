"""GET-only billing settlement guarded by durable read-only evidence."""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from dataclasses import dataclass
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
    OPERATION_ID,
    ProductionAdmissionError,
    YoungIdentityModelSheetProduction,
)

LOCAL_REQUEST_ID = "c81bec2124005d306d2f843ac7b5d5c747feb254d090610582d79099f0535cc1"
REMOTE_REQUEST_ID = "01a1b7bf-ef29-77c1-ad38-5214baa197de"


@dataclass(frozen=True)
class _Snapshot:
    factory: YoungIdentityModelSheetProduction
    ledger_path: Path
    financial_sha256: str
    execution: Path
    submission_at: datetime


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _one(path: Path, sql: str, args: tuple[object, ...], label: str):
    if not path.is_file():
        raise ProductionAdmissionError(f"durable {label} database is unavailable")
    try:
        with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as db:
            rows = db.execute(sql, args).fetchall()
    except (OSError, sqlite3.Error) as exc:
        raise ProductionAdmissionError(f"durable {label} database is unavailable") from exc
    if len(rows) != 1:
        raise ProductionAdmissionError(f"durable {label} receipt is unavailable")
    return rows[0]


def _submission_timestamp(path: Path) -> datetime:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
        stamp = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ProductionAdmissionError("durable submission receipt is unavailable") from exc
    expected = {"schema": 1, "provider_request_id": REMOTE_REQUEST_ID,
                "status_url": f"https://queue.fal.run/blackforestlabs/flux-3/requests/{REMOTE_REQUEST_ID}/status",
                "response_url": f"https://queue.fal.run/blackforestlabs/flux-3/requests/{REMOTE_REQUEST_ID}"}
    if receipt != expected:
        raise ProductionAdmissionError("durable submission receipt changed")
    return stamp


def _verifier(factory, authority, execution):
    production = __import__("src.hybrid.young_identity_model_sheet_production", fromlist=["x"])
    return ModelSheetV3BillingVerifier(
        root=factory.root, execution_root=execution, authority_path=factory.authority_path,
        policy_path=Path(authority["policy_path"]), preflight_path=Path(authority["preflight_path"]),
        qualification_path=Path(authority["deployment_qualification"]["path"]),
        financial_source_path=factory.root / "financial-record-source.json",
        authority_sha256=sha256(factory.authority_path.read_bytes()).hexdigest(),
        execution_code_sha256=sha256(Path(production.__file__).read_bytes()).hexdigest(),
    )


def _readonly_snapshot() -> _Snapshot:
    """Validate exact durable inputs before any mutable constructor or credential."""
    factory = YoungIdentityModelSheetProduction()
    authority, _policy, _preflight = factory._authority()
    if factory._qualification(authority)["mode"] != "LIVE":
        raise ProductionAdmissionError("production billing requires LIVE qualification")
    ledger_path, financial_sha, _financial = factory._ledger_binding(authority)
    execution = factory.root / "execution" / OPERATION_ID
    if factory._execution_phase(execution) != "RECOVERY":
        raise ProductionAdmissionError("durable model-sheet execution receipt is unavailable")
    row = _one(execution / "executor.sqlite3", "SELECT data FROM jobs WHERE id=?", (LOCAL_REQUEST_ID,), "executor")
    try:
        receipt = json.loads(row[0])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProductionAdmissionError("durable executor receipt is unavailable") from exc
    if not isinstance(receipt, dict) or receipt.get("status") not in {"SUBMITTED", "COMPLETE"}:
        raise ProductionAdmissionError("durable executor receipt is unavailable")
    claim_row = _one(ledger_path, "SELECT request_id, episode_id, provider, cap, state, actual, settlement_mode, auth_scope, provider_request_id, authority_mode FROM claims WHERE request_id=?", (LOCAL_REQUEST_ID,), "standing ledger")
    claim = {"request_id": claim_row[0], "episode_id": claim_row[1], "provider": claim_row[2],
             "reserved_usd": f"{Decimal(claim_row[3]) / 1_000_000:.6f}", "state": claim_row[4],
             "actual_usd": None if claim_row[5] is None else f"{Decimal(claim_row[5]) / 1_000_000:.6f}",
             "settlement_mode": claim_row[6], "auth_scope": claim_row[7], "provider_request_id": claim_row[8],
             "authority_mode": claim_row[9], "currency": "USD"}
    try:
        _verifier(factory, authority, execution).verify(receipt, receipt.get("settlement_binding"), claim)
    except Exception as exc:
        raise ProductionAdmissionError("durable model-sheet settlement contract changed") from exc
    return _Snapshot(factory, ledger_path, financial_sha, execution, _submission_timestamp(execution / "provider-receipt.recovery.json"))


def _construct_service(snapshot: _Snapshot, credential: str) -> FalLedgerSettlementService:
    """Only valid existing durable stores may now be opened by mutable wrappers."""
    ledger = StandingBudgetLedger(snapshot.ledger_path, policy_sha256=snapshot.financial_sha256, episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="LIVE")
    executor = Executor(snapshot.execution / "executor.sqlite3", Config(limit=Decimal(10)), standing_ledger=ledger)
    authority, _policy, _preflight = snapshot.factory._authority()
    return FalLedgerSettlementService(ledger, executor, FalBillingConsumer(AiohttpFalBillingClient(credential)), jaco_final_admission_verifier=_verifier(snapshot.factory, authority, snapshot.execution))


async def _recover_after_settlement(snapshot: _Snapshot) -> dict:
    """Delegate completion solely to the pre-existing public GET-only recovery path."""
    result = await snapshot.factory._execute_with_composition(
        quote_and_openapi=lambda _credential: (_ for _ in ()).throw(ProductionAdmissionError("recovery must not quote, review, or POST")),
        credential_loader=lambda: resolve_default_fal_credential()[0],
    )
    return {"status": result.get("status"), "request_id": result.get("request_id")}


async def reconcile() -> dict:
    snapshot = _readonly_snapshot()
    end = _utc_now()
    if end <= snapshot.submission_at:
        raise ProductionAdmissionError("current UTC end does not cover durable submission")
    credential, _presence = resolve_default_fal_credential()
    settled = await _construct_service(snapshot, credential).reconcile(LOCAL_REQUEST_ID, start=snapshot.submission_at, end=end)
    if settled.get("status") != "SETTLED":
        return settled
    return {**settled, "completion": await _recover_after_settlement(snapshot)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reconcile", action="store_true")
    args = parser.parse_args(argv)
    if not args.reconcile:
        parser.error("--reconcile is required; the billing window is automatic")
    print(json.dumps(asyncio.run(reconcile()), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())