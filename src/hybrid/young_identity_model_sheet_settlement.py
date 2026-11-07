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
# This companion is for one already-generated operation only.  These are not
# caller-supplied attestations: reconciliation accepts only the raw raster and
# canonical settlement evidence recorded by the protected v3 qualification.
APPROVED_QUARANTINED_PNG_SHA256 = "5ebe417dd1b7b7d05b9eba9fd0ae5bb7f63e926b6d9946897c2b20a1d0b51933"
APPROVED_SETTLEMENT_RECEIPT_SHA256 = "4745428d97cf3de5d2b7bcd7056c5575362acba1ce82d25d2bf7c3e747e7be99"
APPROVED_EXECUTION_CODE_SHA256 = "6b7c4aeb15e1bcee528e69905a52c04182fa686d82915d84dd8f536171c66d12"


@dataclass(frozen=True)
class _Snapshot:
    factory: YoungIdentityModelSheetProduction
    ledger_path: Path
    financial_sha256: str
    execution: Path
    submission_at: datetime
    already_settled: bool


@dataclass(frozen=True)
class _LedgerSnapshot:
    """Read-only, transactional evidence before and after the one billing GET."""

    binding: str
    claims: tuple[tuple[object, ...], ...]
    receipts: tuple[tuple[object, ...], ...]


_SETTLEMENT_RECEIPT_FIELDS = {
    "schema", "authority", "local_request_id", "provider_request_id", "endpoint_id",
    "auth_scope", "authority_mode", "currency", "cost_total_micro_usd", "evidence_hash",
    "provenance",
}


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


def _readonly_ledger_snapshot(path: Path) -> _LedgerSnapshot:
    """Capture all canonical rows in one SQLite read transaction, never a writer."""
    if not path.is_file():
        raise ProductionAdmissionError("canonical standing ledger is unavailable")
    try:
        with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as db:
            db.execute("BEGIN")
            binding_rows = db.execute("SELECT binding FROM policy WHERE id=1").fetchall()
            claims = tuple(db.execute("SELECT * FROM claims ORDER BY request_id").fetchall())
            receipts = tuple(db.execute(
                "SELECT request_id, receipt_sha256, actual, receipt_body, provenance "
                "FROM settlement_receipts ORDER BY request_id"
            ).fetchall())
    except (OSError, sqlite3.Error) as exc:
        raise ProductionAdmissionError("canonical standing ledger is unavailable") from exc
    if len(binding_rows) != 1 or not isinstance(binding_rows[0][0], str):
        raise ProductionAdmissionError("canonical standing ledger policy is unavailable")
    return _LedgerSnapshot(binding_rows[0][0], claims, receipts)


def _prove_settlement(snapshot: _Snapshot, before: _LedgerSnapshot) -> None:
    """Treat durable canonical readback, not a service return value, as settlement proof."""
    after = _readonly_ledger_snapshot(snapshot.ledger_path)
    try:
        policy = json.loads(after.binding)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ProductionAdmissionError("canonical standing ledger policy changed") from exc
    if policy.get("mode") != "LIVE" or policy.get("currency") != "USD":
        raise ProductionAdmissionError("canonical standing ledger policy changed")

    # The protected ledger has exactly two immutable historical settlements.
    before_claims = {row[0]: row for row in before.claims}
    before_receipts = {row[0]: row for row in before.receipts}
    historical_ids = set(before_claims) - {LOCAL_REQUEST_ID}
    current_before = before_claims.get(LOCAL_REQUEST_ID)
    current_was_settled = current_before is not None and current_before[6] == "SETTLED"
    expected_before_receipts = historical_ids | ({LOCAL_REQUEST_ID} if current_was_settled else set())
    if (
        len(before_claims) != 3 or len(historical_ids) != 2
        or set(before_receipts) != expected_before_receipts
        or len(before_receipts) != (3 if current_was_settled else 2)
        or any(row[6] != "SETTLED" or row[7] is None for key, row in before_claims.items() if key != LOCAL_REQUEST_ID)
        or (current_before is None or current_before[6] not in {"RESERVED", "SETTLED"})
        or (current_was_settled and current_before[7] is None)
    ):
        raise ProductionAdmissionError("canonical historical settlement snapshot is invalid")
    after_claims = {row[0]: row for row in after.claims}
    after_receipts = {row[0]: row for row in after.receipts}
    if (
        len(after_claims) != len(after.claims) or len(after_receipts) != len(after.receipts)
        or set(after_claims) != set(before_claims)
        or set(after_receipts) != historical_ids | {LOCAL_REQUEST_ID}
        or len(after.receipts) != 3
        or any(after_claims[key] != before_claims[key] for key in historical_ids)
        or any(after_receipts[key] != before_receipts[key] for key in historical_ids)
        or (current_was_settled and (
            after_claims[LOCAL_REQUEST_ID] != current_before
            or after_receipts.get(LOCAL_REQUEST_ID) != before_receipts.get(LOCAL_REQUEST_ID)
        ))
    ):
        raise ProductionAdmissionError("canonical settlement history changed")

    current = after_claims.get(LOCAL_REQUEST_ID)
    receipt_row = after_receipts.get(LOCAL_REQUEST_ID)
    if current is None or receipt_row is None or sum(row[7] for row in after_claims.values()) != 144_000:
        raise ProductionAdmissionError("canonical current settlement receipt is unavailable")
    # claims columns are the canonical StandingBudgetLedger schema; do not infer from a service result.
    if (
        current[6] != "SETTLED" or current[7] is None
        or current[8] != "FAL_READBACK_V2" or current[10] != REMOTE_REQUEST_ID
        or current[11] != "LIVE" or receipt_row[4] != "FAL_BILLING_READBACK"
    ):
        raise ProductionAdmissionError("canonical current settlement binding mismatch")
    try:
        receipt = json.loads(receipt_row[3])
        canonical = json.dumps(receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProductionAdmissionError("canonical current settlement receipt is invalid") from exc
    if (
        not isinstance(receipt, dict) or set(receipt) != _SETTLEMENT_RECEIPT_FIELDS
        or receipt_row[3] != canonical or receipt_row[1] != sha256(canonical.encode("utf-8")).hexdigest()
        or receipt_row[1] != APPROVED_SETTLEMENT_RECEIPT_SHA256
        or receipt.get("schema") != 2 or receipt.get("authority") != "TRANSPORT_AUTHENTICATED_FAL_READBACK"
        or receipt.get("local_request_id") != LOCAL_REQUEST_ID
        or receipt.get("provider_request_id") != REMOTE_REQUEST_ID
        or receipt.get("authority_mode") != "LIVE" or receipt.get("currency") != "USD"
        or receipt.get("cost_total_micro_usd") != str(current[7])
        or receipt.get("provenance") != "FAL_BILLING_READBACK"
    ):
        raise ProductionAdmissionError("canonical current settlement receipt is invalid")
    if any(count != 0 for count, in _receipt_counts(snapshot.ledger_path)):
        raise ProductionAdmissionError("canonical settlement receipt is duplicated or orphaned")


def _receipt_counts(path: Path) -> tuple[tuple[int], ...]:
    """Reject duplicate, orphan, and receipt-less settled rows before recovery."""
    try:
        with sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True) as db:
            db.execute("BEGIN")
            return tuple(db.execute(
                "SELECT COUNT(*) FROM ("
                "SELECT request_id FROM settlement_receipts GROUP BY request_id HAVING COUNT(*) != 1 "
                "UNION ALL SELECT r.request_id FROM settlement_receipts r LEFT JOIN claims c ON c.request_id=r.request_id WHERE c.request_id IS NULL "
                "UNION ALL SELECT c.request_id FROM claims c LEFT JOIN settlement_receipts r ON r.request_id=c.request_id WHERE c.state='SETTLED' AND r.request_id IS NULL"
                ")"
            ).fetchall())
    except (OSError, sqlite3.Error) as exc:
        raise ProductionAdmissionError("canonical standing ledger is unavailable") from exc


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
    execution_code_sha256 = sha256(Path(production.__file__).read_bytes()).hexdigest()
    if execution_code_sha256 != APPROVED_EXECUTION_CODE_SHA256:
        raise ProductionAdmissionError("protected source-code qualification changed")
    return ModelSheetV3BillingVerifier(
        root=factory.root, execution_root=execution, authority_path=factory.authority_path,
        policy_path=Path(authority["policy_path"]), preflight_path=Path(authority["preflight_path"]),
        qualification_path=Path(authority["deployment_qualification"]["path"]),
        financial_source_path=factory.root / "financial-record-source.json",
        authority_sha256=sha256(factory.authority_path.read_bytes()).hexdigest(),
        execution_code_sha256=execution_code_sha256,
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
        verifier = _verifier(factory, authority, execution)
        if claim["state"] == "RESERVED":
            verifier.verify(receipt, receipt.get("settlement_binding"), claim)
        elif claim["state"] != "SETTLED":
            raise ProductionAdmissionError("durable model-sheet claim is not reserved or settled")
    except Exception as exc:
        raise ProductionAdmissionError("durable model-sheet settlement contract changed") from exc
    return _Snapshot(
        factory, ledger_path, financial_sha, execution,
        _submission_timestamp(execution / "provider-receipt.recovery.json"),
        claim["state"] == "SETTLED",
    )


def _construct_service(snapshot: _Snapshot, credential: str) -> FalLedgerSettlementService:
    """Only valid existing durable stores may now be opened by mutable wrappers."""
    ledger = StandingBudgetLedger(snapshot.ledger_path, policy_sha256=snapshot.financial_sha256, episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="LIVE")
    executor = Executor(snapshot.execution / "executor.sqlite3", Config(limit=Decimal(10)), standing_ledger=ledger)
    authority, _policy, _preflight = snapshot.factory._authority()
    return FalLedgerSettlementService(ledger, executor, FalBillingConsumer(AiohttpFalBillingClient(credential)), jaco_final_admission_verifier=_verifier(snapshot.factory, authority, snapshot.execution))


async def _recover_after_settlement(snapshot: _Snapshot) -> dict:
    """Complete from canonical settlement and quarantined bytes; no provider call."""
    authority, _policy, preflight = snapshot.factory._authority()
    receipt_path = snapshot.execution / "provider-receipt.json"
    media = snapshot.execution / "provider-receipt.quarantine.png"
    try:
        provider_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        media_sha256 = sha256(media.read_bytes()).hexdigest()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ProductionAdmissionError("durable quarantined model-sheet media is unavailable") from exc
    if (
        media_sha256 != APPROVED_QUARANTINED_PNG_SHA256
        or provider_receipt != {"sha256": media_sha256, "kind": "PNG_RESULT", "actual_billed_amount": "UNKNOWN_UNTIL_BILLING_RECONCILIATION"}
    ):
        raise ProductionAdmissionError("durable quarantined model-sheet media changed")
    ledger = StandingBudgetLedger(snapshot.ledger_path, policy_sha256=snapshot.financial_sha256,
                                  episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="LIVE")
    verifier = _verifier(snapshot.factory, authority, snapshot.execution)
    holder = {}
    def complete_verifier(job, _marker, claim):
        row = holder["executor"].inspect(job.request_id)
        verifier.verify_completion(row, row.get("settlement_binding"), claim)
    executor = Executor(snapshot.execution / "executor.sqlite3", Config(limit=Decimal(10)),
                        standing_ledger=ledger, _jaco_final_admission_verifier=complete_verifier,
                        _jaco_production_binding={
                            "kind": "JACO_FLUX3_FIXED_PRODUCTION_COMPOSITION",
                            "auth_scope": f"{OPERATION_ID}:{authority['policy_sha256']}",
                        })
    holder["executor"] = executor
    job = __import__("src.hybrid.execution", fromlist=["Job"]).Job(
        OPERATION_ID, authority["executor_category"], "LIVE", preflight["operation"]["endpoint"],
        preflight["operation"]["payload"], None, Decimal("0.048"),
        predecessor=authority["executor_predecessor_request_id"], input_kind="TEXT_TO_IMAGE",
    )
    completed = executor.complete_settled_recovery(job, media)
    return {"status": completed["status"], "request_id": completed["request_id"],
            "result_sha256": completed["result_sha256"]}


async def reconcile() -> dict:
    snapshot = _readonly_snapshot()
    before = _readonly_ledger_snapshot(snapshot.ledger_path)
    # A pre-existing SETTLED row is a GET-only replay only after reopening and
    # proving its canonical receipt/history.  It must not resolve credentials,
    # construct transport, quote, reserve, or issue HTTP.
    if getattr(snapshot, "already_settled", False):
        _prove_settlement(snapshot, before)
        return {
            "request_id": LOCAL_REQUEST_ID,
            "status": "SETTLED",
            "completion": await _recover_after_settlement(snapshot),
        }
    end = _utc_now()
    if end <= snapshot.submission_at:
        raise ProductionAdmissionError("current UTC end does not cover durable submission")
    credential, _presence = resolve_default_fal_credential()
    settled = await _construct_service(snapshot, credential).reconcile(LOCAL_REQUEST_ID, start=snapshot.submission_at, end=end)
    if settled.get("status") != "SETTLED":
        return settled
    _prove_settlement(snapshot, before)
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