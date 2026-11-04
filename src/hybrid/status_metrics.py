"""Measured-only status metrics with a JSON-only snapshot consumer."""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from src.hybrid.locks import file_lock


class StatusMetricsLedger:
    _SNAPSHOT_SCHEMA = "status-measurements-snapshot/v1"

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.snapshot_path = self.path.with_suffix(".snapshot.json")
        self.lock_path = self.path.with_suffix(".status-metrics.lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.lock_path), self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS status_measurements (event_id TEXT PRIMARY KEY, request_id TEXT NOT NULL, stage TEXT NOT NULL, queue_wait_s REAL, stage_latency_s REAL, actual_cost_usd TEXT, accepted_asset_count INTEGER NOT NULL, retries INTEGER NOT NULL, tokens_input INTEGER NOT NULL, quota_units INTEGER NOT NULL, status TEXT NOT NULL, payload_sha256 TEXT NOT NULL)")
            self._publish(db)

    def _db(self):
        return sqlite3.connect(self.path, timeout=30)

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()

    @staticmethod
    def _finite(value: object, name: str, *, nullable: bool = True) -> float | None:
        if value is None and nullable:
            return None
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            raise ValueError(f"{name} must be an actual nonnegative finite measurement")
        return float(value)

    @staticmethod
    def _count(value: object, name: str) -> int:
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be an actual nonnegative integer")
        return value

    def _publish(self, db) -> None:
        report = self._report(list(db.execute("SELECT * FROM status_measurements")))
        document = {"schema": self._SNAPSHOT_SCHEMA, "status": "CURRENT", "generated_at_utc": datetime.now(UTC).isoformat(), "report": report}
        document["snapshot_sha256"] = sha256(self._canonical(document)).hexdigest()
        with NamedTemporaryFile("wb", dir=self.snapshot_path.parent, prefix=".status-metrics.", delete=False) as stream:
            stream.write(self._canonical(document)); stream.flush(); os.fsync(stream.fileno()); temporary = Path(stream.name)
        try:
            os.replace(temporary, self.snapshot_path)
        finally:
            temporary.unlink(missing_ok=True)

    def record_executor_receipt(self, receipt: dict[str, Any]) -> bool:
        """Record one exact durable executor receipt without caller-made metrics.

        Executor receipts do not measure queue/stage latency, token use, or quota
        consumption, so those values remain unknown rather than being invented.
        """
        required = {"request_id", "status", "result_sha256", "actual_cost"}
        if not isinstance(receipt, dict) or not required.issubset(receipt):
            raise ValueError("durable executor receipt is required")
        request_id, result_sha256 = receipt["request_id"], receipt["result_sha256"]
        if (
            not isinstance(request_id, str) or not request_id
            or not isinstance(result_sha256, str) or len(result_sha256) != 64
            or receipt["status"] != "COMPLETE"
        ):
            raise ValueError("completed executor receipt identity is invalid")
        return self.record({
            "event_id": f"executor-receipt:{request_id}:{result_sha256}",
            "request_id": request_id,
            "stage": "executor_receipt",
            "queue_wait_s": None,
            "stage_latency_s": None,
            "actual_cost_usd": receipt["actual_cost"],
            "accepted_asset_count": 0,
            "retries": 0,
            "tokens_input": 0,
            "quota_units": 0,
            "status": "COMPLETE",
        })

    def record(self, measurement: dict[str, Any]) -> bool:
        required = {"event_id", "request_id", "stage", "queue_wait_s", "stage_latency_s", "actual_cost_usd", "accepted_asset_count", "retries", "tokens_input", "quota_units", "status"}
        if not isinstance(measurement, dict) or set(measurement) != required:
            raise ValueError("closed measured metrics schema required")
        for name in ("event_id", "request_id", "stage"):
            if not isinstance(measurement[name], str) or not measurement[name]:
                raise ValueError(f"{name} is required")
        if measurement["status"] not in {"PENDING", "COMPLETE", "UNKNOWN"}:
            raise ValueError("closed status required")
        actual_cost = measurement["actual_cost_usd"]
        if actual_cost is not None:
            try:
                amount = Decimal(str(actual_cost))
            except Exception as error:
                raise ValueError("actual_cost_usd must be finite") from error
            if not amount.is_finite() or amount < 0:
                raise ValueError("actual_cost_usd must be finite")
            actual_cost = str(amount)
        row = {**measurement, "queue_wait_s": self._finite(measurement["queue_wait_s"], "queue_wait_s"), "stage_latency_s": self._finite(measurement["stage_latency_s"], "stage_latency_s"), "actual_cost_usd": actual_cost}
        for name in ("accepted_asset_count", "retries", "tokens_input", "quota_units"):
            row[name] = self._count(row[name], name)
        payload = sha256(self._canonical(row)).hexdigest()
        with file_lock(self.lock_path), self._db() as db:
            existing = db.execute("SELECT payload_sha256 FROM status_measurements WHERE event_id=?", (row["event_id"],)).fetchone()
            if existing:
                if existing[0] != payload:
                    raise ValueError("event identity conflict")
                self._publish(db)
                return False
            db.execute("INSERT INTO status_measurements VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (row["event_id"], row["request_id"], row["stage"], row["queue_wait_s"], row["stage_latency_s"], row["actual_cost_usd"], row["accepted_asset_count"], row["retries"], row["tokens_input"], row["quota_units"], row["status"], payload))
            db.commit(); self._publish(db)
        return True

    @staticmethod
    def _report(rows) -> dict[str, Any]:
        values = [dict(zip(("event_id", "request_id", "stage", "queue_wait_s", "stage_latency_s", "actual_cost_usd", "accepted_asset_count", "retries", "tokens_input", "quota_units", "status", "payload_sha256"), row)) for row in rows]
        costs = [Decimal(value["actual_cost_usd"]) for value in values if value["actual_cost_usd"] is not None]
        return {"events": len(values), "stage_latency_s": sum(value["stage_latency_s"] or 0 for value in values) if any(value["stage_latency_s"] is not None for value in values) else None, "queue_wait_s": sum(value["queue_wait_s"] or 0 for value in values) if any(value["queue_wait_s"] is not None for value in values) else None, "actual_cost_usd": str(sum(costs, Decimal(0))) if costs else None, "pending_requests": sum(value["status"] == "PENDING" for value in values), "unknown_requests": sum(value["status"] == "UNKNOWN" for value in values), "accepted_asset_count": sum(value["accepted_asset_count"] for value in values), "retries": sum(value["retries"] for value in values), "tokens_input": sum(value["tokens_input"] for value in values), "quota_units": sum(value["quota_units"] for value in values)}

    def report(self) -> dict[str, Any]:
        with self._db() as db:
            return self._report(list(db.execute("SELECT * FROM status_measurements")))


def benchmark_rolling_vs_barrier(fake_provider_latency_s: list[float], *, cache_hits: set[int] = frozenset()) -> dict[str, Any]:
    """Measure local wall time using only explicit TEST fake provider delays.

    This benchmark never creates a provider, transport, or billing record.  Its
    elapsed times measure local sleep scheduling only and cannot substantiate a
    LIVE latency or financial claim.
    """
    if any(type(value) not in {int, float} or value < 0 or not math.isfinite(value) for value in fake_provider_latency_s):
        raise ValueError("fake TEST latencies must be finite nonnegative numbers")
    effective = [0.0 if index in cache_hits else float(value) for index, value in enumerate(fake_provider_latency_s)]

    def delay(seconds: float) -> None:
        time.sleep(seconds)

    barrier_started = time.perf_counter()
    for latency in effective:
        delay(latency)
    barrier_wall_time_s = time.perf_counter() - barrier_started

    rolling_started = time.perf_counter()
    if effective:
        with ThreadPoolExecutor(max_workers=len(effective), thread_name_prefix="test-fake-delay") as pool:
            tuple(pool.map(delay, effective))
    rolling_wall_time_s = time.perf_counter() - rolling_started
    return {
        "mode": "TEST_ONLY_FAKE_PROVIDER_LATENCY",
        "benchmark_scope": "LOCAL_WALL_TIME_WITH_FAKE_PROVIDER_DELAY",
        "barrier_elapsed_s": sum(effective),
        "rolling_elapsed_s": max(effective, default=0.0),
        "barrier_wall_time_s": barrier_wall_time_s,
        "rolling_wall_time_s": rolling_wall_time_s,
        "cache_hits": len(cache_hits),
        "real_cost_gain": "NOT_MEASURED",
    }
