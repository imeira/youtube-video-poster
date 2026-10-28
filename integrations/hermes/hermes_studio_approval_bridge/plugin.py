"""Standalone, disabled-by-default Hermes plugin template.

Deployment must pass a configured bridge and a loopback-only transport to
``register``. This file has no import-time registration or network side effect.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Callable
from inspect import isawaitable
from types import SimpleNamespace

from src.approval.hermes_inbound_bridge import HermesBridgeConfig, canonical_envelope

_DOMAIN = b"hermes-studio-approval-bridge/v1\0"


def _raw_update(update: object, config: HermesBridgeConfig) -> bytes | None:
    message = getattr(update, "effective_message", None)
    sender, chat, reply = (getattr(message, name, None) for name in ("from_user", "chat", "reply_to_message"))
    update_id, message_id = getattr(update, "update_id", None), getattr(message, "message_id", None)
    sender_id, chat_id, reply_id = getattr(sender, "id", None), getattr(chat, "id", None), getattr(reply, "message_id", None)
    text = getattr(message, "text", None)
    if (type(update_id) is not int or update_id <= 0 or type(message_id) is not int or message_id <= 0 or
        type(sender_id) is not int or str(sender_id) not in config.allowed_sender_ids or getattr(sender, "is_bot", None) is not False or
        type(chat_id) is not int or str(chat_id) != config.expected_chat_id or getattr(chat, "type", None) not in {"private", "group", "supergroup"} or
        type(reply_id) is not int or reply_id <= 0 or not isinstance(text, str) or not text.strip() or len(text) > 4096):
        return None
    import json
    return json.dumps({"update_id": update_id, "message": {"message_id": message_id, "from": {"id": sender_id, "is_bot": False}, "chat": {"id": chat_id, "type": chat.type}, "reply_to_message": {"message_id": reply_id}, "text": text}}, separators=(",", ":")).encode()


def register(
    ctx: object,
    *,
    config: HermesBridgeConfig | None = None,
    transport: Callable[[bytes, dict[str, str]], object] | None = None,
    recognize: Callable[[bytes], bool] | None = None,
) -> bool:
    """Register one bound approval handler only when trusted dependencies exist."""
    if config is None or transport is None or recognize is None:
        return False

    def factory(application: object, _adapter: object) -> None:
        async def callback(update: object, _context: object) -> None:
            raw = _raw_update(update, config)
            if raw is None:
                return
            try:
                recognized = recognize(raw)
            except Exception:  # noqa: BLE001 - an uncertain binding must not reach Studio or core
                return
            if recognized is not True:
                return
            envelope = canonical_envelope(config, raw, nonce=secrets.token_hex(16), timestamp=int(time.time()))
            signature = hmac.new(config.hmac_key.encode(), _DOMAIN + envelope, hashlib.sha256).hexdigest()
            try:
                # One local attempt only. No Telegram API, polling, webhook, or reply.
                delivered = transport(envelope, {"X-Studio-Bridge-Version": "1", "X-Studio-Bridge-Instance": config.instance_id, "X-Studio-Bridge-Signature": f"v1={signature}"})
                if isawaitable(delivered):
                    await delivered
            except Exception:  # noqa: BLE001 - delivery may be ambiguous; never retry or expose it to core
                if application_handler_stop is not None:
                    raise application_handler_stop
                return
            if application_handler_stop is not None:
                raise application_handler_stop

        try:
            from telegram.ext import ApplicationHandlerStop, MessageHandler, filters
            application_handler_stop: type[Exception] | None = ApplicationHandlerStop
            handler = MessageHandler(filters.REPLY & filters.TEXT & ~filters.COMMAND, callback)
        except ImportError:  # Offline unit fakes; production Hermes provides PTB.
            application_handler_stop = None
            handler = SimpleNamespace(callback=callback)
        application.add_handler(handler, group=-1)

    ctx.register_platform_handler("telegram", factory)
    return True
