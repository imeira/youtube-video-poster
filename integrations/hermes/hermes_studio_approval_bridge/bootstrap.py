"""Fail-closed normal-loader bootstrap for the Studio approval bridge.

The Hermes plugin loader calls ``register(ctx)`` without application arguments.
This module reads only the plugin's authenticated settings subtree after an
explicit opt-in. It validates all configuration and secret separation before
importing or constructing Studio's production Telegram composition.
"""
from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from src.hybrid.locks import reject_reparse_path

# Secret selectors are deliberately not configurable: generic uppercase-name
# validation would permit PATH, HOME, a provider key, or a lookalike alias.
_SECRET_ENV_NAMES = {
    "bridge_hmac_env": "STUDIO_BRIDGE_HMAC",
    "telegram_bot_token_env": "STUDIO_TELEGRAM_BOT_TOKEN",
    "telegram_webhook_secret_env": "STUDIO_TELEGRAM_WEBHOOK_SECRET",
}


class _DormantTelegramProvider:
    """Satisfy the existing production factory without opening a Telegram client."""

    def __init__(self, _token: str, _chat_id: str) -> None:
        pass

    async def send_photo(self, *_args: object, **_kwargs: object) -> int:
        raise RuntimeError("approval bridge bootstrap never sends Telegram media")

    async def send_video(self, *_args: object, **_kwargs: object) -> int:
        raise RuntimeError("approval bridge bootstrap never sends Telegram media")


def _required_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError(f"approval bridge {label} is required")
    return value


def _secret_names(settings: Mapping[str, object]) -> dict[str, str]:
    """Validate every fixed selector before retrieving any secret value."""
    names: dict[str, str] = {}
    for key, expected in _SECRET_ENV_NAMES.items():
        name = _required_string(settings.get(key), key)
        if name != expected:
            raise ValueError(f"approval bridge {key} must be {expected}")
        names[key] = name
    return names


def _env_secret(name: str) -> str:
    return _required_string(os.environ.get(name), f"environment variable {name}")


def _canonical_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal() or str(int(value)) != value or int(value) <= 0:
        raise ValueError(f"approval bridge {label} must be a canonical positive decimal string")
    return value


def _absolute_path(value: object, label: str) -> Path:
    path = Path(_required_string(value, label))
    if not path.is_absolute():
        raise ValueError(f"approval bridge {label} must be an absolute path")
    return path


def _absolute_existing_regular_path(value: object, label: str) -> Path:
    path = _absolute_path(value, label)
    path = reject_reparse_path(path)
    if not path.is_file():
        raise ValueError(f"approval bridge {label} must name an existing regular file")
    return path


def _settings(ctx: object) -> Mapping[str, object] | None:
    getter = getattr(ctx, "get_config", None)
    if not callable(getter):
        return None
    configured = getter("approval_bridge", None)
    if not isinstance(configured, Mapping) or configured.get("enabled") is not True:
        return None
    return configured


def compose_from_context(ctx: object) -> Any | None:
    """Return the real bridge only for a complete, explicit enabled configuration.

    Invalid configuration is deliberately indistinguishable from disabled to
    Hermes's generic plugin loader: no Telegram handler is registered.
    """
    settings = _settings(ctx)
    if settings is None:
        return None
    try:
        instance_id = _required_string(settings.get("instance_id"), "instance_id")
        chat_id = _canonical_id(settings.get("chat_id"), "chat_id")
        raw_senders = settings.get("trusted_sender_ids")
        if not isinstance(raw_senders, list):
            raise TypeError("approval bridge trusted_sender_ids must be a list")
        sender_ids = frozenset(_canonical_id(value, "trusted_sender_ids") for value in raw_senders)
        if not sender_ids:
            raise ValueError("approval bridge trusted_sender_ids is required")
        config_path = _absolute_existing_regular_path(settings.get("studio_config_path"), "studio_config_path")
        journal_path = _absolute_path(settings.get("journal_path"), "journal_path")
        registry_path = _absolute_path(settings.get("registry_path"), "registry_path")
        secret_names = _secret_names(settings)
        bridge_hmac = _env_secret(secret_names["bridge_hmac_env"])
        bot_token = _env_secret(secret_names["telegram_bot_token_env"])
        webhook_secret = _env_secret(secret_names["telegram_webhook_secret_env"])
        if hmac.compare_digest(bridge_hmac, webhook_secret):
            raise ValueError("approval bridge HMAC and Telegram webhook secret must differ")
    except (OSError, TypeError, ValueError):
        return None

    # Keep production imports behind successful opt-in validation. Director is
    # given a non-provider sentinel, and the production factory receives an
    # inert provider, so composition creates no LLM client or Telegram client.
    try:
        from src.agents.director import DirectorAgent
        from src.approval.hermes_bridge_composition import (
            compose_hermes_approval_bridge,
        )
        from src.approval.hermes_inbound_bridge import HermesBridgeConfig
        from src.approval.production_telegram_gate import (
            ProductionTelegramGateConfig,
            create_production_telegram_gate,
        )
        from src.config.loader import load_config

        director = DirectorAgent(config=load_config(config_path), model_router=object(), approval_gate=object())
        gate = create_production_telegram_gate(
            director=director,
            config=ProductionTelegramGateConfig(
                bot_token=bot_token,
                webhook_secret=webhook_secret,
                chat_id=chat_id,
                trusted_sender_ids=sender_ids,
                instance_id=instance_id,
                journal_path=journal_path,
                registry_path=registry_path,
            ),
            provider_factory=_DormantTelegramProvider,
        )
        return compose_hermes_approval_bridge(
            config=HermesBridgeConfig(
                instance_id=instance_id,
                hmac_key=bridge_hmac,
                expected_chat_id=chat_id,
                allowed_sender_ids=sender_ids,
            ),
            service=gate.service,
        )
    except (OSError, TypeError, ValueError):
        return None
