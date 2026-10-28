"""Standalone, disabled-by-default Hermes plugin template.

Deployment must pass a configured bridge to ``register``. This file has no
import-time registration or network side effect. A normal ``False`` recognition
continues Hermes dispatch; an exception after a raw update passes the configured
sender/chat/reply boundary is conservatively consumed as uncertain.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import time
from inspect import isawaitable
from types import SimpleNamespace

from src.approval.hermes_bridge_composition import HermesApprovalBridgeComposition
from src.approval.hermes_inbound_bridge import canonical_envelope

_DOMAIN = b"hermes-studio-approval-bridge/v1\0"


def _raw_update(update: object, bridge: HermesApprovalBridgeComposition) -> bytes | None:
    config = bridge.config
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


def register(ctx: object, *, bridge: HermesApprovalBridgeComposition | None = None) -> bool:
    """Register one pre-core handler bound to the composition's ingress and recognizer."""
    if not isinstance(bridge, HermesApprovalBridgeComposition):
        return False

    def factory(application: object, _adapter: object) -> None:
        async def callback(update: object, _context: object) -> None:
            raw = _raw_update(update, bridge)
            if raw is None:
                return
            try:
                recognized = await asyncio.to_thread(bridge.recognize, raw)
            except Exception:  # noqa: BLE001 - raw-bound lookup uncertainty must not reach Studio or core
                if application_handler_stop is not None:
                    raise application_handler_stop
                return
            if recognized is not True:
                return
            config = bridge.config
            envelope = canonical_envelope(config, raw, nonce=secrets.token_hex(16), timestamp=int(time.time()))
            signature = hmac.new(config.hmac_key.encode(), _DOMAIN + envelope, hashlib.sha256).hexdigest()
            try:
                delivered = await asyncio.to_thread(
                    bridge.deliver,
                    envelope,
                    {"X-Studio-Bridge-Version": "1", "X-Studio-Bridge-Instance": config.instance_id, "X-Studio-Bridge-Signature": f"v1={signature}"},
                )
                if isawaitable(delivered):
                    await delivered
            except Exception:  # noqa: BLE001 - ambiguous local delivery remains locally consumed
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
