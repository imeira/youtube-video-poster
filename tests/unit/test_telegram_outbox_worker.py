"""Event-driven hosted delivery of the second Telegram gate."""
from __future__ import annotations

import asyncio
import hashlib
import multiprocessing
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


def _queue_row(registry: Path, episode_id: str) -> tuple[str, str | None]:
    with sqlite3.connect(registry) as connection:
        return connection.execute(
            "SELECT status, reason FROM video_delivery_outbox WHERE episode_id = ?", (episode_id,)
        ).fetchone()


def _probe_outbox_lock(lock_path: str, acquired) -> None:
    from src.delivery.controller import DeliveryController

    async def acquire() -> None:
        async with DeliveryController._gate_lock(Path(lock_path)):
            acquired.set()

    asyncio.run(acquire())


def test_worker_startup_drain_and_coalesced_wakeup_deliver_each_pending_row_once(tmp_path: Path):
    from src.approval.delivery_session import (
        DeliveryOutboxWorker,
        DeliverySessionRunner,
    )

    class Composition:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def revalidate_thumbnail_approval(self, **_kwargs) -> None:
            return None

        async def deliver_for_approval(self, episode_id: str, *, thumbnail_path: Path):
            self.calls.append(episode_id)
            return {"artifact_kind": "video", "message_id": 700 + len(self.calls)}

    registry = tmp_path / "delivery.sqlite3"
    thumbnail = tmp_path / "thumbnail.png"
    thumbnail.write_bytes(b"approved-thumbnail")
    sha256 = hashlib.sha256(thumbnail.read_bytes()).hexdigest()
    composition = Composition()
    runner = DeliverySessionRunner(registry_path=registry, composition=composition)
    runner.enqueue_video(episode_id="EP8", thumbnail_path=thumbnail, thumbnail_sha256=sha256)
    worker = DeliveryOutboxWorker(runner, reconcile_period_seconds=60)

    async def exercise() -> None:
        await worker.start()
        await worker.wait_idle()
        runner.enqueue_video(episode_id="EP9", thumbnail_path=thumbnail, thumbnail_sha256=sha256)
        worker.wake()
        worker.wake()
        worker.wake()
        await worker.wait_idle()
        await worker.stop()

    asyncio.run(exercise())

    assert composition.calls == ["EP8", "EP9"]


def test_blocked_head_is_durable_and_does_not_starve_later_eligible_episode(tmp_path: Path):
    from src.approval.delivery_session import (
        DeliveryOutboxWorker,
        DeliverySessionRunner,
    )

    class Composition:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def revalidate_thumbnail_approval(self, **_kwargs) -> None:
            return None

        async def deliver_for_approval(self, episode_id: str, *, thumbnail_path: Path):
            self.calls.append(episode_id)
            return {"artifact_kind": "video", "message_id": 700 + len(self.calls)}

    registry = tmp_path / "delivery.sqlite3"
    valid = tmp_path / "valid-thumbnail.png"
    valid.write_bytes(b"approved-thumbnail")
    sha256 = hashlib.sha256(valid.read_bytes()).hexdigest()
    composition = Composition()
    runner = DeliverySessionRunner(registry_path=registry, composition=composition)
    runner.enqueue_video(episode_id="EP8", thumbnail_path=tmp_path / "missing-thumbnail.png", thumbnail_sha256=sha256)
    runner.enqueue_video(episode_id="EP9", thumbnail_path=valid, thumbnail_sha256=sha256)
    worker = DeliveryOutboxWorker(runner, reconcile_period_seconds=60)

    async def exercise() -> None:
        await worker.start()
        await worker.wait_idle()
        worker.wake()
        await worker.wait_idle()
        await worker.stop()

    asyncio.run(exercise())

    assert composition.calls == ["EP9"]
    assert _queue_row(registry, "EP8") == ("BLOCKED", "thumbnail unavailable")
    assert _queue_row(registry, "EP9") == ("DELIVERED", None)


def test_ambiguous_first_delivery_is_unknown_never_reposted_and_does_not_starve_second(tmp_path: Path):
    from src.approval.delivery_session import (
        DeliveryOutboxWorker,
        DeliverySessionRunner,
    )

    class Composition:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def revalidate_thumbnail_approval(self, **_kwargs) -> None:
            return None

        async def deliver_for_approval(self, episode_id: str, *, thumbnail_path: Path):
            self.calls.append(episode_id)
            if episode_id == "EP8":
                raise TimeoutError("provider response lost after POST")
            return {"artifact_kind": "video", "message_id": 700 + len(self.calls)}

    registry = tmp_path / "delivery.sqlite3"
    thumbnail = tmp_path / "thumbnail.png"
    thumbnail.write_bytes(b"approved-thumbnail")
    sha256 = hashlib.sha256(thumbnail.read_bytes()).hexdigest()
    composition = Composition()
    runner = DeliverySessionRunner(registry_path=registry, composition=composition)
    runner.enqueue_video(episode_id="EP8", thumbnail_path=thumbnail, thumbnail_sha256=sha256)
    runner.enqueue_video(episode_id="EP9", thumbnail_path=thumbnail, thumbnail_sha256=sha256)
    worker = DeliveryOutboxWorker(runner, reconcile_period_seconds=60)

    async def exercise() -> None:
        await worker.start()
        await worker.wait_idle()
        worker.wake()
        await worker.wait_idle()
        await worker.stop()

    asyncio.run(exercise())

    assert composition.calls == ["EP8", "EP9"]
    assert _queue_row(registry, "EP8") == ("UNKNOWN", "delivery outcome ambiguous; manual reconciliation required")
    assert _queue_row(registry, "EP9") == ("DELIVERED", None)


def test_stop_deadline_cancels_never_returning_delivery_after_marking_unknown(tmp_path: Path):
    from src.approval.delivery_session import (
        DeliveryOutboxWorker,
        DeliverySessionRunner,
    )

    class Composition:
        def revalidate_thumbnail_approval(self, **_kwargs) -> None:
            return None

        async def deliver_for_approval(self, _episode_id: str, *, thumbnail_path: Path):
            await asyncio.Event().wait()

    registry = tmp_path / "delivery.sqlite3"
    thumbnail = tmp_path / "thumbnail.png"
    thumbnail.write_bytes(b"approved-thumbnail")
    runner = DeliverySessionRunner(registry_path=registry, composition=Composition())
    runner.enqueue_video(
        episode_id="EP8", thumbnail_path=thumbnail,
        thumbnail_sha256=hashlib.sha256(thumbnail.read_bytes()).hexdigest(),
    )
    worker = DeliveryOutboxWorker(runner, reconcile_period_seconds=60, shutdown_timeout_seconds=0.05)

    async def exercise() -> None:
        await worker.start()
        await asyncio.sleep(0)
        await worker.stop()

    asyncio.run(exercise())

    assert _queue_row(registry, "EP8") == ("UNKNOWN", "delivery cancelled; manual reconciliation required")


def test_stop_returns_stopping_and_retains_ignoring_cancelled_task_and_lock(tmp_path: Path):
    from src.approval.delivery_session import (
        DeliveryOutboxWorker,
        DeliverySessionRunner,
    )

    release = asyncio.Event()
    entered = asyncio.Event()

    class Composition:
        def revalidate_thumbnail_approval(self, **_kwargs) -> None:
            return None

        async def deliver_for_approval(self, _episode_id: str, *, thumbnail_path: Path):
            entered.set()
            while not release.is_set():
                try:
                    await asyncio.sleep(10)
                except asyncio.CancelledError:
                    continue
            raise RuntimeError("controlled release")

    registry = tmp_path / "delivery.sqlite3"
    thumbnail = tmp_path / "thumbnail.png"
    thumbnail.write_bytes(b"approved-thumbnail")
    runner = DeliverySessionRunner(registry_path=registry, composition=Composition())
    runner.enqueue_video(
        episode_id="EP8", thumbnail_path=thumbnail,
        thumbnail_sha256=hashlib.sha256(thumbnail.read_bytes()).hexdigest(),
    )
    worker = DeliveryOutboxWorker(runner, reconcile_period_seconds=60, shutdown_timeout_seconds=0.02)

    async def exercise() -> None:
        await worker.start()
        await entered.wait()
        started = time.monotonic()
        assert await worker.stop() == {"status": "stopping", "ownership": "retained"}
        assert time.monotonic() - started < 0.2
        with pytest.raises(RuntimeError, match="already running"):
            await worker.start()
        context = multiprocessing.get_context("spawn")
        acquired = context.Event()
        probe = context.Process(target=_probe_outbox_lock, args=(str(registry.with_suffix(".outbox.lock")), acquired))
        probe.start()
        try:
            await asyncio.sleep(0.05)
            assert not acquired.is_set()
            release.set()
            await asyncio.sleep(0.05)
        finally:
            probe.join(2)
            if probe.is_alive():
                probe.kill()

    asyncio.run(exercise())
    assert _queue_row(registry, "EP8") == ("UNKNOWN", "delivery cancelled; manual reconciliation required")


def test_low_frequency_reconciliation_recovers_a_lost_wakeup(tmp_path: Path):
    from src.approval.delivery_session import (
        DeliveryOutboxWorker,
        DeliverySessionRunner,
    )

    class Composition:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def revalidate_thumbnail_approval(self, **_kwargs) -> None:
            return None

        async def deliver_for_approval(self, episode_id: str, *, thumbnail_path: Path):
            self.calls.append(episode_id)
            return {"artifact_kind": "video", "message_id": 701}

    registry = tmp_path / "delivery.sqlite3"
    thumbnail = tmp_path / "thumbnail.png"
    thumbnail.write_bytes(b"approved-thumbnail")
    composition = Composition()
    runner = DeliverySessionRunner(registry_path=registry, composition=composition)
    worker = DeliveryOutboxWorker(runner, reconcile_period_seconds=0.01)

    async def exercise() -> None:
        await worker.start()
        await worker.wait_idle()
        runner.enqueue_video(
            episode_id="EP8", thumbnail_path=thumbnail,
            thumbnail_sha256=hashlib.sha256(thumbnail.read_bytes()).hexdigest(),
        )
        await asyncio.sleep(0.03)  # no wake: exercise the bounded fallback only
        await worker.wait_idle()
        await worker.stop()

    asyncio.run(exercise())

    assert composition.calls == ["EP8"]
    assert _queue_row(registry, "EP8") == ("DELIVERED", None)


class _DelayedTelegramServer:
    """Loopback Telegram-shaped transport that holds a POST after receiving it."""

    def __init__(self) -> None:
        self.received = threading.Event()
        self.release = threading.Event()
        self.responded_at: float | None = None
        self.posts = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                self.rfile.read(int(self.headers["Content-Length"]))
                owner.posts += 1
                owner.received.set()
                owner.release.wait(2)
                owner.responded_at = time.monotonic()
                body = b'{"ok":true,"result":{"message_id":702}}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                try:
                    self.wfile.write(body)
                except BrokenPipeError:
                    pass

            def log_message(self, *_args) -> None:
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def api_base(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}/botlocal"

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args) -> None:
        self.release.set()
        self.httpd.shutdown()
        self.thread.join(timeout=3)
        self.httpd.server_close()

    def release_after(self, seconds: float) -> None:
        threading.Timer(seconds, self.release.set).start()


def test_concrete_provider_delayed_http_keeps_event_loop_heartbeating(tmp_path: Path):
    from src.providers.notification.telegram_provider import (
        TelegramNotificationProvider,
    )

    artifact = tmp_path / "video.mp4"
    artifact.write_bytes(b"approved-video")
    with _DelayedTelegramServer() as server:
        provider = TelegramNotificationProvider("local", "10001")
        provider.api_base = server.api_base

        async def exercise() -> int:
            heartbeat_at: float | None = None

            async def pulse() -> None:
                nonlocal heartbeat_at
                await asyncio.sleep(0.01)
                heartbeat_at = time.monotonic()

            pulse_task = asyncio.create_task(pulse())
            send_task = asyncio.create_task(provider.send_video("10001", str(artifact)))
            assert await asyncio.to_thread(server.received.wait, 1)
            server.release_after(0.1)
            result = await send_task
            await pulse_task
            assert heartbeat_at is not None
            assert server.responded_at is not None
            assert heartbeat_at < server.responded_at, "concrete HTTP must not freeze the event loop"
            return result

        assert asyncio.run(exercise()) == 702


def test_shutdown_cancels_concrete_transport_to_unknown_within_deadline(tmp_path: Path):
    from src.approval.delivery_session import (
        DeliveryOutboxWorker,
        DeliverySessionRunner,
    )
    from src.providers.notification.telegram_provider import (
        TelegramNotificationProvider,
    )

    registry = tmp_path / "delivery.sqlite3"
    thumbnail = tmp_path / "thumbnail.png"
    thumbnail.write_bytes(b"approved-thumbnail")
    with _DelayedTelegramServer() as server:
        provider = TelegramNotificationProvider("local", "10001")
        provider.api_base = server.api_base

        class Composition:
            def revalidate_thumbnail_approval(self, **_kwargs) -> None:
                return None

            async def deliver_for_approval(self, _episode_id: str, *, thumbnail_path: Path):
                return {"artifact_kind": "video", "message_id": await provider.send_video("10001", str(thumbnail_path))}

        runner = DeliverySessionRunner(registry_path=registry, composition=Composition())
        runner.enqueue_video(
            episode_id="EP8", thumbnail_path=thumbnail,
            thumbnail_sha256=hashlib.sha256(thumbnail.read_bytes()).hexdigest(),
        )
        worker = DeliveryOutboxWorker(runner, reconcile_period_seconds=60, shutdown_timeout_seconds=0.05)

        async def exercise() -> object:
            await worker.start()
            assert await asyncio.to_thread(server.received.wait, 1)
            started = time.monotonic()
            outcome = await worker.stop()
            assert time.monotonic() - started < 0.25
            return outcome

        assert asyncio.run(exercise()) == {"status": "stopping", "ownership": "retained"}

    assert _queue_row(registry, "EP8") == ("UNKNOWN", "delivery cancelled; manual reconciliation required")


def test_other_process_cannot_take_outbox_lock_during_concrete_active_post(tmp_path: Path):
    from src.approval.delivery_session import (
        DeliveryOutboxWorker,
        DeliverySessionRunner,
    )
    from src.providers.notification.telegram_provider import (
        TelegramNotificationProvider,
    )

    registry = tmp_path / "delivery.sqlite3"
    thumbnail = tmp_path / "thumbnail.png"
    thumbnail.write_bytes(b"approved-thumbnail")
    with _DelayedTelegramServer() as server:
        provider = TelegramNotificationProvider("local", "10001")
        provider.api_base = server.api_base

        class Composition:
            def revalidate_thumbnail_approval(self, **_kwargs) -> None:
                return None

            async def deliver_for_approval(self, _episode_id: str, *, thumbnail_path: Path):
                await provider.send_video("10001", str(thumbnail_path))
                return {"artifact_kind": "video", "message_id": 702}

        runner = DeliverySessionRunner(registry_path=registry, composition=Composition())
        runner.enqueue_video(
            episode_id="EP8", thumbnail_path=thumbnail,
            thumbnail_sha256=hashlib.sha256(thumbnail.read_bytes()).hexdigest(),
        )
        worker = DeliveryOutboxWorker(runner, reconcile_period_seconds=60, shutdown_timeout_seconds=0.05)

        async def exercise() -> None:
            await worker.start()
            assert await asyncio.to_thread(server.received.wait, 1)
            context = multiprocessing.get_context("spawn")
            acquired = context.Event()
            probe = context.Process(target=_probe_outbox_lock, args=(str(registry.with_suffix(".outbox.lock")), acquired))
            probe.start()
            try:
                await asyncio.sleep(0.1)
                assert not acquired.is_set(), "another process acquired while the POST still owned the lock"
                assert await worker.stop() == {"status": "stopping", "ownership": "retained"}
                assert not acquired.is_set(), "retained worker must keep the lock until it actually unwinds"
            finally:
                probe.join(3)
                if probe.is_alive():
                    probe.kill()
            # Process remains blocked until host-controlled transport completion.

        asyncio.run(exercise())
        assert server.posts == 1

    assert _queue_row(registry, "EP8") == ("UNKNOWN", "delivery cancelled; manual reconciliation required")


@pytest.mark.parametrize("value", [True, False, 0, -1, float("inf"), float("nan"), "1"])
def test_worker_rejects_nonfinite_or_non_numeric_periods_before_creating_state(value):
    from src.approval.delivery_session import DeliveryOutboxWorker

    class Runner:
        pass

    with pytest.raises(ValueError, match="positive finite"):
        DeliveryOutboxWorker(Runner(), reconcile_period_seconds=value)
    with pytest.raises(ValueError, match="positive finite"):
        DeliveryOutboxWorker(Runner(), shutdown_timeout_seconds=value)
