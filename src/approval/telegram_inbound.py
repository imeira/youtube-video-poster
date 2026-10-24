"""Fail-closed, offline Telegram webhook adapter for approval replies.

Telegram webhooks use a configured shared secret header, not a Telegram
cryptographic payload signature.  This module never contacts Telegram and only
returns replies that were authenticated and durably journaled here.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.approval.inbound import AuthenticatedApprovalReply

_MAX_BODY_BYTES = 65_536
_MAX_TEXT_CHARS = 4_096
_TRANSPORT = "TELEGRAM"
_SCHEMA_VERSION = "telegram-inbound-v1"


@dataclass(frozen=True)
class _ReplyClaims:
    update_id: int
    message_id: int
    chat_id: str
    sender_id: str
    reply_to_message_id: int
    text: str

    def canonical_document(self) -> dict[str, object]:
        return {
            "chat_id": self.chat_id,
            "message_id": self.message_id,
            "reply_to_message_id": self.reply_to_message_id,
            "sender_id": self.sender_id,
            "text": self.text,
            "update_id": self.update_id,
        }


class TelegramInboundAdapter:
    """Journal only webhook-secret-authenticated Telegram approval messages.

    ``webhook_secret`` and ``instance_id`` must originate from trusted external
    configuration.  Neither is read from the webhook body or persisted in the
    SQLite journal.  A journal cannot be opened under a different binding.
    """

    def __init__(
        self,
        *,
        journal_path: Path | str,
        expected_chat_id: str,
        trusted_sender_ids: frozenset[str],
        webhook_secret: str | None,
        instance_id: str,
    ) -> None:
        self._journal_path = Path(journal_path)
        self._expected_chat_id = self._canonical_config_id(expected_chat_id, "chat")
        self._trusted_sender_ids = frozenset(
            self._canonical_config_id(sender_id, "sender")
            for sender_id in trusted_sender_ids
        )
        if not self._trusted_sender_ids:
            raise ValueError("trusted Telegram sender configuration is required")
        if not isinstance(instance_id, str) or not instance_id.strip() or len(instance_id) > 256:
            raise ValueError("trusted Telegram instance binding is required")
        self._instance_id = instance_id
        self._webhook_secret = webhook_secret if isinstance(webhook_secret, str) else None
        if self._webhook_secret:
            self._initialize_journal()

    @staticmethod
    def _canonical_config_id(value: str, label: str) -> str:
        if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
            raise ValueError(f"trusted Telegram {label} id must be a positive decimal string")
        canonical = str(int(value))
        if int(canonical) <= 0 or canonical != value:
            raise ValueError(f"trusted Telegram {label} id must be a canonical positive decimal string")
        return canonical

    def _binding_digest(self) -> str:
        key = (self._webhook_secret or "missing-webhook-secret").encode("utf-8")
        document = {
            "expected_chat_id": self._expected_chat_id,
            "instance_id": self._instance_id,
            "schema": _SCHEMA_VERSION,
            "trusted_sender_ids": sorted(self._trusted_sender_ids),
        }
        return hmac.new(key, self._canonical_json(document), hashlib.sha256).hexdigest()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._journal_path, timeout=10, isolation_level=None)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize_journal(self) -> None:
        self._journal_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS journal_metadata ("
                "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS replies ("
                "update_id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL UNIQUE, "
                "chat_id TEXT NOT NULL, sender_id TEXT NOT NULL, "
                "reply_to_message_id INTEGER NOT NULL, text TEXT NOT NULL, "
                "update_hash TEXT NOT NULL, message_hash TEXT NOT NULL, mac TEXT NOT NULL, "
                "consumed_at TEXT, UNIQUE(chat_id, reply_to_message_id))"
            )
            row = connection.execute(
                "SELECT value FROM journal_metadata WHERE key = 'binding_digest'"
            ).fetchone()
            digest = self._binding_digest()
            if row is None:
                connection.execute(
                    "INSERT INTO journal_metadata(key, value) VALUES ('binding_digest', ?)",
                    (digest,),
                )
            elif not hmac.compare_digest(row[0], digest):
                raise ValueError("Telegram inbound journal binding does not match trusted configuration")

    @staticmethod
    def _canonical_json(document: dict[str, object]) -> bytes:
        return json.dumps(
            document, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")

    @staticmethod
    def _load_json(body: bytes) -> dict[str, Any] | None:
        def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result

        try:
            parsed = json.loads(body.decode("utf-8"), object_pairs_hook=reject_duplicate_keys)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
        return parsed if type(parsed) is dict else None

    @staticmethod
    def _positive_int(value: object) -> int | None:
        return value if type(value) is int and value > 0 else None

    def _claims_from_body(self, body: bytes) -> _ReplyClaims | None:
        document = self._load_json(body)
        if document is None or set(document) != {"update_id", "message"}:
            return None
        update_id = self._positive_int(document.get("update_id"))
        message = document.get("message")
        if update_id is None or type(message) is not dict:
            return None
        message_id = self._positive_int(message.get("message_id"))
        sender = message.get("from")
        chat = message.get("chat")
        reply_to = message.get("reply_to_message")
        text = message.get("text")
        if (
            message_id is None
            or type(sender) is not dict
            or type(chat) is not dict
            or type(reply_to) is not dict
            or type(text) is not str
            or not text.strip()
            or len(text) > _MAX_TEXT_CHARS
        ):
            return None
        sender_id = self._positive_int(sender.get("id"))
        chat_id = self._positive_int(chat.get("id"))
        reply_to_message_id = self._positive_int(reply_to.get("message_id"))
        if (
            sender_id is None
            or chat_id is None
            or reply_to_message_id is None
            or sender.get("is_bot") is not False
            or chat.get("type") not in {"private", "group", "supergroup"}
        ):
            return None
        canonical_sender = str(sender_id)
        canonical_chat = str(chat_id)
        if (
            canonical_sender not in self._trusted_sender_ids
            or canonical_chat != self._expected_chat_id
        ):
            return None
        return _ReplyClaims(
            update_id=update_id,
            message_id=message_id,
            chat_id=canonical_chat,
            sender_id=canonical_sender,
            reply_to_message_id=reply_to_message_id,
            text=text,
        )

    def _row_mac(self, claims: _ReplyClaims, *, consumed_at: str | None = None) -> str:
        key = (self._webhook_secret or "missing-webhook-secret").encode("utf-8")
        document = claims.canonical_document()
        document["consumed_at"] = consumed_at
        return hmac.new(key, self._canonical_json(document), hashlib.sha256).hexdigest()

    @staticmethod
    def _hash(document: dict[str, object]) -> str:
        return hashlib.sha256(TelegramInboundAdapter._canonical_json(document)).hexdigest()

    def ingest_webhook(self, *, body: bytes, secret_header: str | None) -> bool:
        """Authenticate and journal a single webhook update without network I/O.

        Invalid, conflicting, oversized, or unauthenticated inputs return
        ``False`` and are not usable through :meth:`verified_reply`.
        """
        if (
            self._webhook_secret is None
            or not self._webhook_secret
            or not isinstance(secret_header, str)
            or not hmac.compare_digest(self._webhook_secret, secret_header)
            or type(body) is not bytes
            or len(body) == 0
            or len(body) > _MAX_BODY_BYTES
        ):
            return False
        claims = self._claims_from_body(body)
        if claims is None:
            return False
        canonical = claims.canonical_document()
        update_hash = self._hash(canonical)
        message_hash = self._hash({key: value for key, value in canonical.items() if key != "update_id"})
        mac = self._row_mac(claims)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                update_row = connection.execute(
                    "SELECT update_hash FROM replies WHERE update_id = ?", (claims.update_id,)
                ).fetchone()
                if update_row is not None:
                    return hmac.compare_digest(update_row[0], update_hash)
                collision = connection.execute(
                    "SELECT message_hash FROM replies WHERE message_id = ? "
                    "OR (chat_id = ? AND reply_to_message_id = ?)",
                    (claims.message_id, claims.chat_id, claims.reply_to_message_id),
                ).fetchone()
                if collision is not None:
                    return False
                connection.execute(
                    "INSERT INTO replies(update_id, message_id, chat_id, sender_id, "
                    "reply_to_message_id, text, update_hash, message_hash, mac, consumed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                    (
                        claims.update_id,
                        claims.message_id,
                        claims.chat_id,
                        claims.sender_id,
                        claims.reply_to_message_id,
                        claims.text,
                        update_hash,
                        message_hash,
                        mac,
                    ),
                )
                return True
        except sqlite3.Error:
            return False

    def _validated_claims(self, row: tuple[object, ...], *, consumed_at: str | None) -> _ReplyClaims | None:
        if (
            len(row) not in {9, 10}
            or any(type(row[index]) is not int or row[index] <= 0 for index in (0, 1, 4))
            or any(not isinstance(row[index], str) for index in (2, 3, 5))
        ):
            return None
        claims = _ReplyClaims(
            update_id=row[0], message_id=row[1], chat_id=row[2], sender_id=row[3],
            reply_to_message_id=row[4], text=row[5],
        )
        canonical = claims.canonical_document()
        if (
            not isinstance(row[6], str) or not isinstance(row[7], str) or not isinstance(row[8], str)
            or not hmac.compare_digest(row[6], self._hash(canonical))
            or not hmac.compare_digest(row[7], self._hash({key: value for key, value in canonical.items() if key != "update_id"}))
            or not hmac.compare_digest(row[8], self._row_mac(claims, consumed_at=consumed_at))
        ):
            return None
        return claims

    @staticmethod
    def _reply(claims: _ReplyClaims) -> AuthenticatedApprovalReply:
        return AuthenticatedApprovalReply(
            transport=_TRANSPORT, chat_id=claims.chat_id, sender_id=claims.sender_id,
            message_id=claims.message_id, reply_to_message_id=claims.reply_to_message_id,
            text=claims.text,
        )

    def claim_verified_reply(self, *, chat_id: str, message_id: int) -> AuthenticatedApprovalReply | None:
        """Atomically consume one fresh integrity-verified reply for a delivery."""
        if not self._webhook_secret or chat_id != self._expected_chat_id or type(message_id) is not int or message_id <= 0:
            return None
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT update_id, message_id, chat_id, sender_id, reply_to_message_id, text, update_hash, message_hash, mac "
                    "FROM replies WHERE chat_id = ? AND reply_to_message_id = ? AND consumed_at IS NULL",
                    (chat_id, message_id),
                ).fetchone()
                if row is None:
                    return None
                claims = self._validated_claims(row, consumed_at=None)
                if claims is None:
                    return None
                consumed_at = datetime.now(UTC).isoformat(timespec="microseconds")
                consumed_mac = self._row_mac(claims, consumed_at=consumed_at)
                changed = connection.execute(
                    "UPDATE replies SET consumed_at = ?, mac = ? WHERE update_id = ? AND consumed_at IS NULL AND mac = ?",
                    (consumed_at, consumed_mac, claims.update_id, row[8]),
                ).rowcount
                return self._reply(claims) if changed == 1 else None
        except (sqlite3.Error, ValueError):
            return None

    def _read_consumed_reply(self, *, chat_id: str, message_id: int) -> AuthenticatedApprovalReply | None:
        if not self._webhook_secret or chat_id != self._expected_chat_id or type(message_id) is not int or message_id <= 0:
            return None
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT update_id, message_id, chat_id, sender_id, reply_to_message_id, text, update_hash, message_hash, mac, consumed_at "
                    "FROM replies WHERE chat_id = ? AND reply_to_message_id = ? AND consumed_at IS NOT NULL",
                    (chat_id, message_id),
                ).fetchone()
                if row is None or not isinstance(row[9], str):
                    return None
                claims = self._validated_claims(row, consumed_at=row[9])
                return None if claims is None else self._reply(claims)
        except (sqlite3.Error, ValueError):
            return None

    def recover_verified_reply(self, *, chat_id: str, message_id: int) -> AuthenticatedApprovalReply | None:
        """Recover only an already-consumed reply after interrupted local persistence."""
        return self._read_consumed_reply(chat_id=chat_id, message_id=message_id)

    def revalidate_reply(self, document: object) -> AuthenticatedApprovalReply | None:
        """Read-revalidate a persisted approval without consuming the reply again."""
        if type(document) is not dict or set(document) != {"transport", "chat_id", "sender_id", "message_id", "reply_to_message_id", "text"}:
            return None
        if document.get("transport") != _TRANSPORT:
            return None
        chat_id, reply_to = document.get("chat_id"), document.get("reply_to_message_id")
        if not isinstance(chat_id, str) or type(reply_to) is not int:
            return None
        reply = self._read_consumed_reply(chat_id=chat_id, message_id=reply_to)
        return reply if reply is not None and reply.document() == document else None

    def verified_reply(self, *, chat_id: str, message_id: int) -> AuthenticatedApprovalReply | None:
        """Compatibility alias; approval controllers use explicit claim/recovery."""
        return self.claim_verified_reply(chat_id=chat_id, message_id=message_id)
