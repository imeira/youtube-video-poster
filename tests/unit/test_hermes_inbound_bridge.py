"""Offline contracts for the disabled-by-default Hermes-owned approval bridge."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import importlib
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agents.director import DirectorAgent
from src.approval.hermes_inbound_bridge import (
    HermesBridgeConfig,
    HermesStudioIngress,
    canonical_envelope,
)
from src.approval.receipts import ApprovalReceipt
from src.approval.telegram_service import (
    TelegramDirectorService,
    TelegramInboundSettings,
)

BRIDGE_SECRET = "test-only-bridge-secret"
WEBHOOK_SECRET = "test-only-webhook-secret"
CHAT_ID = "10001"
SENDER_ID = "20002"
INSTANCE_ID = "studio-test-instance"


def _update(*, update_id: int = 1, message_id: int = 99, reply_to: int = 42, sender_id: int = int(SENDER_ID), chat_id: int = int(CHAT_ID), text: str = "APROVAR THUMBNAIL EP8") -> bytes:
    return json.dumps({"update_id": update_id, "message": {
        "message_id": message_id, "from": {"id": sender_id, "is_bot": False},
        "chat": {"id": chat_id, "type": "private"},
        "reply_to_message": {"message_id": reply_to}, "text": text,
    }}, separators=(",", ":")).encode()


def _service(tmp_path: Path):
    episodes = tmp_path / "episodes"
    episode = episodes / "EP8"
    (episode / "qa").mkdir(parents=True)
    from src.state.machine import EpisodeState, EpisodeStateStore
    state = EpisodeStateStore.load_or_create(episode / "state.json", "EP8")
    state.current_state = EpisodeState.WAITING_THUMBNAIL_APPROVAL
    state.save(episode / "state.json")
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = SimpleNamespace(episodes_dir=episodes, approval_delivery_chat_id=CHAT_ID, approval_delivery_trusted_sender_ids=frozenset({SENDER_ID}))
    service = TelegramDirectorService(director=director, settings=TelegramInboundSettings(
        webhook_secret=WEBHOOK_SECRET, chat_id=CHAT_ID, trusted_sender_ids=frozenset({SENDER_ID}),
        instance_id=INSTANCE_ID, journal_path=tmp_path / "external" / "journal.sqlite3", registry_path=tmp_path / "external" / "deliveries.sqlite3"))
    artifact = tmp_path / "thumbnail.png"
    artifact.write_bytes(b"frozen-thumbnail")
    receipt = tmp_path / "thumbnail-delivery.json"
    receipt.write_text(json.dumps({"episode_id": "EP8", "artifact_kind": "thumbnail", "approval": asdict(ApprovalReceipt.approve("thumbnail", artifact, "delivery-audit")), "chat_id": CHAT_ID, "message_id": 42}), encoding="utf-8")
    service.register_delivery(receipt)
    return service, episode, artifact


def _config(*, secret: str | None = BRIDGE_SECRET) -> HermesBridgeConfig:
    return HermesBridgeConfig(instance_id=INSTANCE_ID, hmac_key=secret, expected_chat_id=CHAT_ID, allowed_sender_ids=frozenset({SENDER_ID}))


def _signed(config: HermesBridgeConfig, raw: bytes, *, nonce: str = "nonce-0001", timestamp: int = 1_700_000_000) -> tuple[bytes, dict[str, str]]:
    envelope = canonical_envelope(config, raw, nonce=nonce, timestamp=timestamp)
    signature = hmac.new(config.hmac_key.encode(), b"hermes-studio-approval-bridge/v1\0" + envelope, hashlib.sha256).hexdigest()
    return envelope, {"X-Studio-Bridge-Version": "1", "X-Studio-Bridge-Instance": config.instance_id, "X-Studio-Bridge-Signature": f"v1={signature}"}


def test_actual_plugin_registration_uses_single_isolated_handler_and_forwards_normalized_update():
    from integrations.hermes.hermes_studio_approval_bridge.plugin import register

    class App:
        def __init__(self): self.handlers = []
        def add_handler(self, handler, group=0): self.handlers.append((handler, group))
    class Context:
        def __init__(self): self.application = App(); self.factories = []
        def register_platform_handler(self, platform, factory): self.factories.append((platform, factory))
    captured: list[bytes] = []
    ctx = Context()
    register(ctx, config=_config(), transport=lambda body, headers: captured.append(body) or 200, recognize=lambda _raw: True)
    assert [name for name, _ in ctx.factories] == ["telegram"]
    factory = ctx.factories[0][1]
    factory(ctx.application, object())
    assert len(ctx.application.handlers) == 1
    handler, group = ctx.application.handlers[0]
    assert group == -1
    update = SimpleNamespace(update_id=1, effective_message=SimpleNamespace(message_id=99, text="APROVAR THUMBNAIL EP8", from_user=SimpleNamespace(id=int(SENDER_ID), is_bot=False), chat=SimpleNamespace(id=int(CHAT_ID), type="private"), reply_to_message=SimpleNamespace(message_id=42)))
    asyncio.run(handler.callback(update, object()))
    forwarded = json.loads(captured[0])
    assert base64.b64decode(forwarded["raw_update_b64"]) == _update()


def test_registered_handler_local_transport_reuses_studio_approval_entrypoint(tmp_path: Path):
    from integrations.hermes.hermes_studio_approval_bridge.plugin import register

    service, episode, artifact = _service(tmp_path)
    ingress = HermesStudioIngress(config=_config(), service=service)

    class App:
        def __init__(self): self.handler = None
        def add_handler(self, handler, group=0):
            assert group == -1
            self.handler = handler
    class Context:
        def __init__(self): self.factory = None
        def register_platform_handler(self, platform, factory):
            assert platform == "telegram"
            self.factory = factory

    results = []
    ctx, app = Context(), App()
    assert register(ctx, config=_config(), transport=lambda body, headers: results.append(ingress.handle(body, headers) or 200), recognize=lambda _raw: True)
    ctx.factory(app, object())
    update = SimpleNamespace(update_id=1, effective_message=SimpleNamespace(message_id=99, text="APROVAR THUMBNAIL EP8", from_user=SimpleNamespace(id=int(SENDER_ID), is_bot=False), chat=SimpleNamespace(id=int(CHAT_ID), type="private"), reply_to_message=SimpleNamespace(message_id=42)))
    asyncio.run(app.handler.callback(update, object()))

    assert results[0].body["status"] == "approved"
    assert json.loads((episode / "qa" / "approval-thumbnail.json").read_text())["inbound_receipt"]["reply_to_message_id"] == 42
    import sqlite3
    with sqlite3.connect(tmp_path / "external" / "deliveries.sqlite3") as connection:
        assert connection.execute("SELECT thumbnail_sha256, status FROM video_delivery_outbox").fetchone() == (hashlib.sha256(artifact.read_bytes()).hexdigest(), "PENDING")


def test_bridge_ingress_advances_existing_thumbnail_gate_and_creates_video_outbox(tmp_path: Path):
    service, episode, artifact = _service(tmp_path)
    config = _config()
    ingress = HermesStudioIngress(config=config, service=service, now=lambda: 1_700_000_000)
    envelope, headers = _signed(config, _update())

    result = ingress.handle(envelope, headers)

    assert result.status == 200
    assert result.body["status"] == "approved"
    assert json.loads((episode / "qa" / "approval-thumbnail.json").read_text())["inbound_receipt"]["text"] == "APROVAR THUMBNAIL EP8"
    import sqlite3
    with sqlite3.connect(tmp_path / "external" / "deliveries.sqlite3") as connection:
        assert connection.execute("SELECT thumbnail_sha256, status FROM video_delivery_outbox").fetchone() == (hashlib.sha256(artifact.read_bytes()).hexdigest(), "PENDING")


@pytest.mark.parametrize("mutate", ["signature", "instance", "sender", "chat", "reply", "stale"])
def test_tampered_or_wrong_bound_event_fails_closed(tmp_path: Path, mutate: str):
    service, episode, _ = _service(tmp_path)
    config = _config()
    ingress = HermesStudioIngress(config=config, service=service, now=lambda: 1_700_000_000)
    raw = _update(sender_id=999 if mutate == "sender" else int(SENDER_ID), chat_id=999 if mutate == "chat" else int(CHAT_ID), reply_to=777 if mutate == "reply" else 42)
    envelope, headers = _signed(config, raw, timestamp=1_600_000_000 if mutate == "stale" else 1_700_000_000)
    if mutate == "signature": headers["X-Studio-Bridge-Signature"] = "v1=" + "0" * 64
    if mutate == "instance": headers["X-Studio-Bridge-Instance"] = "wrong"

    result = ingress.handle(envelope, headers)

    assert result.status in {400, 401, 404, 409}
    assert not (episode / "qa" / "approval-thumbnail.json").exists()


def test_replay_and_changed_same_update_fail_closed_without_second_transition(tmp_path: Path):
    service, episode, _ = _service(tmp_path)
    config = _config()
    ingress = HermesStudioIngress(config=config, service=service, now=lambda: 1_700_000_000)
    first, headers = _signed(config, _update())
    assert ingress.handle(first, headers).body["status"] == "approved"
    assert ingress.handle(first, headers).body["status"] == "acknowledged"
    changed, changed_headers = _signed(config, _update(text="APROVAR VÍDEO EP8"), nonce="nonce-0002")
    assert ingress.handle(changed, changed_headers).status == 400
    assert (episode / "qa" / "approval-thumbnail.json").is_file()


def test_missing_bridge_configuration_is_disabled_and_plugin_registers_nothing():
    from integrations.hermes.hermes_studio_approval_bridge.plugin import register
    class Context:
        def register_platform_handler(self, *_args): raise AssertionError("must not register")
    assert register(Context(), config=None, transport=None) is False
    with pytest.raises(ValueError, match="HMAC"):
        _config(secret=None)


def test_plugin_stops_only_bound_approval_replies_before_core_dispatch(monkeypatch: pytest.MonkeyPatch):
    """A fake PTB dispatcher proves ApplicationHandlerStop controls group 0."""
    class ApplicationHandlerStop(Exception):
        pass

    class Filters:
        REPLY = 1
        TEXT = 1
        COMMAND = 1

        def __and__(self, _other): return self
        def __invert__(self): return self

    class MessageHandler:
        def __init__(self, _filter, callback): self.callback = callback

    telegram = SimpleNamespace(ext=SimpleNamespace(ApplicationHandlerStop=ApplicationHandlerStop, MessageHandler=MessageHandler, filters=Filters()))
    monkeypatch.setitem(sys.modules, "telegram", telegram)
    monkeypatch.setitem(sys.modules, "telegram.ext", telegram.ext)
    plugin = importlib.reload(importlib.import_module("integrations.hermes.hermes_studio_approval_bridge.plugin"))

    class App:
        def __init__(self): self.handlers: list[tuple[object, int]] = []
        def add_handler(self, handler, group=0): self.handlers.append((handler, group))
        def dispatch(self, update):
            async def run():
                for handler, _group in sorted(self.handlers, key=lambda item: item[1]):
                    try:
                        await handler.callback(update, object())
                    except ApplicationHandlerStop:
                        return "stopped"
                return "completed"
            return asyncio.run(run())

    class Context:
        def __init__(self): self.factory = None
        def register_platform_handler(self, platform, factory):
            assert platform == "telegram"
            self.factory = factory

    def update(reply_to: int) -> object:
        return SimpleNamespace(update_id=1, effective_message=SimpleNamespace(
            message_id=99, text="APROVAR THUMBNAIL EP8", from_user=SimpleNamespace(id=int(SENDER_ID), is_bot=False),
            chat=SimpleNamespace(id=int(CHAT_ID), type="private"), reply_to_message=SimpleNamespace(message_id=reply_to),
        ))

    for transport in (lambda _body, _headers: 200, lambda _body, _headers: 409, lambda _body, _headers: (_ for _ in ()).throw(RuntimeError("ambiguous local failure"))):
        app, ctx, core_calls, bridge_calls = App(), Context(), [], []
        assert plugin.register(ctx, config=_config(), transport=lambda body, headers, send=transport, calls=bridge_calls: calls.append(body) or send(body, headers), recognize=lambda raw: json.loads(raw)["message"]["reply_to_message"]["message_id"] == 42)
        ctx.factory(app, object())
        async def core(_update, _context, calls=core_calls):
            calls.append("core")
        app.add_handler(SimpleNamespace(callback=core), group=0)
        assert app.dispatch(update(42)) == "stopped"
        assert len(bridge_calls) == 1
        assert core_calls == []

    app, ctx, core_calls, bridge_calls = App(), Context(), [], []
    assert plugin.register(ctx, config=_config(), transport=lambda body, _headers: bridge_calls.append(body), recognize=lambda raw: json.loads(raw)["message"]["reply_to_message"]["message_id"] == 42)
    ctx.factory(app, object())
    async def unrelated_core(_update, _context):
        core_calls.append("core")
    app.add_handler(SimpleNamespace(callback=unrelated_core), group=0)
    assert app.dispatch(update(777)) == "completed"
    assert bridge_calls == []
    assert core_calls == ["core"]


def test_identical_bridge_and_webhook_secret_fails_before_replay_or_journal_mutation(tmp_path: Path):
    service, _, _ = _service(tmp_path)
    journal = tmp_path / "external" / "journal.sqlite3"
    with sqlite3.connect(journal) as connection:
        before_journal = connection.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall()

    with pytest.raises(ValueError, match="must differ"):
        HermesStudioIngress(config=_config(secret=WEBHOOK_SECRET), service=service)

    assert not (tmp_path / "external" / "hermes-bridge-replays.sqlite3").exists()
    with sqlite3.connect(journal) as connection:
        assert connection.execute("SELECT name FROM sqlite_master ORDER BY name").fetchall() == before_journal
