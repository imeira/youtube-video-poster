"""Approval decisions require an authenticated reply bound to delivered media."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from src.approval.controller import ApprovalController, ApprovalControllerError
from src.approval.inbound import AuthenticatedApprovalReply
from src.approval.receipts import ApprovalReceipt
from src.approval.telegram_inbound import TelegramInboundAdapter


class FakeAuthenticatedSource:
    """TEST-only transport boundary; production never treats a dict as this source."""

    def __init__(self, reply: AuthenticatedApprovalReply | None):
        self.reply = reply
        self.requests: list[tuple[str, int]] = []

    def claim_verified_reply(self, *, chat_id: str, message_id: int):
        self.requests.append(("claim", chat_id, message_id))
        return self.reply

    def recover_verified_reply(self, *, chat_id: str, message_id: int):
        self.requests.append(("recover", chat_id, message_id))
        return self.reply

    def revalidate_reply(self, document):
        self.requests.append(("revalidate", document))
        return self.reply if self.reply is not None and document == self.reply.document() else None


def _delivery(tmp_path: Path) -> tuple[Path, Path]:
    artifact = tmp_path / "asset.png"
    artifact.write_bytes(b"thumbnail")
    approval = ApprovalReceipt.approve("thumbnail", artifact, "delivery-audit")
    path = tmp_path / "delivery.json"
    path.write_text(json.dumps({
        "episode_id": "EP8", "artifact_kind": "thumbnail", "approval": asdict(approval),
        "chat_id": "authorized-chat", "message_id": 42,
    }), encoding="utf-8")
    return artifact, path


def _reply(*, text: str = "APROVAR THUMBNAIL EP8", sender: str = "trusted-user", chat: str = "authorized-chat", reply_to: int = 42):
    return AuthenticatedApprovalReply(
        transport="TEST", chat_id=chat, sender_id=sender, message_id=99,
        reply_to_message_id=reply_to, text=text,
    )


def test_authenticated_reply_persists_bound_human_receipt(tmp_path: Path):
    artifact, delivery = _delivery(tmp_path)
    source = FakeAuthenticatedSource(_reply())

    receipt = ApprovalController().confirm_delivery(
        episode_id="EP8", artifact_kind="thumbnail", artifact_path=artifact,
        delivery_receipt_path=delivery, approval_receipt_path=tmp_path / "approval.json",
        expected_chat_id="authorized-chat", trusted_sender_ids=frozenset({"trusted-user"}),
        inbound_source=source,
    )

    assert receipt.approver == "TEST:trusted-user"
    persisted = json.loads((tmp_path / "approval.json").read_text(encoding="utf-8"))
    assert persisted["inbound_receipt"] == {
        "transport": "TEST", "chat_id": "authorized-chat", "sender_id": "trusted-user",
        "message_id": 99, "reply_to_message_id": 42, "text": "APROVAR THUMBNAIL EP8",
    }
    assert source.requests == [("claim", "authorized-chat", 42)]


@pytest.mark.parametrize("reply", [
    _reply(sender="attacker"), _reply(chat="other-chat"), _reply(reply_to=41), _reply(text="APROVAR"),
])
def test_untrusted_or_unbound_reply_cannot_authorize(tmp_path: Path, reply: AuthenticatedApprovalReply):
    artifact, delivery = _delivery(tmp_path)

    with pytest.raises(ApprovalControllerError, match="authenticated|exact"):
        ApprovalController().confirm_delivery(
            episode_id="EP8", artifact_kind="thumbnail", artifact_path=artifact,
            delivery_receipt_path=delivery, approval_receipt_path=tmp_path / "approval.json",
            expected_chat_id="authorized-chat", trusted_sender_ids=frozenset({"trusted-user"}),
            inbound_source=FakeAuthenticatedSource(reply),
        )


def test_missing_trusted_transport_fails_closed(tmp_path: Path):
    artifact, delivery = _delivery(tmp_path)
    with pytest.raises(ApprovalControllerError, match="authenticated"):
        ApprovalController().confirm_delivery(
            episode_id="EP8", artifact_kind="thumbnail", artifact_path=artifact,
            delivery_receipt_path=delivery, approval_receipt_path=tmp_path / "approval.json",
            expected_chat_id="authorized-chat", trusted_sender_ids=frozenset({"trusted-user"}),
            inbound_source=None,
        )


@pytest.mark.parametrize("kind, command", [("thumbnail", "APROVAR THUMBNAIL EP8"), ("video", "APROVAR VÍDEO EP8")])
def test_persisted_receipt_requires_revalidated_external_authority(tmp_path: Path, kind: str, command: str):
    artifact = tmp_path / f"asset-{kind}.bin"
    artifact.write_bytes(kind.encode())
    approval_path = tmp_path / "approval.json"
    reply = AuthenticatedApprovalReply(
        transport="TEST", chat_id="authorized-chat", sender_id="trusted-user",
        message_id=99, reply_to_message_id=42, text=command,
    )
    persisted = ApprovalReceipt.approve(
        kind, artifact, "TEST:trusted-user", inbound_receipt=reply.document()
    )
    approval_path.write_text(json.dumps(asdict(persisted)), encoding="utf-8")
    delivery = tmp_path / "delivery.json"
    delivery_approval = ApprovalReceipt.approve(kind, artifact, "delivery-audit")
    delivery.write_text(json.dumps({
        "episode_id": "EP8", "artifact_kind": kind, "approval": asdict(delivery_approval),
        "chat_id": "authorized-chat", "message_id": 42,
    }), encoding="utf-8")

    receipt = ApprovalController().confirm_delivery(
        episode_id="EP8", artifact_kind=kind, artifact_path=artifact,
        delivery_receipt_path=delivery, approval_receipt_path=approval_path,
        expected_chat_id="authorized-chat", trusted_sender_ids=frozenset({"trusted-user"}),
        inbound_source=FakeAuthenticatedSource(reply),
    )

    assert receipt == persisted


@pytest.mark.parametrize("kind", ["thumbnail", "video"])
def test_legacy_or_adultered_persisted_receipt_cannot_bypass_authority(tmp_path: Path, kind: str):
    artifact = tmp_path / f"asset-{kind}.bin"
    artifact.write_bytes(kind.encode())
    approval_path = tmp_path / "approval.json"
    legacy = ApprovalReceipt.approve(kind, artifact, "local-user")
    approval_path.write_text(json.dumps(asdict(legacy)), encoding="utf-8")
    delivery = tmp_path / "delivery.json"
    delivered = ApprovalReceipt.approve(kind, artifact, "delivery-audit")
    delivery.write_text(json.dumps({
        "episode_id": "EP8", "artifact_kind": kind, "approval": asdict(delivered),
        "chat_id": "authorized-chat", "message_id": 42,
    }), encoding="utf-8")

    with pytest.raises(ApprovalControllerError, match="authenticated"):
        ApprovalController().confirm_delivery(
            episode_id="EP8", artifact_kind=kind, artifact_path=artifact,
            delivery_receipt_path=delivery, approval_receipt_path=approval_path,
            expected_chat_id="authorized-chat", trusted_sender_ids=frozenset({"trusted-user"}),
            inbound_source=FakeAuthenticatedSource(None),
        )


def test_claim_crash_recovery_persists_once_then_revalidates_without_reconsuming(tmp_path: Path):
    artifact = tmp_path / "asset.png"
    artifact.write_bytes(b"thumbnail")
    delivered = ApprovalReceipt.approve("thumbnail", artifact, "delivery-audit")
    delivery = tmp_path / "delivery.json"
    delivery.write_text(json.dumps({
        "episode_id": "EP8", "artifact_kind": "thumbnail", "approval": asdict(delivered),
        "chat_id": "10001", "message_id": 42,
    }), encoding="utf-8")
    adapter = TelegramInboundAdapter(
        journal_path=tmp_path / "journal.sqlite3", expected_chat_id="10001",
        trusted_sender_ids=frozenset({"20002"}), webhook_secret="external-secret", instance_id="test-a",
    )
    body = json.dumps({"update_id": 1, "message": {
        "message_id": 99, "from": {"id": 20002, "is_bot": False},
        "chat": {"id": 10001, "type": "private"},
        "reply_to_message": {"message_id": 42}, "text": "APROVAR THUMBNAIL EP8",
    }}).encode()
    assert adapter.ingest_webhook(body=body, secret_header="external-secret")
    # Simulate a crash after the durable consume transaction, before receipt write.
    assert adapter.claim_verified_reply(chat_id="10001", message_id=42) is not None

    approval_path = tmp_path / "approval.json"
    recovered = ApprovalController().confirm_delivery(
        episode_id="EP8", artifact_kind="thumbnail", artifact_path=artifact,
        delivery_receipt_path=delivery, approval_receipt_path=approval_path,
        expected_chat_id="10001", trusted_sender_ids=frozenset({"20002"}), inbound_source=adapter,
    )
    restarted = TelegramInboundAdapter(
        journal_path=tmp_path / "journal.sqlite3", expected_chat_id="10001",
        trusted_sender_ids=frozenset({"20002"}), webhook_secret="external-secret", instance_id="test-a",
    )
    # Simulate a second crash after receipt persistence but before state transition.
    assert ApprovalController().confirm_delivery(
        episode_id="EP8", artifact_kind="thumbnail", artifact_path=artifact,
        delivery_receipt_path=delivery, approval_receipt_path=approval_path,
        expected_chat_id="10001", trusted_sender_ids=frozenset({"20002"}), inbound_source=restarted,
    ) == recovered
    assert restarted.claim_verified_reply(chat_id="10001", message_id=42) is None


@pytest.mark.parametrize("field, value", [
    ("sender_id", "attacker"), ("chat_id", "other-chat"), ("reply_to_message_id", 41),
    ("text", "APROVAR VÍDEO EP8"),
])
def test_adultered_persisted_inbound_document_never_authorizes(tmp_path: Path, field: str, value: object):
    artifact, delivery = _delivery(tmp_path)
    reply = _reply()
    document = reply.document() | {field: value}
    persisted = ApprovalReceipt.approve(
        "thumbnail", artifact, "TEST:trusted-user", inbound_receipt=document
    )
    approval_path = tmp_path / "approval.json"
    approval_path.write_text(json.dumps(asdict(persisted)), encoding="utf-8")

    with pytest.raises(ApprovalControllerError, match="authenticated|differs"):
        ApprovalController().confirm_delivery(
            episode_id="EP8", artifact_kind="thumbnail", artifact_path=artifact,
            delivery_receipt_path=delivery, approval_receipt_path=approval_path,
            expected_chat_id="authorized-chat", trusted_sender_ids=frozenset({"trusted-user"}),
            inbound_source=FakeAuthenticatedSource(reply),
        )
