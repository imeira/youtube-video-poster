"""Explicit, non-activating composition for the two Telegram delivery gates.

This is the only production-shaped composition point for the existing concrete
Telegram provider, DeliveryController, Director delivery callback and inbound
TelegramDirectorService.  Constructing it performs local validation and SQLite
initialization only: it never calls Telegram, starts aiohttp, binds a socket,
changes a webhook, starts a runner, or enables publication.
"""
from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.approval.delivery_session import DeliveryOutboxWorker, DeliverySessionRunner
from src.approval.telegram_service import (
    TelegramDirectorService,
    TelegramInboundSettings,
    create_telegram_inbound_app,
)
from src.delivery.controller import DeliveryController
from src.hybrid.locks import reject_reparse_path
from src.providers.notification.telegram_provider import TelegramNotificationProvider


@dataclass(frozen=True)
class ProductionTelegramGateConfig:
    """Trusted, externally supplied transport settings; secrets are never loaded here."""

    bot_token: str
    webhook_secret: str
    chat_id: str
    trusted_sender_ids: frozenset[str]
    instance_id: str
    journal_path: Path
    registry_path: Path
    host: str = "127.0.0.1"
    port: int = 9443
    outbox_reconcile_period_seconds: float = 60.0
    outbox_shutdown_timeout_seconds: float = 30.0


class RetainedOutboxWorkerError(RuntimeError):
    """The host shut down, but duplicate-prevention ownership is still live."""

    def __init__(self, worker: DeliveryOutboxWorker, outcome: dict[str, str]) -> None:
        super().__init__("outbox worker is still stopping; ownership is retained")
        self.worker = worker
        self.outcome = outcome


@dataclass(frozen=True)
class TelegramInboundLauncher:
    """An explicit, loopback-only aiohttp lifecycle; construction remains inert."""

    app: Any
    host: str
    port: int
    outbox_worker: DeliveryOutboxWorker

    @asynccontextmanager
    async def serve(self) -> AsyncIterator[TelegramInboundLauncher]:
        """Bind only when explicitly requested and always clean up aiohttp."""
        try:
            from aiohttp import web
        except ImportError as error:  # pragma: no cover
            raise RuntimeError("aiohttp is required to serve the local Telegram inbound route") from error
        runner = web.AppRunner(self.app)
        site = None
        worker_started = False
        host_error: BaseException | None = None
        try:
            await runner.setup()
            site = web.TCPSite(runner, self.host, self.port)
            await site.start()
            await self.outbox_worker.start()
            worker_started = True
            yield self
        except BaseException as error:
            host_error = error
            raise
        finally:
            cleanup_error: BaseException | None = None
            retained_error: RetainedOutboxWorkerError | None = None
            try:
                if site is not None:
                    await site.stop()
            except BaseException as error:  # noqa: BLE001 - all cleanup must finish before propagation
                cleanup_error = error
            try:
                if worker_started:
                    outcome = await self.outbox_worker.stop()
                    if outcome == {"status": "stopping", "ownership": "retained"}:
                        retained_error = RetainedOutboxWorkerError(self.outbox_worker, outcome)
            except BaseException as error:  # noqa: BLE001 - all cleanup must finish before propagation
                if cleanup_error is None:
                    cleanup_error = error
            try:
                await runner.cleanup()
            except BaseException as error:  # noqa: BLE001 - all cleanup must finish before propagation
                if cleanup_error is None:
                    cleanup_error = error

            if host_error is not None:
                if retained_error is not None:
                    host_error.add_note(f"{retained_error}; worker reference is available on the launcher")
                if cleanup_error is not None:
                    host_error.add_note(f"launcher cleanup also failed: {cleanup_error!r}")
            elif cleanup_error is not None:
                raise cleanup_error
            elif retained_error is not None:
                raise retained_error


@dataclass(frozen=True)
class ProductionTelegramGateComposition:
    """One concrete, non-activating transport composition for Director gates."""

    provider: TelegramNotificationProvider
    delivery_controller: DeliveryController
    service: TelegramDirectorService
    launcher: TelegramInboundLauncher
    outbox_worker: DeliveryOutboxWorker
    director: Any
    config: ProductionTelegramGateConfig

    def delivery_session_runner(self):
        """Return the explicit outbox consumer; constructing it does not deliver."""
        from src.approval.delivery_session import DeliverySessionRunner

        return DeliverySessionRunner(registry_path=self.config.registry_path, composition=self)

    def revalidate_thumbnail_approval(
        self, *, episode_id: str, thumbnail_path: Path, thumbnail_sha256: str
    ) -> None:
        """Delegate second-gate authority checks to the configured authenticated source."""
        self.service.revalidate_thumbnail_approval(
            episode_id=episode_id, thumbnail_path=thumbnail_path, thumbnail_sha256=thumbnail_sha256
        )

    async def deliver_for_approval(self, episode_id: str, *, thumbnail_path: Path) -> dict[str, Any]:
        """Use Director's existing sequential state/DeliveryController path, then bind receipt inbound."""
        async def register_after_persist(receipt_path: Path) -> None:
            await asyncio.to_thread(self.service.register_delivery, receipt_path)

        receipt = await self.director.deliver_for_approval(
            episode_id,
            messenger=self.provider,
            chat_id=self.config.chat_id,
            thumbnail_path=Path(thumbnail_path),
            after_persist=register_after_persist,
        )
        if not isinstance(receipt, dict) or receipt.get("artifact_kind") not in {"thumbnail", "video"}:
            raise ValueError("Director did not return a registered delivery receipt")
        receipt_path = (
            Path(self.director.config.episodes_dir) / episode_id / "qa" / "delivery"
            / f"{receipt['artifact_kind']}.json"
        )
        registered = await asyncio.to_thread(
            self.service._lookup, chat_id=self.config.chat_id, message_id=receipt["message_id"]
        )
        if registered is None:
            await asyncio.to_thread(self.service.register_delivery, receipt_path)
        return receipt


def _external_secret(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError(f"external Telegram {label} is required")
    return value


def _trusted_path(value: Path | str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"trusted {label} path must be absolute")
    return reject_reparse_path(path)


def _canonical_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.isascii() or not value.isdecimal():
        raise ValueError(f"trusted Telegram {label} id must be a canonical positive decimal string")
    canonical = str(int(value))
    if int(canonical) <= 0 or canonical != value:
        raise ValueError(f"trusted Telegram {label} id must be a canonical positive decimal string")
    return canonical


def _validate(config: ProductionTelegramGateConfig, director: Any) -> ProductionTelegramGateConfig:
    token = _external_secret(config.bot_token, "bot token")
    secret = _external_secret(config.webhook_secret, "webhook secret")
    chat_id = _canonical_id(config.chat_id, "chat")
    sender_ids = frozenset(_canonical_id(sender, "sender") for sender in config.trusted_sender_ids)
    if not sender_ids:
        raise ValueError("trusted Telegram sender configuration is required")
    if not isinstance(config.instance_id, str) or not config.instance_id.strip() or len(config.instance_id) > 256:
        raise ValueError("trusted Telegram instance binding is required")
    inbound = TelegramInboundSettings(
        webhook_secret=secret,
        chat_id=chat_id,
        trusted_sender_ids=sender_ids,
        instance_id=config.instance_id,
        journal_path=_trusted_path(config.journal_path, "journal"),
        registry_path=_trusted_path(config.registry_path, "registry"),
    )
    if not isinstance(config.host, str) or config.host not in {"127.0.0.1", "::1"}:
        raise ValueError("Telegram inbound host must be explicit loopback")
    if type(config.port) is not int or not 1 <= config.port <= 65535:
        raise ValueError("Telegram inbound port must be valid")
    for value, label in (
        (config.outbox_reconcile_period_seconds, "outbox reconcile period"),
        (config.outbox_shutdown_timeout_seconds, "outbox shutdown timeout"),
    ):
        if type(value) not in {int, float} or not math.isfinite(value) or value <= 0:
            raise ValueError(f"Telegram {label} must be a positive finite number")
    director_config = getattr(director, "config", None)
    if director_config is None or getattr(director_config, "approval_delivery_chat_id", None) != inbound.chat_id:
        raise ValueError("trusted Telegram chat must match Director delivery configuration")
    expected_senders = getattr(director_config, "approval_delivery_trusted_sender_ids", None)
    if expected_senders is not None and frozenset(expected_senders) != inbound.trusted_sender_ids:
        raise ValueError("trusted Telegram senders must match Director delivery configuration")
    return ProductionTelegramGateConfig(
        bot_token=token,
        webhook_secret=secret,
        chat_id=inbound.chat_id,
        trusted_sender_ids=inbound.trusted_sender_ids,
        instance_id=config.instance_id,
        journal_path=inbound.journal_path,
        registry_path=inbound.registry_path,
        host=config.host,
        port=config.port,
        outbox_reconcile_period_seconds=float(config.outbox_reconcile_period_seconds),
        outbox_shutdown_timeout_seconds=float(config.outbox_shutdown_timeout_seconds),
    )


def create_production_telegram_gate(
    *,
    director: Any,
    config: ProductionTelegramGateConfig,
    provider_factory: Callable[[str, str], TelegramNotificationProvider] = TelegramNotificationProvider,
) -> ProductionTelegramGateComposition:
    """Construct local components only; all remote activation remains external and explicit."""
    trusted = _validate(config, director)
    provider = provider_factory(trusted.bot_token, trusted.chat_id)
    controller = DeliveryController(provider)
    service = TelegramDirectorService(
        director=director,
        settings=TelegramInboundSettings(
            webhook_secret=trusted.webhook_secret,
            chat_id=trusted.chat_id,
            trusted_sender_ids=trusted.trusted_sender_ids,
            instance_id=trusted.instance_id,
            journal_path=trusted.journal_path,
            registry_path=trusted.registry_path,
        ),
    )
    app = create_telegram_inbound_app(service)
    runner = DeliverySessionRunner(registry_path=trusted.registry_path, composition=None)
    outbox_worker = DeliveryOutboxWorker(
        runner,
        reconcile_period_seconds=trusted.outbox_reconcile_period_seconds,
        shutdown_timeout_seconds=trusted.outbox_shutdown_timeout_seconds,
    )
    composition = ProductionTelegramGateComposition(
        provider=provider,
        delivery_controller=controller,
        service=service,
        launcher=TelegramInboundLauncher(
            app=app, host=trusted.host, port=trusted.port, outbox_worker=outbox_worker,
        ),
        outbox_worker=outbox_worker,
        director=director,
        config=trusted,
    )
    runner.composition = composition
    service.set_outbox_wakeup(outbox_worker.wake)
    return composition


def preflight_production_telegram_gate(**kwargs: Any) -> dict[str, str]:
    """Exercise composition without delivery or serving; output intentionally contains no credentials."""
    create_production_telegram_gate(**kwargs)
    return {"status": "ready", "network": "disabled", "publication": "disabled"}
