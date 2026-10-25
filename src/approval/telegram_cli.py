"""Offline-only preflight for the explicit Telegram delivery-gate composition."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from src.agents.director import DirectorAgent
from src.approval.production_telegram_gate import (
    ProductionTelegramGateConfig,
    preflight_production_telegram_gate,
)
from src.config.loader import load_config
from src.providers.notification.telegram_provider import TelegramNotificationProvider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="studio telegram-inbound-preflight",
        description="Construct and validate the local Telegram gate composition; never serves or contacts Telegram.",
    )
    parser.add_argument("--config", required=True, help="Trusted deployment config YAML with approved chat and sender IDs")
    parser.add_argument("--journal", required=True, help="Absolute external SQLite inbound journal path")
    parser.add_argument("--registry", required=True, help="Absolute external SQLite delivery registry path")
    parser.add_argument("--chat-id", required=True, help="Configured Telegram delivery chat")
    parser.add_argument("--sender-id", action="append", required=True, help="Trusted Telegram sender ID; repeatable")
    parser.add_argument("--instance-id", required=True, help="Trusted local bot-instance binding")
    parser.add_argument("--token-env", required=True, help="Environment variable holding the bot token")
    parser.add_argument("--secret-env", required=True, help="Environment variable holding the webhook secret")
    parser.add_argument("--host", default="127.0.0.1", help="Declared loopback inbound host; no socket is bound")
    parser.add_argument("--port", type=int, default=9443, help="Declared inbound port; no socket is bound")
    args = parser.parse_args(argv)

    token = os.environ.get(args.token_env)
    secret = os.environ.get(args.secret_env)
    if not token:
        parser.error(f"required bot token environment variable is missing: {args.token_env}")
    if not secret:
        parser.error(f"required webhook secret environment variable is missing: {args.secret_env}")

    director = DirectorAgent(load_config(Path(args.config)))
    result = preflight_production_telegram_gate(
        director=director,
        config=ProductionTelegramGateConfig(
            bot_token=token,
            webhook_secret=secret,
            chat_id=args.chat_id,
            trusted_sender_ids=frozenset(args.sender_id),
            instance_id=args.instance_id,
            journal_path=Path(args.journal),
            registry_path=Path(args.registry),
            host=args.host,
            port=args.port,
        ),
        provider_factory=TelegramNotificationProvider,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())