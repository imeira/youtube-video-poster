"""Narrow, financial-only reconciliation of one Telegram confirmation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from src.hybrid.locks import reject_reparse_path

HASH = re.compile(r"[a-f0-9]{64}\Z")
REV = re.compile(r"[a-f0-9]{16}\Z")
ID = re.compile(r"[1-9][0-9]*\Z")


@dataclass(frozen=True)
class GatewayFinancialReconciliationSettings:
    gateway_state_db: Path | str
    session_id: str
    configured_chat_id: str
    configured_sender_id: str
    inbound_message_id: str
    outbound_message_id: str
    request_hash: str
    revision: str
    amount_usd: str
    command: str
    valid_until: datetime | None
    delivery_receipt_path: Path | str | None = None
    factory_request_root: Path | str | None = None
    delivery_source_session_id: str | None = None
    delivery_tool_call_id: str | None = None
    readback_tool_call_id: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "gateway_state_db", Path(self.gateway_state_db))
        for n in ("delivery_receipt_path", "factory_request_root"):
            if getattr(self, n) is not None:
                object.__setattr__(self, n, Path(getattr(self, n)))
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ValueError("configured gateway session is required")
        for label, v in (
            ("chat", self.configured_chat_id),
            ("sender", self.configured_sender_id),
            ("inbound message", self.inbound_message_id),
            ("outbound message", self.outbound_message_id),
        ):
            if not isinstance(v, str) or not ID.fullmatch(v):
                raise ValueError(f"configured gateway {label} id is invalid")
        if not isinstance(self.request_hash, str) or not HASH.fullmatch(
            self.request_hash
        ):
            raise ValueError("configured request hash is invalid")
        if not isinstance(self.revision, str) or not REV.fullmatch(self.revision):
            raise ValueError("configured revision is invalid")
        try:
            amount = Decimal(self.amount_usd)
        except (InvalidOperation, ValueError) as e:
            raise ValueError("configured USD amount is invalid") from e
        if not amount.is_finite() or amount <= 0 or f"{amount:.2f}" != self.amount_usd:
            raise ValueError(
                "configured USD amount must be a positive two-decimal value"
            )
        if self.command != f"CONFIRMAR FINANCEIRO JACO USD10 {self.request_hash}":
            raise ValueError(
                "configured financial command must exactly bind the request hash"
            )
        if self.valid_until is not None and (
            not isinstance(self.valid_until, datetime)
            or self.valid_until.tzinfo is None
        ):
            raise ValueError("configured financial validity must be timezone-aware")
        if (self.delivery_receipt_path is None) != (self.factory_request_root is None):
            raise ValueError("delivery receipt and factory request root must be paired")
        tool_ids = (
            self.delivery_source_session_id,
            self.delivery_tool_call_id,
            self.readback_tool_call_id,
        )
        if any(v is not None for v in tool_ids) and not all(
            isinstance(v, str) and re.fullmatch(r"[a-f0-9]{32}", v)
            for v in tool_ids[1:]
        ):
            raise ValueError(
                "canonical Hermes tool record fields must be complete and literal"
            )
        if any(v is not None for v in tool_ids) and (
            not isinstance(self.delivery_source_session_id, str)
            or not self.delivery_source_session_id
        ):
            raise ValueError("canonical Hermes source session is required")


@dataclass(frozen=True)
class GatewayFinancialReconciliationResult:
    status: str
    request_hash: str
    amount_usd: str
    command_platform_message_id: str | None
    outbound_message_id: str | None
    reply_to_message_id: None = None
    outbound_body_sha256: str | None = None


class GatewayFinancialApprovalReconciler:
    def __init__(self, settings, *, now=lambda: datetime.now(UTC)):
        self._settings, self._now = settings, now

    def _result(self, status, command=None, outbound=None, digest=None):
        return GatewayFinancialReconciliationResult(
            status,
            self._settings.request_hash,
            self._settings.amount_usd,
            command,
            outbound,
            None,
            digest,
        )

    @staticmethod
    def _load(path):
        try:
            path = reject_reparse_path(Path(path))
            if not path.is_absolute() or not path.is_file():
                return None
            raw = path.read_bytes()
            doc = json.loads(
                raw.decode("utf-8"),
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
            )
            return (doc, raw) if type(doc) is dict else None
        except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            return None

    def _scope(self):
        if self._settings.delivery_receipt_path is None:
            return None
        loaded = self._load(self._settings.delivery_receipt_path)
        if loaded is None:
            return None
        r, _ = loaded
        s = self._settings
        required = {
            "schema": "jaco-financial-telegram-delivery-receipt/1",
            "platform": "telegram",
            "chat_id": s.configured_chat_id,
            "message_id": s.outbound_message_id,
            "request_hash": s.request_hash,
            "revision": s.revision,
            "currency": "USD",
            "financial_scope_usd": s.amount_usd,
            "readback_method": "copyMessage_then_delete_ephemeral_copy",
            "readback_verified": True,
            "ephemeral_copy_deleted": True,
            "no_generation_authorized": True,
            "no_publication_authorized": True,
            "no_purchase_authorized": True,
        }
        if any(r.get(k) != v for k, v in required.items()) or not all(
            isinstance(r.get(k), str)
            for k in ("gate_path", "gate_sha256", "preflight_path", "preflight_sha256")
        ):
            return None
        try:
            gate = reject_reparse_path(Path(r["gate_path"]))
            if not gate.is_absolute() or not gate.is_file():
                return None
            raw = gate.read_bytes()
        except (OSError, ValueError):
            return None
        digest = hashlib.sha256(raw).hexdigest()
        if digest != r["gate_sha256"]:
            return None
        try:
            body = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
        if any(
            v not in body
            for v in (
                f"REQUEST_HASH: {s.request_hash}",
                f"REVISION: {s.revision}",
                f"teto TOTAL de USD {s.amount_usd}",
                s.command,
            )
        ):
            return None
        pre = self._load(Path(r["preflight_path"]))
        if pre is None or hashlib.sha256(pre[1]).hexdigest() != r["preflight_sha256"]:
            return None
        p = pre[0]
        terms = p.get("financial_terms")
        if (
            p.get("schema") != "jaco-new-production-financial-preflight/1"
            or p.get("request_hash") != s.request_hash
            or p.get("revision") != s.revision
            or type(terms) is not dict
            or terms.get("currency") != "USD"
            or terms.get("total_episode_cap") != s.amount_usd
            or p.get("authorizes_generation") is True
        ):
            return None
        return digest

    def _persist(self, command, digest):
        try:
            root = Path(self._settings.factory_request_root)
            if root.exists():
                root = reject_reparse_path(root)
            root.mkdir(parents=True, exist_ok=True)
            target = (
                root / f"financial_reconciliation_{self._settings.request_hash}.json"
            )
            d = {
                "schema": "jaco-factory-financial-reconciliation/1",
                "request_hash": self._settings.request_hash,
                "revision": self._settings.revision,
                "currency": "USD",
                "total_episode_cap": self._settings.amount_usd,
                "configured_chat_id": self._settings.configured_chat_id,
                "configured_sender_id": self._settings.configured_sender_id,
                "command_platform_message_id": command,
                "outbound_message_id": self._settings.outbound_message_id,
                "reply_to_message_id": None,
                "outbound_body_sha256": digest,
                "status": "APPROVED_USD10",
                "approval_scope": "FINANCIAL_ONLY",
                "authorizes_generation": False,
                "delivery_source": "HERMES_CANONICAL_TOOL_RECORD",
                "raw_telegram_api_payload_available": False,
                "delivery_tool_call_id": self._settings.delivery_tool_call_id,
                "readback_tool_call_id": self._settings.readback_tool_call_id,
                "next_preflight": "LIVE_PRICE_CAP_AND_LEDGER_RECONCILIATION_REQUIRED",
            }
            raw = (json.dumps(d, sort_keys=True, separators=(",", ":")) + "\n").encode()
            try:
                fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                return target.read_bytes() == raw
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            return True
        except (OSError, ValueError):
            return False

    def _canonical_tool_proof(self, c, gate_digest):
        s = self._settings
        if not s.delivery_source_session_id:
            return True
        try:
            source = c.execute(
                "SELECT source FROM sessions WHERE id=?",
                (s.delivery_source_session_id,),
            ).fetchone()
            if source is None or source["source"] not in ("subagent", "cli", "local"):
                return False

            def output(call_id):
                prefixed = f"call_{call_id}"
                declarations = c.execute(
                    "SELECT tool_calls FROM messages WHERE session_id=? AND role='assistant' AND tool_calls IS NOT NULL",
                    (s.delivery_source_session_id,),
                ).fetchall()
                declared = 0
                for row in declarations:
                    calls = json.loads(row["tool_calls"])
                    if isinstance(calls, list):
                        declared += sum(
                            isinstance(x, dict)
                            and x.get("id") == prefixed
                            and isinstance(x.get("function"), dict)
                            and x["function"].get("name") == "terminal"
                            for x in calls
                        )
                rows = c.execute(
                    "SELECT content FROM messages WHERE session_id=? AND role='tool' AND tool_call_id=? AND tool_name='terminal'",
                    (s.delivery_source_session_id, prefixed),
                ).fetchall()
                if declared != 1 or len(rows) != 1:
                    return None
                event = json.loads(rows[0]["content"])
                return (
                    event.get("output")
                    if type(event) is dict
                    and event.get("exit_code") == 0
                    and isinstance(event.get("output"), str)
                    else None
                )

            sent = output(s.delivery_tool_call_id)
            checked = output(s.readback_tool_call_id)
            if sent is None or checked is None:
                return False
            success = re.search(
                r'\{\s*"success"\s*:\s*true\s*,\s*"platform"\s*:\s*"telegram"\s*,\s*"chat_id"\s*:\s*"'
                + re.escape(s.configured_chat_id)
                + r'"\s*,\s*"message_id"\s*:\s*"'
                + re.escape(s.outbound_message_id)
                + r'"',
                sent,
            )
            return bool(
                success
                and f"GATE_SHA256 {gate_digest}" in sent
                and "READBACK_VERIFIED True" in checked
                and f"ORIGINAL_MESSAGE_ID {s.outbound_message_id}" in checked
                and "EPHEMERAL_COPY_DELETED True" in checked
                and "RECEIPT_SHA256 d2b073b36c5fb0323e4ef792eb91c7d99080b7d9489b0e9257a20da87fec2c14"
                in checked
            )
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError):
            return False

    def readback(self):
        now = self._now()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or (
                self._settings.valid_until is not None
                and now.astimezone(UTC) > self._settings.valid_until.astimezone(UTC)
            )
        ):
            return self._result("EXPIRED_SCOPE")
        try:
            db = reject_reparse_path(self._settings.gateway_state_db)
            if not db.is_absolute() or not db.is_file():
                return self._result("GATEWAY_PROVENANCE_UNAVAILABLE")
            c = sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True)
            c.row_factory = sqlite3.Row
        except (OSError, ValueError, sqlite3.Error):
            return self._result("GATEWAY_PROVENANCE_UNAVAILABLE")
        try:
            s = c.execute(
                "SELECT source,chat_id,user_id FROM sessions WHERE id=?",
                (self._settings.session_id,),
            ).fetchone()
            if s is None or (s["source"], s["chat_id"], s["user_id"]) != (
                "telegram",
                self._settings.configured_chat_id,
                self._settings.configured_sender_id,
            ):
                return self._result("GATEWAY_IDENTITY_UNVERIFIED")
            rows = c.execute(
                "SELECT platform_message_id FROM messages WHERE session_id=? AND role='user' AND content=? AND platform_message_id=?",
                (
                    self._settings.session_id,
                    self._settings.command,
                    self._settings.inbound_message_id,
                ),
            ).fetchall()
            if len(rows) != 1:
                return self._result(
                    "COMMAND_NOT_FOUND" if not rows else "AMBIGUOUS_COMMAND"
                )
            command = str(rows[0]["platform_message_id"])
            digest = self._scope()
            if digest is None:
                return self._result("HUMAN_APPROVED_TECH_UNBOUND", command)
            if not self._canonical_tool_proof(c, digest):
                return self._result("DELIVERY_TOOL_PROVENANCE_UNVERIFIED", command)
            if not self._persist(command, digest):
                return self._result(
                    "FACTORY_RECORD_PERSISTENCE_FAILED",
                    command,
                    self._settings.outbound_message_id,
                    digest,
                )
            return self._result(
                "RECONCILED_FINANCIAL_ONLY",
                command,
                self._settings.outbound_message_id,
                digest,
            )
        except sqlite3.Error:
            return self._result("GATEWAY_PROVENANCE_UNAVAILABLE")
        finally:
            c.close()
