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


def _reply() -> bytes:
    return json.dumps({"update_id": 1, "message": {
        "message_id": 99, "from": {"id": int(SENDER_ID), "is_bot": False},
        "chat": {"id": int(CHAT_ID), "type": "private"},
        "reply_to_message": {"message_id": 701}, "text": "APROVAR THUMBNAIL EP8",
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


def _gate(root: str, config_path: str, port: int):
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


def test_fresh_process_protocol_reuses_video_receipt_after_crash_and_replay_is_idempotent(tmp_path: Path):
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
    assert "injected crash before outbox ack" in crashed["error"] if "error" in crashed else False
    assert drained["result"] == {"status": "delivered", "episode_id": "EP8", "artifact_kind": "video"}
    assert replayed["result"]["body"]["status"] == "acknowledged"
    assert len({thumbnail["pid"], approved["pid"], crashed["pid"], drained["pid"], replayed["pid"]}) == 5
    assert server.posts == [f"/bot{TOKEN}/sendPhoto", f"/bot{TOKEN}/sendVideo"]
    with sqlite3.connect(tmp_path / "trusted" / "registry.sqlite3") as connection:
        assert connection.execute("SELECT status FROM video_delivery_outbox WHERE episode_id = 'EP8'").fetchone() == ("DELIVERED",)
    assert (tmp_path / "episodes" / "EP8" / "qa" / "delivery" / "video.json").is_file()
    assert hashlib.sha256((tmp_path / "episodes" / "EP8" / "thumbnails" / "thumbnail.png").read_bytes()).hexdigest()
