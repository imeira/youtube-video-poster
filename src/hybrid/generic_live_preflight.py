"""Read-only generic preflight verification with separate TEST and LIVE domains.

``for_test`` validates only a hermetic, self-hashed TEST packet.  Its
``VERIFIED`` verdict means structural consistency in the TEST domain and never
authority.  Production has no root, callback, or validator injection seam.  It
consults the repository-owned generic canonical-binding registry before any
protected-root read; the registry is currently intentionally unprovisioned.
"""
from __future__ import annotations

import json
import re
import stat
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

_PROTECTED_ROOT = Path("C:/HermesStudio/protected/generic-live-preflights")
_ID = re.compile(r"[A-Z][A-Z0-9_-]{2,159}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class GenericLivePreflightBlocked(PermissionError):
    """A preflight has no authenticated, deployable generic LIVE authority."""


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise GenericLivePreflightBlocked("duplicate JSON key")
        result[key] = value
    return result


def _no_reparse_ancestor(path: Path, label: str) -> None:
    """Reject links/junctions before the first protected-file open."""
    for ancestor in (path, *path.parents):
        try:
            info = ancestor.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(info.st_mode) or bool(
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        ):
            raise GenericLivePreflightBlocked(f"{label} traverses a reparse point")


def _read(path: Path, label: str) -> tuple[dict[str, Any], str]:
    _no_reparse_ancestor(path, label)
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
        raise GenericLivePreflightBlocked(f"{label} is unavailable strict JSON") from error
    if not isinstance(value, dict):
        raise GenericLivePreflightBlocked(f"{label} must be an object")
    return value, sha256(raw).hexdigest()


def _literal(value: object, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise GenericLivePreflightBlocked(f"literal {label} is invalid")
    return value


def _sha(value: object, label: str) -> str:
    if not isinstance(value, str) or not _SHA.fullmatch(value):
        raise GenericLivePreflightBlocked(f"{label} SHA-256 is invalid")
    return value


def _file(root: Path, descriptor: object, label: str) -> tuple[dict[str, Any], str]:
    if not isinstance(descriptor, dict) or set(descriptor) != {"path", "sha256"}:
        raise GenericLivePreflightBlocked(f"{label} descriptor is not closed")
    relative = descriptor.get("path")
    if (
        not isinstance(relative, str)
        or not relative
        or "/" in relative
        or "\\" in relative
        or ":" in relative
    ):
        raise GenericLivePreflightBlocked(f"{label} path is not a literal repository-owned locator")
    expected = _sha(descriptor.get("sha256"), label)
    document, actual = _read(root / relative, label)
    if actual != expected:
        raise GenericLivePreflightBlocked(f"{label} immutable hash mismatch")
    return document, actual


def _verify_test_packet(root: Path, operation_id: str, *, now: int) -> dict[str, Any]:
    """Validate fixture structure only; descriptor hashes are not trust anchors."""
    packet, packet_sha = _read(root / "operations" / f"{operation_id}.json", "operation packet")
    required = {
        "schema", "operation_id", "mode", "episode_id", "consent",
        "financial_policy", "independent_review", "quote", "license", "payload",
    }
    if set(packet) != required or packet.get("schema") != "generic-live-operation-packet/v1":
        raise GenericLivePreflightBlocked("closed operation packet is required")
    if packet.get("operation_id") != operation_id or packet.get("mode") != "TEST":
        raise GenericLivePreflightBlocked("TEST packet mode is required")
    episode_id = _literal(packet.get("episode_id"), "episode ID")
    payload, payload_sha = _file(root, packet.get("payload"), "payload")
    if not payload:
        raise GenericLivePreflightBlocked("payload is empty")
    consent, consent_sha = _file(root, packet.get("consent"), "canonical consent")
    financial, financial_sha = _file(root, packet.get("financial_policy"), "financial policy")
    review, review_sha = _file(root, packet.get("independent_review"), "independent review")
    quote, quote_sha = _file(root, packet.get("quote"), "quote")
    license_doc, license_sha = _file(root, packet.get("license"), "license provenance")
    if consent != {
        "schema": "canonical-consent/v1", "operation_id": operation_id,
        "episode_id": episode_id, "scope": "IMAGE_GENERATION", "status": "ACTIVE",
    }:
        raise GenericLivePreflightBlocked("canonical consent scope or status is invalid")
    if financial != {
        "schema": "standing-financial-policy/v1", "operation_id": operation_id,
        "episode_id": episode_id, "consent_sha256": consent_sha, "currency": "USD",
        "maximum_usd": "0.048", "status": "ACTIVE",
    }:
        raise GenericLivePreflightBlocked("financial policy binding is invalid")
    if (
        set(quote) != {"schema", "provider", "endpoint", "model", "payload_sha256", "currency", "maximum_usd", "expires_at_epoch"}
        or quote.get("schema") != "provider-finite-quote/v1"
        or quote.get("provider") != "TEST_PROVIDER"
        or quote.get("endpoint") != "https://provider.test/v1/images"
        or quote.get("model") != "test-image-v1"
        or quote.get("payload_sha256") != payload_sha
        or quote.get("currency") != "USD"
        or quote.get("maximum_usd") != "0.048"
    ):
        raise GenericLivePreflightBlocked("quote wire provider/model/payload binding is invalid")
    if type(quote.get("expires_at_epoch")) is not int or quote["expires_at_epoch"] <= now:
        raise GenericLivePreflightBlocked("quote expired")
    if license_doc != {
        "schema": "license-provenance/v1", "provider": quote["provider"],
        "model": quote["model"], "license": "TEST-COMMERCIAL",
        "payload_sha256": payload_sha, "status": "VALID",
    }:
        raise GenericLivePreflightBlocked("license provenance binding is invalid")
    if review != {
        "schema": "local-process-review-receipt/v1", "operation_id": operation_id,
        "verdict": "PASS", "consent_sha256": consent_sha,
        "financial_sha256": financial_sha, "quote_sha256": quote_sha,
        "license_sha256": license_sha, "payload_sha256": payload_sha,
        "reviewer": "LOCAL_PROCESS_REVIEW_TEST",
    }:
        raise GenericLivePreflightBlocked("independent review binding is invalid")
    return {
        "schema": "generic-test-bound-preflight/v2", "status": "VERIFIED",
        "domain": "TEST", "evidence_mode": "TEST",
        "verification": "STRUCTURAL_TEST_FIXTURE_ONLY", "authority": "NONE",
        "dispatch_permitted": False,
        "bindings": {
            "operation_id": operation_id, "episode_id": episode_id,
            "operation_packet_sha256": packet_sha, "consent_sha256": consent_sha,
            "financial_policy_sha256": financial_sha, "independent_review_sha256": review_sha,
            "quote_sha256": quote_sha, "license_sha256": license_sha,
            "payload_sha256": payload_sha,
        },
    }


def _generic_canonical_binding(operation_id: str) -> object:
    """Return a repository-owned fixed binding for one literal operation only."""
    from src.hybrid.canonical_operation_binding import CanonicalOperationBinding
    from src.hybrid.generic_live_canonical_config import GENERIC_CANONICAL_BINDINGS

    binding = GENERIC_CANONICAL_BINDINGS.get(operation_id)
    if binding is None:
        raise GenericLivePreflightBlocked("generic canonical binding is not provisioned")
    if not isinstance(binding, CanonicalOperationBinding) or binding.operation_id != operation_id:
        raise GenericLivePreflightBlocked("generic canonical binding is invalid")
    return binding


_CONSTRUCTION_TOKEN = object()


@dataclass(frozen=True, init=False)
class GenericLivePreflightVerifier:
    """Fixed-root production verifier or an explicitly TEST-only fixture reader."""

    _root: Path
    _domain: str

    def __init__(self, *, _root: Path, _domain: str, _token: object) -> None:
        if _token is not _CONSTRUCTION_TOKEN:
            raise TypeError("verifier instances come only from production/test factories")
        if _domain not in {"TEST", "LIVE"}:
            raise TypeError("verifier domain is invalid")
        object.__setattr__(self, "_root", Path(_root))
        object.__setattr__(self, "_domain", _domain)

    @classmethod
    def production(cls) -> GenericLivePreflightVerifier:
        return cls(_root=_PROTECTED_ROOT, _domain="LIVE", _token=_CONSTRUCTION_TOKEN)

    @classmethod
    def for_test(cls, root: Path) -> GenericLivePreflightVerifier:
        root = Path(root)
        if not root.is_absolute():
            raise GenericLivePreflightBlocked("TEST root must be absolute")
        return cls(_root=root, _domain="TEST", _token=_CONSTRUCTION_TOKEN)

    def verify(self, operation_id: str, *, now: int) -> dict[str, Any]:
        operation_id = _literal(operation_id, "operation ID")
        if type(now) is not int:
            raise GenericLivePreflightBlocked("verification clock must be an integer")
        if self._domain == "TEST":
            return _verify_test_packet(self._root, operation_id, now=now)
        binding = _generic_canonical_binding(operation_id)
        try:
            from src.hybrid.canonical_operation_binding import (
                verify_fixed_canonical_operation_binding,
            )
            evidence = verify_fixed_canonical_operation_binding(binding)
        except PermissionError as error:
            raise GenericLivePreflightBlocked(str(error)) from error
        return {
            "schema": "generic-live-preflight/v3",
            "operation_id": operation_id,
            "status": "CANONICAL_BINDING_VERIFIED",
            "live_enabled": False,
            "dispatch_permitted": False,
            "canonical_binding": evidence,
            "blocked": {
                "quote": "MISSING_PROVIDER_QUOTE",
                "independent_review": "MISSING_INDEPENDENT_REVIEW",
                "license": "MISSING_LICENSE_PROVENANCE",
            },
            "reason": "canonical mandate and financial binding verified; quote/review/license remain blocked",
        }
