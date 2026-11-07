"""PR53 facade for the protected Jacó operation composition.

This module deliberately owns no policy, manifest, ledger, request identity, or
provider path. Those are selected by ``OperationAdmissionFactory`` from the
protected composition. It remains only as a compatibility import for callers
which formerly used the bounded-operation adapter.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.hybrid.jaco_style_remediation_operation import (
    OperationAdmissionError,
    OperationAdmissionFactory,
)

BoundedAdmissionError = OperationAdmissionError


class BoundedOperationAdmission:
    """Compatibility facade over the sole composition-owned authority."""

    def __init__(self) -> None:
        self._factory = OperationAdmissionFactory()

    @classmethod
    def _for_test(cls, *, root: Path, authority_path: Path) -> BoundedOperationAdmission:
        """Private fixture hook; production cannot select a root or authority."""
        adapter = object.__new__(cls)
        adapter._factory = OperationAdmissionFactory._for_test(
            root=root, authority_path=authority_path
        )
        return adapter

    async def _execute_prepared(
        self,
        *,
        quote: dict[str, Any],
        openapi: dict[str, Any],
        test_provider_factory=None,
    ) -> dict[str, Any]:
        """Execute a composition-selected TEST fixture through the real Executor."""
        return await self._factory._execute_prepared(
            quote=quote,
            openapi=openapi,
            credential="",
            test_provider_factory=test_provider_factory,
        )

    async def _execute_with_composition(
        self, *, quote_and_openapi, credential_loader, test_provider_factory=None
    ) -> dict[str, Any]:
        """Delegate validation, qualification and effects to the factory."""
        return await self._factory._execute_with_composition(
            quote_and_openapi=quote_and_openapi,
            credential_loader=credential_loader,
            test_provider_factory=test_provider_factory,
        )

    async def execute(self) -> dict[str, Any]:
        """Run only the deployment-pinned production composition."""
        return await self._factory.execute()
