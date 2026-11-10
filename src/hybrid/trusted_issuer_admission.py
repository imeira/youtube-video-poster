"""TEST-only Studio consumer for a separately authenticated trusted issuer.

The public admission surface accepts one literal José operation identifier.
Receipt, revocation, policy, storage, verifier, provider, and clock are owned by
the composition.  Production construction is deliberately unavailable until a
real local authenticated transport and protected deployment are provisioned.
"""
from __future__ import annotations

import hmac
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.hybrid.execution import Executor, Job
from src.hybrid.planner import Config
from src.hybrid.standing_budget import StandingBudgetLedger
from src.path_guard import reject_reparse_path

JOSE_OPERATION_ID = "JOSE_GEN37_IDENTITY_FRAME_001"
_JOSE_EPISODE_ID = "JOSE_GENESIS37"
_RECEIPT_DOMAIN = b"studio-trusted-issuer-receipt/v1\0"
_REVOCATION_DOMAIN = b"studio-trusted-issuer-revocation/v1\0"
_SHA = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_TOKEN = object()
_RECEIPT_MAX_AGE = 300
_QUOTE_MAX_AGE = 300
_REVOCATION_MAX_AGE = 30


class TrustedIssuerAdmissionError(PermissionError):
    """The fixed trusted-issuer composition did not authorize execution."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise TrustedIssuerAdmissionError("duplicate issuer JSON key")
        result[key] = value
    return result


def _strict(raw: object, label: str) -> dict[str, Any]:
    if type(raw) is not bytes or not raw or len(raw) > 131_072:
        raise TrustedIssuerAdmissionError(f"{label} must be bounded authenticated bytes")
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as error:
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
        raise TrustedIssuerAdmissionError(f"{label} must be a canonical positive decimal")
    return amount


def _protected_path(path: Path, label: str) -> Path:
    try:
        return reject_reparse_path(path)
    except (OSError, ValueError) as error:
        raise TrustedIssuerAdmissionError(
            f"{label} must not traverse a link or reparse point"
        ) from error


def _literal_child(root: Path, name: str, label: str) -> Path:
    if not isinstance(name, str) or not name or any(token in name for token in ("/", "\\", ":", "..")):
        raise TrustedIssuerAdmissionError(f"{label} must be a source-owned filename")
    return _protected_path(root / name, label)


@dataclass(frozen=True, init=False)
class FinancialCapability:
    """Unforgeable-by-API cap derived from the protected policy document."""

    operation_id: str
    episode_id: str
    policy_sha256: str
    currency: str
    maximum_usd: Decimal

    def __init__(self, *, operation_id: str, episode_id: str, policy_sha256: str,
                 currency: str, maximum_usd: Decimal, _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("financial capabilities are minted only by canonical admission")
        object.__setattr__(self, "operation_id", operation_id)
        object.__setattr__(self, "episode_id", episode_id)
        object.__setattr__(self, "policy_sha256", policy_sha256)
        object.__setattr__(self, "currency", currency)
        object.__setattr__(self, "maximum_usd", maximum_usd)


@dataclass(frozen=True)
class _VerifiedAdmission:
    descriptor: dict[str, Any]
    descriptor_sha256: str
    receipt_id: str
    capability: FinancialCapability


class _PrePostFixtureProvider:
    """Composition-owned wrapper that revalidates immediately before POST/GET."""

    mode = "TEST"

    def __init__(self, provider: object, verify: Callable[[], _VerifiedAdmission]) -> None:
        if getattr(provider, "mode", None) != "TEST":
            raise TrustedIssuerAdmissionError("fixture provider must be TEST-only")
        self._provider, self._verify = provider, verify

    async def submit(self, job, request_id, checkpoint):
        self._verify()
        return await self._provider.submit(job, request_id, checkpoint)

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        self._verify()
        return await self._provider.recover(job, request_id, provider_id, partial, checkpoint)


class JoseTrustedIssuerAdmission:
    """Fixed José admission composition; LIVE is intentionally unprovisioned."""

    def __init__(self, *, root: Path, transport: object, issuer_key: bytes,
                 provider: object, now: Callable[[], int], _token: object) -> None:
        if _token is not _TOKEN:
            raise TypeError("use the fixed production or TEST fixture factory")
        root = Path(root)
        if not root.is_absolute():
            raise TrustedIssuerAdmissionError("fixture protected root must be absolute and non-linked")
        root = _protected_path(root, "fixture protected root")
        if getattr(transport, "mode", None) != "TEST" or getattr(provider, "mode", None) != "TEST":
            raise TrustedIssuerAdmissionError("only TEST fixture transport/provider are accepted")
        if type(issuer_key) is not bytes or len(issuer_key) < 16:
            raise TrustedIssuerAdmissionError("TEST issuer authentication key is invalid")
        if not callable(getattr(transport, "read_receipt", None)) or not callable(getattr(transport, "read_revocation", None)):
            raise TrustedIssuerAdmissionError("local issuer transport is incomplete")
        if not callable(now):
            raise TrustedIssuerAdmissionError("fixture clock is required")
        self._root, self._transport, self._issuer_key = root, transport, issuer_key
        self._provider, self._now = provider, now
        self.last_financial_capability: FinancialCapability | None = None

    @classmethod
    def production(cls):
        raise TrustedIssuerAdmissionError("LIVE activation is not provisioned")

    @classmethod
    def for_test(cls, *, root: Path, transport: object, issuer_key: bytes,
                 provider: object, now: Callable[[], int]):
        return cls(root=root, transport=transport, issuer_key=issuer_key,
                   provider=provider, now=now, _token=_TOKEN)

    def _authenticated(self, raw: bytes, *, domain: bytes, label: str) -> dict[str, Any]:
        envelope = _closed(_strict(raw, label), {"payload", "signature"}, f"{label} envelope")
        payload = envelope["payload"]
        signature = envelope["signature"]
        if type(payload) is not dict or not isinstance(signature, str) or not _SHA.fullmatch(signature):
            raise TrustedIssuerAdmissionError(f"{label} authentication envelope is invalid")
        expected = hmac.new(self._issuer_key, domain + _canonical(payload), sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise TrustedIssuerAdmissionError(f"{label} authentication failed")
        return payload

    def _source(self) -> tuple[dict[str, Any], str, dict[str, Any], str]:
        descriptor_path = _protected_path(
            self._root / "operations" / f"{JOSE_OPERATION_ID}.json",
            "José operation descriptor",
        )
        try:
            descriptor_raw = descriptor_path.read_bytes()
        except OSError as error:
            raise TrustedIssuerAdmissionError("José operation descriptor is unavailable") from error
        descriptor = _closed(_strict(descriptor_raw, "José operation descriptor"), {
            "schema", "operation_id", "episode_id", "mode", "endpoint", "scene",
            "category", "input_kind", "payload", "financial_policy",
        }, "José operation descriptor")
        if (
            descriptor["schema"] != "jose-trusted-operation/v1"
            or descriptor["operation_id"] != JOSE_OPERATION_ID
            or descriptor["episode_id"] != _JOSE_EPISODE_ID
            or descriptor["mode"] != "TEST"
            or descriptor["category"] not in {"first", "thumbnail"}
            or descriptor["input_kind"] != "TEXT_TO_IMAGE"
            or not all(isinstance(descriptor[key], str) and descriptor[key] for key in ("endpoint", "scene"))
            or type(descriptor["payload"]) is not dict
        ):
            raise TrustedIssuerAdmissionError("distinct closed José operation descriptor is required")
        binding = _closed(descriptor["financial_policy"], {"name", "sha256"}, "financial policy binding")
        policy_path = _literal_child(self._root, binding["name"], "financial policy")
        try:
            policy_raw = policy_path.read_bytes()
        except OSError as error:
            raise TrustedIssuerAdmissionError("José financial policy is unavailable") from error
        policy_sha = sha256(policy_raw).hexdigest()
        if policy_sha != _digest(binding["sha256"], "financial policy binding"):
            raise TrustedIssuerAdmissionError("José financial policy changed")
        policy = _closed(_strict(policy_raw, "José financial policy"), {
            "schema", "operation_id", "episode_id", "currency", "maximum_usd", "status",
        }, "José financial policy")
        if (
            policy["schema"] != "jose-financial-policy/v1"
            or policy["operation_id"] != JOSE_OPERATION_ID
            or policy["episode_id"] != _JOSE_EPISODE_ID
            or policy["currency"] != "USD"
            or policy["status"] != "ACTIVE"
        ):
            raise TrustedIssuerAdmissionError("active José financial policy is required")
        _money(policy["maximum_usd"], "José policy maximum")
        return descriptor, sha256(descriptor_raw).hexdigest(), policy, policy_sha

    def _verify(self, expected: _VerifiedAdmission | None = None) -> _VerifiedAdmission:
        descriptor, descriptor_sha, policy, policy_sha = self._source()
        receipt = self._authenticated(
            self._transport.read_receipt(JOSE_OPERATION_ID), domain=_RECEIPT_DOMAIN,
            label="issuer receipt",
        )
        revocation = self._authenticated(
            self._transport.read_revocation(JOSE_OPERATION_ID), domain=_REVOCATION_DOMAIN,
            label="issuer revocation",
        )
        receipt = _closed(receipt, {
            "schema", "receipt_id", "operation_id", "descriptor_sha256", "policy_sha256",
            "status", "issued_at", "expires_at", "telegram_approval", "quote", "license",
        }, "issuer receipt")
        now = self._now()
        if type(now) is not int:
            raise TrustedIssuerAdmissionError("canonical verification clock is invalid")
        if (
            receipt["schema"] != "studio-trusted-issuer-receipt/v1"
            or receipt["operation_id"] != JOSE_OPERATION_ID
            or receipt["descriptor_sha256"] != descriptor_sha
            or receipt["policy_sha256"] != policy_sha
            or receipt["status"] != "APPROVED"
            or type(receipt["issued_at"]) is not int
            or type(receipt["expires_at"]) is not int
            or not receipt["issued_at"] <= now < receipt["expires_at"]
            or now - receipt["issued_at"] > _RECEIPT_MAX_AGE
            or not isinstance(receipt["receipt_id"], str) or not receipt["receipt_id"]
        ):
            raise TrustedIssuerAdmissionError("fresh approved José issuer receipt is required")
        telegram = _closed(receipt["telegram_approval"], {
            "chat_id", "sender_id", "message_id", "reply_to_message_id", "update_sha256",
        }, "Telegram approval binding")
        if (
            not all(isinstance(telegram[key], str) and telegram[key].isdecimal() for key in ("chat_id", "sender_id"))
            or type(telegram["message_id"]) is not int or telegram["message_id"] <= 0
            or type(telegram["reply_to_message_id"]) is not int or telegram["reply_to_message_id"] <= 0
        ):
            raise TrustedIssuerAdmissionError("canonical Telegram approval binding is invalid")
        _digest(telegram["update_sha256"], "Telegram update")
        cap = _money(policy["maximum_usd"], "José policy maximum")
        quote = _closed(receipt["quote"], {
            "endpoint", "currency", "maximum_usd", "observed_at", "expires_at", "evidence_sha256",
        }, "authenticated quote")
        if (
            quote["endpoint"] != descriptor["endpoint"] or quote["currency"] != policy["currency"]
            or _money(quote["maximum_usd"], "quote maximum") != cap
            or type(quote["observed_at"]) is not int or type(quote["expires_at"]) is not int
            or not quote["observed_at"] <= now < quote["expires_at"]
            or now - quote["observed_at"] > _QUOTE_MAX_AGE
        ):
            raise TrustedIssuerAdmissionError("fresh cap-bound authenticated quote is required")
        _digest(quote["evidence_sha256"], "quote evidence")
        license_receipt = _closed(receipt["license"], {
            "scope", "status", "valid_until", "evidence_sha256",
        }, "license capability")
        if (
            license_receipt["scope"] != f"{_JOSE_EPISODE_ID}:PAID_GENERATION"
            or license_receipt["status"] != "VALID"
            or type(license_receipt["valid_until"]) is not int
            or license_receipt["valid_until"] <= now
        ):
            raise TrustedIssuerAdmissionError("fresh José license capability is required")
        _digest(license_receipt["evidence_sha256"], "license evidence")
        revocation = _closed(revocation, {
            "schema", "operation_id", "receipt_id", "status", "observed_at", "expires_at",
        }, "revocation status")
        if (
            revocation["schema"] != "studio-trusted-issuer-revocation/v1"
            or revocation["operation_id"] != JOSE_OPERATION_ID
            or revocation["receipt_id"] != receipt["receipt_id"]
            or revocation["status"] != "NOT_REVOKED"
            or type(revocation["observed_at"]) is not int
            or type(revocation["expires_at"]) is not int
            or not revocation["observed_at"] <= now < revocation["expires_at"]
            or now - revocation["observed_at"] > _REVOCATION_MAX_AGE
        ):
            raise TrustedIssuerAdmissionError("fresh non-revoked issuer status is required")
        capability = FinancialCapability(
            operation_id=JOSE_OPERATION_ID, episode_id=_JOSE_EPISODE_ID,
            policy_sha256=policy_sha, currency=policy["currency"], maximum_usd=cap,
            _token=_TOKEN,
        )
        verified = _VerifiedAdmission(
            descriptor, descriptor_sha, receipt["receipt_id"], capability
        )
        if expected is not None and (
            verified.descriptor_sha256 != expected.descriptor_sha256
            or verified.capability.policy_sha256 != expected.capability.policy_sha256
            or verified.receipt_id != expected.receipt_id
        ):
            raise TrustedIssuerAdmissionError(
                "verification snapshot changed the admitted issuer identity"
            )
        self.last_financial_capability = capability
        return verified

    async def admit(self, operation_id: str) -> dict[str, Any]:
        if operation_id != JOSE_OPERATION_ID:
            raise TrustedIssuerAdmissionError("fixed José operation ID is required")
        admitted = self._verify()  # before factory construction
        descriptor, capability = admitted.descriptor, admitted.capability
        state = _protected_path(self._root / "state", "admission state directory")
        ledger_path = _protected_path(
            state / "ledger.sqlite3", "standing budget database"
        )
        executor_path = _protected_path(
            state / "executor.sqlite3", "executor database"
        )
        ledger = StandingBudgetLedger(
            ledger_path, policy_sha256=capability.policy_sha256,
            episode_limit_usd=str(capability.maximum_usd), month_limit_usd=str(capability.maximum_usd),
            experiment_limit_usd=str(capability.maximum_usd), mode="TEST",
        )
        executor = Executor(
            executor_path,
            Config(limit=capability.maximum_usd, concurrency=1), standing_ledger=ledger,
        )
        job = Job(
            descriptor["scene"], descriptor["category"], "TEST", descriptor["endpoint"],
            descriptor["payload"], None, capability.maximum_usd,
            input_kind=descriptor["input_kind"],
        )
        self._verify(admitted)  # before the canonical executor claim/intent
        if executor.inspect(job.request_id) is None:
            executor.prepare_intent(job, auth_scope="TEST_LOCAL_ONLY")
        self._verify(admitted)  # before the canonical standing-ledger reservation
        ledger.reserve(
            job.request_id, capability.episode_id, job.endpoint, capability.maximum_usd,
            settlement_mode="FAL_READBACK_V2", auth_scope="TEST_LOCAL_ONLY",
            provider_request_id="UNASSIGNED", authority_mode="TEST",
        )
        provider = _PrePostFixtureProvider(
            self._provider, lambda: self._verify(admitted)
        )
        return await executor.run(job, provider, submit_prepared=True)
