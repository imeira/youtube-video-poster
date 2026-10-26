"""Fresh-process evidence for the authenticated two-gate Telegram protocol."""
from __future__ import annotations

import asyncio
import hashlib
import json
import multiprocessing
import os
import shutil
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from queue import Empty

CHAT_ID = "10001"
SENDER_ID = "20002"
SECRET = "fresh-process-test-hmac-secret"
TOKEN = "fresh-process-test-token"


def _reply(
    *, reply_to: int = 701, text: str = "APROVAR THUMBNAIL EP8", update_id: int = 1, message_id: int = 99
) -> bytes:
    return json.dumps({"update_id": update_id, "message": {
        "message_id": message_id, "from": {"id": int(SENDER_ID), "is_bot": False},
        "chat": {"id": int(CHAT_ID), "type": "private"},
        "reply_to_message": {"message_id": reply_to}, "text": text,
    }}, separators=(",", ":")).encode()


def _local_provider(port: int):
    from src.providers.notification.telegram_provider import (
        TelegramNotificationProvider,
    )

    def factory(token: str, chat_id: str) -> TelegramNotificationProvider:
        provider = TelegramNotificationProvider(token, chat_id)
        provider.api_base = f"http://127.0.0.1:{port}/bot{token}"
        return provider

    return factory


def _gate(root: str, config_path: str, port: int, inbound_port: int = 19443):
    from src.agents.director import DirectorAgent
    from src.approval.production_telegram_gate import (
        ProductionTelegramGateConfig,
        create_production_telegram_gate,
    )
    from src.config.loader import load_config

    os.environ["STUDIO_EPISODES_DIR"] = str(Path(root) / "episodes")
    director = DirectorAgent(load_config(Path(config_path)))
    return create_production_telegram_gate(
        director=director,
        config=ProductionTelegramGateConfig(
            bot_token=TOKEN, webhook_secret=SECRET, chat_id=CHAT_ID,
            trusted_sender_ids=frozenset({SENDER_ID}), instance_id="fresh-process-test",
            journal_path=Path(root) / "trusted" / "journal.sqlite3",
            registry_path=Path(root) / "trusted" / "registry.sqlite3",
            port=inbound_port,
        ),
        provider_factory=_local_provider(port),
    )


def _worker(action: str, root: str, config_path: str, port: int, result_queue) -> None:
    try:
        gate = _gate(root, config_path, port)
        thumbnail = Path(root) / "episodes" / "EP8" / "thumbnails" / "thumbnail.png"
        if action == "thumbnail":
            result = asyncio.run(gate.deliver_for_approval("EP8", thumbnail_path=thumbnail))
        elif action == "reply":
            accepted = gate.service.handle_webhook(_reply(), SECRET)
            result = {"status": accepted.status, "body": accepted.body}
        elif action in {"reply-host", "video-reply-host"}:
            from aiohttp import ClientSession

            reply = _reply(
                reply_to=702 if action == "video-reply-host" else 701,
                text="APROVAR VÍDEO EP8" if action == "video-reply-host" else "APROVAR THUMBNAIL EP8",
                update_id=2 if action == "video-reply-host" else 1,
                message_id=100 if action == "video-reply-host" else 99,
            )

            async def host_event():
                async with gate.launcher.serve():
                    async with ClientSession() as client:
                        response = await client.post(
                            "http://127.0.0.1:19443/telegram/inbound", data=reply,
                            headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
                        )
                        accepted = {"status": response.status, "body": await response.json()}
                    await gate.outbox_worker.wait_idle()
                    return accepted

            result = asyncio.run(host_event())
        elif action == "crash-before-ack":
            from src.approval.delivery_session import DeliverySessionRunner

            class CrashBeforeAck(DeliverySessionRunner):
                def _mark_delivered(self, episode_id: str) -> None:
                    raise RuntimeError(f"injected crash before outbox ack for {episode_id}")

            result = asyncio.run(CrashBeforeAck(
                registry_path=gate.config.registry_path, composition=gate,
            ).drain_once())
        elif action == "drain":
            result = asyncio.run(gate.delivery_session_runner().drain_once())
        else:
            raise AssertionError(f"unknown action: {action}")
        result_queue.put({"pid": os.getpid(), "action": action, "result": result})
    except (OSError, RuntimeError, ValueError) as error:
        result_queue.put({"pid": os.getpid(), "action": action, "error": f"{type(error).__name__}: {error}"})


def _run_fresh(
    action: str, root: Path, config_path: Path, port: int, *, allow_error: bool = False
) -> dict[str, object]:
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    process = context.Process(target=_worker, args=(action, str(root), str(config_path), port, queue))
    process.start()
    process.join(30)
    assert process.exitcode == 0
    try:
        event = queue.get(timeout=3)
    except Empty as error:  # pragma: no cover - diagnostic guard
        raise AssertionError("fresh worker did not return an event") from error
    if not allow_error:
        assert "error" not in event, event
    return event


class _CountingTelegramServer:
    def __init__(self):
        self.posts: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                self.rfile.read(length)
                owner.posts.append(self.path)
                message_id = 701 if self.path.endswith("/sendPhoto") else 702
                body = json.dumps({"ok": True, "result": {"message_id": message_id}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_args):
        self.httpd.shutdown()
        self.thread.join(timeout=5)
        self.httpd.server_close()

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]


def _materialize_protocol(root: Path) -> Path:
    from src.state.machine import EpisodeState, EpisodeStateStore

    episode = root / "episodes" / "EP8"
    (episode / "qa").mkdir(parents=True)
    (episode / "thumbnails").mkdir()
    (episode / "renders").mkdir()
    (episode / "thumbnails" / "thumbnail.png").write_bytes(b"frozen-thumbnail")
    (episode / "renders" / "final.mp4").write_bytes(b"frozen-video")
    state = EpisodeStateStore.load_or_create(episode / "state.json", "EP8")
    state.current_state = EpisodeState.WAITING_THUMBNAIL_APPROVAL
    state.save(episode / "state.json")
    config = root / "configured-test.yaml"
    source = Path(__file__).parents[2] / "config.yaml"
    shutil.copyfile(source, config)
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'chat_id: ""', f'chat_id: "{CHAT_ID}"', 1
        ).replace(
            "trusted_sender_ids: []", f'trusted_sender_ids: ["{SENDER_ID}"]', 1
        ), encoding="utf-8",
    )
    return config


def test_fresh_process_acknowledgement_failure_is_unknown_and_a_later_drain_never_reposts(tmp_path: Path):
    config = _materialize_protocol(tmp_path)

    with _CountingTelegramServer() as server:
        thumbnail = _run_fresh("thumbnail", tmp_path, config, server.port)
        approved = _run_fresh("reply", tmp_path, config, server.port)
        crashed = _run_fresh("crash-before-ack", tmp_path, config, server.port, allow_error=True)
        drained = _run_fresh("drain", tmp_path, config, server.port)
        replayed = _run_fresh("reply", tmp_path, config, server.port)

    proof = {
        "fresh_pids": [thumbnail["pid"], approved["pid"], crashed["pid"], drained["pid"], replayed["pid"]],
        "post_paths": server.posts,
        "post_count": len(server.posts),
    }
    print("fresh-process-proof=" + json.dumps(proof, sort_keys=True))
    assert thumbnail["result"]["artifact_kind"] == "thumbnail"
    assert approved["result"]["body"]["status"] == "approved"
    assert crashed["result"] == {
        "status": "unknown", "episode_id": "EP8",
        "reason": "delivery acknowledgement failed; manual reconciliation required",
    }
    assert drained["result"] == {"status": "idle"}
    assert replayed["result"]["body"]["status"] == "acknowledged"
    assert len({thumbnail["pid"], approved["pid"], crashed["pid"], drained["pid"], replayed["pid"]}) == 5
    assert server.posts == [f"/bot{TOKEN}/sendPhoto", f"/bot{TOKEN}/sendVideo"]
    with sqlite3.connect(tmp_path / "trusted" / "registry.sqlite3") as connection:
        assert connection.execute(
            "SELECT status, reason FROM video_delivery_outbox WHERE episode_id = 'EP8'"
        ).fetchone() == (
            "UNKNOWN", "delivery acknowledgement failed; manual reconciliation required",
        )
        assert connection.execute(
            "SELECT artifact_kind, message_id FROM deliveries WHERE episode_id = 'EP8' ORDER BY message_id"
        ).fetchall() == [("thumbnail", 701), ("video", 702)]
        assert connection.execute(
            "SELECT artifact_kind, state FROM outcomes WHERE episode_id = 'EP8'"
        ).fetchall() == [("thumbnail", "WAITING_VIDEO_APPROVAL")]
    video_receipt = json.loads((tmp_path / "episodes" / "EP8" / "qa" / "delivery" / "video.json").read_text())
    assert video_receipt["artifact_kind"] == "video"
    assert video_receipt["message_id"] == 702
    assert hashlib.sha256((tmp_path / "episodes" / "EP8" / "thumbnails" / "thumbnail.png").read_bytes()).hexdigest()


def test_fresh_process_host_event_wakes_worker_and_final_video_approval_never_publishes(tmp_path: Path):
    from src.state.machine import EpisodeState, EpisodeStateStore

    config = _materialize_protocol(tmp_path)
    with _CountingTelegramServer() as server:
        thumbnail = _run_fresh("thumbnail", tmp_path, config, server.port)
        approved_thumbnail = _run_fresh("reply-host", tmp_path, config, server.port)
        approved_video = _run_fresh("video-reply-host", tmp_path, config, server.port)

    assert thumbnail["result"]["artifact_kind"] == "thumbnail"
    assert approved_thumbnail["result"]["body"]["status"] == "approved"
    assert approved_video["result"]["body"]["status"] == "approved"
    assert server.posts == [f"/bot{TOKEN}/sendPhoto", f"/bot{TOKEN}/sendVideo"]
    assert EpisodeStateStore.load(tmp_path / "episodes" / "EP8" / "state.json").current_state is EpisodeState.WAITING_FINAL_APPROVAL
