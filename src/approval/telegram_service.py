"""Offline operational composition for authenticated Telegram approval replies.

This module deliberately exposes an in-process HTTP route only.  It does not
configure Telegram, poll updates, start a listener, or send media.  A caller
must explicitly construct it from externally trusted configuration and register
the durable delivery receipt produced by the existing delivery controller.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.approval.controller import ApprovalController, ApprovalControllerError
from src.approval.receipts import ApprovalReceipt
from src.approval.telegram_inbound import TelegramInboundAdapter
from src.hybrid.locks import file_lock, reject_reparse_path
from src.state.machine import EpisodeState, EpisodeStateStore

_HEADER = "X-Telegram-Bot-Api-Secret-Token"
_MAX_BODY_BYTES = 65_536
_SCHEMA = "telegram-director-registry-v1"


@dataclass(frozen=True)
class TelegramInboundSettings:
    """Externally supplied bindings; no secret is read from a webhook payload."""

    webhook_secret: str | None
    chat_id: str
    trusted_sender_ids: frozenset[str]
    instance_id: str
    journal_path: Path
    registry_path: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "journal_path", Path(self.journal_path))
        object.__setattr__(self, "registry_path", Path(self.registry_path))

@dataclass(frozen=True)
class WebhookResult:
    status: int
    body: dict[str, object]


class TelegramDirectorService:
    """Compose journaled Telegram replies with the currently pending Director gate."""

    def __init__(self, *, director: Any, settings: TelegramInboundSettings) -> None:
        self._director = director
        self._settings = settings
        self._enabled = bool(settings.webhook_secret and director is not None)
        self._adapter = TelegramInboundAdapter(
            journal_path=settings.journal_path,
            expected_chat_id=settings.chat_id,
            trusted_sender_ids=settings.trusted_sender_ids,
            webhook_secret=settings.webhook_secret,
            instance_id=settings.instance_id,
        )
        if self._enabled:
            self._initialize_registry()
            self._reconcile_configured_deliveries()
            # Director receives transport authority only from this configured adapter.
            self._director.inbound_approval_source = self._adapter

    @staticmethod
    def _json(value: dict[str, object]) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")

    def _registry_mac(self, document: dict[str, object]) -> str:
        return hmac.new(self._settings.webhook_secret.encode("utf-8"), self._json(document), hashlib.sha256).hexdigest()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._settings.registry_path, timeout=10, isolation_level=None)
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        return connection

    def _initialize_registry(self) -> None:
        self._settings.registry_path.parent.mkdir(parents=True, exist_ok=True)
        binding = {
            "chat_id": self._settings.chat_id,
            "instance_id": self._settings.instance_id,
            "schema": _SCHEMA,
            "senders": sorted(self._settings.trusted_sender_ids),
        }
        with self._connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS deliveries ("
                "chat_id TEXT NOT NULL, message_id INTEGER NOT NULL, episode_id TEXT NOT NULL, "
                "artifact_kind TEXT NOT NULL, artifact_path TEXT NOT NULL, artifact_sha256 TEXT NOT NULL, "
                "delivery_receipt_path TEXT NOT NULL, mac TEXT NOT NULL, PRIMARY KEY(chat_id, message_id))"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS outcomes ("
                "chat_id TEXT NOT NULL, message_id INTEGER NOT NULL, episode_id TEXT NOT NULL, "
                "artifact_kind TEXT NOT NULL, artifact_sha256 TEXT NOT NULL, receipt_sha256 TEXT NOT NULL, "
                "state TEXT NOT NULL, mac TEXT NOT NULL, PRIMARY KEY(chat_id, message_id))"
            )
            row = connection.execute("SELECT value FROM metadata WHERE key = 'binding'").fetchone()
            digest = self._registry_mac(binding)
            if row is None:
                connection.execute("INSERT INTO metadata(key, value) VALUES ('binding', ?)", (digest,))
            elif not hmac.compare_digest(row[0], digest):
                raise ValueError("Telegram delivery registry binding does not match trusted configuration")

    @staticmethod
    def _load_delivery(path: Path | str) -> tuple[dict[str, object], ApprovalReceipt]:
        try:
            document = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("delivery receipt is unreadable") from error
        expected = {"episode_id", "artifact_kind", "approval", "chat_id", "message_id"}
        if set(document) != expected or type(document["episode_id"]) is not str or not document["episode_id"].strip():
            raise ValueError("delivery receipt schema is invalid")
        if document["artifact_kind"] not in {"thumbnail", "video"} or type(document["chat_id"]) is not str:
            raise ValueError("delivery receipt gate binding is invalid")
        if type(document["message_id"]) is not int or document["message_id"] <= 0:
            raise ValueError("delivery receipt message binding is invalid")
        try:
            receipt = ApprovalReceipt(**document["approval"])
            receipt.verify()
        except Exception as error:
            raise ValueError("delivery receipt media binding is invalid") from error
        if receipt.artifact_kind != document["artifact_kind"]:
            raise ValueError("delivery receipt artifact kind differs from media")
        return document, receipt

    def register_delivery(self, delivery_receipt_path: Path | str) -> None:
        """Persist one existing outbound delivery mapping; no remote operation occurs."""
        if not self._enabled:
            raise ValueError("Telegram inbound admission is not configured")
        document, receipt = self._load_delivery(delivery_receipt_path)
        if document["chat_id"] != self._settings.chat_id:
            raise ValueError("delivery receipt destination is not the configured Telegram chat")
        record = {
            "chat_id": document["chat_id"], "message_id": document["message_id"],
            "episode_id": document["episode_id"], "artifact_kind": document["artifact_kind"],
            "artifact_path": str(Path(receipt.artifact_path).resolve()),
            "artifact_sha256": receipt.artifact_sha256,
            "delivery_receipt_path": str(Path(delivery_receipt_path).resolve()),
        }
        mac = self._registry_mac(record)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT episode_id, artifact_kind, artifact_path, artifact_sha256, delivery_receipt_path, mac "
                "FROM deliveries WHERE chat_id = ? AND message_id = ?", (record["chat_id"], record["message_id"])
            ).fetchone()
            if existing is not None:
                persisted = dict(zip(("episode_id", "artifact_kind", "artifact_path", "artifact_sha256", "delivery_receipt_path", "mac"), existing, strict=True))
                if persisted.pop("mac") != mac or persisted != {key: record[key] for key in persisted}:
                    raise ValueError("delivery message is already bound to different media")
                return
            connection.execute(
                "INSERT INTO deliveries(chat_id, message_id, episode_id, artifact_kind, artifact_path, artifact_sha256, delivery_receipt_path, mac) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (*record.values(), mac),
            )

    def _configured_delivery_receipts(self) -> tuple[Path, ...]:
        """Enumerate only the configured episode root's fixed delivery slots."""
        root = reject_reparse_path(Path(self._director.config.episodes_dir))
        if not root.is_dir():
            return ()
        candidates: list[Path] = []
        for entry in root.iterdir():
            episode = reject_reparse_path(entry)
            if not episode.is_dir():
                continue
            qa = reject_reparse_path(episode / "qa")
            delivery = reject_reparse_path(qa / "delivery")
            for kind in ("thumbnail", "video"):
                receipt = reject_reparse_path(delivery / f"{kind}.json")
                if receipt.is_file():
                    candidates.append(receipt)
        return tuple(candidates)

    def _reconcile_configured_deliveries(self) -> None:
        """Recover durable, authenticated outbound receipts without any resend."""
        for receipt in self._configured_delivery_receipts():
            lock_path = reject_reparse_path(receipt.with_suffix(".lock"))
            with file_lock(lock_path):
                self.register_delivery(receipt)

    def _recover_delivery(self, *, chat_id: str, message_id: int) -> dict[str, object] | None:
        """Close persist/register interruption from fixed configured receipt slots only."""
        for receipt_path in self._configured_delivery_receipts():
            try:
                document, _ = self._load_delivery(receipt_path)
            except ValueError:
                continue
            if document["chat_id"] != chat_id or document["message_id"] != message_id:
                continue
            lock_path = reject_reparse_path(receipt_path.with_suffix(".lock"))
            with file_lock(lock_path):
                self.register_delivery(receipt_path)
                return self._lookup(chat_id=chat_id, message_id=message_id)
        return None

    @staticmethod
    def _reply_locator(body: bytes) -> tuple[str, int] | None:
        try:
            value = json.loads(body.decode("utf-8"))
            message = value["message"]
            chat_id = message["chat"]["id"]
            reply_to = message["reply_to_message"]["message_id"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
            return None
        if type(chat_id) is not int or chat_id <= 0 or type(reply_to) is not int or reply_to <= 0:
            return None
        return str(chat_id), reply_to

    def _lookup(self, *, chat_id: str, message_id: int) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT episode_id, artifact_kind, artifact_path, artifact_sha256, delivery_receipt_path, mac FROM deliveries WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            ).fetchone()
        if row is None:
            return None
        record = dict(zip(("episode_id", "artifact_kind", "artifact_path", "artifact_sha256", "delivery_receipt_path", "mac"), row, strict=True))
        mac = record.pop("mac")
        return record if isinstance(mac, str) and hmac.compare_digest(mac, self._registry_mac({"chat_id": chat_id, "message_id": message_id, **record})) else None

    def _gate_lock_path(self, *, chat_id: str, message_id: int) -> Path:
        key = hashlib.sha256(f"{self._settings.instance_id}\0{chat_id}\0{message_id}".encode()).hexdigest()
        return self._settings.registry_path.parent / ".telegram-gates" / f"{key}.lock"

    @staticmethod
    def _receipt_sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _outcome_mac(self, outcome: dict[str, object]) -> str:
        return self._registry_mac(outcome)

    def _read_outcome(self, *, chat_id: str, message_id: int) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT episode_id, artifact_kind, artifact_sha256, receipt_sha256, state, mac "
                "FROM outcomes WHERE chat_id = ? AND message_id = ?", (chat_id, message_id)
            ).fetchone()
        if row is None:
            return None
        outcome = dict(zip(("episode_id", "artifact_kind", "artifact_sha256", "receipt_sha256", "state", "mac"), row, strict=True))
        mac = outcome.pop("mac")
        bound = {"chat_id": chat_id, "message_id": message_id, **outcome}
        return outcome if isinstance(mac, str) and hmac.compare_digest(mac, self._outcome_mac(bound)) else None

    def _persist_outcome(self, *, chat_id: str, message_id: int, record: dict[str, object], state: EpisodeState) -> dict[str, object]:
        receipt_path = Path(str(record["approval_path"]))
        outcome = {
            "chat_id": chat_id, "message_id": message_id, "episode_id": str(record["episode_id"]),
            "artifact_kind": str(record["artifact_kind"]), "artifact_sha256": str(record["artifact_sha256"]),
            "receipt_sha256": self._receipt_sha256(receipt_path), "state": state.value,
        }
        mac = self._outcome_mac(outcome)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT episode_id, artifact_kind, artifact_sha256, receipt_sha256, state, mac FROM outcomes WHERE chat_id = ? AND message_id = ?",
                (chat_id, message_id),
            ).fetchone()
            if existing is not None:
                stored = dict(zip(("episode_id", "artifact_kind", "artifact_sha256", "receipt_sha256", "state", "mac"), existing, strict=True))
                if stored.pop("mac") != mac or stored != {key: outcome[key] for key in stored}:
                    raise ValueError("Telegram approval outcome is inconsistent")
                return stored
            connection.execute(
                "INSERT INTO outcomes(chat_id, message_id, episode_id, artifact_kind, artifact_sha256, receipt_sha256, state, mac) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (*outcome.values(), mac),
            )
        return {key: outcome[key] for key in ("episode_id", "artifact_kind", "artifact_sha256", "receipt_sha256", "state")}

    def _approval_path(self, record: dict[str, object]) -> Path:
        return Path(self._director.config.episodes_dir) / str(record["episode_id"]) / "qa" / f"approval-{record['artifact_kind']}.json"

    def _revalidate_completed(self, *, record: dict[str, object]) -> EpisodeState | None:
        """Prove an existing result still binds current authority and state."""
        expected = {"thumbnail": EpisodeState.WAITING_THUMBNAIL_APPROVAL, "video": EpisodeState.WAITING_VIDEO_APPROVAL}
        next_state = {"thumbnail": EpisodeState.WAITING_VIDEO_APPROVAL, "video": EpisodeState.WAITING_FINAL_APPROVAL}
        kind = str(record["artifact_kind"])
        approval_path = self._approval_path(record)
        if kind not in expected or not approval_path.is_file():
            return None
        try:
            state = EpisodeStateStore.load(Path(self._director.config.episodes_dir) / str(record["episode_id"]) / "state.json")
            if state.current_state is not next_state[kind]:
                return None
            receipt = ApprovalController().confirm_delivery(
                episode_id=str(record["episode_id"]), artifact_kind=kind,
                artifact_path=Path(str(record["artifact_path"])),
                delivery_receipt_path=Path(str(record["delivery_receipt_path"])),
                approval_receipt_path=approval_path,
                expected_chat_id=self._settings.chat_id, trusted_sender_ids=self._settings.trusted_sender_ids,
                inbound_source=self._adapter,
            )
            if receipt.artifact_sha256 != record["artifact_sha256"]:
                return None
            record["approval_path"] = str(approval_path)
            return state.current_state
        except (ApprovalControllerError, ValueError, OSError):
            return None

    def _process_record(self, *, chat_id: str, message_id: int, record: dict[str, object]) -> WebhookResult:
        record = dict(record)
        record["approval_path"] = str(self._approval_path(record))
        recovering = Path(str(record["approval_path"])).is_file()
        completed_state = self._revalidate_completed(record=record)
        if completed_state is not None:
            outcome = self._persist_outcome(chat_id=chat_id, message_id=message_id, record=record, state=completed_state)
            return WebhookResult(200, {"status": "acknowledged", "result_status": "approved", "episode_id": outcome["episode_id"], "artifact_kind": outcome["artifact_kind"]})
        try:
            receipt = self._director.confirm_delivered_artifact(
                str(record["episode_id"]), artifact_kind=str(record["artifact_kind"]),
                artifact_path=Path(str(record["artifact_path"])), delivery_receipt_path=Path(str(record["delivery_receipt_path"])),
            )
        except ApprovalControllerError as error:
            return WebhookResult(422, {"status": "not_approved", "reason": str(error)})
        except ValueError as error:
            return WebhookResult(409, {"status": "not_pending", "reason": str(error)})
        if receipt.artifact_sha256 != record["artifact_sha256"]:
            return WebhookResult(409, {"status": "binding_mismatch"})
        state = EpisodeStateStore.load(Path(self._director.config.episodes_dir) / str(record["episode_id"]) / "state.json")
        outcome = self._persist_outcome(chat_id=chat_id, message_id=message_id, record=record, state=state.current_state)
        if recovering:
            return WebhookResult(200, {"status": "recovered", "result_status": "approved", "episode_id": outcome["episode_id"], "artifact_kind": outcome["artifact_kind"]})
        return WebhookResult(200, {"status": "approved", "episode_id": outcome["episode_id"], "artifact_kind": outcome["artifact_kind"]})

    def handle_webhook(self, body: bytes, secret_header: str | None) -> WebhookResult:
        """Validate, journal, resolve only a durable mapping, then confirm the active gate."""
        admitted = self._admit(body, secret_header)
        if isinstance(admitted, WebhookResult):
            return admitted
        chat_id, message_id, record = admitted
        with file_lock(self._gate_lock_path(chat_id=chat_id, message_id=message_id)):
            return self._process_record(chat_id=chat_id, message_id=message_id, record=record)

    def _admit(self, body: bytes, secret_header: str | None) -> WebhookResult | tuple[str, int, dict[str, object]]:
        if not self._enabled:
            return WebhookResult(503, {"status": "not_configured"})
        if type(body) is not bytes or len(body) == 0 or len(body) > _MAX_BODY_BYTES:
            return WebhookResult(413, {"status": "invalid_request"})
        if not isinstance(secret_header, str) or not hmac.compare_digest(self._settings.webhook_secret, secret_header):
            return WebhookResult(401, {"status": "unauthorized"})
        if not self._adapter.ingest_webhook(body=body, secret_header=secret_header):
            return WebhookResult(400, {"status": "rejected"})
        locator = self._reply_locator(body)
        if locator is None:
            return WebhookResult(400, {"status": "rejected"})
        record = self._lookup(chat_id=locator[0], message_id=locator[1])
        if record is None:
            record = self._recover_delivery(chat_id=locator[0], message_id=locator[1])
        if record is None:
            return WebhookResult(404, {"status": "unknown_delivery"})
        return locator[0], locator[1], record

    @staticmethod
    @asynccontextmanager
    async def _gate_lock(lock_path: Path):
        """Balance a thread-held OS lock even if task cancellation races acquire."""
        lock = file_lock(lock_path)
        acquire = asyncio.create_task(asyncio.to_thread(lock.__enter__))
        cancelled = False
        while not acquire.done():
            try:
                await asyncio.shield(acquire)
            except asyncio.CancelledError:
                cancelled = True
        acquire.result()
        if cancelled:
            await asyncio.shield(asyncio.to_thread(lock.__exit__, None, None, None))
            raise asyncio.CancelledError
        try:
            yield
        finally:
            release = asyncio.create_task(asyncio.to_thread(lock.__exit__, None, None, None))
            cancelled = False
            while not release.done():
                try:
                    await asyncio.shield(release)
                except asyncio.CancelledError:
                    cancelled = True
            release.result()
            if cancelled:
                raise asyncio.CancelledError

    async def handle_webhook_async(self, body: bytes, secret_header: str | None) -> WebhookResult:
        """HTTP-safe serialized path; cancellation cannot release an active gate early."""
        admitted = await asyncio.to_thread(self._admit, body, secret_header)
        if isinstance(admitted, WebhookResult):
            return admitted
        chat_id, message_id, record = admitted
        async with self._gate_lock(self._gate_lock_path(chat_id=chat_id, message_id=message_id)):
            task = asyncio.create_task(asyncio.to_thread(
                self._process_record, chat_id=chat_id, message_id=message_id, record=record
            ))
            cancelled = False
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    cancelled = True
            result = task.result()
            if cancelled:
                raise asyncio.CancelledError
            return result


def create_telegram_inbound_app(service: TelegramDirectorService):
    """Return an aiohttp app; the caller alone decides whether to serve it."""
    try:
        from aiohttp import web
    except ImportError as error:  # pragma: no cover - covered by preflight environments without aiohttp
        raise RuntimeError("aiohttp is required to expose the local Telegram inbound route") from error

    async def inbound(request):
        body = await request.read()
        result = await service.handle_webhook_async(body, request.headers.get(_HEADER))
        return web.json_response(result.body, status=result.status)

    app = web.Application(client_max_size=_MAX_BODY_BYTES)
    app.router.add_post("/telegram/inbound", inbound)
    return app
