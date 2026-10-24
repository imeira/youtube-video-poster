"""Explicit offline preflight command for the Telegram inbound composition."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
from types import SimpleNamespace

from src.approval.telegram_service import (
    TelegramDirectorService,
    TelegramInboundSettings,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="studio telegram-inbound-preflight",
        description="Validate offline Telegram inbound bindings; never starts a server or contacts Telegram.",
    )
    parser.add_argument("--journal", required=True, help="External SQLite journal path")
    parser.add_argument("--registry", required=True, help="External SQLite delivery registry path")
    parser.add_argument("--chat-id", required=True, help="Configured Telegram delivery chat")
    parser.add_argument("--sender-id", action="append", required=True, help="Trusted Telegram sender ID; repeatable")
    parser.add_argument("--instance-id", required=True, help="Trusted local bot-instance binding")
    parser.add_argument("--secret-env", required=True, help="Environment variable holding the webhook secret")
    args = parser.parse_args(argv)
    secret = os.environ.get(args.secret_env)
    if not secret:
        parser.error(f"required webhook secret environment variable is missing: {args.secret_env}")
    TelegramDirectorService(
        director=SimpleNamespace(),
        settings=TelegramInboundSettings(
            webhook_secret=secret,
            chat_id=args.chat_id,
            trusted_sender_ids=frozenset(args.sender_id),
            instance_id=args.instance_id,
            journal_path=Path(args.journal),
            registry_path=Path(args.registry),
        ),
    )
    print("telegram inbound preflight passed; no server started and no Telegram request was made")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
