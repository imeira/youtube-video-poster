"""Generic, protected admission into the existing durable executor.

Production deliberately has no registered operation.  A deployment may install
one only through its fixed protected configuration after independent provider,
identity, price/billing, licensing, budget, image-reference, and TTS bindings
exist.  Callers never provide a provider, root, callback, quote, or authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.hybrid.execution import Executor, Job
from src.hybrid.planner import Config


class LiveCompositionBlocked(PermissionError):
    """A protected operation is absent or not qualified for admission."""


_REQUIRED_BINDINGS = (
    "provider_qualification",
    "independent_identity_attestation",
    "price_quote_and_billing_readback",
    "license_attestation",
    "canonical_budget_authority",
    "image_reference_capability",
    "real_tts_method",
)


_REGISTRY_FACTORY_CAPABILITY = object()


class _RegisteredOperation(Protocol):
    async def execute(self) -> dict: ...


@dataclass(frozen=True)
class _StaticTestOperation:
    """TEST-only adapter that exercises the real executor's receipt/recovery."""

    executor: Executor
    job: Job
    transport: object

    async def execute(self) -> dict:
        if self.job.mode != "TEST" or getattr(self.transport, "mode", None) != "TEST":
            raise LiveCompositionBlocked("static operation is TEST-only")
        return await self.executor.run(self.job, self.transport)


class GenericLiveCompositionRegistry:
    """Composition-owned registry; an empty production registry is fail-closed.

    The registry is intentionally not populated from a next-video packet,
    worker task, filesystem root, callback, provider object, quote, or ledger.
    """

    def __init__(
        self,
        *,
        operations: dict[str, _RegisteredOperation],
        domain: str,
        _factory_capability: object | None = None,
    ) -> None:
        if _factory_capability is not _REGISTRY_FACTORY_CAPABILITY:
            raise TypeError("registry entries may be created only by protected factories")
        self._operations = dict(operations)
        self._domain = domain

    @classmethod
    def production(cls) -> GenericLiveCompositionRegistry:
        """Return the fixed production registry, currently with no qualified entry."""
        return cls(
            operations={},
            domain="LIVE",
            _factory_capability=_REGISTRY_FACTORY_CAPABILITY,
        )

    @classmethod
    def for_static_test(
        cls,
        *,
        operation_id: str,
        executor_database: Path,
        executor_config: Config,
        job: Job,
        transport: object,
    ) -> GenericLiveCompositionRegistry:
        """Build a TEST fixture composition, never a substitute for LIVE trust."""
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError("TEST operation id is required")
        if job.mode != "TEST" or getattr(transport, "mode", None) != "TEST":
            raise ValueError("static composition accepts TEST fixture transport only")
        operation = _StaticTestOperation(Executor(Path(executor_database), executor_config), job, transport)
        return cls(
            operations={operation_id: operation},
            domain="TEST",
            _factory_capability=_REGISTRY_FACTORY_CAPABILITY,
        )

    def readiness(self, operation_id: str) -> dict:
        """Report fixed-config blockers without claiming LIVE enablement."""
        return {
            "operation_id": operation_id,
            "status": "READY" if operation_id in self._operations and self._domain == "LIVE" else "BLOCKED",
            "live_enabled": operation_id in self._operations and self._domain == "LIVE",
            "missing": list(_REQUIRED_BINDINGS) if self._domain == "LIVE" and operation_id not in self._operations else [],
            "tts": "UNRESOLVED_NO_AZURE" if self._domain == "LIVE" and operation_id not in self._operations else "TEST_STATIC_FIXTURE",
            "image_references": "UNPROVISIONED" if self._domain == "LIVE" and operation_id not in self._operations else "TEST_STATIC_FIXTURE",
            "note": "Readiness is not provider, billing, credential, financial, media, or publication evidence.",
        }

    async def admit(self, operation_id: str) -> dict:
        """Resolve only a pre-registered operation, then use its existing executor."""
        operation = self._operations.get(operation_id)
        if operation is None:
            raise LiveCompositionBlocked("operation is not qualified by protected composition")
        return await operation.execute()
