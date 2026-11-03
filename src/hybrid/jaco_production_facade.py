"""The single Jacó production facade for policy dispatch and local status.

This facade deliberately composes existing durable components.  It does not own a
provider, queue, executor, QA reviewer, renderer, or approval gate.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from src.hybrid.canonical_status import CanonicalStatusProducer
from src.hybrid.jaco_asset_integration import EPISODE, REQUEST_ID, REVISION
from src.hybrid.status_metrics import StatusMetricsLedger


class JacoProductionFacade:
    """Project only exact durable executor receipts into canonical operator status."""

    _SOURCES = ("manifest", "policy_qa", "ledger", "provider_eligibility")

    def __init__(self, factory_root: Path | str):
        self.factory_root = Path(factory_root)
        self._root = self.factory_root / "requests" / REQUEST_ID / "jaco-asset-integration"
        self._source_root = self._root / "canonical-status-sources"
        self._metrics = StatusMetricsLedger(self._root / "status-metrics.sqlite3")

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

    @staticmethod
    def _hash(value: object) -> str:
        return sha256(JacoProductionFacade._canonical(value)).hexdigest()

    def _write_source(self, name: str, document: dict[str, Any]) -> dict[str, str]:
        self._source_root.mkdir(parents=True, exist_ok=True)
        target = self._source_root / f"{name}.json"
        encoded = self._canonical(document)
        with NamedTemporaryFile("wb", dir=self._source_root, prefix=f".{name}.", delete=False) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        return {"path": str(target), "sha256": sha256(target.read_bytes()).hexdigest()}

    @staticmethod
    def _money(value: object, field: str) -> Decimal:
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError) as error:
            raise ValueError(f"durable executor {field} is invalid") from error
        if not amount.is_finite() or amount < 0:
            raise ValueError(f"durable executor {field} is invalid")
        return amount

    @staticmethod
    def _receipt(executor, request_id: str) -> dict[str, Any]:
        receipt = executor.inspect(request_id)
        required = {"request_id", "status", "mode", "result_sha256", "actual_cost", "charged"}
        if (
            not isinstance(receipt, dict)
            or not required.issubset(receipt)
            or receipt.get("request_id") != request_id
            or receipt.get("status") != "COMPLETE"
            or receipt.get("mode") != "LIVE"
            or not isinstance(receipt.get("result_sha256"), str)
            or len(receipt["result_sha256"]) != 64
        ):
            raise ValueError("canonical status requires exact completed LIVE executor receipt")
        JacoProductionFacade._money(receipt["actual_cost"], "actual_cost")
        JacoProductionFacade._money(receipt["charged"], "charged")
        return receipt

    def publish_from_executor(self, pipeline) -> dict[str, Any]:
        """Publish status from the durable executor ledger, never caller verdicts.

        The projection stays local/non-authoritative; a completed receipt proves
        neither provider billing readback nor human approval.
        """
        executor = getattr(getattr(pipeline, "run", None), "executor", None)
        jobs_method = getattr(pipeline, "baseline_jobs", None)
        if executor is None or not callable(jobs_method):
            raise TypeError("compiled pipeline with durable executor is required")
        jobs = tuple(jobs_method())
        if not jobs:
            raise ValueError("canonical status requires exact compiled baseline jobs")
        request_ids = []
        receipts = []
        for job in jobs:
            request_id = getattr(job, "request_id", None)
            if not isinstance(request_id, str) or len(request_id) != 64 or request_id in request_ids:
                raise ValueError("compiled baseline request identity is invalid")
            request_ids.append(request_id)
            receipts.append(self._receipt(executor, request_id))

        qa_pass = all(receipt.get("qa") is True for receipt in receipts)
        admissions = [receipt.get("authorization") for receipt in receipts]
        eligible = all(
            isinstance(admission, dict)
            and admission.get("reviewer") == "CURRENT_POLICY_MACHINE_ADMISSION"
            for admission in admissions
        )
        actual_cost = sum((self._money(receipt["actual_cost"], "actual_cost") for receipt in receipts), Decimal(0))
        pending_cost = sum((self._money(receipt["charged"], "charged") for receipt in receipts), Decimal(0)) - actual_cost
        scope = {"request_id": REQUEST_ID, "active_revision": REVISION, "episode": EPISODE}
        sources = {
            "manifest": self._write_source("manifest", {
                "schema": "manifest/v1", "scope": scope,
                "assets": [{"request_id": receipt["request_id"], "result_sha256": receipt["result_sha256"]} for receipt in receipts],
            }),
            "policy_qa": self._write_source("policy_qa", {
                "schema": "policy-qa/v1", "scope": scope,
                "verdict": "PASS" if qa_pass else "PENDING",
                "receipt_hashes": [self._hash(receipt) for receipt in receipts],
            }),
            "ledger": self._write_source("ledger", {
                "schema": "ledger/v1", "scope": scope,
                "actual_cost_usd": str(actual_cost), "pending_cost_usd": str(pending_cost),
                "receipt_hashes": [self._hash(receipt) for receipt in receipts],
            }),
            "provider_eligibility": self._write_source("provider_eligibility", {
                "schema": "provider-eligibility/v1", "scope": scope,
                "status": "ELIGIBLE" if eligible else "BLOCKED",
                "policy_admission_receipt_hashes": [self._hash(admission) if isinstance(admission, dict) else None for admission in admissions],
            }),
        }
        for receipt in receipts:
            self._metrics.record_executor_receipt(receipt)
        status = CanonicalStatusProducer(self.factory_root).publish(sources)
        return {"status": status, "metrics": self._metrics.report()}
