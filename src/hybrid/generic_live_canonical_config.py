"""Repository-owned SSOT for generic LIVE canonical bindings.

This registry is deliberately unprovisioned.  Adding an operation requires a
reviewed, operation-specific fixed binding and a pure parameterized validator;
it must never reuse an episode-specific factory, authority, transcript, or
ledger as generic readiness evidence.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from src.hybrid.canonical_operation_binding import CanonicalOperationBinding

# No generic operation has a reviewed canonical binding yet.  The absence is a
# terminal pre-I/O readiness result, not a reason to inspect any production root.
GENERIC_CANONICAL_BINDINGS: Final[Mapping[str, CanonicalOperationBinding]] = {}
