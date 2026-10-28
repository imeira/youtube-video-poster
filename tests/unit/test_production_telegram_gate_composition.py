"""Offline composition tests for the explicit production Telegram gate launcher."""
from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.approval.receipts import ApprovalReceipt


class _FakeProvider:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.calls: list[tuple[str, str]] = []

    async def send_photo(self, chat_id: str, path: str, caption: str = "") -> int:
        self.calls.append((chat_id, path))
        return 701

    async def send_video(self, chat_id: str, path: str, caption: str = "") -> int:
        self.calls.append((chat_id, path))
        return 702


class _Director:
    def __init__(self, root: Path, chat_id: str):
        self.config = SimpleNamespace(episodes_dir=root, approval_delivery_chat_id=chat_id)
        self.inbound_approval_source = None
        self.calls: list[tuple[object, ...]] = []

    async def deliver_for_approval(self, episode_id: str, *, messenger, chat_id: str, thumbnail_path: Path, after_persist=None):
        self.calls.append((episode_id, messenger, chat_id, thumbnail_path))
        artifact = Path(thumbnail_path)
        approval = ApprovalReceipt.approve("thumbnail", artifact, "delivery")
        receipt_path = self.config.episodes_dir / episode_id / "qa" / "delivery" / "thumbnail.json"
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(json.dumps({
            "episode_id": episode_id, "artifact_kind": "thumbnail", "approval": asdict(approval),
            "chat_id": chat_id, "message_id": 701,
        }), encoding="utf-8")
        delivered = json.loads(receipt_path.read_text(encoding="utf-8"))
        if after_persist is not None:
            result = after_persist(receipt_path)
            if hasattr(result, "__await__"):
                await result
        return delivered


def _config(tmp_path: Path):
    from src.approval.production_telegram_gate import ProductionTelegramGateConfig

    return ProductionTelegramGateConfig(
        bot_token="external-token-only",
        webhook_secret="external-webhook-secret-only",
        chat_id="10001",
        trusted_sender_ids=frozenset({"20002"}),
        instance_id="bot-instance-a",
        journal_path=tmp_path / "trusted" / "journal.sqlite3",
        registry_path=tmp_path / "trusted" / "registry.sqlite3",
        host="127.0.0.1",
        port=9443,
    )


def test_factory_constructs_concrete_offline_boundaries_and_explicit_host_without_network(tmp_path: Path, monkeypatch):
    from src.approval.production_telegram_gate import create_production_telegram_gate

    director = _Director(tmp_path / "episodes", "10001")
    app_calls = []
    monkeypatch.setattr(
        "src.approval.production_telegram_gate.create_telegram_inbound_app",
        lambda service: app_calls.append(service) or object(),
    )
    gate = create_production_telegram_gate(
        director=director,
        config=_config(tmp_path),
        provider_factory=_FakeProvider,
    )

    assert isinstance(gate.provider, _FakeProvider)
    assert gate.provider.token == "external-token-only"
    assert gate.delivery_controller.messenger is gate.provider
    assert director.inbound_approval_source is gate.service._adapter
    assert gate.launcher.host == "127.0.0.1"
    assert gate.launcher.port == 9443
    assert gate.launcher.app is not None
    assert len(app_calls) == 1


def test_preflight_builds_no_http_provider_and_never_leaks_secrets(tmp_path: Path, capsys):
    from src.approval.production_telegram_gate import preflight_production_telegram_gate

    director = _Director(tmp_path / "episodes", "10001")
    result = preflight_production_telegram_gate(
        director=director,
        config=_config(tmp_path),
        provider_factory=_FakeProvider,
    )
    output = capsys.readouterr().out

    assert result == {"status": "ready", "network": "disabled", "publication": "disabled"}
    assert output == ""
    assert "external-token-only" not in repr(result)
    assert "external-webhook-secret-only" not in repr(result)


def test_cli_preflight_reads_named_external_secret_variables_and_never_activates(tmp_path: Path, monkeypatch, capsys):
    from src.approval import telegram_cli

    config = SimpleNamespace(
        episodes_dir=tmp_path / "episodes",
        approval_delivery_chat_id="10001",
        approval_delivery_trusted_sender_ids=frozenset({"20002"}),
    )
    director = _Director(tmp_path / "episodes", "10001")
    monkeypatch.setattr(telegram_cli, "load_config", lambda path: config)
    monkeypatch.setattr(telegram_cli, "DirectorAgent", lambda loaded: director)
    monkeypatch.setattr(telegram_cli, "TelegramNotificationProvider", _FakeProvider)
    monkeypatch.setenv("TEST_TELEGRAM_TOKEN", "cli-token-secret")
    monkeypatch.setenv("TEST_TELEGRAM_WEBHOOK", "cli-webhook-secret")

    assert telegram_cli.main([
        "--config", str(tmp_path / "deployment.yaml"), "--journal", str(tmp_path / "journal.sqlite3"),
        "--registry", str(tmp_path / "registry.sqlite3"), "--chat-id", "10001", "--sender-id", "20002",
        "--instance-id", "instance-a", "--token-env", "TEST_TELEGRAM_TOKEN",
        "--secret-env", "TEST_TELEGRAM_WEBHOOK", "--host", "127.0.0.1", "--port", "9443",
    ]) == 0
    output = capsys.readouterr().out
    assert '"network": "disabled"' in output
    assert "cli-token-secret" not in output
    assert "cli-webhook-secret" not in output


def test_concrete_telegram_provider_returns_loopback_http_message_id_without_network(tmp_path: Path):
    from src.providers.notification.telegram_provider import (
        TelegramNotificationProvider,
    )

    artifact = tmp_path / "approved.png"
    artifact.write_bytes(b"thumbnail")

    async def exercise() -> int:
        async def handler(reader, writer) -> None:
            head = await reader.readuntil(b"\r\n\r\n")
            content_length = next(
                int(line.split(b":", 1)[1])
                for line in head.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            await reader.readexactly(content_length)
            body = b'{"ok": true, "result": {"message_id": 703}}'
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                + str(len(body)).encode() + b"\r\n\r\n" + body
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        provider = TelegramNotificationProvider("not-logged", "10001")
        provider.api_base = f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}/botlocal"
        try:
            return await provider.send_photo("10001", str(artifact))
        finally:
            server.close()
            await server.wait_closed()

    message_id = asyncio.run(exercise())

    assert message_id == 703


def test_delivery_registers_exact_director_receipt_after_existing_controller_path(tmp_path: Path):
    from src.approval.production_telegram_gate import create_production_telegram_gate

    director = _Director(tmp_path / "episodes", "10001")
    gate = create_production_telegram_gate(director=director, config=_config(tmp_path), provider_factory=_FakeProvider)
    artifact = tmp_path / "thumbnail.png"
    artifact.write_bytes(b"approved-thumbnail")

    receipt = asyncio.run(gate.deliver_for_approval("EP8", thumbnail_path=artifact))

    assert receipt["message_id"] == 701
    assert len(director.calls) == 1
    assert gate.service._lookup(chat_id="10001", message_id=701)["episode_id"] == "EP8"


def test_delivery_registers_before_returning_from_director_persist_hook(tmp_path: Path):
    from src.approval.production_telegram_gate import create_production_telegram_gate

    director = _Director(tmp_path / "episodes", "10001")
    gate = create_production_telegram_gate(director=director, config=_config(tmp_path), provider_factory=_FakeProvider)
    artifact = tmp_path / "thumbnail.png"
    artifact.write_bytes(b"approved-thumbnail")

    asyncio.run(gate.deliver_for_approval("EP8", thumbnail_path=artifact))

    assert gate.service._lookup(chat_id="10001", message_id=701)["episode_id"] == "EP8"


@pytest.mark.asyncio
async def test_launcher_serve_uses_mocked_aiohttp_lifecycle_and_cleans_up(tmp_path: Path, monkeypatch):
    from src.approval.production_telegram_gate import create_production_telegram_gate

    class Runner:
        def __init__(self, app):
            self.app = app
            self.setup_calls = 0
            self.cleanup_calls = 0

        async def setup(self):
            self.setup_calls += 1

        async def cleanup(self):
            self.cleanup_calls += 1

    class Site:
        def __init__(self, runner, host, port):
            self.runner, self.host, self.port = runner, host, port
            self.started = 0
            self.stopped = 0

        async def start(self):
            self.started += 1

        async def stop(self):
            self.stopped += 1

    created = {}
    monkeypatch.setattr("aiohttp.web.AppRunner", lambda app: created.setdefault("runner", Runner(app)))
    monkeypatch.setattr("aiohttp.web.TCPSite", lambda runner, host, port: created.setdefault("site", Site(runner, host, port)))
    gate = create_production_telegram_gate(director=_Director(tmp_path / "episodes", "10001"), config=_config(tmp_path), provider_factory=_FakeProvider)
    assert gate.outbox_worker._task is None

    async with gate.launcher.serve() as running:
        assert running is gate.launcher
        assert created["site"].host == "127.0.0.1"
        assert created["site"].port == 9443
        assert created["site"].started == 1
        assert gate.outbox_worker._task is not None

    assert created["site"].stopped == 1
    assert created["runner"].cleanup_calls == 1
    assert gate.outbox_worker._task is None


@pytest.mark.asyncio
async def test_launcher_surfaces_retained_worker_and_cleans_aiohttp_after_bounded_stop(
    tmp_path: Path, monkeypatch
):
    from src.approval.delivery_session import DeliverySessionRunner
    from src.approval.production_telegram_gate import (
        RetainedOutboxWorkerError,
        create_production_telegram_gate,
    )

    class Runner:
        def __init__(self, app):
            self.cleanup_calls = 0

        async def setup(self):
            return None

        async def cleanup(self):
            self.cleanup_calls += 1

    class Site:
        def __init__(self, runner, host, port):
            self.stopped = 0

        async def start(self):
            return None

        async def stop(self):
            self.stopped += 1

    release = asyncio.Event()
    entered = asyncio.Event()

    class CancellationIgnoringComposition:
        def revalidate_thumbnail_approval(self, **_kwargs) -> None:
            return None

        async def deliver_for_approval(self, _episode_id: str, *, thumbnail_path: Path):
            entered.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    continue
            raise RuntimeError("controlled finish")

    created = {}
    monkeypatch.setattr("aiohttp.web.AppRunner", lambda app: created.setdefault("runner", Runner(app)))
    monkeypatch.setattr("aiohttp.web.TCPSite", lambda runner, host, port: created.setdefault("site", Site(runner, host, port)))
    base = _config(tmp_path)
    gate = create_production_telegram_gate(
        director=_Director(tmp_path / "episodes", "10001"),
        config=type(base)(**{**base.__dict__, "outbox_shutdown_timeout_seconds": 0.02}),
        provider_factory=_FakeProvider,
    )
    thumbnail = tmp_path / "thumbnail.png"
    thumbnail.write_bytes(b"approved-thumbnail")
    runner = gate.outbox_worker._runner
    assert isinstance(runner, DeliverySessionRunner)
    runner.composition = CancellationIgnoringComposition()
    runner.enqueue_video(
        episode_id="EP8", thumbnail_path=thumbnail,
        thumbnail_sha256=hashlib.sha256(thumbnail.read_bytes()).hexdigest(),
    )

    with pytest.raises(RetainedOutboxWorkerError) as retained:
        async with gate.launcher.serve():
            await entered.wait()

    assert retained.value.worker is gate.outbox_worker
    assert retained.value.outcome == {"status": "stopping", "ownership": "retained"}
    assert gate.outbox_worker._task is not None
    assert created["site"].stopped == 1
    assert created["runner"].cleanup_calls == 1
    with pytest.raises(RuntimeError, match="already running"):
        await gate.outbox_worker.start()
    release.set()
    await asyncio.wait_for(gate.outbox_worker._task, timeout=1)
    assert gate.outbox_worker._task is None


@pytest.mark.asyncio
async def test_launcher_preserves_host_error_when_worker_stop_is_retained(monkeypatch):
    from src.approval.production_telegram_gate import TelegramInboundLauncher

    class Runner:
        async def setup(self):
            return None

        async def cleanup(self):
            return None

    class Site:
        async def start(self):
            return None

        async def stop(self):
            return None

    class Worker:
        async def start(self):
            return None

        async def stop(self):
            return {"status": "stopping", "ownership": "retained"}

    monkeypatch.setattr("aiohttp.web.AppRunner", lambda app: Runner())
    monkeypatch.setattr("aiohttp.web.TCPSite", lambda runner, host, port: Site())
    launcher = TelegramInboundLauncher(app=object(), host="127.0.0.1", port=9443, outbox_worker=Worker())

    with pytest.raises(RuntimeError, match="host failure") as raised:
        async with launcher.serve():
            raise RuntimeError("host failure")

    assert any("outbox worker is still stopping" in note for note in raised.value.__notes__)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["setup", "cancelled"])
async def test_launcher_cleans_runner_when_setup_fails_or_task_is_cancelled(tmp_path: Path, monkeypatch, failure: str):
    from src.approval.production_telegram_gate import create_production_telegram_gate

    class Runner:
        def __init__(self, app):
            self.cleanup_calls = 0

        async def setup(self):
            if failure == "setup":
                raise RuntimeError("setup failed")
            if failure == "cancelled":
                raise asyncio.CancelledError()

        async def cleanup(self):
            self.cleanup_calls += 1

    class Site:
        def __init__(self, runner, host, port):
            self.stopped = 0

        async def start(self):
            pytest.fail("site must not start after setup fails")

        async def stop(self):
            self.stopped += 1

    created = {}
    monkeypatch.setattr("aiohttp.web.AppRunner", lambda app: created.setdefault("runner", Runner(app)))
    monkeypatch.setattr("aiohttp.web.TCPSite", lambda runner, host, port: created.setdefault("site", Site(runner, host, port)))
    gate = create_production_telegram_gate(director=_Director(tmp_path / "episodes", "10001"), config=_config(tmp_path), provider_factory=_FakeProvider)

    expected = asyncio.CancelledError if failure == "cancelled" else RuntimeError
    with pytest.raises(expected):
        async with gate.launcher.serve():
            pytest.fail("the mocked lifecycle must not yield")

    assert created["runner"].cleanup_calls == 1
    assert created.get("site") is None


@pytest.mark.parametrize("field", ["journal_path", "registry_path"])
def test_factory_rejects_reparse_trusted_storage_before_provider_or_app(tmp_path: Path, field: str):
    from src.approval.production_telegram_gate import (
        ProductionTelegramGateConfig,
        create_production_telegram_gate,
    )

    target = tmp_path / "target"
    target.mkdir()
    linked = tmp_path / "linked"
    try:
        linked.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"symlinks unsupported: {error}")
    base = _config(tmp_path)
    config = ProductionTelegramGateConfig(
        **{**base.__dict__, field: linked / "state.sqlite3"}
    )
    called = False

    def provider(*args):
        nonlocal called
        called = True
        return _FakeProvider(*args)

    with pytest.raises(ValueError, match="reparse"):
        create_production_telegram_gate(director=_Director(tmp_path / "episodes", "10001"), config=config, provider_factory=provider)
    assert called is False


@pytest.mark.parametrize("mutate", [
    lambda config: {**config.__dict__, "chat_id": "10002"},
    lambda config: {**config.__dict__, "trusted_sender_ids": frozenset({"bad"})},
    lambda config: {**config.__dict__, "webhook_secret": ""},
    lambda config: {**config.__dict__, "bot_token": ""},
])
def test_factory_fails_closed_on_untrusted_external_bindings(tmp_path: Path, mutate):
    from src.approval.production_telegram_gate import (
        ProductionTelegramGateConfig,
        create_production_telegram_gate,
    )

    base = _config(tmp_path)
    config = ProductionTelegramGateConfig(**mutate(base))
    provider_created = False

    def provider(*args):
        nonlocal provider_created
        provider_created = True
        return _FakeProvider(*args)

    with pytest.raises(ValueError):
        create_production_telegram_gate(
            director=_Director(tmp_path / "episodes", "10001"), config=config, provider_factory=provider
        )
    assert provider_created is False
