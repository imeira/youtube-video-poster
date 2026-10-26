"""Telegram Notification Provider — HITL approval workflow (§6-8).

§7: Human-in-the-loop — send Telegram for important decisions.
§8: Silence is NOT approval.
§65: Budget alerts with inline keyboard A/B/C/D.
§95: Final approval with APROVAR/REJEITAR.

Uses Telegram Bot API directly (JSON payload to avoid UTF-8 encoding issues with curl).
Bot credentials and destination are loaded from explicit configuration.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from src.providers.base import NotificationProvider


class TelegramNotificationProvider(NotificationProvider):
    """Telegram bot for HITL approvals and notifications (§6-8).

    Uses long polling (no webhook needed — home machine behind NAT, B6 finding).
    """

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
        *,
        transport_timeout_seconds: float = 60.0,
    ):
        if bot_token is None:
            bot_token = self._read_env("TELEGRAM_BOT_TOKEN")
        if chat_id is None:
            chat_id = self._read_env("TELEGRAM_HOME_CHANNEL")
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_base = f"https://api.telegram.org/bot{bot_token}"
        if (
            type(transport_timeout_seconds) not in {int, float}
            or not math.isfinite(transport_timeout_seconds)
            or transport_timeout_seconds <= 0
        ):
            raise ValueError("Telegram transport timeout must be a positive finite number")
        self.transport_timeout_seconds = float(transport_timeout_seconds)

    def _read_env(self, key: str, default: str = "") -> str:
        """Read from Hermes .env file."""
        env_path = os.path.expanduser("~/AppData/Local/hermes/.env")
        if os.path.exists(env_path):
            with open(env_path) as f:
                for line in f:
                    if line.strip().startswith(f"{key}="):
                        return line.split("=", 1)[1].strip()
        return default

    def estimate_cost(self, **params) -> float:
        """Telegram Bot API is free."""
        return 0.0

    async def send_message(
        self,
        chat_id: str = "",
        text: str = "",
        inline_keyboard: list[list[dict]] | None = None,
    ) -> int:
        """Send a text message. Returns message_id (§7 format)."""
        chat_id = chat_id or self.chat_id
        if not str(chat_id).strip():
            raise ValueError("explicit Telegram destination required")
        payload: dict[str, Any] = {"chat_id": int(chat_id), "text": text}
        if inline_keyboard:
            payload["reply_markup"] = {"inline_keyboard": inline_keyboard}

        data = json.dumps(payload).encode("utf-8")
        result = await self._post("sendMessage", data, {"Content-Type": "application/json"})
        return self._message_id(result)

    @staticmethod
    def _message_id(result):
        value = result.get("result", {}).get("message_id")
        if result.get("ok") is not True or type(value) is not int or value <= 0:
            raise ValueError("Telegram did not confirm a positive message_id")
        return value

    async def _post(self, endpoint: str, data: bytes, headers: dict[str, str]) -> dict[str, Any]:
        """Perform one cancellable HTTP POST and close its socket on cancellation.

        Cancellation cannot undo a request already accepted by Telegram.  Callers
        persist their UNKNOWN intent before cancelling and never retry it; closing
        this transport only ends this process's ownership of the active I/O.
        """
        parsed = urlsplit(f"{self.api_base.rstrip('/')}/{endpoint}")
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Telegram API base must be an absolute HTTP(S) URL")
        timeout = aiohttp.ClientTimeout(total=self.transport_timeout_seconds)
        # aiohttp accepts chunked transfer encoding and enforces TLS verification
        # by default.  Bound parser lines/fields and the decoded body separately.
        async with aiohttp.ClientSession(
            timeout=timeout, trust_env=False, read_bufsize=65_536,
            max_line_size=8_192, max_field_size=8_192,
        ) as session, session.post(
            f"{self.api_base.rstrip('/')}/{endpoint}", data=data, headers=headers,
        ) as response:
            body = await response.content.read(1_048_577)
            if len(body) > 1_048_576:
                raise ValueError("Telegram response exceeds transport limit")
            if not 200 <= response.status < 300:
                raise ValueError(f"Telegram HTTP status {response.status}")
            try:
                result = json.loads(body)
            except (TypeError, ValueError) as error:
                raise ValueError("Telegram returned invalid JSON") from error
            if not isinstance(result, dict):
                raise TypeError("Telegram returned a non-object response")
            return result

    async def _send_media(self, kind, chat_id, path, caption):
        import mimetypes
        import uuid
        path = Path(path)
        chat_id = chat_id or self.chat_id
        if not str(chat_id).strip():
            raise ValueError("explicit Telegram destination required")
        if path.stat().st_size > 50 * 1024 * 1024:
            raise ValueError("Telegram media exceeds 50 MiB; delivery blocked")
        boundary = "ep8-" + uuid.uuid4().hex
        chunks = []
        for name, value in (("chat_id", chat_id or self.chat_id), ("caption", caption)):
            chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                           f'\r\n\r\n{value}\r\n').encode())
        filename = path.name.replace('"', '_').replace('\r', '_').replace('\n', '_').replace('\\', '_')
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{kind}"; '
                       f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n').encode())
        chunks.extend((path.read_bytes(), f"\r\n--{boundary}--\r\n".encode()))
        result = await self._post(
            f"send{kind.title()}", b"".join(chunks),
            {"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        return self._message_id(result)

    async def send_photo(self, chat_id: str, photo_path: str, caption: str = "") -> int:
        return await self._send_media("photo", chat_id, photo_path, caption)

    async def send_video(self, chat_id: str, video_path: str, caption: str = "") -> int:
        return await self._send_media("video", chat_id, video_path, caption)

    async def execute(self, **params) -> Any:
        """Execute notification."""
        return await self.send_message(**params)

    # ── HITL Helpers (§7, §65, §95) ────────────────────────────────────────────

    async def send_preproduction_approval(
        self, chat_id: str, episode: str, plan_text: str
    ) -> int:
        """§95: Send pre-production plan for approval."""
        text = f"PRE-PRODUCAO\nEpisodio: {episode}\n\n{plan_text}\n\nAprovar?"
        keyboard = [[
            {"text": "APROVAR", "callback_data": f"plan_approve:{episode}"},
            {"text": "REJEITAR", "callback_data": f"plan_reject:{episode}"},
        ]]
        return await self.send_message(chat_id, text, keyboard)

    async def send_budget_alert(
        self, chat_id: str, episode: str, budget_text: str
    ) -> int:
        """§65: Budget alert with A/B/C/D options."""
        keyboard = [[
            {"text": "A — Autorizar job", "callback_data": f"budget_A:{episode}"},
        ], [
            {"text": "B — Animacao local", "callback_data": f"budget_B:{episode}"},
        ], [
            {"text": "C — Novo orcamento", "callback_data": f"budget_C:{episode}"},
        ], [
            {"text": "D — Cancelar", "callback_data": f"budget_D:{episode}"},
        ]]
        text = f"LIMITE DE ORCAMENTO\nEpisodio: {episode}\n\n{budget_text}"
        return await self.send_message(chat_id, text, keyboard)

    async def send_final_approval(
        self, chat_id: str, episode: str, title: str, duration: str, cost: str
    ) -> int:
        """§95: Final video approval."""
        text = f"VIDEO PRONTO PARA PUBLICACAO\nTitulo: {title}\nDuracao: {duration}\nCusto: {cost}\n\nAprovar publicacao?"
        keyboard = [[
            {"text": "APROVAR", "callback_data": f"final_approve:{episode}"},
            {"text": "REJEITAR", "callback_data": f"final_reject:{episode}"},
        ]]
        return await self.send_message(chat_id, text, keyboard)

    async def send_published_notification(
        self, chat_id: str, title: str, url: str, cost: str
    ) -> int:
        """§96: Final notification with YouTube link."""
        text = f"EPISODIO PUBLICADO\nTitulo: {title}\nURL: {url}\nCusto externo: {cost}"
        return await self.send_message(chat_id, text)