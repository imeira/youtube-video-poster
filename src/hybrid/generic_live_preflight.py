"""Bounded, data-driven verifier for a protected generic LIVE preflight.

This module only reads closed immutable evidence and returns a non-authorizing
binding artifact.  It never imports executor, provider, credential, ledger, or
network code.  The production locator is repository-owned; ``for_test`` exists
solely for hermetic non-secret fixtures.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

_PROTECTED_ROOT = Path("C:/HermesStudio/protected/generic-live-preflights")
_ID = re.compile(r"[A-Z][A-Z0-9_-]{2,159}\Z", re.ASCII)
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)


class GenericLivePreflightBlocked(PermissionError):
    """A closed preflight document or one of its immutable bindings is invalid."""


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise GenericLivePreflightBlocked("duplicate JSON key")
        result[key] = value
    return result


def _read(path: Path, label: str) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
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
    if not isinstance(relative, str) or not relative or "/" in relative or "\\" in relative or ":" in relative:
        raise GenericLivePreflightBlocked(f"{label} path is not a literal repository-owned locator")
    expected = _sha(descriptor.get("sha256"), label)
    document, actual = _read(root / relative, label)
    if actual != expected:
        raise GenericLivePreflightBlocked(f"{label} immutable hash mismatch")
    return document, actual


@dataclass(frozen=True)
class GenericLivePreflightVerifier:
    """Read-only verifier with a fixed production root and explicit TEST seam."""

    _root: Path
    _test_only: bool = False

    @classmethod
    def production(cls) -> GenericLivePreflightVerifier:
        return cls(_PROTECTED_ROOT)

    @classmethod
    def for_test(cls, root: Path) -> GenericLivePreflightVerifier:
        root = Path(root)
        if not root.is_absolute():
            raise GenericLivePreflightBlocked("TEST root must be absolute")
        return cls(root, True)

    def verify(self, operation_id: str, *, now: int) -> dict[str, Any]:
        operation_id = _literal(operation_id, "operation ID")
        if type(now) is not int:
            raise GenericLivePreflightBlocked("verification clock must be an integer")
        packet, packet_sha = _read(self._root / "operations" / f"{operation_id}.json", "operation packet")
        required = {"schema", "operation_id", "mode", "episode_id", "consent", "financial_policy", "independent_review", "quote", "license", "payload"}
        if set(packet) != required or packet.get("schema") != "generic-live-operation-packet/v1":
            raise GenericLivePreflightBlocked("closed operation packet is required")
        if packet.get("operation_id") != operation_id or packet.get("mode") not in {"TEST", "LIVE"}:
            raise GenericLivePreflightBlocked("operation packet scope is invalid")
        if packet["mode"] == "TEST" and not self._test_only:
            raise GenericLivePreflightBlocked("TEST evidence cannot enter production verifier")
        episode_id = _literal(packet.get("episode_id"), "episode ID")
        payload, payload_sha = _file(self._root, packet.get("payload"), "payload")
        if not payload:
            raise GenericLivePreflightBlocked("payload is empty")
        consent, consent_sha = _file(self._root, packet.get("consent"), "canonical consent")
        financial, financial_sha = _file(self._root, packet.get("financial_policy"), "financial policy")
        review, review_sha = _file(self._root, packet.get("independent_review"), "independent review")
        quote, quote_sha = _file(self._root, packet.get("quote"), "quote")
        license_doc, license_sha = _file(self._root, packet.get("license"), "license provenance")
        if consent != {"schema": "canonical-consent/v1", "operation_id": operation_id, "episode_id": episode_id, "scope": "IMAGE_GENERATION", "status": "ACTIVE"}:
            raise GenericLivePreflightBlocked("canonical consent scope or status is invalid")
        if financial != {"schema": "standing-financial-policy/v1", "operation_id": operation_id, "episode_id": episode_id, "consent_sha256": consent_sha, "currency": "USD", "maximum_usd": "0.048", "status": "ACTIVE"}:
            raise GenericLivePreflightBlocked("financial policy binding is invalid")
        if set(quote) != {"schema", "provider", "endpoint", "model", "payload_sha256", "currency", "maximum_usd", "expires_at_epoch"} or quote.get("schema") != "provider-finite-quote/v1" or quote.get("provider") != "TEST_PROVIDER" or quote.get("endpoint") != "https://provider.test/v1/images" or quote.get("model") != "test-image-v1" or quote.get("payload_sha256") != payload_sha or quote.get("currency") != "USD" or quote.get("maximum_usd") != "0.048":
            raise GenericLivePreflightBlocked("quote wire provider/model/payload binding is invalid")
        if type(quote.get("expires_at_epoch")) is not int or quote["expires_at_epoch"] <= now:
            raise GenericLivePreflightBlocked("quote expired")
        if license_doc != {"schema": "license-provenance/v1", "provider": quote["provider"], "model": quote["model"], "license": "TEST-COMMERCIAL", "payload_sha256": payload_sha, "status": "VALID"}:
            raise GenericLivePreflightBlocked("license provenance binding is invalid")
        expected_review = {"schema": "local-process-review-receipt/v1", "operation_id": operation_id, "verdict": "PASS", "consent_sha256": consent_sha, "financial_sha256": financial_sha, "quote_sha256": quote_sha, "license_sha256": license_sha, "payload_sha256": payload_sha, "reviewer": "LOCAL_PROCESS_REVIEW_TEST"}
        if review != expected_review:
            raise GenericLivePreflightBlocked("independent review binding is invalid")
        return {"schema": "generic-live-verified-bound-preflight/v1", "status": "VERIFIED_BOUND_PREFLIGHT", "authority": "NONE", "dispatch_permitted": False, "mode": packet["mode"], "bindings": {"operation_id": operation_id, "episode_id": episode_id, "operation_packet_sha256": packet_sha, "consent_sha256": consent_sha, "financial_policy_sha256": financial_sha, "independent_review_sha256": review_sha, "quote_sha256": quote_sha, "license_sha256": license_sha, "payload_sha256": payload_sha}}
