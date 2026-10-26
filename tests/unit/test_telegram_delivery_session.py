"""Durable, explicit delivery-session recovery for the two Director gates."""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path


def test_explicit_outbox_drain_delivers_one_video_gate_once_across_restart(tmp_path: Path):
    from src.approval.delivery_session import DeliverySessionRunner

    class Composition:
        def __init__(self):
            self.calls: list[tuple[str, Path]] = []

        def revalidate_thumbnail_approval(self, **_kwargs):
            return None

        async def deliver_for_approval(self, episode_id: str, *, thumbnail_path: Path):
            self.calls.append((episode_id, thumbnail_path))
            return {"artifact_kind": "video", "message_id": 702}

    thumbnail = tmp_path / "approved-thumbnail.png"
    thumbnail.write_bytes(b"thumbnail")
    composition = Composition()
    runner = DeliverySessionRunner(registry_path=tmp_path / "delivery.sqlite3", composition=composition)
    runner.enqueue_video(
        episode_id="EP8", thumbnail_path=thumbnail,
        thumbnail_sha256=hashlib.sha256(thumbnail.read_bytes()).hexdigest(),
    )

    assert asyncio.run(runner.drain_once()) == {"status": "delivered", "episode_id": "EP8", "artifact_kind": "video"}
    assert composition.calls == [("EP8", thumbnail)]

    restarted = DeliverySessionRunner(registry_path=tmp_path / "delivery.sqlite3", composition=composition)
    assert asyncio.run(restarted.drain_once()) == {"status": "idle"}
    assert composition.calls == [("EP8", thumbnail)]


def test_crash_after_video_receipt_before_outbox_ack_restarts_without_duplicate_post(tmp_path: Path):
    from src.approval.delivery_session import DeliverySessionRunner

    class Composition:
        def __init__(self):
            self.posts = 0
            self.receipt = tmp_path / "video-receipt.json"

        def revalidate_thumbnail_approval(self, **_kwargs):
            return None

        async def deliver_for_approval(self, _episode_id: str, *, thumbnail_path: Path):
            if not self.receipt.exists():
                self.posts += 1
                self.receipt.write_text("durable video receipt", encoding="utf-8")
            return {"artifact_kind": "video", "message_id": 702}

    class CrashBeforeOutboxAck(DeliverySessionRunner):
        def _mark_delivered(self, _episode_id: str) -> None:
            raise RuntimeError("crash after delivery receipt")

    thumbnail = tmp_path / "approved-thumbnail.png"
    thumbnail.write_bytes(b"thumbnail")
    composition = Composition()
    first = CrashBeforeOutboxAck(registry_path=tmp_path / "delivery.sqlite3", composition=composition)
    first.enqueue_video(
        episode_id="EP8", thumbnail_path=thumbnail,
        thumbnail_sha256=hashlib.sha256(thumbnail.read_bytes()).hexdigest(),
    )
    assert asyncio.run(first.drain_once()) == {
        "status": "unknown", "episode_id": "EP8",
        "reason": "delivery acknowledgement failed; manual reconciliation required",
    }

    restarted = DeliverySessionRunner(registry_path=tmp_path / "delivery.sqlite3", composition=composition)
    assert asyncio.run(restarted.drain_once()) == {"status": "idle"}
    assert composition.posts == 1


def test_outbox_rejects_changed_thumbnail_hash_before_delivery(tmp_path: Path):
    from src.approval.delivery_session import DeliverySessionRunner

    class Composition:
        def revalidate_thumbnail_approval(self, **_kwargs):
            return None

        async def deliver_for_approval(self, episode_id: str, *, thumbnail_path: Path):
            raise AssertionError("changed thumbnail must not send video")

    thumbnail = tmp_path / "approved-thumbnail.png"
    thumbnail.write_bytes(b"original")
    runner = DeliverySessionRunner(registry_path=tmp_path / "delivery.sqlite3", composition=Composition())
    runner.enqueue_video(episode_id="EP8", thumbnail_path=thumbnail, thumbnail_sha256="a" * 64)

    assert asyncio.run(runner.drain_once()) == {"status": "blocked", "episode_id": "EP8", "reason": "thumbnail hash changed"}


def test_outbox_revalidates_authenticated_thumbnail_approval_before_video_post(tmp_path: Path):
    from src.approval.delivery_session import DeliverySessionRunner

    class Composition:
        def revalidate_thumbnail_approval(self, **_kwargs):
            raise ValueError("persisted thumbnail reply was revoked")

        async def deliver_for_approval(self, *_args, **_kwargs):
            raise AssertionError("revoked approval must block before video POST")

    thumbnail = tmp_path / "approved-thumbnail.png"
    thumbnail.write_bytes(b"thumbnail")
    runner = DeliverySessionRunner(registry_path=tmp_path / "delivery.sqlite3", composition=Composition())
    runner.enqueue_video(
        episode_id="EP8", thumbnail_path=thumbnail,
        thumbnail_sha256=hashlib.sha256(thumbnail.read_bytes()).hexdigest(),
    )

    assert asyncio.run(runner.drain_once()) == {
        "status": "blocked", "episode_id": "EP8", "reason": "thumbnail approval is no longer authenticated",
    }


def test_malformed_post_submit_receipt_becomes_unknown_and_never_reposts_after_restart(tmp_path: Path):
    from src.approval.delivery_session import DeliverySessionRunner

    class Composition:
        def __init__(self) -> None:
            self.posts = 0

        def revalidate_thumbnail_approval(self, **_kwargs) -> None:
            return None

        async def deliver_for_approval(self, _episode_id: str, *, thumbnail_path: Path):
            self.posts += 1
            return {"artifact_kind": "thumbnail", "message_id": 702}

    thumbnail = tmp_path / "approved-thumbnail.png"
    thumbnail.write_bytes(b"thumbnail")
    composition = Composition()
    registry = tmp_path / "delivery.sqlite3"
    runner = DeliverySessionRunner(registry_path=registry, composition=composition)
    runner.enqueue_video(
        episode_id="EP8", thumbnail_path=thumbnail,
        thumbnail_sha256=hashlib.sha256(thumbnail.read_bytes()).hexdigest(),
    )

    assert asyncio.run(runner.drain_once()) == {
        "status": "unknown", "episode_id": "EP8",
        "reason": "delivery receipt invalid; manual reconciliation required",
    }
    assert asyncio.run(DeliverySessionRunner(registry_path=registry, composition=composition).drain_once()) == {"status": "idle"}
    assert composition.posts == 1


def test_cli_session_preflight_does_not_open_or_inspect_sqlite(tmp_path: Path, monkeypatch, capsys):
    import sqlite3

    from src.approval import telegram_cli

    registry = tmp_path / "delivery.sqlite3"
    with sqlite3.connect(registry) as connection:
        connection.execute(
            "CREATE TABLE video_delivery_outbox (episode_id TEXT PRIMARY KEY, thumbnail_path TEXT NOT NULL, "
            "thumbnail_sha256 TEXT NOT NULL, status TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO video_delivery_outbox VALUES ('EP8', 'C:/approved-thumbnail.png', ?, 'PENDING')", ("a" * 64,)
        )
    before = {path.name: path.read_bytes() for path in tmp_path.glob("delivery.sqlite3*")}
    monkeypatch.setattr(
        telegram_cli, "create_production_telegram_gate",
        lambda **_: (_ for _ in ()).throw(AssertionError("preflight must not construct transport")),
        raising=False,
    )

    assert telegram_cli.delivery_session_main(["preflight", "--registry", str(registry)]) == 0

    assert capsys.readouterr().out.strip() == '{"queue": "NOT_INSPECTED", "status": "ready"}'
    assert {path.name: path.read_bytes() for path in tmp_path.glob("delivery.sqlite3*")} == before
