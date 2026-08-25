"""Telegram Notification Provider — HITL approval workflow (§6-8).

§7: Human-in-the-loop — send Telegram for important decisions.
§8: Silence is NOT approval.
§65: Budget alerts with inline keyboard A/B/C/D.
§95: Final approval with APROVAR/REJEITAR.

Uses Telegram Bot API directly (JSON payload to avoid UTF-8 encoding issues with curl).
Bot: @HermesLocalIMJBot, chat_id=141718934
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path
from typing import Any

from src.providers.base import NotificationProvider


class TelegramNotificationProvider(NotificationProvider):
    """Telegram bot for HITL approvals and notifications (§6-8).

    Uses long polling (no webhook needed — home machine behind NAT, B6 finding).
    """

    def __init__(self, bot_token: str | None = None, chat_id: str | None = None):
        if bot_token is None:
            bot_token = self._read_env("TELEGRAM_BOT_TOKEN")
        if chat_id is None:
            chat_id = self._read_env("TELEGRAM_HOME_CHANNEL", default="141718934")
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.api_base = f"https://api.telegram.org/bot{bot_token}"

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
        payload: dict[str, Any] = {"chat_id": int(chat_id), "text": text}
        if inline_keyboard:
            payload["reply_markup"] = {"inline_keyboard": inline_keyboard}

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.api_base}/sendMessage",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=15)
        result = json.loads(resp.read())
        return result["result"]["message_id"]

    async def send_photo(self, chat_id: str, photo_path: str, caption: str = "") -> int:
        """Send a photo (thumbnail preview)."""
        chat_id = chat_id or self.chat_id
        with open(photo_path, "rb") as photo:
            import urllib.request as ur
            boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
            body = f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode()
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="photo"; filename="{Path(photo_path).name}"\r\n'.encode()
            body += b"Content-Type: image/png\r\n\r\n"
            body += photo.read()
            body += f"\r\n--{boundary}--\r\n".encode()
            if caption:
                # Add caption as a separate field
                pass
            req = ur.Request(
                f"{self.api_base}/sendPhoto",
                data=body,
                headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
            )
            resp = ur.urlopen(req, timeout=30)
            return json.loads(resp.read())["result"]["message_id"]

    async def send_video(self, chat_id: str, video_path: str, caption: str = "") -> int:
        """Send a video (final approval preview).

        Note: Bot API limit is 50MB. For larger files, use a compressed preview.
        """
        chat_id = chat_id or self.chat_id
        file_size = os.path.getsize(video_path)
        if file_size > 50 * 1024 * 1024:
            # Too large — send a message with a download link instead
            return await self.send_message(
                chat_id=chat_id,
                text=f"Video too large for Telegram ({file_size // 1024 // 1024}MB). Preview at: {video_path}",
            )

        import urllib.request as ur
        boundary = "----WebKitFormBoundary7MA4YWxkTrZu0gW"
        body = f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="chat_id"\r\n\r\n{chat_id}\r\n'.encode()
        if caption:
            body += f'Content-Disposition: form-data; name="caption"\r\n\r\n{caption}\r\n'.encode()
            body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="video"; filename="{Path(video_path).name}"\r\n'.encode()
        body += b"Content-Type: video/mp4\r\n\r\n"
        with open(video_path, "rb") as vid:
            body += vid.read()
        body += f"\r\n--{boundary}--\r\n".encode()

        req = ur.Request(
            f"{self.api_base}/sendVideo",
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        resp = ur.urlopen(req, timeout=60)
        return json.loads(resp.read())["result"]["message_id"]

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