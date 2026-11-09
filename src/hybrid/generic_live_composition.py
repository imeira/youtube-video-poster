"""Fixed protected admission into the existing durable executor.

LIVE has no caller-supplied registry, factory capability, callback, provider,
root, quote, verifier, or ledger. Each admission asks the repository-owned
protected-operation resolver to reopen and validate the fixed composition.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

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
_OPERATION_ID = re.compile(r"[A-Z][A-Z0-9_-]{2,159}\Z", re.ASCII)


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


class GenericLiveOperationResolver:
    """Resolve a literal operation ID through fixed repository-owned policy only."""

    @staticmethod
    def _operation_id(operation_id: str) -> str:
        if not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id):
            raise LiveCompositionBlocked("literal protected operation ID is invalid")
        return operation_id

    def readiness(self, operation_id: str) -> dict:
        operation_id = self._operation_id(operation_id)
        # This is a bounded read-only preflight integration, never admission.
        # The absent production locator falls through to the legacy blocked
        # projection so existing operator diagnostics remain compatible.
        from src.hybrid.generic_live_preflight import (
            GenericLivePreflightBlocked,
            GenericLivePreflightVerifier,
        )
        try:
            return GenericLivePreflightVerifier.production().verify(
                operation_id, now=0
            )
        except GenericLivePreflightBlocked:
            from src.hybrid.qualified_episode_operation import (
                qualified_operation_readiness,
            )
            return qualified_operation_readiness(operation_id)

    async def admit(self, operation_id: str) -> dict:
        operation_id = self._operation_id(operation_id)
        try:
            from src.hybrid.qualified_episode_operation import (
                build_qualified_episode_operation,
            )
            operation = build_qualified_episode_operation(operation_id)
        except (OSError, ValueError, PermissionError) as error:
            raise LiveCompositionBlocked("operation is not qualified by protected composition") from error
        return await operation.execute()


class GenericLiveCompositionRegistry:
    """Compatibility façade with separate TEST and LIVE domains.

    Production has no mutable operation registry. The historical name remains
    for callers, but LIVE routes only through :class:`GenericLiveOperationResolver`.
    """

    def __init__(self, *, operations=None, domain=None, _static_operation=None) -> None:
        if domain == "TEST" and isinstance(_static_operation, _StaticTestOperation) and operations is None:
            self._static_operation = _static_operation
            self._domain = "TEST"
            return
        if domain == "LIVE" and operations is None and _static_operation is None:
            self._resolver = GenericLiveOperationResolver()
            self._domain = "LIVE"
            return
        raise TypeError("registry instances come only from production/static-test factories")

    @classmethod
    def production(cls) -> GenericLiveCompositionRegistry:
        return cls(domain="LIVE")

    @classmethod
    def for_static_test(
        cls, *, operation_id: str, executor_database: Path, executor_config: Config,
        job: Job, transport: object,
    ) -> GenericLiveCompositionRegistry:
        if not isinstance(operation_id, str) or not operation_id:
            raise ValueError("TEST operation id is required")
        if job.mode != "TEST" or getattr(transport, "mode", None) != "TEST":
            raise ValueError("static composition accepts TEST fixture transport only")
        operation = _StaticTestOperation(Executor(Path(executor_database), executor_config), job, transport)
        registry = cls(domain="TEST", _static_operation=operation)
        registry._test_operation_id = operation_id
        return registry

    def readiness(self, operation_id: str) -> dict:
        if self._domain == "LIVE":
            return self._resolver.readiness(operation_id)
        return {
            "operation_id": operation_id,
            "status": "BLOCKED",
            "live_enabled": False,
            "missing": [],
            "tts": "TEST_STATIC_FIXTURE",
            "image_references": "TEST_STATIC_FIXTURE",
            "note": "TEST fixture composition cannot report LIVE readiness.",
        }

    async def admit(self, operation_id: str, **caller_injection) -> dict:
        if caller_injection:
            raise LiveCompositionBlocked("caller inputs cannot select protected composition")
        if self._domain == "LIVE":
            return await self._resolver.admit(operation_id)
        if operation_id != self._test_operation_id:
            raise LiveCompositionBlocked("TEST operation ID does not match fixed fixture")
        return await self._static_operation.execute()