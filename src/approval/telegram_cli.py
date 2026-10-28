"""Offline-only preflight for the explicit Telegram delivery-gate composition."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from src.agents.director import DirectorAgent
from src.approval.delivery_session import DeliverySessionRunner
from src.approval.production_telegram_gate import (
    ProductionTelegramGateConfig,
    create_production_telegram_gate,
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


def delivery_session_main(argv: list[str] | None = None) -> int:
    """Read one durable second-gate eligibility row or explicitly drain one row."""
    parser = argparse.ArgumentParser(
        prog="studio telegram-delivery-session",
        description="Read or explicitly deliver one hash-bound Director video gate; no server or polling is started.",
    )
    parser.add_argument("action", choices=("preflight", "drain"))
    parser.add_argument("--registry", required=True, help="Absolute delivery registry SQLite path")
    parser.add_argument("--config", help="Trusted deployment config YAML; required only for drain")
    parser.add_argument("--journal", help="Absolute inbound journal SQLite path; required only for drain")
    parser.add_argument("--chat-id", help="Configured Telegram delivery chat; required only for drain")
    parser.add_argument("--sender-id", action="append", help="Trusted sender ID; repeatable, required only for drain")
    parser.add_argument("--instance-id", help="Trusted bot instance binding; required only for drain")
    parser.add_argument("--token-env", help="Bot-token environment variable; required only for drain")
    parser.add_argument("--secret-env", help="Webhook-secret environment variable; required only for drain")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9443)

    args = parser.parse_args(argv)
    registry = Path(args.registry)
    if args.action == "preflight":
        print(json.dumps(DeliverySessionRunner(registry_path=registry, composition=None).peek(), sort_keys=True))
        return 0
    required = ("config", "journal", "chat_id", "sender_id", "instance_id", "token_env", "secret_env")

    missing = [name.replace("_", "-") for name in required if not getattr(args, name)]
    if missing:
        parser.error(f"{args.action} requires " + ", ".join("--" + name for name in missing))
    token, secret = os.environ.get(args.token_env), os.environ.get(args.secret_env)
    if not token:
        parser.error(f"required bot token environment variable is missing: {args.token_env}")
    if not secret:
        parser.error(f"required webhook secret environment variable is missing: {args.secret_env}")
    director = DirectorAgent(load_config(Path(args.config)))
    gate = create_production_telegram_gate(
        director=director,
        config=ProductionTelegramGateConfig(
            bot_token=token, webhook_secret=secret, chat_id=args.chat_id,
            trusted_sender_ids=frozenset(args.sender_id), instance_id=args.instance_id,
            journal_path=Path(args.journal), registry_path=registry, host=args.host, port=args.port,
        ),
        provider_factory=TelegramNotificationProvider,
    )
    result = asyncio.run(gate.delivery_session_runner().drain_once())
    print(json.dumps(result, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())