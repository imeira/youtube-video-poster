"""Issue short-lived, exact LIVE authority from current price evidence and ledger."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from src.hybrid.artifacts import atomic_json
from src.hybrid.execution import Authorization, Job, Price
from src.hybrid.planner import money


class LivePriceResolver(Protocol):
    def quote(self, job: Job) -> dict: ...


@dataclass(frozen=True)
class LivePreflight:
    authorizations: dict[str, Authorization]
    prices: dict[str, Price]


class LivePreflightIssuer:
    """Creates only sanitized, request-bound authority; it never submits media."""

    def __init__(self, ledger_path: Path, *, hard_limit: Decimal, price_resolver: LivePriceResolver):
        self.ledger_path = Path(ledger_path)
        self.hard_limit = money(hard_limit)
        self.price_resolver = price_resolver

    def _spent(self) -> Decimal:
        if not self.ledger_path.exists():
            return Decimal(0)
        try:
            ledger = json.loads(self.ledger_path.read_text(encoding="utf-8"))
            return money(ledger.get("spent", 0))
        except (OSError, json.JSONDecodeError, AttributeError) as error:
            raise ValueError("canonical cost ledger is invalid") from error

    def issue(self, jobs: list[Job] | tuple[Job, ...], *, reviewer: str, receipt_path: Path, ttl_seconds: int = 300) -> LivePreflight:
        jobs = tuple(jobs)
        if not jobs or not isinstance(reviewer, str) or not reviewer.strip() or ttl_seconds < 1:
            raise ValueError("nonempty LIVE jobs, reviewer and positive TTL are required")
        if len({job.request_id for job in jobs}) != len(jobs) or any(job.mode != "LIVE" for job in jobs):
            raise ValueError("LIVE preflight requires unique LIVE request IDs")
        spent = self._spent()
        total = sum((money(job.cost) for job in jobs), Decimal(0))
        if spent + total > self.hard_limit:
            raise RuntimeError("budget reservation blocked")
        now = time.time()
        authorizations: dict[str, Authorization] = {}
        prices: dict[str, Price] = {}
        records = []
        for job in jobs:
            quote = self.price_resolver.quote(job)
            if not isinstance(quote, dict) or set(quote) != {"endpoint", "amount", "evidence"}:
                raise ValueError("price quote schema is invalid")
            amount = money(quote["amount"])
            evidence = quote["evidence"]
            if quote["endpoint"] != job.endpoint or amount != job.cost:
                raise ValueError("price quote must bind exact job cost and endpoint")
            if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 256:
                raise ValueError("sanitized price evidence is required")
            price = Price(job.endpoint, job.request_id, amount, now + ttl_seconds, evidence)
            authorization = Authorization(job.request_id, reviewer, now + ttl_seconds, amount)
            prices[job.request_id] = price
            authorizations[job.request_id] = authorization
            records.append({
                "request_id": job.request_id,
                "endpoint": job.endpoint,
                "maximum_cost": str(amount),
                "manifest_sha256": job.manifest.checksum,
                "price_evidence": evidence,
                "valid_until": price.valid_until,
            })
        atomic_json(Path(receipt_path), {
            "schema_version": 1,
            "kind": "LIVE_PREFLIGHT",
            "reviewer": reviewer,
            "ledger_spent": str(spent),
            "hard_limit": str(self.hard_limit),
            "jobs": records,
        })
        return LivePreflight(authorizations, prices)
