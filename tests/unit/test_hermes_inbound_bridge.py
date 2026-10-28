"""Offline contracts for the disabled-by-default Hermes-owned approval bridge."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import importlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import threading
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


def _installed_hermes_discovery_runtime() -> Path | None:
    """Find an installed Hermes interpreter without assuming a Windows home."""
    explicit = os.environ.get("HERMES_TEST_PYTHON")
    if explicit is not None:
        candidate = Path(explicit)
        return candidate if candidate.is_file() else None
    try:
        current_has_discovery = importlib.util.find_spec("hermes_cli.plugins_discovery") is not None
    except ModuleNotFoundError as error:
        if error.name != "hermes_cli":
            raise
        current_has_discovery = False
    if current_has_discovery:
        candidate = Path(sys.executable)
        return candidate if candidate.is_file() else None
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        roots = [Path(local_app_data) / "hermes"] if local_app_data else []
        runtime_pattern = "installs/*/environments/*/venv/Scripts/python.exe"
    else:
        hermes_home = os.environ.get("HERMES_HOME")
        roots = [Path(hermes_home)] if hermes_home else [Path.home() / ".hermes"]
        runtime_pattern = "installs/*/environments/*/venv/bin/python"
    for root in roots:
        runtime = next((path for path in sorted(root.glob(runtime_pattern)) if path.is_file()), None)
        if runtime is not None:
            return runtime
    return None


def _require_installed_hermes_discovery_runtime() -> Path:
    runtime = _installed_hermes_discovery_runtime()
    if runtime is None:
        pytest.skip("installed Hermes runtime is unavailable")
    return runtime


def test_installed_hermes_discovery_runtime_skips_cleanly_without_localappdata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """POSIX discovery must not dereference Windows-only LOCALAPPDATA."""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("HERMES_TEST_PYTHON", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "no-hermes-runtime"))
    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "missing-python"))
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: None)

    assert _installed_hermes_discovery_runtime() is None
    with pytest.raises(pytest.skip.Exception, match="installed Hermes runtime is unavailable"):
        _require_installed_hermes_discovery_runtime()


def _signed(config: HermesBridgeConfig, raw: bytes, *, nonce: str = "nonce-0001", timestamp: int = 1_700_000_000) -> tuple[bytes, dict[str, str]]:
    envelope = canonical_envelope(config, raw, nonce=nonce, timestamp=timestamp)
    signature = hmac.new(config.hmac_key.encode(), b"hermes-studio-approval-bridge/v1\0" + envelope, hashlib.sha256).hexdigest()
    return envelope, {"X-Studio-Bridge-Version": "1", "X-Studio-Bridge-Instance": config.instance_id, "X-Studio-Bridge-Signature": f"v1={signature}"}


def test_actual_plugin_registration_uses_single_isolated_handler_and_forwards_normalized_update(tmp_path: Path):
    from integrations.hermes.hermes_studio_approval_bridge.plugin import register
    from src.approval.hermes_bridge_composition import HermesApprovalBridgeComposition

    service, _, _ = _service(tmp_path)
    bridge = HermesApprovalBridgeComposition(config=_config(), service=service)

    class App:
        def __init__(self): self.handlers = []
        def add_handler(self, handler, group=0): self.handlers.append((handler, group))
    class Context:
        def __init__(self): self.application = App(); self.factories = []
        def register_platform_handler(self, platform, factory): self.factories.append((platform, factory))
    captured: list[bytes] = []
    delivered = bridge.deliver
    bridge.deliver = lambda body, headers: captured.append(body) or delivered(body, headers)
    ctx = Context()
    assert register(ctx, bridge=bridge)
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
    from src.approval.hermes_bridge_composition import HermesApprovalBridgeComposition

    service, episode, artifact = _service(tmp_path)
    bridge = HermesApprovalBridgeComposition(config=_config(), service=service)

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
    delivered = bridge.deliver
    bridge.deliver = lambda body, headers: results.append(delivered(body, headers)) or results[-1]
    ctx, app = Context(), App()
    assert register(ctx, bridge=bridge)
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
    assert register(Context(), bridge=None) is False
    with pytest.raises(ValueError, match="HMAC"):
        _config(secret=None)


def test_normal_hermes_registration_is_disabled_before_bootstrap_import(monkeypatch: pytest.MonkeyPatch):
    """An ordinary loader call has no Studio bootstrap side effects by default."""
    from integrations.hermes.hermes_studio_approval_bridge.plugin import register

    sys.modules.pop("integrations.hermes.hermes_studio_approval_bridge.bootstrap", None)
    sys.modules.pop("src.approval.production_telegram_gate", None)

    class Context:
        def get_config(self, _key, default=None):
            return default

        def register_platform_handler(self, *_args):
            raise AssertionError("disabled bridge must not register a Telegram handler")

    assert register(Context()) is False
    assert "integrations.hermes.hermes_studio_approval_bridge.bootstrap" not in sys.modules
    assert "src.approval.production_telegram_gate" not in sys.modules


def test_real_installed_hermes_manifest_discovery_parses_bridge_manifest_offline():
    """Use the installed Hermes discovery parser, not a standalone YAML parse."""
    runtime = _require_installed_hermes_discovery_runtime()
    plugin_parent = Path(__file__).parents[2] / "integrations" / "hermes"
    script = (
        "import json, sys; from pathlib import Path; "
        "from hermes_cli.plugins_discovery import scan_directory; "
        "items = scan_directory(Path(sys.argv[1]), source='user'); "
        "item = next(x for x in items if x.name == 'hermes-studio-approval-bridge'); "
        "print(json.dumps({'kind': item.kind, 'key': item.key, 'env': item.requires_env, 'schema': item.config_schema}))"
    )
    result = subprocess.run(
        [str(runtime), "-c", script, str(plugin_parent)],
        cwd=Path(__file__).parents[2], text=True, capture_output=True, timeout=30, check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads(result.stdout)
    assert manifest["key"] == "hermes-studio-approval-bridge"
    assert manifest["kind"] == "standalone"
    assert [entry["name"] for entry in manifest["env"]] == [
        "STUDIO_BRIDGE_HMAC", "STUDIO_TELEGRAM_BOT_TOKEN", "STUDIO_TELEGRAM_WEBHOOK_SECRET",
    ]
    assert manifest["schema"]["approval_bridge"]["type"] == "object"


@pytest.mark.parametrize(
    "settings",
    [
        {"enabled": True},
        {"enabled": True, "bridge_hmac_env": "BRIDGE", "telegram_webhook_secret_env": "BRIDGE"},
    ],
)
def test_normal_registration_rejects_incomplete_or_equal_secret_configuration_before_registration(
    settings: dict[str, object], monkeypatch: pytest.MonkeyPatch
):
    from integrations.hermes.hermes_studio_approval_bridge.plugin import register

    monkeypatch.setenv("BRIDGE", "same-secret")

    class Context:
        def get_config(self, _key, default=None):
            return settings

        def register_platform_handler(self, *_args):
            raise AssertionError("invalid bootstrap configuration must not register")

    assert register(Context()) is False


@pytest.mark.parametrize("selector", ["PATH", "HOME", "TELEGRAM_BOT_TOKEN", "STUDIO_BRIDGE_HMAC_ALIAS"])
def test_normal_registration_rejects_non_allowlisted_secret_selectors_before_environment_read(
    tmp_path: Path, selector: str
):
    """Only the three documented fixed secret names may be selected."""
    from integrations.hermes.hermes_studio_approval_bridge import bootstrap

    config_path = tmp_path / "studio.yaml"
    config_path.write_text("approval_delivery: {}\n", encoding="utf-8")
    settings = {
        "enabled": True, "studio_config_path": str(config_path),
        "journal_path": str(tmp_path / "journal.sqlite3"), "registry_path": str(tmp_path / "registry.sqlite3"),
        "chat_id": CHAT_ID, "trusted_sender_ids": [SENDER_ID], "instance_id": INSTANCE_ID,
        "bridge_hmac_env": selector, "telegram_bot_token_env": "STUDIO_TELEGRAM_BOT_TOKEN",
        "telegram_webhook_secret_env": "STUDIO_TELEGRAM_WEBHOOK_SECRET",
    }

    class Context:
        def get_config(self, _key, default=None): return settings

    class NoEnvironmentReads(dict):
        def get(self, *_args, **_kwargs):
            raise AssertionError("invalid selector must fail before an environment read")

    with patch.object(bootstrap.os, "environ", NoEnvironmentReads()):
        assert bootstrap.compose_from_context(Context()) is None


@pytest.mark.parametrize("link_kind", ["exact", "ancestor"])
def test_normal_registration_rejects_studio_config_reparse_path_before_environment_or_factory_effects(
    tmp_path: Path, link_kind: str
):
    """The config lexical path is reparse-safe and an existing regular file before secrets or stores."""
    from integrations.hermes.hermes_studio_approval_bridge import bootstrap

    real = tmp_path / "real"
    real.mkdir()
    (real / "studio.yaml").write_text("approval_delivery: {}\n", encoding="utf-8")
    if link_kind == "exact":
        config_path = tmp_path / "studio-link.yaml"
        config_path.symlink_to(real / "studio.yaml")
    else:
        linked_parent = tmp_path / "linked-parent"
        linked_parent.symlink_to(real, target_is_directory=True)
        config_path = linked_parent / "studio.yaml"
    journal = tmp_path / "journal.sqlite3"
    registry = tmp_path / "registry.sqlite3"
    settings = {
        "enabled": True, "studio_config_path": str(config_path),
        "journal_path": str(journal), "registry_path": str(registry), "chat_id": CHAT_ID,
        "trusted_sender_ids": [SENDER_ID], "instance_id": INSTANCE_ID,
        "bridge_hmac_env": "STUDIO_BRIDGE_HMAC", "telegram_bot_token_env": "STUDIO_TELEGRAM_BOT_TOKEN",
        "telegram_webhook_secret_env": "STUDIO_TELEGRAM_WEBHOOK_SECRET",
    }

    class Context:
        def get_config(self, _key, default=None): return settings

    class NoEnvironmentReads(dict):
        def get(self, *_args, **_kwargs):
            raise AssertionError("unsafe config path must fail before an environment read")

    with patch.object(bootstrap.os, "environ", NoEnvironmentReads()):
        assert bootstrap.compose_from_context(Context()) is None
    assert not journal.exists()
    assert not registry.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions require native Windows")
def test_normal_registration_rejects_junction_parent_before_environment_factory_or_store_effects(
    tmp_path: Path,
):
    """A native junction ancestor is rejected before bootstrap has side effects."""
    from integrations.hermes.hermes_studio_approval_bridge import bootstrap
    from integrations.hermes.hermes_studio_approval_bridge.plugin import register

    real = tmp_path / "real"
    real.mkdir()
    config = real / "studio.yaml"
    config.write_text("approval_delivery: {}\n", encoding="utf-8")
    junction = tmp_path / "junction-parent"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(real)],
        text=True,
        capture_output=True,
        encoding="mbcs",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    journal = tmp_path / "journal.sqlite3"
    registry = tmp_path / "registry.sqlite3"
    settings = {
        "enabled": True,
        "studio_config_path": str(junction / "studio.yaml"),
        "journal_path": str(journal),
        "registry_path": str(registry),
        "chat_id": CHAT_ID,
        "trusted_sender_ids": [SENDER_ID],
        "instance_id": INSTANCE_ID,
        "bridge_hmac_env": "STUDIO_BRIDGE_HMAC",
        "telegram_bot_token_env": "STUDIO_TELEGRAM_BOT_TOKEN",
        "telegram_webhook_secret_env": "STUDIO_TELEGRAM_WEBHOOK_SECRET",
    }

    class Context:
        factory_calls = 0

        def get_config(self, _key, default=None):
            return settings

        def register_platform_handler(self, *_args):
            self.factory_calls += 1

    class CountingEnvironment(dict):
        reads = 0

        def get(self, *_args, **_kwargs):
            self.reads += 1
            raise AssertionError("junction path must fail before environment reads")

    context = Context()
    environment = CountingEnvironment()
    try:
        with patch.object(bootstrap.os, "environ", environment):
            assert register(context) is False
        store_writes = int(journal.exists()) + int(registry.exists())
        assert environment.reads == 0
        assert context.factory_calls == 0
        assert store_writes == 0
        assert config.is_file()
    finally:
        junction.rmdir()
    assert config.is_file()


def test_equal_secrets_fail_before_journal_or_registry_effects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from integrations.hermes.hermes_studio_approval_bridge.plugin import register

    monkeypatch.setenv("BRIDGE", "same-secret")
    monkeypatch.setenv("TOKEN", "token")
    journal = tmp_path / "trusted" / "journal.sqlite3"
    registry = tmp_path / "trusted" / "registry.sqlite3"
    settings = {
        "enabled": True, "studio_config_path": str(tmp_path / "studio.yaml"),
        "journal_path": str(journal), "registry_path": str(registry), "chat_id": CHAT_ID,
        "trusted_sender_ids": [SENDER_ID], "instance_id": INSTANCE_ID,
        "bridge_hmac_env": "BRIDGE", "telegram_bot_token_env": "TOKEN",
        "telegram_webhook_secret_env": "BRIDGE",
    }

    class Context:
        def get_config(self, _key, default=None): return settings
        def register_platform_handler(self, *_args): raise AssertionError("must not register")

    assert register(Context()) is False
    assert not journal.exists()
    assert not registry.exists()


def test_normal_registration_bootstraps_real_service_and_reaches_existing_video_outbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Normal ``register(ctx)`` composes the existing service without an LLM/provider call."""
    from integrations.hermes.hermes_studio_approval_bridge.plugin import register

    class ApplicationHandlerStop(Exception):
        pass

    class Filters:
        REPLY = TEXT = COMMAND = 1

        def __and__(self, _other): return self
        def __invert__(self): return self

    class MessageHandler:
        def __init__(self, _filter, callback): self.callback = callback

    telegram = SimpleNamespace(ext=SimpleNamespace(
        ApplicationHandlerStop=ApplicationHandlerStop, MessageHandler=MessageHandler, filters=Filters(),
    ))
    monkeypatch.setitem(sys.modules, "telegram", telegram)
    monkeypatch.setitem(sys.modules, "telegram.ext", telegram.ext)
    monkeypatch.setenv("STUDIO_BRIDGE_HMAC", BRIDGE_SECRET)
    monkeypatch.setenv("STUDIO_TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("STUDIO_TELEGRAM_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path / "episodes"))
    config_path = tmp_path / "studio.yaml"
    config_path.write_text(
        "approval_delivery:\n  chat_id: '10001'\n  trusted_sender_ids: ['20002']\n", encoding="utf-8"
    )
    episode = tmp_path / "episodes" / "EP8"
    (episode / "qa").mkdir(parents=True)
    from src.state.machine import EpisodeState, EpisodeStateStore
    state = EpisodeStateStore.load_or_create(episode / "state.json", "EP8")
    state.current_state = EpisodeState.WAITING_THUMBNAIL_APPROVAL
    state.save(episode / "state.json")
    artifact = tmp_path / "thumbnail.png"
    artifact.write_bytes(b"frozen-thumbnail")
    receipt = episode / "qa" / "delivery" / "thumbnail.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps({
        "episode_id": "EP8", "artifact_kind": "thumbnail", "approval": asdict(
            ApprovalReceipt.approve("thumbnail", artifact, "delivery-audit")
        ), "chat_id": CHAT_ID, "message_id": 42,
    }), encoding="utf-8")
    settings = {
        "enabled": True,
        "studio_config_path": str(config_path),
        "journal_path": str(tmp_path / "trusted" / "journal.sqlite3"),
        "registry_path": str(tmp_path / "trusted" / "registry.sqlite3"),
        "chat_id": CHAT_ID,
        "trusted_sender_ids": [SENDER_ID],
        "instance_id": INSTANCE_ID,
        "bridge_hmac_env": "STUDIO_BRIDGE_HMAC",
        "telegram_bot_token_env": "STUDIO_TELEGRAM_BOT_TOKEN",
        "telegram_webhook_secret_env": "STUDIO_TELEGRAM_WEBHOOK_SECRET",
    }

    class App:
        def __init__(self): self.handler = None
        def add_handler(self, handler, group=0):
            assert group == -1
            self.handler = handler

    class Context:
        def __init__(self): self.factory = None
        def get_config(self, key, default=None):
            return settings if key == "approval_bridge" else default
        def register_platform_handler(self, platform, factory):
            assert platform == "telegram"
            self.factory = factory

    ctx, app = Context(), App()
    assert register(ctx) is True
    assert ctx.factory is not None
    ctx.factory(app, object())
    update = SimpleNamespace(update_id=1, effective_message=SimpleNamespace(
        message_id=99, text="APROVAR THUMBNAIL EP8", from_user=SimpleNamespace(id=int(SENDER_ID), is_bot=False),
        chat=SimpleNamespace(id=int(CHAT_ID), type="private"), reply_to_message=SimpleNamespace(message_id=42),
    ))
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(app.handler.callback(update, object()))
    with sqlite3.connect(tmp_path / "trusted" / "registry.sqlite3") as connection:
        assert connection.execute("SELECT status FROM video_delivery_outbox").fetchone() == ("PENDING",)


def test_plugin_stops_only_bound_approval_replies_before_core_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
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

    from src.approval.hermes_bridge_composition import HermesApprovalBridgeComposition

    for index, transport in enumerate((lambda _body, _headers: 200, lambda _body, _headers: 409, lambda _body, _headers: (_ for _ in ()).throw(RuntimeError("ambiguous local failure")))):
        app, ctx, core_calls, bridge_calls = App(), Context(), [], []
        service, _, _ = _service(tmp_path / f"bound-{index}")
        bridge = HermesApprovalBridgeComposition(config=_config(), service=service)
        bridge.deliver = lambda body, headers, send=transport, calls=bridge_calls: calls.append(body) or send(body, headers)
        assert plugin.register(ctx, bridge=bridge)
        ctx.factory(app, object())
        async def core(_update, _context, calls=core_calls):
            calls.append("core")
        app.add_handler(SimpleNamespace(callback=core), group=0)
        assert app.dispatch(update(42)) == "stopped"
        assert len(bridge_calls) == 1
        assert core_calls == []

    app, ctx, core_calls, bridge_calls = App(), Context(), [], []
    service, _, _ = _service(tmp_path / "unrelated")
    bridge = HermesApprovalBridgeComposition(config=_config(), service=service)
    bridge.deliver = lambda body, _headers: bridge_calls.append(body)
    assert plugin.register(ctx, bridge=bridge)
    ctx.factory(app, object())
    async def unrelated_core(_update, _context):
        core_calls.append("core")
    app.add_handler(SimpleNamespace(callback=unrelated_core), group=0)
    assert app.dispatch(update(777)) == "completed"
    assert bridge_calls == []
    assert core_calls == ["core"]

    app, ctx, core_calls, bridge_calls = App(), Context(), [], []
    service, _, _ = _service(tmp_path / "uncertain-recognition")
    bridge = HermesApprovalBridgeComposition(config=_config(), service=service)
    bridge.recognize = lambda _raw: (_ for _ in ()).throw(RuntimeError("uncertain receipt lookup"))
    bridge.deliver = lambda body, _headers: bridge_calls.append(body)
    assert plugin.register(ctx, bridge=bridge)
    ctx.factory(app, object())
    async def uncertain_core(_update, _context):
        core_calls.append("core")
    app.add_handler(SimpleNamespace(callback=uncertain_core), group=0)
    assert app.dispatch(update(42)) == "stopped"
    assert bridge_calls == []
    assert core_calls == []


def test_plugin_offloads_slow_recognition_so_the_event_loop_heartbeats_and_delivery_is_not_lost(tmp_path: Path):
    """SQLite-backed recognition must not stall unrelated PTB loop work."""
    from integrations.hermes.hermes_studio_approval_bridge.plugin import register
    from src.approval.hermes_bridge_composition import HermesApprovalBridgeComposition

    service, _, _ = _service(tmp_path)
    bridge = HermesApprovalBridgeComposition(config=_config(), service=service)
    original_recognize = bridge.recognize
    recognition_started = threading.Event()
    release_recognition = threading.Event()
    deliveries: list[object] = []

    def slow_recognize(raw: bytes) -> bool:
        recognition_started.set()
        assert release_recognition.wait(timeout=1)
        return original_recognize(raw)

    original_deliver = bridge.deliver
    bridge.recognize = slow_recognize
    bridge.deliver = lambda body, headers: deliveries.append(original_deliver(body, headers)) or deliveries[-1]

    class App:
        def add_handler(self, handler, group=0):
            assert group == -1
            self.handler = handler

    class Context:
        def register_platform_handler(self, platform, factory):
            assert platform == "telegram"
            self.factory = factory

    ctx, app = Context(), App()
    assert register(ctx, bridge=bridge)
    ctx.factory(app, object())
    update = SimpleNamespace(update_id=1, effective_message=SimpleNamespace(
        message_id=99, text="APROVAR THUMBNAIL EP8", from_user=SimpleNamespace(id=int(SENDER_ID), is_bot=False),
        chat=SimpleNamespace(id=int(CHAT_ID), type="private"), reply_to_message=SimpleNamespace(message_id=42),
    ))

    async def exercise() -> None:
        heartbeat = asyncio.Event()

        async def other_loop_work() -> None:
            await asyncio.sleep(0.01)
            heartbeat.set()

        callback = asyncio.create_task(app.handler.callback(update, object()))
        asyncio.create_task(other_loop_work())
        await asyncio.to_thread(recognition_started.wait, 1)
        await heartbeat.wait()
        assert not callback.done(), "slow recognition blocked the event loop instead of running in a worker"
        release_recognition.set()
        await callback

    asyncio.run(exercise())
    assert [result.body["status"] for result in deliveries] == ["approved"]


def test_plugin_offloads_blocked_ingress_so_the_event_loop_heartbeats_once_and_approves_once(tmp_path: Path):
    """The full synchronous callback path, including ingress, leaves PTB responsive."""
    from integrations.hermes.hermes_studio_approval_bridge.plugin import register
    from src.approval.hermes_bridge_composition import HermesApprovalBridgeComposition

    service, _, _ = _service(tmp_path)
    bridge = HermesApprovalBridgeComposition(config=_config(), service=service)
    original_deliver = bridge.deliver
    ingress_started = threading.Event()
    release_ingress = threading.Event()
    deliveries: list[object] = []

    def blocked_deliver(body: bytes, headers: dict[str, str]) -> object:
        ingress_started.set()
        assert release_ingress.wait(timeout=1)
        result = original_deliver(body, headers)
        deliveries.append(result)
        return result

    bridge.deliver = blocked_deliver

    class App:
        def add_handler(self, handler, group=0):
            assert group == -1
            self.handler = handler

    class Context:
        def register_platform_handler(self, platform, factory):
            assert platform == "telegram"
            self.factory = factory

    ctx, app = Context(), App()
    assert register(ctx, bridge=bridge)
    ctx.factory(app, object())
    update = SimpleNamespace(update_id=1, effective_message=SimpleNamespace(
        message_id=99, text="APROVAR THUMBNAIL EP8", from_user=SimpleNamespace(id=int(SENDER_ID), is_bot=False),
        chat=SimpleNamespace(id=int(CHAT_ID), type="private"), reply_to_message=SimpleNamespace(message_id=42),
    ))

    async def exercise() -> None:
        heartbeat = asyncio.Event()
        escape_hatch = threading.Timer(0.2, release_ingress.set)
        escape_hatch.start()
        callback = asyncio.create_task(app.handler.callback(update, object()))
        await asyncio.to_thread(ingress_started.wait, 1)

        async def tick_once() -> None:
            await asyncio.sleep(0.01)
            heartbeat.set()

        tick = asyncio.create_task(tick_once())
        await heartbeat.wait()
        assert not callback.done(), "blocked ingress stalled the event loop"
        release_ingress.set()
        escape_hatch.cancel()
        await callback
        await tick
        assert heartbeat.is_set()

    asyncio.run(exercise())
    assert [result.body["status"] for result in deliveries] == ["approved"]


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
