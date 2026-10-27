"""Offline-only, authenticated financial authority for standing TEST budgets.

This is intentionally separate from media approvals: it cannot approve QA,
publish media, settle billing, construct a provider, or enable LIVE dispatch.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol

from src.approval.inbound import AuthenticatedApprovalSource
from src.hybrid.locks import file_lock, reject_reparse_path
from src.hybrid.standing_budget import StandingBudgetLedger

_REQUEST_KEYS = {
    "schema", "kind", "mode", "request_id", "episode_id", "policy_sha256", "expires_at",
    "allowed_providers", "currency", "caps_usd",
}
_CAP_KEYS = {"episode", "month", "experiment"}
_RECEIPT_KEYS = {"schema", "kind", "request", "request_sha256", "policy_sha256", "delivery", "inbound_receipt"}
_DELIVERY_KEYS = {"schema", "kind", "request_id", "request_sha256", "chat_id", "message_id", "transport", "body_sha256"}
_INTENT_KEYS = {"schema", "kind", "request_id", "request_sha256", "chat_id", "body_sha256"}
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_HASH = re.compile(r"[a-f0-9]{64}\Z")


class BudgetAuthorizationError(ValueError):
    """Financial authority is absent, malformed, stale, or untrusted."""


class AuthenticatedBudgetOutbound(Protocol):
    """Composition-owned outbound boundary; no package implementation sends."""
    def deliver(self, *, chat_id: str, canonical_body: bytes) -> dict[str, object]: ...
    def revalidate_delivery(self, document: object) -> dict[str, object] | None: ...


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(reject_reparse_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise BudgetAuthorizationError(f"{label} is unreadable") from error
    if type(value) is not dict:
        raise BudgetAuthorizationError(f"{label} must be an object")
    return value


def _fsync_parent(path: Path) -> None:
    """Durably publish a POSIX directory entry; Windows has no directory fd API."""
    if os.name == "nt":
        return
    fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _write_once(path: Path, document: dict[str, Any], label: str) -> None:
    """Publish a fully-fsynced immutable document without exposing staging bytes."""
    encoded = _canonical(document)
    path = reject_reparse_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path = reject_reparse_path(path)
    lock_path = path.with_name(f".{path.name}.write-once.lock")
    with file_lock(lock_path):
        try:
            if path.exists():
                if _load(path, label) != document:
                    raise BudgetAuthorizationError(f"{label} is write-once")
                return
        except OSError as error:
            raise BudgetAuthorizationError(f"{label} is unreadable") from error
        staging = path.with_name(f".{path.name}.{uuid.uuid4().hex}.staging")
        staging_identity = None
        try:
            fd = os.open(staging, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
            try:
                staging_identity = os.fstat(fd)
                with os.fdopen(fd, "wb") as stream:
                    fd = None
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
            finally:
                if fd is not None:
                    os.close(fd)
            try:
                os.link(staging, path)
            except FileExistsError:
                if _load(path, label) != document:
                    raise BudgetAuthorizationError(f"{label} is write-once")
                return
            except OSError as error:
                raise BudgetAuthorizationError(f"{label} could not publish immutable document") from error
            _fsync_parent(path)
            if _load(path, label) != document:
                raise BudgetAuthorizationError(f"{label} readback mismatch")
        finally:
            try:
                if staging_identity is not None and _same_file(os.lstat(staging), staging_identity):
                    os.unlink(staging)
            except FileNotFoundError:
                pass


def _positive_usd(value: object, label: str) -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise BudgetAuthorizationError(f"{label} must be a positive finite USD decimal") from error
    if not amount.is_finite() or amount <= 0 or amount * 1_000_000 != (amount * 1_000_000).to_integral_value():
        raise BudgetAuthorizationError(f"{label} must be a positive finite USD decimal with at most six decimals")
    if amount * 1_000_000 > 2**63 - 1:
        raise BudgetAuthorizationError(f"{label} exceeds ledger range")
    return f"{amount:.6f}"


def _request_document(raw: object, *, required_mode: str, now: datetime) -> dict[str, Any]:
    if type(raw) is not dict or set(raw) != _REQUEST_KEYS:
        raise BudgetAuthorizationError("BUDGET_AUTHORIZATION request must use the closed schema")
    if raw.get("schema") != 1 or raw.get("kind") != "BUDGET_AUTHORIZATION" or raw.get("mode") != required_mode:
        raise BudgetAuthorizationError("BUDGET_AUTHORIZATION schema, kind and mode are required")
    for key in ("request_id", "episode_id"):
        if not isinstance(raw.get(key), str) or not _ID.fullmatch(raw[key]):
            raise BudgetAuthorizationError(f"BUDGET_AUTHORIZATION {key} is invalid")
    policy_sha256 = raw.get("policy_sha256")
    if not isinstance(policy_sha256, str) or not _HASH.fullmatch(policy_sha256):
        raise BudgetAuthorizationError("BUDGET_AUTHORIZATION policy hash is invalid")
    if raw.get("currency") != "USD":
        raise BudgetAuthorizationError("BUDGET_AUTHORIZATION currency must be USD")
    providers = raw.get("allowed_providers")
    if not isinstance(providers, list) or not providers or len(providers) != len(set(providers)) or any(not isinstance(item, str) or not _ID.fullmatch(item) for item in providers):
        raise BudgetAuthorizationError("BUDGET_AUTHORIZATION allowed providers are invalid")
    caps = raw.get("caps_usd")
    if type(caps) is not dict or set(caps) != _CAP_KEYS:
        raise BudgetAuthorizationError("BUDGET_AUTHORIZATION requires exact episode/month/experiment caps")
    normalized_caps = {key: _positive_usd(caps[key], f"BUDGET_AUTHORIZATION {key} cap") for key in sorted(_CAP_KEYS)}
    expiry = raw.get("expires_at")
    if not isinstance(expiry, str):
        raise BudgetAuthorizationError("BUDGET_AUTHORIZATION expiry is required")
    try:
        expires_at = datetime.fromisoformat(expiry)
    except ValueError as error:
        raise BudgetAuthorizationError("BUDGET_AUTHORIZATION expiry is invalid") from error
    if expires_at.tzinfo is None or expires_at.astimezone(UTC) <= now.astimezone(UTC):
        raise BudgetAuthorizationError("BUDGET_AUTHORIZATION is expired")
    return {**raw, "caps_usd": normalized_caps, "allowed_providers": sorted(providers), "expires_at": expires_at.astimezone(UTC).isoformat()}


@dataclass(frozen=True)
class BudgetAuthorizationSettings:
    """Trusted composition; request JSON cannot choose a chat or financial role."""
    chat_id: str
    financial_sender_ids: frozenset[str]
    mode: str

    def __post_init__(self) -> None:
        if self.mode != "TEST":
            raise BudgetAuthorizationError("only explicit TEST budget authorization is available")
        if not isinstance(self.chat_id, str) or not self.chat_id.isdecimal() or str(int(self.chat_id)) != self.chat_id or int(self.chat_id) <= 0:
            raise BudgetAuthorizationError("configured financial chat is required")
        if not self.financial_sender_ids:
            raise BudgetAuthorizationError("configured financial sender role is required")
        if any(not isinstance(item, str) or not item.isdecimal() or str(int(item)) != item or int(item) <= 0 for item in self.financial_sender_ids):
            raise BudgetAuthorizationError("configured financial sender role is invalid")


@dataclass(frozen=True)
class BudgetAuthorizationRequest:
    path: Path
    document: dict[str, Any]

    @property
    def sha256(self) -> str:
        return _sha256(self.document)


@dataclass(frozen=True)
class BudgetAuthorizationDelivery:
    path: Path
    document: dict[str, Any]


@dataclass(frozen=True)
class BudgetAuthorizationReceipt:
    path: Path
    document: dict[str, Any]


@dataclass(frozen=True)
class AuthorizedBudgetPolicy:
    request_id: str
    episode_id: str
    policy_sha256: str
    allowed_providers: frozenset[str]
    episode_cap_usd: str
    month_cap_usd: str
    experiment_cap_usd: str
    mode: str


class AuthorizedBudgetPolicyLoader:
    """Read-revalidate an authority receipt before preflight/ledger admission."""
    def __init__(self, service: BudgetAuthorizationService) -> None:
        self._service = service

    def load(self, receipt_path: Path | str) -> AuthorizedBudgetPolicy:
        receipt = self._service._verify_receipt(Path(receipt_path))
        request = receipt["request"]
        return AuthorizedBudgetPolicy(
            request_id=request["request_id"], episode_id=request["episode_id"],
            policy_sha256=request["policy_sha256"], allowed_providers=frozenset(request["allowed_providers"]),
            episode_cap_usd=request["caps_usd"]["episode"], month_cap_usd=request["caps_usd"]["month"],
            experiment_cap_usd=request["caps_usd"]["experiment"], mode=request["mode"],
        )


class AuthenticatedBudgetBridge:
    """Central reservation path; revalidates trusted authority on every call."""
    def __init__(self, service, receipt_path):
        self.service, self.receipt_path = service, Path(receipt_path)
        policy = service.policy_loader().load(self.receipt_path)
        self.binding = (policy.request_id, policy.episode_id, policy.policy_sha256, policy.allowed_providers, policy.episode_cap_usd, policy.month_cap_usd, policy.experiment_cap_usd)
        self._ledger = StandingBudgetLedger.from_authorized_policy(service.root / "standing-budget.sqlite3", loader=service.policy_loader(), receipt_path=self.receipt_path, clock=service.clock)

    def reserve(self, *, operation_id=None, provider, quoted_cap_usd, experiment=False, episode_id=None):
        policy = self.service.policy_loader().load(self.receipt_path)
        current = (policy.request_id, policy.episode_id, policy.policy_sha256, policy.allowed_providers, policy.episode_cap_usd, policy.month_cap_usd, policy.experiment_cap_usd)
        if current != self.binding:
            raise BudgetAuthorizationError("authenticated policy binding changed")
        if provider not in policy.allowed_providers or policy.mode != "TEST":
            raise BudgetAuthorizationError("provider is not authorized by authenticated TEST budget policy")
        if not isinstance(operation_id, str) or not _ID.fullmatch(operation_id):
            raise BudgetAuthorizationError("stable child operation id is required")
        if episode_id not in {None, policy.episode_id}:
            raise BudgetAuthorizationError("episode scope is not authorized")
        return self._ledger.reserve(operation_id, policy.episode_id, provider, quoted_cap_usd, experiment=experiment)


class BudgetAuthorizationService:
    """No-network authority composition; delivery and Telegram hosting remain external."""
    def __init__(self, *, root: Path | str, inbound_source: AuthenticatedApprovalSource | None, outbound_source: AuthenticatedBudgetOutbound | None,
                 settings: BudgetAuthorizationSettings, clock=lambda: datetime.now(UTC)) -> None:
        if inbound_source is None or outbound_source is None:
            raise BudgetAuthorizationError("authenticated financial inbound and outbound transports are required")
        self.root, self.inbound_source, self.outbound_source, self.settings, self.clock = Path(root), inbound_source, outbound_source, settings, clock
        self.root.mkdir(parents=True, exist_ok=True)

    def create_request(self, raw: dict[str, Any]) -> BudgetAuthorizationRequest:
        document = _request_document(raw, required_mode=self.settings.mode, now=self.clock())
        path = self.root / "requests" / f"{document['request_id']}.json"
        _write_once(path, document, "budget authorization request")
        return BudgetAuthorizationRequest(path, document)

    def deliver_request(self, request: BudgetAuthorizationRequest) -> BudgetAuthorizationDelivery:
        persisted = _load(request.path, "budget authorization request")
        if persisted != request.document or _sha256(persisted) != request.sha256:
            raise BudgetAuthorizationError("budget authorization request readback differs")
        intent = {"schema": 1, "kind": "BUDGET_AUTHORIZATION_INTENT", "request_id": request.document["request_id"], "request_sha256": request.sha256, "chat_id": self.settings.chat_id, "body_sha256": request.sha256}
        intent_path = self.root / "intents" / f"{request.document['request_id']}.json"
        path = self.root / "deliveries" / f"{request.document['request_id']}.json"
        lock_path = self.root / "locks" / "deliveries" / f"{request.sha256}.lock"
        # One OS-released lock spans observation, durable intent, POST, readback, and receipt.
        with file_lock(lock_path):
            if path.exists():
                document = _load(path, "budget authorization delivery")
                outbound = {key: document.get(key) for key in ("transport", "chat_id", "message_id", "body_sha256")}
                if self.outbound_source.revalidate_delivery(outbound) != outbound:
                    raise BudgetAuthorizationError("trusted outbound delivery readback is invalid")
                return BudgetAuthorizationDelivery(path, document)
            if intent_path.exists():
                raise BudgetAuthorizationError("outbound intent is unknown; never POST again")
            _write_once(intent_path, intent, "budget authorization intent")
            try:
                outbound = self.outbound_source.deliver(chat_id=self.settings.chat_id, canonical_body=_canonical(request.document))
            except BaseException as error:
                raise BudgetAuthorizationError("outbound intent is unknown; never POST again") from error
            if type(outbound) is not dict or set(outbound) != {"transport", "chat_id", "message_id", "body_sha256"} or not isinstance(outbound.get("transport"), str) or not outbound["transport"] or outbound.get("chat_id") != self.settings.chat_id or type(outbound.get("message_id")) is not int or outbound["message_id"] <= 0 or outbound.get("body_sha256") != request.sha256:
                raise BudgetAuthorizationError("trusted outbound body readback does not bind exact canonical request")
            document = {"schema": 1, "kind": "BUDGET_AUTHORIZATION_DELIVERY", "request_id": request.document["request_id"], "request_sha256": request.sha256, **outbound}
            _write_once(path, document, "budget authorization delivery")
            if self.outbound_source.revalidate_delivery(outbound) != outbound:
                raise BudgetAuthorizationError("trusted outbound delivery readback is invalid")
            return BudgetAuthorizationDelivery(path, document)

    @staticmethod
    def _command(request_id: str) -> str:
        return f"AUTORIZAR ORÇAMENTO {request_id}"

    def _receipt_path(self, request_id: str) -> Path:
        return self.root / "receipts" / f"{request_id}.json"

    def confirm(self, delivery: BudgetAuthorizationDelivery) -> BudgetAuthorizationReceipt:
        current_delivery = _load(delivery.path, "budget authorization delivery")
        outbound = {key: current_delivery.get(key) for key in ("transport", "chat_id", "message_id", "body_sha256")}
        if current_delivery != delivery.document or set(current_delivery) != _DELIVERY_KEYS or current_delivery["chat_id"] != self.settings.chat_id or current_delivery.get("body_sha256") != current_delivery.get("request_sha256") or self.outbound_source.revalidate_delivery(outbound) != outbound:
            raise BudgetAuthorizationError("budget authorization delivery is invalid")
        request_path = self.root / "requests" / f"{current_delivery['request_id']}.json"
        request = _request_document(_load(request_path, "budget authorization request"), required_mode=self.settings.mode, now=self.clock())
        if current_delivery["request_sha256"] != _sha256(request):
            raise BudgetAuthorizationError("budget authorization delivery does not bind request")
        receipt_path = self._receipt_path(request["request_id"])
        if receipt_path.is_file():
            return BudgetAuthorizationReceipt(receipt_path, self._verify_receipt(receipt_path))
        reply = self.inbound_source.claim_verified_reply(chat_id=self.settings.chat_id, message_id=current_delivery["message_id"])
        if reply is None:
            reply = self.inbound_source.recover_verified_reply(chat_id=self.settings.chat_id, message_id=current_delivery["message_id"])
        if reply is None or reply.chat_id != self.settings.chat_id or reply.sender_id not in self.settings.financial_sender_ids or reply.reply_to_message_id != current_delivery["message_id"]:
            raise BudgetAuthorizationError("authenticated financial sender/chat/message reply is required")
        if reply.text != self._command(request["request_id"]):
            raise BudgetAuthorizationError("exact internally-derived financial authorization command is required")
        document = {"schema": 1, "kind": "BUDGET_AUTHORIZATION_RECEIPT", "request": request,
                    "request_sha256": _sha256(request), "policy_sha256": request["policy_sha256"],
                    "delivery": current_delivery, "inbound_receipt": reply.document()}
        _write_once(receipt_path, document, "budget authorization receipt")
        return BudgetAuthorizationReceipt(receipt_path, document)

    def _verify_receipt(self, path: Path) -> dict[str, Any]:
        receipt = _load(path, "budget authorization receipt")
        if set(receipt) != _RECEIPT_KEYS or receipt.get("schema") != 1 or receipt.get("kind") != "BUDGET_AUTHORIZATION_RECEIPT":
            raise BudgetAuthorizationError("budget authorization receipt schema is invalid")
        request = _request_document(receipt.get("request"), required_mode=self.settings.mode, now=self.clock())
        delivery = receipt.get("delivery")
        outbound = {key: delivery.get(key) for key in ("transport", "chat_id", "message_id", "body_sha256")} if type(delivery) is dict else None
        if type(delivery) is not dict or set(delivery) != _DELIVERY_KEYS or delivery.get("chat_id") != self.settings.chat_id or delivery.get("body_sha256") != receipt.get("request_sha256") or self.outbound_source.revalidate_delivery(outbound) != outbound:
            raise BudgetAuthorizationError("budget authorization receipt delivery is invalid")
        if receipt.get("request_sha256") != _sha256(request) or receipt.get("policy_sha256") != request["policy_sha256"]:
            raise BudgetAuthorizationError("budget authorization receipt policy binding is invalid")
        if delivery.get("request_id") != request["request_id"] or delivery.get("request_sha256") != _sha256(request) or type(delivery.get("message_id")) is not int or delivery["message_id"] <= 0:
            raise BudgetAuthorizationError("budget authorization receipt request binding is invalid")
        reply = self.inbound_source.revalidate_reply(receipt.get("inbound_receipt"))
        if reply is None or reply.document() != receipt["inbound_receipt"] or reply.chat_id != self.settings.chat_id or reply.sender_id not in self.settings.financial_sender_ids or reply.reply_to_message_id != delivery["message_id"] or reply.text != self._command(request["request_id"]):
            raise BudgetAuthorizationError("budget authorization receipt lacks revalidated financial authority")
        return receipt

    def policy_loader(self) -> AuthorizedBudgetPolicyLoader:
        return AuthorizedBudgetPolicyLoader(self)

    def authorized_bridge(self, receipt_path: Path | str) -> AuthenticatedBudgetBridge:
        return AuthenticatedBudgetBridge(self, receipt_path)

    def admit_simulated_test(self, receipt_path: Path | str, *, operation_id: str, provider: str, quoted_cap_usd: object, experiment: bool = False) -> dict[str, object]:
        """Offline admission only: no provider construction, billing, or dispatch."""
        return self.authorized_bridge(receipt_path).reserve(operation_id=operation_id, provider=provider, quoted_cap_usd=quoted_cap_usd, experiment=experiment)
