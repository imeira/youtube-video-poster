"""Hermes-owned Telegram update bridge into the existing Studio approval journal.

This module is inert until a host supplies explicit configuration and a locally
bound transport.  It never creates a Telegram client, polls, sets a webhook,
or sends Telegram messages.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from src.approval.telegram_service import TelegramDirectorService, WebhookResult

_DOMAIN = b"hermes-studio-approval-bridge/v1\0"
_MAX_BODY = 65_536
_MAX_SKEW_SECONDS = 300


def _json(value: dict[str, object]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _load_json(body: bytes) -> dict[str, object] | None:
    def no_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=no_duplicates)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    return value if type(value) is dict else None


@dataclass(frozen=True)
class HermesBridgeConfig:
    instance_id: str
    hmac_key: str | None
    expected_chat_id: str
    allowed_sender_ids: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.hmac_key, str) or not self.hmac_key:
            raise ValueError("explicit bridge HMAC configuration is required")
        if not isinstance(self.instance_id, str) or not self.instance_id or len(self.instance_id) > 256:
            raise ValueError("explicit bridge instance configuration is required")
        for label, value in (("chat", self.expected_chat_id), *[("sender", item) for item in self.allowed_sender_ids]):
            if not isinstance(value, str) or not value.isdecimal() or str(int(value)) != value or int(value) <= 0:
                raise ValueError(f"bridge {label} identity must be canonical positive decimal")
        if not self.allowed_sender_ids:
            raise ValueError("bridge sender allowlist is required")


def canonical_envelope(config: HermesBridgeConfig, raw_update: bytes, *, nonce: str, timestamp: int) -> bytes:
    """Build the only accepted signed wire representation; raw update bytes remain exact."""
    if type(raw_update) is not bytes or not raw_update or len(raw_update) > _MAX_BODY:
        raise ValueError("raw update is not bounded bytes")
    if not isinstance(nonce, str) or not nonce.isascii() or not 8 <= len(nonce) <= 128:
        raise ValueError("nonce must be bounded ASCII")
    if type(timestamp) is not int or timestamp <= 0:
        raise ValueError("timestamp must be a positive integer")
    return _json({"instance": config.instance_id, "nonce": nonce, "raw_update_b64": base64.b64encode(raw_update).decode("ascii"), "timestamp": timestamp, "version": 1})


class HermesStudioIngress:
    """Verify the bridge envelope then reuse Studio's exact approval entrypoint."""

    def __init__(self, *, config: HermesBridgeConfig, service: TelegramDirectorService, now: Callable[[], float] = time.time) -> None:
        self._config, self._service, self._now = config, service, now
        if not getattr(service, "_enabled", False):
            raise ValueError("Studio approval ingress is not configured")
        studio_webhook_secret = service._settings.webhook_secret
        if not isinstance(studio_webhook_secret, str) or hmac.compare_digest(config.hmac_key, studio_webhook_secret):
            raise ValueError("bridge HMAC key must differ from the Studio webhook secret")
        # The existing service's distinct trusted secret remains only inside its
        # own admission boundary; bridge callers cannot supply it.
        self._replay_path = Path(service._settings.registry_path).with_name("hermes-bridge-replays.sqlite3")
        self._initialize_replay_store()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._replay_path, timeout=10, isolation_level=None)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize_replay_store(self) -> None:
        self._replay_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS bridge_replays (nonce TEXT PRIMARY KEY, digest TEXT NOT NULL)")

    def _remember_or_match(self, nonce: str, envelope: bytes) -> bool:
        digest = hashlib.sha256(envelope).hexdigest()
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT digest FROM bridge_replays WHERE nonce = ?", (nonce,)).fetchone()
                if row is None:
                    connection.execute("INSERT INTO bridge_replays(nonce, digest) VALUES (?, ?)", (nonce, digest))
                    return True
                return isinstance(row[0], str) and hmac.compare_digest(row[0], digest)
        except sqlite3.Error:
            return False

    def _verify(self, body: bytes, headers: Mapping[str, str]) -> bytes | None:
        if type(body) is not bytes or not body or len(body) > _MAX_BODY:
            return None
        version = headers.get("X-Studio-Bridge-Version")
        instance = headers.get("X-Studio-Bridge-Instance")
        signature = headers.get("X-Studio-Bridge-Signature")
        if version != "1" or instance != self._config.instance_id or not isinstance(signature, str) or not signature.startswith("v1="):
            return None
        expected = hmac.new(self._config.hmac_key.encode("utf-8"), _DOMAIN + body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature[3:], expected):
            return None
        envelope = _load_json(body)
        if envelope is None or set(envelope) != {"instance", "nonce", "raw_update_b64", "timestamp", "version"}:
            return None
        if envelope["version"] != 1 or envelope["instance"] != self._config.instance_id or type(envelope["timestamp"]) is not int:
            return None
        if abs(self._now() - envelope["timestamp"]) > _MAX_SKEW_SECONDS:
            return None
        nonce = envelope["nonce"]
        encoded = envelope["raw_update_b64"]
        if not isinstance(nonce, str) or not nonce.isascii() or not 8 <= len(nonce) <= 128 or not isinstance(encoded, str):
            return None
        try:
            raw = base64.b64decode(encoded.encode("ascii"), validate=True)
        except (UnicodeEncodeError, ValueError):
            return None
        if not raw or len(raw) > _MAX_BODY or not self._remember_or_match(nonce, body):
            return None
        return raw

    def handle(self, body: bytes, headers: Mapping[str, str]) -> WebhookResult:
        raw = self._verify(body, headers)
        if raw is None:
            return WebhookResult(401, {"status": "bridge_rejected"})
        # Reuse the established journal, delivery binding, claim/recovery, and
        # Director transition. The webhook secret never crosses this boundary.
        return self._service.handle_webhook(raw, self._service._settings.webhook_secret)
