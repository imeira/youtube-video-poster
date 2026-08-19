"""Telegram Approval Gate — human-in-the-loop pre-production approval (§4, §6-8, §20-21).

Responsibility: Send the pre-production plan to Telegram and BLOCK until the
human approves, rejects, or picks an alternative. Silence is NEVER approval (§8).

This is used by the Director BEFORE any paid resource generation begins.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.request
import urllib.parse
from dataclasses import dataclass

logger = logging.getLogger(__name__)


def _read_env_var(name: str) -> str | None:
    val = os.environ.get(name)
    if val:
        return val.strip()
    env_path = os.path.expanduser("~/AppData/Local/hermes/.env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(f"{name}="):
                    return line.split("=", 1)[1].strip()
    return None


@dataclass
class ApprovalResult:
    """Outcome of a human-in-the-loop approval request."""
    approved: bool
    response: str = ""  # raw text response, e.g. "A", "APROVAR", "CANCELAR"
    timed_out: bool = False
    reason: str = ""


class TelegramApprovalGate:
    """Sends decisions to Telegram and polls for a human reply (§6-8).

    Silence is NEVER approval (§8): if no reply arrives within the timeout,
    the result is `approved=False, timed_out=True` and the caller must halt.
    """

    def __init__(self, bot_token: str = "", chat_id: str = "", poll_interval: float = 3.0):
        self.bot_token = bot_token or _read_env_var("TELEGRAM_BOT_TOKEN") or ""
        self.chat_id = chat_id or _read_env_var("TELEGRAM_CHAT_ID") or "141718934"
        self.poll_interval = poll_interval

    def available(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send_message(self, text: str) -> int | None:
        """Send a message to Telegram. Returns the message_id, or None on failure."""
        if not self.available():
            logger.warning("Telegram not configured — cannot send approval request")
            return None
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": self.chat_id,
                "text": text,
            }).encode("utf-8")
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
            if result.get("ok"):
                return result["result"]["message_id"]
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
        return None

    def _get_updates(self, offset: int | None = None) -> list[dict]:
        """Poll Telegram getUpdates for new messages."""
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
            params = {"timeout": 0}
            if offset is not None:
                params["offset"] = offset
            url += "?" + urllib.parse.urlencode(params)
            with urllib.request.urlopen(url, timeout=15) as resp:
                result = json.loads(resp.read())
            if result.get("ok"):
                return result["result"]
        except Exception as e:
            logger.error(f"Failed to poll Telegram updates: {e}")
        return []

    async def request_approval(
        self,
        message: str,
        valid_responses: list[str] | None = None,
        timeout_s: float = 3600.0,
        after_message_id: int | None = None,
    ) -> ApprovalResult:
        """Send a decision request and BLOCK until a valid human reply arrives (§7-8).

        Args:
            message: The decision message to send (formatted per §7 template).
            valid_responses: Accepted response tokens (case-insensitive), e.g.
                ["A", "B", "C", "CANCELAR"]. If None, any non-empty reply from
                the configured chat_id counts as approval text (caller interprets it).
            timeout_s: Max time to wait for a reply. On timeout, approved=False (§8).
            after_message_id: If set, only consider replies with update_id greater
                than this (avoids picking up stale messages from before the request).

        Returns:
            ApprovalResult — never silently approves on timeout (§8).
        """
        if not self.available():
            return ApprovalResult(
                approved=False,
                reason="Telegram not configured (missing TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID)",
            )

        # Get current update offset baseline (ignore messages sent before this call)
        baseline_updates = self._get_updates()
        last_update_id = max((u["update_id"] for u in baseline_updates), default=0)

        sent_id = self.send_message(message)
        if sent_id is None:
            return ApprovalResult(approved=False, reason="Failed to send Telegram message")

        logger.info(f"Waiting for Telegram approval (timeout={timeout_s}s)...")

        start = time.time()
        while time.time() - start < timeout_s:
            updates = self._get_updates(offset=last_update_id + 1)
            for update in updates:
                last_update_id = max(last_update_id, update["update_id"])
                msg = update.get("message", {})
                text = msg.get("text", "").strip()
                chat_id = str(msg.get("chat", {}).get("id", ""))

                if chat_id != str(self.chat_id) or not text:
                    continue

                if valid_responses:
                    text_upper = text.upper()
                    for valid in valid_responses:
                        if text_upper == valid.upper() or text_upper.startswith(valid.upper()):
                            return ApprovalResult(approved=True, response=valid.upper())
                    # Reply doesn't match any valid option — keep waiting, but log it
                    logger.info(f"Received reply '{text}' — not a valid option, still waiting")
                else:
                    # Any non-empty reply = approval (caller decides what it means)
                    return ApprovalResult(approved=True, response=text)

            await asyncio.sleep(self.poll_interval)

        # §8: Silence is NOT approval
        return ApprovalResult(
            approved=False,
            timed_out=True,
            reason=f"No valid response received within {timeout_s}s (§8: silence is not approval)",
        )


def format_decision_message(
    episode_title: str,
    stage: str,
    situation: str,
    analysis: str,
    options: dict[str, str],
    recommendation: str,
) -> str:
    """Format a decision message per the §7 template.

    Args:
        options: {"A": "description", "B": "description", ...}
    """
    options_str = "\n".join(f"OPÇÃO {k}:\n{v}" for k, v in options.items())
    valid = "/".join(list(options.keys()) + ["CANCELAR"])

    return f"""⚠️ DECISÃO NECESSÁRIA

EPISÓDIO:
{episode_title}

ETAPA:
{stage}

SITUAÇÃO:
{situation}

ANÁLISE:
{analysis}

{options_str}

RECOMENDAÇÃO:
{recommendation}

RESPONDA:
{valid}"""
