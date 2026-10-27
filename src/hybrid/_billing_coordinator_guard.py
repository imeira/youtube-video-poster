"""Private in-process guard for coordinated billing-store transitions.

The OS lock remains the interprocess boundary.  This guard prevents public
ledger/executor APIs from treating a caller-provided generation integer as
authority to mutate a coordinator-owned FAL transition.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

_generation: ContextVar[int | None] = ContextVar("billing_snapshot_generation", default=None)
_CAPABILITY_SECRET = object()


class _CoordinatorCapability:
    """Trusted-composition proof for one coordinator-owned executor request."""

    def __init__(self, secret, coordinator, request_id, executor, ledger, config, owner_binding):
        if secret is not _CAPABILITY_SECRET:
            raise TypeError("coordinator capability is created only by trusted composition")
        self._coordinator = coordinator
        self._request_id = request_id
        self._executor = executor
        self._ledger = ledger
        self._config = config
        self._owner_binding = dict(owner_binding)

    def matches(self, *, expected_coordinator, request_id, executor, ledger, config, owner_binding) -> bool:
        return (self._coordinator is expected_coordinator and self._request_id == request_id
                and self._executor is executor and self._ledger is ledger
                and self._config is config and self._owner_binding == owner_binding)

    def checkpoint_provider_binding(self, request_id, provider_request_id, *, partial=None):
        if request_id != self._request_id:
            raise PermissionError("coordinator capability request mismatch")
        return self._coordinator.checkpoint_provider_binding(
            request_id, provider_request_id, partial=partial
        )


def _coordinator_capability(coordinator, request_id, executor, ledger, config, owner_binding):
    return _CoordinatorCapability(_CAPABILITY_SECRET, coordinator, request_id, executor, ledger, config, owner_binding)


def _require_coordinator_capability(capability, *, expected_coordinator, request_id, executor, ledger, config, owner_binding) -> None:
    if not isinstance(capability, _CoordinatorCapability) or not capability.matches(
        expected_coordinator=expected_coordinator, request_id=request_id, executor=executor,
        ledger=ledger, config=config, owner_binding=owner_binding
    ):
        raise PermissionError("generation-pinned FAL submit requires coordinator capability")


@contextmanager
def _coordinator_write(generation: int):
    if type(generation) is not int or generation < 1:
        raise ValueError("billing snapshot generation must be a positive integer")
    token = _generation.set(generation)
    try:
        yield
    finally:
        _generation.reset(token)


def _require_coordinator_write(generation: int | None) -> None:
    if generation is not None and _generation.get() != generation:
        raise PermissionError("billing snapshot generation requires coordinator writer context")
