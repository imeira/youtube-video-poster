"""Fail-closed, preflight-only boundary for the proposed LIVE service.

There is deliberately no provider factory or dispatch implementation here.  This
package has no externally administered trust/signature verifier or authenticated
billing adapter, so it cannot qualify a paid operation.  Preflight records the
missing qualification without creating a reservation or constructing a transport.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any, Protocol

from src.hybrid.execution import Job
from src.hybrid.external_trust_root import (
    ExternalTrustRootVerifier,
    TrustRootVerificationError,
    load_strict_json_snapshot,
)
from src.hybrid.locks import reject_reparse_path


class LiveQualificationError(ValueError):
    """A LIVE preflight input is malformed, stale, or untrustworthy."""


_ADAPTER_ENDPOINTS = {"fal_flux": "fal-ai/flux-2/klein/9b/edit", "runpod_seedance": "seedance-v1-5-pro-i2v"}
_POLICY_FIELDS = {"schema", "version", "mode", "episode_cap_usd", "month_cap_usd", "experiment_cap_usd", "allowed_adapters", "publication"}
_QA_FIELDS = {"schema", "kind", "approved", "request_id", "manifest_sha256", "reviewer", "evidence"}
_QUOTE_FIELDS = {"endpoint", "amount", "valid_until", "evidence"}
_TRUST_ROOT_FIELDS = {"schema", "kind", "issuer", "verification"}
_SENSITIVE_KEY = re.compile(r"auth|token|secret|credential|signature|cookie|key|url|bearer|session", re.IGNORECASE)
_SENSITIVE_VALUE = re.compile(r"(?:bearer|basic)\s+[a-z0-9._~+\-/=]{6,}|(?:api[_ -]?key|token|secret)\s*[:=]", re.IGNORECASE)
_MAX_EVIDENCE_BYTES = 4096
_MAX_EVIDENCE_DEPTH = 4
_MAX_QUOTE_SECONDS = 300


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _hash(value: object) -> str:
    return sha256(_canonical(value)).hexdigest()


def _amount(value: object, field: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise LiveQualificationError(f"{field} must be a finite decimal") from error
    if not amount.is_finite() or amount <= 0:
        raise LiveQualificationError(f"{field} must be a positive finite decimal")
    return amount


def _safe_evidence(value: object, field: str) -> dict[str, Any]:
    """Accept a bounded JSON projection; reject secrets in keys *and* values."""
    if not isinstance(value, dict) or not value:
        raise LiveQualificationError(f"{field} must be a nonempty object")

    def inspect(item: object, depth: int) -> None:
        if depth > _MAX_EVIDENCE_DEPTH:
            raise LiveQualificationError(f"{field} exceeds maximum nesting")
        if isinstance(item, dict):
            if not item:
                raise LiveQualificationError(f"{field} contains an empty object")
            for key, nested in item.items():
                if not isinstance(key, str) or not key or len(key) > 64 or _SENSITIVE_KEY.search(key):
                    raise LiveQualificationError(f"{field} contains a sensitive or invalid key")
                inspect(nested, depth + 1)
        elif isinstance(item, list):
            if len(item) > 16:
                raise LiveQualificationError(f"{field} contains too many items")
            for nested in item:
                inspect(nested, depth + 1)
        elif isinstance(item, str):
            if len(item) > 256 or _SENSITIVE_VALUE.search(item):
                raise LiveQualificationError(f"{field} contains a sensitive or oversized value")
        elif item is None or type(item) in (int, float, bool):
            return
        else:
            raise LiveQualificationError(f"{field} must contain JSON primitives only")

    inspect(value, 0)
    if len(_canonical(value)) > _MAX_EVIDENCE_BYTES:
        raise LiveQualificationError(f"{field} exceeds maximum size")
    return value


class _JsonSnapshot(dict[str, Any]):
    """A strict JSON document bound to the SHA-256 of its parsed bytes."""

    def __init__(self, document: dict[str, Any], sha256_digest: str) -> None:
        super().__init__(document)
        self.sha256_digest = sha256_digest


def _load_json(path: Path, label: str, *, contained_by: Path | None = None) -> _JsonSnapshot:
    try:
        document, digest = load_strict_json_snapshot(path, label, contained_by=contained_by)
    except TrustRootVerificationError as error:
        raise LiveQualificationError(str(error)) from error
    return _JsonSnapshot(document, digest)


@dataclass(frozen=True)
class LiveExecutionPolicy:
    version: str
    episode_cap_usd: Decimal
    month_cap_usd: Decimal
    experiment_cap_usd: Decimal
    allowed_adapters: frozenset[str]
    publication_enabled: bool
    digest: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LiveExecutionPolicy:
        if not isinstance(raw, dict) or set(raw) != _POLICY_FIELDS or raw.get("schema") != 2 or raw.get("mode") != "LIVE":
            raise LiveQualificationError("closed LIVE policy schema and mode are required")
        version, allowed = raw.get("version"), raw.get("allowed_adapters")
        if not isinstance(version, str) or not version.strip() or len(version) > 128:
            raise LiveQualificationError("LIVE policy version is required")
        if not isinstance(allowed, list) or not allowed or len(allowed) != len(set(allowed)) or any(item not in _ADAPTER_ENDPOINTS for item in allowed):
            raise LiveQualificationError("LIVE policy adapter is not allowlisted")
        publication = raw.get("publication")
        if not isinstance(publication, dict) or set(publication) != {"enabled"} or type(publication["enabled"]) is not bool:
            raise LiveQualificationError("LIVE publication policy must contain only explicit enabled")
        return cls(version, _amount(raw["episode_cap_usd"], "episode_cap_usd"), _amount(raw["month_cap_usd"], "month_cap_usd"), _amount(raw["experiment_cap_usd"], "experiment_cap_usd"), frozenset(allowed), publication["enabled"], _hash(raw))


@dataclass(frozen=True)
class LiveQualification:
    """A preflight report, explicitly not a paid-execution authorization."""

    document: dict[str, Any]
    document_sha256: str
    execution_state: str
    authorization: None = None
    price: None = None


class LivePriceResolver(Protocol):
    def quote(self, job: Job) -> dict[str, Any]: ...


class LiveExecutionService:
    """Validate offline inputs and produce a write-once, blocked preflight report.

    ``executor`` and ``ledger`` are retained only to avoid accidental integration
    churn; this class never dereferences either one while execution is disabled.
    """

    def __init__(self, *, executor: object, ledger: object, policy: LiveExecutionPolicy,
                 quote_resolver: LivePriceResolver, trust_root: Path, clock=time.time):
        self.executor = executor
        self.ledger = ledger
        self.policy = policy
        self.quote_resolver = quote_resolver
        self.clock = clock
        self.trust_root = Path(trust_root)
        if not self.trust_root.is_absolute():
            raise LiveQualificationError("external trust root must be an absolute non-link directory")
        try:
            self.trust_root = reject_reparse_path(self.trust_root)
        except ValueError as error:
            raise LiveQualificationError("external trust root rejects symlink or reparse-point paths") from error
        if not self.trust_root.is_dir():
            raise LiveQualificationError("external trust root must be an absolute non-link directory")
        package_root = Path(__file__).absolute().parents[2]
        try:
            self.trust_root.relative_to(package_root)
        except ValueError:
            pass
        else:
            raise LiveQualificationError("trust root must be configured outside this package")
        root = _load_json(self.trust_root / "live-trust-root.json", "external trust root", contained_by=self.trust_root)
        if set(root) != _TRUST_ROOT_FIELDS or type(root.get("schema")) is not int or root.get("schema") != 1 or root.get("kind") != "LIVE_TRUST_ROOT" or not isinstance(root.get("issuer"), str) or not root["issuer"].strip() or root.get("verification") != "UNAVAILABLE":
            raise LiveQualificationError("unsupported external trust root contract")
        self.trust_root_sha256 = _hash(root)

    def _trusted_receipt(self, value: object, *, kind: str, job: Job) -> tuple[dict[str, Any], str]:
        if not isinstance(value, (str, Path)):
            raise LiveQualificationError("persisted QA receipt path is required; inline QA is not trusted")
        path = Path(value)
        if not path.is_absolute():
            raise LiveQualificationError("persisted QA receipt must be a regular absolute file")
        try:
            path = reject_reparse_path(path)
        except ValueError as error:
            raise LiveQualificationError("persisted QA receipt rejects symlink or reparse-point paths") from error
        if not path.is_file():
            raise LiveQualificationError("persisted QA receipt must be a regular absolute file")
        try:
            path.relative_to(self.trust_root)
        except ValueError as error:
            raise LiveQualificationError("persisted QA receipt must live below external trust root") from error
        receipt = _load_json(path, f"{kind} receipt", contained_by=self.trust_root)
        if set(receipt) != _QA_FIELDS:
            raise LiveQualificationError("QA receipt must use the closed schema")
        if (
            type(receipt.get("schema")) is not int
            or receipt.get("schema") != 1
            or receipt.get("kind") != kind
            or type(receipt.get("approved")) is not bool
            or receipt.get("approved") is not True
            or receipt.get("request_id") != job.request_id
            or receipt.get("manifest_sha256") != job.manifest.checksum
        ):
            raise LiveQualificationError("QA receipt does not bind this LIVE job")
        reviewer = receipt.get("reviewer")
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 128:
            raise LiveQualificationError("QA receipt reviewer is required")
        _safe_evidence(receipt["evidence"], "QA evidence")
        return receipt, receipt.sha256_digest

    def _quote(self, job: Job) -> tuple[dict[str, Any], Decimal]:
        quote = self.quote_resolver.quote(job)
        if not isinstance(quote, dict) or set(quote) != _QUOTE_FIELDS:
            raise LiveQualificationError("LIVE price quote must use the closed schema")
        amount = _amount(quote.get("amount"), "LIVE quoted maximum")
        valid_until = quote.get("valid_until")
        now = self.clock()
        if type(valid_until) not in (int, float) or not now < valid_until <= now + _MAX_QUOTE_SECONDS:
            raise LiveQualificationError("LIVE price quote must have a finite maximum window")
        if quote.get("endpoint") != job.endpoint or amount != job.cost:
            raise LiveQualificationError("LIVE quote must bind exact endpoint and reserved maximum")
        _safe_evidence(quote["evidence"], "LIVE price evidence")
        return quote, amount

    @staticmethod
    def _write_once(path: Path, document: dict[str, Any]) -> None:
        """Create once with O_EXCL; a crash leaves a fail-closed partial receipt."""
        encoded = _canonical(document)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                0o600,
            )
        except FileExistsError:
            existing = _load_json(path, "preflight receipt")
            if existing != document:
                raise LiveQualificationError("preflight receipt path is write-once")
            return
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if _load_json(path, "preflight receipt") != document:
            raise LiveQualificationError("preflight receipt readback mismatch")

    def preflight(self, job: Job, *, adapter: str, identity_qa_path: Path,
                  assets_qa_path: Path, receipt_path: Path,
                  signed_qa_evidence_path: Path | None = None) -> LiveQualification:
        if job.mode != "LIVE" or adapter not in self.policy.allowed_adapters or job.endpoint != _ADAPTER_ENDPOINTS.get(adapter):
            raise LiveQualificationError("LIVE adapter is not allowed by policy")
        job.manifest.verify("LIVE")
        identity, identity_sha256 = self._trusted_receipt(identity_qa_path, kind="IDENTITY_QA", job=job)
        assets, assets_sha256 = self._trusted_receipt(assets_qa_path, kind="ASSETS_QA", job=job)
        if identity["reviewer"] == assets["reviewer"]:
            raise LiveQualificationError("independent QA reviewers must be distinct")
        signed_qa_evidence = {"state": "UNAVAILABLE"}
        if signed_qa_evidence_path is not None:
            packet = _load_json(Path(signed_qa_evidence_path), "signed QA evidence", contained_by=self.trust_root)
            evidence = ExternalTrustRootVerifier(
                self.trust_root / "external-trust-root.json", clock=self.clock,
            ).verify(packet, expected={
                "request_id": job.request_id,
                "episode": job.scene,
                "source_sha256": _hash(job.payload),
                "assets_sha256": job.manifest.checksum,
                "policy_sha256": self.policy.digest,
            })
            signed_qa_evidence = {
                "state": evidence.provenance,
                "signer_id": evidence.signer_id,
                "role": evidence.role,
            }
        body = {
            "schema": 2, "kind": "LIVE_EXECUTION_PREFLIGHT", "execution_state": "BLOCKED_NOT_QUALIFIED",
            "block_reason": "external provenance authority and authenticated billing bridge are unavailable",
            "trust_root_sha256": self.trust_root_sha256, "trust_root_verification": "UNAVAILABLE",
            "policy_sha256": self.policy.digest, "request_id": job.request_id,
            "job_sha256": _hash({"request_id": job.request_id, "endpoint": job.endpoint, "manifest": job.manifest.checksum}),
            "manifest_sha256": job.manifest.checksum, "adapter": adapter,
            "identity_qa_sha256": identity_sha256, "assets_qa_sha256": assets_sha256,
            "signed_qa_evidence": signed_qa_evidence,
            "billing_bridge": "NOT_CONNECTED", "endpoint": job.endpoint,
            "reserved_maximum_cost_usd": "UNKNOWN", "actual_cost_usd": "UNKNOWN",
            "billing_status": "NOT_AUTHENTICATED", "publication_enabled": self.policy.publication_enabled,
        }
        document = {**body, "document_sha256": _hash(body)}
        self._write_once(Path(receipt_path), document)
        return LiveQualification(document, document["document_sha256"], document["execution_state"])

    async def dispatch(self, job: Job, *, preflight_path: Path) -> None:
        """Revalidate the immutable preflight, then unconditionally deny dispatch."""
        document = _load_json(Path(preflight_path), "preflight receipt")
        supplied = document.pop("document_sha256", None)
        if not isinstance(supplied, str) or supplied != _hash(document):
            raise LiveQualificationError("preflight receipt hash mismatch")
        if document.get("request_id") != job.request_id or document.get("job_sha256") != _hash({"request_id": job.request_id, "endpoint": job.endpoint, "manifest": job.manifest.checksum}):
            raise LiveQualificationError("preflight receipt does not bind this LIVE job")
        raise PermissionError("LIVE dispatch is disabled until external trust verification and authenticated billing settlement are implemented")