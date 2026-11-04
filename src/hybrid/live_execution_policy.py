"""Permanent Jacó LIVE execution policy and fail-closed admission.

A historical financial reconciliation proves only the scoped USD ceiling.  Its
``authorizes_generation: false`` is preserved as history: it is never rewritten
or interpreted as provider authority.  Current execution intent is represented
by this immutable policy module, while an exact quote, verified license and a
composition-owned provider registration remain mandatory for every job.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any

from src.hybrid.execution import Authorization, Job, Price

REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
REVISION = "818556d97edb648f"
EPISODE_ID = "JACO_GENESIS28"
CAP_USD = Decimal("10.00")
_ALLOWED_ENDPOINTS = frozenset({"fal-ai/flux-2/klein/9b/edit", "seedance-v1-5-pro-i2v"})


class LiveAdmissionError(PermissionError):
    """The immutable LIVE policy does not qualify this specific operation."""


@dataclass(frozen=True)
class JacoExecutionPolicy:
    request_id: str
    revision: str
    episode_id: str
    episode_cap_usd: Decimal
    allowed_endpoints: frozenset[str]
    routine_human_gates: tuple[str, str]
    publication_enabled: bool
    historical_generation_authorized: bool
    digest: str


@dataclass(frozen=True)
class LiveAdmission:
    policy: JacoExecutionPolicy
    authorization: Authorization
    price: Price


class _TrustedRegistry:
    """Opaque composition result; request payloads cannot register adapters."""

    def __init__(self, entries: Mapping[str, object]):
        self._entries = MappingProxyType(dict(entries))

    def provider_for(self, endpoint: str) -> object:
        provider = self._entries.get(endpoint)
        if provider is None:
            raise LiveAdmissionError("provider is not registered by trusted composition")
        return provider


def trusted_provider_registry(provider: object) -> _TrustedRegistry:
    """Reject ad-hoc registration until deployed trusted composition exists.

    An in-process adapter, its Python attributes, and a caller-provided endpoint
    cannot establish the independently provisioned trust service required for a
    paid LIVE operation.  TEST transports use TEST-only executor paths instead.
    """
    del provider
    raise LiveAdmissionError("trusted composition service is not configured; LIVE remains blocked")


class JacoExecutionPolicyResolver:
    """Resolve the permanent policy without mutating historical evidence."""

    def __init__(self, historical_financial_record: Path | str):
        self._record = Path(historical_financial_record)

    @staticmethod
    def _canonical(value: dict[str, Any]) -> str:
        return sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def resolve(self) -> JacoExecutionPolicy:
        try:
            data = json.loads(self._record.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise LiveAdmissionError("historical financial record is unavailable") from error
        required = {
            "schema": "jaco-factory-financial-reconciliation/1",
            "request_hash": REQUEST_ID,
            "revision": REVISION,
            "currency": "USD",
            "total_episode_cap": "10.00",
            "status": "APPROVED_USD10",
            "approval_scope": "FINANCIAL_ONLY",
            "authorizes_generation": False,
        }
        if not isinstance(data, dict) or any(data.get(key) != value for key, value in required.items()):
            raise LiveAdmissionError("historical financial record does not bind the Jacó USD10 scope")
        policy_payload = {
            "request_id": REQUEST_ID, "revision": REVISION, "episode_id": EPISODE_ID,
            "episode_cap_usd": "10.00", "allowed_endpoints": sorted(_ALLOWED_ENDPOINTS),
            "routine_human_gates": ["thumbnail", "final_video"], "publication_enabled": False,
            "historical_generation_authorized": False,
        }
        return JacoExecutionPolicy(
            REQUEST_ID, REVISION, EPISODE_ID, CAP_USD, _ALLOWED_ENDPOINTS,
            ("thumbnail", "final_video"), False, False, self._canonical(policy_payload),
        )

    def admit(
        self,
        *,
        provider: object,
        license_status: str,
        provider_registry: _TrustedRegistry,
        price_quote: Mapping[str, Any] | None = None,
        job: Job | None = None,
    ) -> LiveAdmission:
        policy = self.resolve()
        # No configured composition-owned trust service currently verifies the
        # complete source, issuer, hash/schema, endpoint, model/payload,
        # request/currency/quote/expiry/price-bound, licensing, and prior
        # authority chain.  Do not turn local strings or object attributes into
        # a second admission route while that service is absent.
        raise LiveAdmissionError(
            "trusted composition service is not available; LIVE remains blocked"
        )
        if license_status != "VERIFIED":
            raise LiveAdmissionError("verified license evidence is required before LIVE admission")
        if not isinstance(provider_registry, _TrustedRegistry):
            raise LiveAdmissionError("trusted provider registry is required")
        if job is None or job.mode != "LIVE":
            raise LiveAdmissionError("exact LIVE job is required")
        if job.endpoint not in policy.allowed_endpoints or provider_registry.provider_for(job.endpoint) is not provider:
            raise LiveAdmissionError("provider is not registered for this exact endpoint")
        if not isinstance(price_quote, Mapping) or set(price_quote) != {"endpoint", "amount", "evidence"}:
            raise LiveAdmissionError("exact bounded provider quote is required")
        try:
            amount = Decimal(str(price_quote["amount"]))
        except (InvalidOperation, ValueError) as error:
            raise LiveAdmissionError("provider quote amount is invalid") from error
        if not amount.is_finite() or amount <= 0 or amount != job.cost or amount > policy.episode_cap_usd:
            raise LiveAdmissionError("provider quote is outside the policy ceiling")
        evidence = price_quote["evidence"]
        if price_quote["endpoint"] != job.endpoint or not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 256:
            raise LiveAdmissionError("provider quote does not bind the exact job")
        valid_until = time.time() + 300
        return LiveAdmission(
            policy,
            Authorization(job.request_id, "CURRENT_POLICY_MACHINE_ADMISSION", valid_until, amount),
            Price(job.endpoint, job.request_id, amount, valid_until, evidence),
        )


class ConfiguredTestExecutionComposition:
    """One TEST-only composition of existing authority and executor gates.

    This deliberately is not a LIVE factory. The existing authenticated budget
    bridge reopens its receipt for reservation and the Executor rereads the
    durable claim immediately before its provider effect.
    """

    def __init__(self, *, bridge, executor, provider_factory, capability_root: Path, clock) -> None:
        self._bridge, self._executor = bridge, executor
        self._provider_factory, self._clock = provider_factory, clock
        self._capability_root = Path(capability_root)
        if not self._capability_root.is_absolute():
            raise ValueError("TEST capability root must be absolute")
        self._capability_root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_authenticated_budget_bridge(cls, *, bridge, executor_database: Path,
                                         executor_config, provider_factory,
                                         capability_root: Path, clock=time.time):
        """Build only from the existing authenticated TEST budget bridge."""
        from src.hybrid.budget_authorization import AuthenticatedBudgetBridge
        if not isinstance(bridge, AuthenticatedBudgetBridge):
            raise TypeError("configured authenticated TEST budget bridge is required")
        if not callable(provider_factory):
            raise TypeError("configured TEST provider factory is required")
        policy = bridge.service.policy_loader().load(bridge.receipt_path)
        if policy.mode != "TEST":
            raise PermissionError("configured composition admits TEST authority only")
        return cls(bridge=bridge, executor=bridge.create_executor(Path(executor_database), executor_config),
                   provider_factory=provider_factory, capability_root=Path(capability_root), clock=clock)

    @property
    def production_eligible(self) -> tuple[()]:
        """Real provider qualification is absent; production remains empty."""
        return ()

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")

    @classmethod
    def _sha256(cls, value: object) -> str:
        return sha256(cls._canonical(value)).hexdigest()

    def _validate_qa(self, evidence: object, job: Job) -> str:
        if (not isinstance(evidence, dict) or set(evidence) != {"verdict", "request"}
                or evidence.get("verdict") != "PASS" or evidence.get("request") != job.request_id):
            raise ValueError("TEST QA evidence must be the closed PASS binding")
        return self._sha256(evidence)

    def _write_capability(self, document: dict[str, object]) -> None:
        request_id = document["request_id"]
        if (not isinstance(request_id, str) or len(request_id) != 64
                or any(char not in "0123456789abcdef" for char in request_id)):
            raise ValueError("TEST request id is invalid")
        path = self._capability_root / f"{request_id}.json"
        encoded = self._canonical(document)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError:
            if json.loads(path.read_text(encoding="utf-8")) != document:
                raise PermissionError("TEST capability is immutable")
            return
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != encoded:
            raise PermissionError("TEST capability readback mismatch")

    async def execute(self, job: Job, *, quote_expires_at: object, qa_evidence: object) -> dict:
        """Persist capability, then use the existing prepare/reserve/submit kernel."""
        if not isinstance(job, Job) or job.mode != "TEST":
            raise PermissionError("configured composition admits TEST jobs only")
        if not callable(getattr(self._bridge, "admit_executor_intent", None)):
            raise PermissionError("configured TEST budget bridge is unavailable")
        policy = self._bridge.service.policy_loader().load(self._bridge.receipt_path)
        if policy.mode != "TEST" or job.endpoint not in policy.allowed_providers:
            raise PermissionError("configured TEST budget authority does not cover job")
        now = self._clock()
        if (type(quote_expires_at) not in (int, float) or type(now) not in (int, float)
                or not now < quote_expires_at <= now + 300):
            raise ValueError("TEST quote expiry must be finite and fresh")
        model = job.payload.get("model") if isinstance(job.payload, dict) else None
        if not isinstance(model, str) or not model or len(model) > 256:
            raise ValueError("TEST capability requires an exact bounded model")
        capability = {
            "schema": 1, "kind": "TEST_EXECUTION_CAPABILITY", "mode": "TEST",
            "request_id": job.request_id, "request_payload_sha256": self._sha256(job.payload),
            "endpoint": job.endpoint, "model": model, "currency": "USD",
            "quote_expires_at": quote_expires_at,
            "budget_scope": f"AUTHENTICATED_TEST_REQUEST:{policy.request_id}",
            "qa_evidence_sha256": self._validate_qa(qa_evidence, job),
        }
        capability["capability_sha256"] = self._sha256(capability)
        self._write_capability(capability)
        self._bridge.admit_executor_intent(executor=self._executor, job=job)
        provider = self._provider_factory()
        if getattr(provider, "mode", None) != "TEST":
            raise PermissionError("configured TEST provider factory returned a non-TEST adapter")
        return await self._executor.run(job, provider, submit_prepared=True)
