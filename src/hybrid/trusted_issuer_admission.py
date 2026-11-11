"""Hermes-backed fixed admission for the Jose Genesis 37 identity frame."""
from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_CEILING, Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import Any

from src.hybrid.canonical_operation_binding import (
    CanonicalOperationBindingError,
    _stable_bytes,
)
from src.hybrid.execution import Authorization, Executor, Job, Price
from src.hybrid.hermes_issuer_client import (
    HermesIssuerClient,
    HermesIssuerClientError,
)
from src.hybrid.live import FalFluxProvider
from src.hybrid.planner import Config
from src.hybrid.standing_budget import StandingBudgetLedger
from src.path_guard import reject_reparse_path

JOSE_OPERATION_ID = "JOSE_GEN37_IDENTITY_FRAME_001"
_JOSE_EPISODE_ID = "JOSE_GENESIS37"
_PRODUCTION_ROOT = Path("C:/HermesStudio/production/trusted-issuer")
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_TOKEN = object()
_EXECUTION_TOKEN = object()
_HERMES_DESCRIPTOR_FIELDS = {
    "schema_version", "descriptor_id", "operation_id", "episode_id", "operation_kind",
    "provider_id", "endpoint_id", "input_binding_sha256", "standing_policy_id",
    "standing_policy_sha256", "financial_policy_id", "financial_policy_sha256",
    "currency", "max_amount_minor", "request_text_utf8_sha256", "request_text",
    "confirmation_token", "expires_at_utc", "producer_instance_id", "generation",
    "authority_mode",
}
_HERMES_RECEIPT_FIELDS = {
    "receipt_id", "receipt_digest", "descriptor_id", "descriptor_digest",
    "delivery_intent_id", "delivery_receipt_digest", "authenticated_inbound_event_digest",
    "gateway_instance_id", "update_id", "platform", "chat_id", "sender_id", "message_id",
    "reply_to_message_id", "issued_at_utc", "authority_mode",
}


class TrustedIssuerAdmissionError(PermissionError):
    """The fixed protected composition did not authorize execution."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise TrustedIssuerAdmissionError("duplicate protected JSON key")
        value[key] = item
    return value


def _strict(raw: bytes, label: str) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > 131_072:
        raise TrustedIssuerAdmissionError(f"{label} must be bounded bytes")
    try:
        value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_pairs,
                           parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise TrustedIssuerAdmissionError(f"{label} is not strict JSON") from error
    if type(value) is not dict:
        raise TrustedIssuerAdmissionError(f"{label} must be an object")
    return value


def _closed(value: object, fields: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != fields:
        raise TrustedIssuerAdmissionError(f"closed {label} schema is required")
    return value


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA.fullmatch(value) is None:
        raise TrustedIssuerAdmissionError(f"{label} must be lowercase SHA-256")
    return value


def _money(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise TrustedIssuerAdmissionError(f"{label} must be a decimal string")
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as error:
        raise TrustedIssuerAdmissionError(f"{label} is invalid") from error
    if not amount.is_finite() or amount <= 0 or str(amount) != value:
        raise TrustedIssuerAdmissionError(f"{label} must be canonical and positive")
    return amount


def _utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise TrustedIssuerAdmissionError(f"{label} must be UTC ISO-8601")
    try:
        parsed = datetime.fromisoformat(value).astimezone(UTC)
    except ValueError as error:
        raise TrustedIssuerAdmissionError(f"{label} is invalid") from error
    if parsed.isoformat(timespec="microseconds").replace("+00:00", "Z") != value:
        raise TrustedIssuerAdmissionError(f"{label} is not canonical Hermes time")
    return parsed


def _evidence_time(value: object, label: str) -> int:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise TrustedIssuerAdmissionError(f"{label} must be UTC ISO-8601")
    try:
        return int(datetime.fromisoformat(value).timestamp())
    except ValueError as error:
        raise TrustedIssuerAdmissionError(f"{label} is invalid") from error


def _protected_path(path: Path, label: str, *, production: bool) -> Path:
    try:
        checked = reject_reparse_path(path)
    except (OSError, ValueError) as error:
        raise TrustedIssuerAdmissionError(f"{label} is unavailable or reparse-linked") from error
    return checked


def _literal_child(root: Path, name: object, label: str, *, production: bool) -> Path:
    if not isinstance(name, str) or not name or any(v in name for v in ("/", "\\", ":", "..")):
        raise TrustedIssuerAdmissionError(f"{label} filename is not source-owned")
    return root / name


@dataclass(frozen=True, init=False)
class FinancialCapability:
    operation_id: str
    episode_id: str
    policy_sha256: str
    currency: str
    maximum_usd: Decimal

    def __init__(self, *, operation_id: str, episode_id: str, policy_sha256: str,
                 currency: str, maximum_usd: Decimal, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("financial capabilities are composition-owned")
        for name, value in (("operation_id", operation_id), ("episode_id", episode_id),
                            ("policy_sha256", policy_sha256), ("currency", currency),
                            ("maximum_usd", maximum_usd)):
            object.__setattr__(self, name, value)


@dataclass(frozen=True)
class _VerifiedAdmission:
    operation_sha256: str
    descriptor_digest: str
    receipt_id: str
    job: Job
    capability: FinancialCapability
    quote: dict[str, Any]


class _FrozenRootPolicyLoader:
    __slots__ = ("_production", "_root")

    def __init__(self, root: Path, *, production: bool) -> None:
        self._root, self._production = root, production

    def load(self, binding: object, label: str) -> tuple[dict[str, Any], str]:
        binding = _closed(binding, {"name", "sha256"}, f"{label} binding")
        path = _literal_child(self._root, binding["name"], label, production=self._production)
        try:
            raw = _stable_bytes(path, label, protected=self._production)
        except (CanonicalOperationBindingError, OSError) as error:
            raise TrustedIssuerAdmissionError(f"{label} is unavailable") from error
        actual = sha256(raw).hexdigest()
        if actual != _digest(binding["sha256"], f"{label} binding"):
            raise TrustedIssuerAdmissionError(f"{label} changed")
        return _strict(raw, label), actual


class _ProviderRegistry:
    __slots__ = ("_entries",)

    def __init__(self, endpoint: str, factory: Callable[[], object]) -> None:
        self._entries = MappingProxyType({endpoint: factory})

    def create(self, endpoint: str) -> object:
        factory = self._entries.get(endpoint)
        if factory is None:
            raise TrustedIssuerAdmissionError("provider is not registered by fixed composition")
        provider = factory()
        if not callable(getattr(provider, "submit", None)) or not callable(getattr(provider, "recover", None)):
            raise TrustedIssuerAdmissionError("registered provider factory is invalid")
        return provider


class _RevalidatingProvider:
    __slots__ = ("_admission", "_expected", "_provider", "mode")

    def __init__(self, provider: object, admission: JoseTrustedIssuerAdmission,
                 expected: _VerifiedAdmission, mode: str):
        if getattr(provider, "mode", None) != mode:
            raise TrustedIssuerAdmissionError("registered provider mode changed")
        self._provider = provider
        self._admission = admission
        self._expected = expected
        self.mode = mode

    async def submit(self, job, request_id, checkpoint):
        self._admission._verify(self._expected)
        return await self._provider.submit(job, request_id, checkpoint)

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        self._admission._verify(self._expected)
        return await self._provider.recover(job, request_id, provider_id, partial, checkpoint)


class _TrustedIssuerExecutionLoader:
    """Non-forgeable loader that reopens the fixed protected issuer source."""

    __slots__ = ("_admission", "_expected")

    def __init__(self, admission: JoseTrustedIssuerAdmission,
                 expected: _VerifiedAdmission, *, _token: object) -> None:
        if (
            _token is not _EXECUTION_TOKEN
            or type(admission) is not JoseTrustedIssuerAdmission
            or type(expected) is not _VerifiedAdmission
        ):
            raise TypeError("trusted issuer execution loaders are composition-owned")
        self._admission = admission
        self._expected = expected

    def authorize(self, job: Job) -> str:
        verified = self._admission._verify(self._expected)
        if job.mode != "LIVE" or verified.job != job:
            raise TrustedIssuerAdmissionError("trusted issuer job binding changed")
        return f"HERMES_RECEIPT:{verified.receipt_id}"


class JoseTrustedIssuerAdmission:
    """Fixed composition whose only public input is the literal operation ID."""

    def __init__(self, *, root: Path, client: HermesIssuerClient,
                 provider_factory: Callable[[], object], now: Callable[[], int],
                 production: bool, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("use production() or for_test_ipc()")
        root = Path(root)
        if not root.is_absolute():
            raise TrustedIssuerAdmissionError("protected root must be absolute")
        self._production = production
        self._root = _protected_path(root, "trusted issuer root", production=production)
        self._client = client
        self._loader = _FrozenRootPolicyLoader(self._root, production=production)
        self._provider_factory = provider_factory
        self._now = now
        self.last_financial_capability: FinancialCapability | None = None

    @classmethod
    def production(cls):
        """Compose the fixed deployment, or report its normal unprovisioned state."""
        try:
            admission = cls(
                root=_PRODUCTION_ROOT,
                client=HermesIssuerClient.production(),
                provider_factory=lambda: FalFluxProvider(_PRODUCTION_ROOT / "quarantine"),
                now=lambda: int(time.time()), production=True, _token=_TOKEN,
            )
            admission._source()
            return admission
        except (TrustedIssuerAdmissionError, HermesIssuerClientError, OSError) as error:
            return {
                "status": "BLOCKED", "operation_id": JOSE_OPERATION_ID,
                "reason": f"protected production deployment is unprovisioned: {error}",
            }

    @classmethod
    def for_test_ipc(cls, *, root: Path, client: HermesIssuerClient,
                     provider_factory: Callable[[], object], now: Callable[[], int]):
        if not isinstance(client, HermesIssuerClient) or not callable(provider_factory) or not callable(now):
            raise TypeError("explicit TEST IPC composition is required")
        return cls(root=root, client=client, provider_factory=provider_factory,
                   now=now, production=False, _token=_TOKEN)

    def _source(self) -> dict[str, Any]:
        path = self._root / "operations" / f"{JOSE_OPERATION_ID}.json"
        try:
            raw = _stable_bytes(
                path, "Jose operation descriptor", protected=self._production
            )
        except (CanonicalOperationBindingError, OSError) as error:
            raise TrustedIssuerAdmissionError("Jose operation descriptor is unavailable") from error
        operation = _closed(_strict(raw, "Jose operation descriptor"), {
            "schema", "operation_id", "episode_id", "mode", "scene", "category",
            "input_kind", "endpoint", "payload", "hermes_receipt_id", "hermes_descriptor",
            "standing_policy", "financial_policy", "quote", "license", "provider_registration",
        }, "Jose operation descriptor")
        expected_mode = "LIVE" if self._production else "TEST"
        if (
            operation["schema"] != "jose-trusted-operation/v2"
            or operation["operation_id"] != JOSE_OPERATION_ID
            or operation["episode_id"] != _JOSE_EPISODE_ID
            or operation["mode"] != expected_mode
            or operation["category"] not in {"first", "thumbnail"}
            or operation["input_kind"] != "TEXT_TO_IMAGE"
            or not all(isinstance(operation[k], str) and operation[k]
                       for k in ("scene", "endpoint", "hermes_receipt_id"))
            or type(operation["payload"]) is not dict
        ):
            raise TrustedIssuerAdmissionError("fixed Jose operation descriptor is invalid")
        operation["_raw_sha256"] = sha256(raw).hexdigest()
        return operation

    def _verify(self, expected: _VerifiedAdmission | None = None) -> _VerifiedAdmission:
        operation = self._source()
        standing, standing_sha = self._loader.load(operation["standing_policy"], "standing policy")
        financial, financial_sha = self._loader.load(operation["financial_policy"], "financial policy")
        quote, _ = self._loader.load(operation["quote"], "provider quote")
        license_evidence, _ = self._loader.load(operation["license"], "provider license")
        registration, _ = self._loader.load(operation["provider_registration"], "provider registration")
        standing = _closed(standing, {
            "schema", "policy_id", "operation_id", "episode_id", "operation_kind", "provider_id",
            "endpoint_id", "authority_mode", "gateway_instance_id", "chat_id", "sender_id",
            "receipt_max_age_seconds", "quote_max_age_seconds", "license_scope",
            "provider_endpoint", "provider_kind",
        }, "standing policy")
        financial = _closed(financial, {
            "schema", "policy_id", "operation_id", "episode_id", "currency",
            "max_amount_minor", "maximum_usd", "status",
        }, "financial policy")
        mode = operation["mode"]
        expected_provider_kind = "FAL_FLUX" if mode == "LIVE" else "TEST_FAKE"
        if (
            standing["schema"] != "jose-standing-policy/v1"
            or standing["operation_id"] != JOSE_OPERATION_ID
            or standing["episode_id"] != _JOSE_EPISODE_ID
            or standing["operation_kind"] != "image_generation"
            or standing["provider_id"] != "openai"
            or standing["endpoint_id"] != "openai.images.generations"
            or standing["authority_mode"] != mode
            or standing["provider_endpoint"] != operation["endpoint"]
            or standing["provider_kind"] != expected_provider_kind
            or type(standing["receipt_max_age_seconds"]) is not int
            or not 1 <= standing["receipt_max_age_seconds"] <= 300
            or type(standing["quote_max_age_seconds"]) is not int
            or not 1 <= standing["quote_max_age_seconds"] <= 300
        ):
            raise TrustedIssuerAdmissionError("standing root policy does not bind the operation")
        if (
            financial["schema"] != "jose-financial-policy/v1"
            or not isinstance(financial["policy_id"], str) or not financial["policy_id"]
            or financial["operation_id"] != JOSE_OPERATION_ID
            or financial["episode_id"] != _JOSE_EPISODE_ID
            or financial["currency"] != "USD" or financial["status"] != "ACTIVE"
            or type(financial["max_amount_minor"]) is not int
            or financial["max_amount_minor"] < 1
        ):
            raise TrustedIssuerAdmissionError("financial policy is not active and bound")
        maximum = _money(financial["maximum_usd"], "financial maximum")
        if int((maximum * 100).to_integral_value(rounding=ROUND_CEILING)) != financial["max_amount_minor"]:
            raise TrustedIssuerAdmissionError("financial minor-unit ceiling changed")

        descriptor = _closed(operation["hermes_descriptor"], _HERMES_DESCRIPTOR_FIELDS, "Hermes descriptor")
        descriptor_payload = {key: descriptor[key] for key in descriptor if key != "descriptor_id"}
        descriptor_digest = sha256(_canonical(descriptor_payload)).hexdigest()
        payload_digest = sha256(_canonical(operation["payload"])).hexdigest()
        if (
            descriptor["schema_version"] != 1
            or descriptor["descriptor_id"] != descriptor_digest
            or descriptor["operation_id"] != JOSE_OPERATION_ID
            or descriptor["episode_id"] != _JOSE_EPISODE_ID
            or descriptor["operation_kind"] != standing["operation_kind"]
            or descriptor["provider_id"] != standing["provider_id"]
            or descriptor["endpoint_id"] != standing["endpoint_id"]
            or descriptor["input_binding_sha256"] != payload_digest
            or descriptor["standing_policy_id"] != standing["policy_id"]
            or descriptor["standing_policy_sha256"] != standing_sha
            or descriptor["financial_policy_id"] != financial["policy_id"]
            or descriptor["financial_policy_sha256"] != financial_sha
            or descriptor["currency"] != financial["currency"]
            or descriptor["max_amount_minor"] != financial["max_amount_minor"]
            or descriptor["authority_mode"] != mode
            or not isinstance(descriptor["request_text"], str)
            or descriptor["request_text_utf8_sha256"]
            != sha256(descriptor["request_text"].encode("utf-8")).hexdigest()
            or not isinstance(descriptor["confirmation_token"], str)
            or descriptor["confirmation_token"] not in descriptor["request_text"]
        ):
            raise TrustedIssuerAdmissionError("Hermes descriptor binding is invalid")
        now = self._now()
        if type(now) is not int or int(_utc(descriptor["expires_at_utc"], "Hermes descriptor expiry").timestamp()) <= now:
            raise TrustedIssuerAdmissionError("Hermes descriptor is expired")
        try:
            snapshot = self._client.current(operation["hermes_receipt_id"])
        except HermesIssuerClientError as error:
            raise TrustedIssuerAdmissionError("current Hermes receipt is unavailable") from error
        receipt = _closed(snapshot["receipt"], _HERMES_RECEIPT_FIELDS, "Hermes receipt")
        if snapshot["revocation"] is not None:
            revocation = _closed(snapshot["revocation"], {
                "revocation_id", "receipt_id", "receipt_digest", "generation", "reason", "revoked_at_utc",
            }, "Hermes revocation")
            if (revocation["receipt_id"] != receipt.get("receipt_id")
                    or revocation["receipt_digest"] != receipt.get("receipt_digest")):
                raise TrustedIssuerAdmissionError("Hermes revocation binding is invalid")
            raise TrustedIssuerAdmissionError("Hermes receipt is currently revoked")
        receipt_payload = {key: receipt[key] for key in receipt if key != "receipt_digest"}
        issued = int(_utc(receipt["issued_at_utc"], "Hermes receipt issuance").timestamp())
        if (
            receipt["receipt_id"] != operation["hermes_receipt_id"]
            or receipt["receipt_digest"] != sha256(_canonical(receipt_payload)).hexdigest()
            or receipt["descriptor_id"] != descriptor["descriptor_id"]
            or receipt["descriptor_digest"] != descriptor_digest
            or receipt["authority_mode"] != mode
            or receipt["gateway_instance_id"] != standing["gateway_instance_id"]
            or receipt["platform"] != "telegram"
            or receipt["chat_id"] != standing["chat_id"]
            or receipt["sender_id"] != standing["sender_id"]
            or not issued <= now
            or now - issued > standing["receipt_max_age_seconds"]
        ):
            raise TrustedIssuerAdmissionError("fresh descriptor-bound Hermes receipt is required")

        quote = _closed(quote, {
            "schema", "operation_id", "endpoint", "currency", "maximum_usd",
            "observed_at_utc", "expires_at_utc", "evidence_sha256",
        }, "provider quote")
        observed = _evidence_time(quote["observed_at_utc"], "quote observation")
        quote_expiry = _evidence_time(quote["expires_at_utc"], "quote expiry")
        if (
            quote["schema"] != "jose-provider-quote/v1"
            or quote["operation_id"] != JOSE_OPERATION_ID
            or quote["endpoint"] != operation["endpoint"]
            or quote["currency"] != financial["currency"]
            or _money(quote["maximum_usd"], "quote maximum") != maximum
            or not observed <= now < quote_expiry
            or now - observed > standing["quote_max_age_seconds"]
        ):
            raise TrustedIssuerAdmissionError("fresh independently bound quote is required")
        _digest(quote["evidence_sha256"], "quote evidence")
        license_evidence = _closed(license_evidence, {
            "schema", "operation_id", "scope", "status", "valid_until_utc", "evidence_sha256",
        }, "provider license")
        if (
            license_evidence["schema"] != "jose-provider-license/v1"
            or license_evidence["operation_id"] != JOSE_OPERATION_ID
            or license_evidence["scope"] != standing["license_scope"]
            or license_evidence["status"] != "VALID"
            or _evidence_time(license_evidence["valid_until_utc"], "license expiry") <= now
        ):
            raise TrustedIssuerAdmissionError("current independently bound license is required")
        _digest(license_evidence["evidence_sha256"], "license evidence")
        registration = _closed(registration, {"schema", "endpoint", "provider_kind", "mode"}, "provider registration")
        if registration != {
            "schema": "jose-provider-registration/v1", "endpoint": operation["endpoint"],
            "provider_kind": standing["provider_kind"], "mode": mode,
        }:
            raise TrustedIssuerAdmissionError("fixed provider registration changed")

        capability = FinancialCapability(
            operation_id=JOSE_OPERATION_ID, episode_id=_JOSE_EPISODE_ID,
            policy_sha256=financial_sha, currency="USD", maximum_usd=maximum, _token=_TOKEN,
        )
        job = Job(operation["scene"], operation["category"], mode, operation["endpoint"],
                  operation["payload"], None, maximum, input_kind=operation["input_kind"])
        verified = _VerifiedAdmission(operation["_raw_sha256"], descriptor_digest,
                                      receipt["receipt_id"], job, capability, quote)
        if expected is not None and (
            verified.operation_sha256 != expected.operation_sha256
            or verified.descriptor_digest != expected.descriptor_digest
            or verified.receipt_id != expected.receipt_id
            or verified.job.request_id != expected.job.request_id
            or verified.capability.policy_sha256 != expected.capability.policy_sha256
        ):
            raise TrustedIssuerAdmissionError("admitted issuer identity changed")
        self.last_financial_capability = capability
        return verified

    async def admit(self, operation_id: str) -> dict[str, Any]:
        if operation_id != JOSE_OPERATION_ID:
            raise TrustedIssuerAdmissionError("fixed Jose operation ID is required")
        admitted = self._verify()
        job, capability = admitted.job, admitted.capability
        state = _protected_path(self._root / "state", "admission state", production=self._production)
        ledger = StandingBudgetLedger(
            state / "ledger.sqlite3", policy_sha256=capability.policy_sha256,
            episode_limit_usd=str(capability.maximum_usd),
            month_limit_usd=str(capability.maximum_usd),
            experiment_limit_usd=str(capability.maximum_usd), mode=job.mode,
        )
        auth_scope = f"HERMES_RECEIPT:{admitted.receipt_id}"
        executor = Executor(
            state / "executor.sqlite3", Config(limit=capability.maximum_usd, concurrency=1),
            standing_ledger=ledger,
        )
        execution_loader = (
            _TrustedIssuerExecutionLoader(self, admitted, _token=_EXECUTION_TOKEN)
            if job.mode == "LIVE" else None
        )
        self._verify(admitted)
        if executor.inspect(job.request_id) is None:
            if execution_loader is None:
                executor.prepare_intent(job, auth_scope="TEST_LOCAL_ONLY")
            else:
                executor._prepare_trusted_issuer_intent(job, execution_loader)
        self._verify(admitted)
        ledger.reserve(
            job.request_id, _JOSE_EPISODE_ID, job.endpoint, capability.maximum_usd,
            settlement_mode="FAL_READBACK_V2",
            auth_scope=auth_scope if job.mode == "LIVE" else "TEST_LOCAL_ONLY",
            provider_request_id="UNASSIGNED", authority_mode=job.mode,
        )
        registry = _ProviderRegistry(job.endpoint, self._provider_factory)
        provider = _RevalidatingProvider(
            registry.create(job.endpoint), self, admitted, job.mode
        )
        authorization = price = None
        if job.mode == "LIVE":
            valid_until = _evidence_time(admitted.quote["expires_at_utc"], "quote expiry")
            authorization = Authorization(
                job.request_id, "HERMES_TRUSTED_ISSUER", valid_until, job.cost
            )
            price = Price(
                job.endpoint, job.request_id, job.cost, valid_until,
                admitted.quote["evidence_sha256"],
            )
        if execution_loader is not None:
            return await executor._run_trusted_issuer(
                job, provider, execution_loader,
                authorization=authorization, price=price,
            )
        return await executor.run(job, provider, submit_prepared=True)
