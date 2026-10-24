"""Offline contract tests for the authenticated Telegram webhook boundary."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.approval.telegram_inbound import TelegramInboundAdapter

SECRET = "external-configured-webhook-secret"
CHAT_ID = "10001"
SENDER_ID = "20002"
INSTANCE_ID = "approval-production-a"


def _adapter(tmp_path: Path, *, secret: str | None = SECRET, instance: str = INSTANCE_ID):
    return TelegramInboundAdapter(
        journal_path=tmp_path / "telegram-inbound.sqlite3",
        expected_chat_id=CHAT_ID,
        trusted_sender_ids=frozenset({SENDER_ID}),
        webhook_secret=secret,
        instance_id=instance,
    )


def _update(
    *,
    update_id: int = 10,
    message_id: int = 20,
    sender_id: int = int(SENDER_ID),
    chat_id: int = int(CHAT_ID),
    reply_to_message_id: int = 42,
    text: str = "APROVAR THUMBNAIL EP8",
):
    return {
        "update_id": update_id,
        "message": {
            "message_id": message_id,
            "from": {"id": sender_id, "is_bot": False},
            "chat": {"id": chat_id, "type": "private"},
            "reply_to_message": {"message_id": reply_to_message_id},
            "text": text,
        },
    }


def _body(**changes: object) -> bytes:
    return json.dumps(_update(**changes), separators=(",", ":")).encode("utf-8")


def test_valid_authenticated_webhook_yields_one_journaled_reply(tmp_path: Path):
    adapter = _adapter(tmp_path)

    assert adapter.ingest_webhook(body=_body(), secret_header=SECRET) is True
    reply = adapter.verified_reply(chat_id=CHAT_ID, message_id=42)

    assert reply is not None
    assert reply.transport == "TELEGRAM"
    assert reply.chat_id == CHAT_ID
    assert reply.sender_id == SENDER_ID
    assert reply.message_id == 20
    assert reply.reply_to_message_id == 42
    assert adapter.verified_reply(chat_id=CHAT_ID, message_id=42) is None


@pytest.mark.parametrize("secret", [None, "wrong-secret"])
def test_missing_or_wrong_webhook_secret_fails_closed(tmp_path: Path, secret: str | None):
    adapter = _adapter(tmp_path)

    assert adapter.ingest_webhook(body=_body(), secret_header=secret) is False
    assert adapter.verified_reply(chat_id=CHAT_ID, message_id=42) is None


def test_missing_configured_webhook_secret_fails_closed(tmp_path: Path):
    adapter = _adapter(tmp_path, secret=None)

    assert adapter.ingest_webhook(body=_body(), secret_header=SECRET) is False
    assert adapter.verified_reply(chat_id=CHAT_ID, message_id=42) is None


def test_missing_secret_cannot_initialize_a_journal_binding(tmp_path: Path):
    disabled = _adapter(tmp_path, secret=None)
    assert disabled.ingest_webhook(body=_body(), secret_header=SECRET) is False

    enabled = _adapter(tmp_path)
    assert enabled.ingest_webhook(body=_body(), secret_header=SECRET) is True


@pytest.mark.parametrize(
    "changes",
    [
        {"sender_id": 99999},
        {"chat_id": 99999},
        {"reply_to_message_id": 99},
    ],
)
def test_spoofed_sender_chat_or_reply_cannot_become_authenticated(tmp_path: Path, changes: dict[str, int]):
    adapter = _adapter(tmp_path)

    accepted = adapter.ingest_webhook(body=_body(**changes), secret_header=SECRET)
    assert accepted is ("reply_to_message_id" in changes)
    assert adapter.verified_reply(chat_id=CHAT_ID, message_id=42) is None


def test_rejects_bots_channels_edited_messages_and_oversize_text(tmp_path: Path):
    adapter = _adapter(tmp_path)
    bot = _update()
    bot["message"]["from"]["is_bot"] = True
    channel = _update()
    channel["message"]["chat"]["type"] = "channel"
    edited = {"update_id": 10, "edited_message": _update()["message"]}

    assert adapter.ingest_webhook(body=json.dumps(bot).encode(), secret_header=SECRET) is False
    assert adapter.ingest_webhook(body=json.dumps(channel).encode(), secret_header=SECRET) is False
    assert adapter.ingest_webhook(body=json.dumps(edited).encode(), secret_header=SECRET) is False
    assert adapter.ingest_webhook(body=_body(text="x" * 4097), secret_header=SECRET) is False


def test_replay_is_idempotent_but_conflicting_update_or_message_is_blocked(tmp_path: Path):
    adapter = _adapter(tmp_path)
    first = _body()

    assert adapter.ingest_webhook(body=first, secret_header=SECRET) is True
    assert adapter.ingest_webhook(body=first, secret_header=SECRET) is True
    assert adapter.ingest_webhook(body=_body(text="different"), secret_header=SECRET) is False
    assert adapter.ingest_webhook(
        body=_body(update_id=11, text="different"), secret_header=SECRET
    ) is False


def test_authenticated_journal_survives_restart_and_cannot_be_rebound(tmp_path: Path):
    adapter = _adapter(tmp_path)
    assert adapter.ingest_webhook(body=_body(), secret_header=SECRET) is True

    restarted = _adapter(tmp_path)
    reply = restarted.verified_reply(chat_id=CHAT_ID, message_id=42)
    assert reply is not None
    assert reply.sender_id == SENDER_ID

    with pytest.raises(ValueError, match="binding"):
        _adapter(tmp_path, instance="another-trusted-instance")


def test_sqlite_row_adulteration_does_not_become_authenticated(tmp_path: Path):
    adapter = _adapter(tmp_path)
    assert adapter.ingest_webhook(body=_body(), secret_header=SECRET) is True
    journal = tmp_path / "telegram-inbound.sqlite3"

    import sqlite3

    with sqlite3.connect(journal) as connection:
        connection.execute("UPDATE replies SET text = ?", ("APROVAR VÍDEO EP8",))

    restarted = _adapter(tmp_path)
    assert restarted.verified_reply(chat_id=CHAT_ID, message_id=42) is None


def test_hash_adulteration_does_not_become_authenticated(tmp_path: Path):
    adapter = _adapter(tmp_path)
    assert adapter.ingest_webhook(body=_body(), secret_header=SECRET) is True

    import sqlite3

    with sqlite3.connect(tmp_path / "telegram-inbound.sqlite3") as connection:
        connection.execute("UPDATE replies SET update_hash = ?", ("0" * 64,))

    restarted = _adapter(tmp_path)
    assert restarted.verified_reply(chat_id=CHAT_ID, message_id=42) is None


def test_journal_adulteration_cannot_unconsume_a_reply(tmp_path: Path):
    adapter = _adapter(tmp_path)
    assert adapter.ingest_webhook(body=_body(), secret_header=SECRET) is True
    assert adapter.verified_reply(chat_id=CHAT_ID, message_id=42) is not None

    import sqlite3

    with sqlite3.connect(tmp_path / "telegram-inbound.sqlite3") as connection:
        connection.execute("UPDATE replies SET consumed_at = NULL")

    restarted = _adapter(tmp_path)
    assert restarted.verified_reply(chat_id=CHAT_ID, message_id=42) is None


def test_concurrent_duplicate_webhook_is_idempotent(tmp_path: Path):
    adapter = _adapter(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda _: adapter.ingest_webhook(body=_body(), secret_header=SECRET),
                range(16),
            )
        )

    assert results == [True] * 16
    assert adapter.verified_reply(chat_id=CHAT_ID, message_id=42) is not None


def test_claim_recovery_and_read_revalidation_survive_restart_without_second_consumption(tmp_path: Path):
    adapter = _adapter(tmp_path)
    assert adapter.ingest_webhook(body=_body(), secret_header=SECRET) is True
    claimed = adapter.claim_verified_reply(chat_id=CHAT_ID, message_id=42)
    assert claimed is not None

    restarted = _adapter(tmp_path)
    assert restarted.claim_verified_reply(chat_id=CHAT_ID, message_id=42) is None
    recovered = restarted.recover_verified_reply(chat_id=CHAT_ID, message_id=42)
    assert recovered == claimed
    assert restarted.revalidate_reply(claimed.document()) == claimed
    forged = claimed.document() | {"sender_id": "999"}
    assert restarted.revalidate_reply(forged) is None
