"""Disabled-by-default, single-owner composition for Hermes approval replies.

It binds the Hermes plugin to one existing ``TelegramDirectorService`` through
an authenticated in-process ingress.  Construction creates no network client,
listener, poller, webhook, or provider.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from src.approval.hermes_inbound_bridge import HermesBridgeConfig, HermesStudioIngress
from src.approval.telegram_service import TelegramDirectorService, WebhookResult
from src.state.machine import EpisodeState, EpisodeStateStore

_EXPECTED_GATE = {
    "thumbnail": EpisodeState.WAITING_THUMBNAIL_APPROVAL,
    "video": EpisodeState.WAITING_VIDEO_APPROVAL,
}


@dataclass
class HermesApprovalBridgeComposition:
    """One explicit bridge binding; callers cannot replace its recognizer or transport."""

    config: HermesBridgeConfig
    service: TelegramDirectorService
    ingress: HermesStudioIngress = field(init=False)

    def __post_init__(self) -> None:
        # HermesStudioIngress checks configuration and secret separation before
        # it creates its replay store.
        self.ingress = HermesStudioIngress(config=self.config, service=self.service)

    def _reply(self, raw_update: bytes) -> tuple[str, str, int] | None:
        try:
            update = json.loads(raw_update.decode("utf-8"))
            message = update["message"]
            sender = message["from"]
            chat = message["chat"]
            reply = message["reply_to_message"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            return None
        sender_id, chat_id, reply_id = sender.get("id"), chat.get("id"), reply.get("message_id")
        if (
            type(sender_id) is not int or str(sender_id) not in self.config.allowed_sender_ids
            or sender.get("is_bot") is not False
            or type(chat_id) is not int or str(chat_id) != self.config.expected_chat_id
            or type(reply_id) is not int or reply_id <= 0
        ):
            return None
        return str(sender_id), str(chat_id), reply_id

    def recognize(self, raw_update: bytes) -> bool:
        """Recognize only a configured sender's intact receipt at its active gate."""
        reply = self._reply(raw_update)
        if reply is None:
            return False
        _sender_id, chat_id, message_id = reply
        record = self.service._lookup(chat_id=chat_id, message_id=message_id)
        if record is None:
            return False
        kind = record.get("artifact_kind")
        path = record.get("artifact_path")
        digest = record.get("artifact_sha256")
        episode_id = record.get("episode_id")
        if kind not in _EXPECTED_GATE or not all(isinstance(value, str) and value for value in (path, digest, episode_id)):
            return False
        try:
            artifact = Path(path)
            if not artifact.is_file() or hashlib.sha256(artifact.read_bytes()).hexdigest() != digest:
                return False
            state = EpisodeStateStore.load(Path(self.service._director.config.episodes_dir) / episode_id / "state.json")
        except (OSError, ValueError, KeyError):
            return False
        return state.current_state is _EXPECTED_GATE[kind]

    def deliver(self, body: bytes, headers: dict[str, str]) -> WebhookResult:
        """Authenticated local ingress used only by this composition's plugin."""
        return self.ingress.handle(body, headers)


def compose_hermes_approval_bridge(
    *, config: HermesBridgeConfig, service: TelegramDirectorService
) -> HermesApprovalBridgeComposition:
    """Create the optional bridge explicitly; no deployment action is implied."""
    return HermesApprovalBridgeComposition(config=config, service=service)
