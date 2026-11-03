"""Permanent Jacó LIVE execution policy and fail-closed admission.

A historical financial reconciliation proves only the scoped USD ceiling.  Its
``authorizes_generation: false`` is preserved as history: it is never rewritten
or interpreted as provider authority.  Current execution intent is represented
by this immutable policy module, while an exact quote, verified license and a
composition-owned provider registration remain mandatory for every job.
"""

from __future__ import annotations

import json
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
    """Build the small trusted composition used by Director integration/tests.

    This factory intentionally derives endpoint identity from the adapter class
    contract rather than accepting caller-supplied ``qualified`` flags.
    """
    if getattr(provider, "mode", None) != "LIVE":
        raise LiveAdmissionError("only a LIVE provider can be registered")
    # Production adapters expose an endpoint through their submitted Job.  The
    # sealed transport is allowed solely for offline mechanics coverage and is
    # never a source of policy, quote, license, or publication authority.
    endpoint = "fal-ai/flux-2/klein/9b/edit"
    if provider.__class__.__module__ == "src.hybrid.live":
        from src.hybrid.live import FalFluxProvider

        if not isinstance(provider, FalFluxProvider):
            raise LiveAdmissionError("unrecognized production provider adapter")
    elif getattr(provider, "test_transport", False):
        endpoint = getattr(provider, "trusted_endpoint", None)
        if endpoint not in _ALLOWED_ENDPOINTS:
            raise LiveAdmissionError("sealed transport has no approved endpoint identity")
    else:
        raise LiveAdmissionError("unrecognized provider adapter")
    return _TrustedRegistry({endpoint: provider})


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
