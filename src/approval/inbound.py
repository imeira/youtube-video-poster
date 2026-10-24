"""Trusted inbound approval boundary.

Only a configured transport adapter may construct an approval reply.  Plain
mappings, paths, and caller supplied strings intentionally cannot satisfy this
interface; without an adapter the approval controller fails closed.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(frozen=True)
class AuthenticatedApprovalReply:
    """A transport-authenticated inbound reply, already verified at the edge."""

    transport: str
    chat_id: str
    sender_id: str
    message_id: int
    reply_to_message_id: int
    text: str

    def __post_init__(self) -> None:
        if (
            not self.transport.strip()
            or not self.chat_id.strip()
            or not self.sender_id.strip()
            or type(self.message_id) is not int
            or self.message_id <= 0
            or type(self.reply_to_message_id) is not int
            or self.reply_to_message_id <= 0
            or not self.text.strip()
        ):
            raise ValueError("authenticated reply has invalid transport bindings")

    def document(self) -> dict[str, object]:
        return asdict(self)


class AuthenticatedApprovalSource(Protocol):
    """Adapter boundary for provider-authenticated inbound replies."""

    def claim_verified_reply(
        self, *, chat_id: str, message_id: int
    ) -> AuthenticatedApprovalReply | None:
        """Atomically claim one fresh verified reply bound to a delivery."""

    def recover_verified_reply(
        self, *, chat_id: str, message_id: int
    ) -> AuthenticatedApprovalReply | None:
        """Read a previously claimed reply after an interrupted local persist."""

    def revalidate_reply(
        self, document: object
    ) -> AuthenticatedApprovalReply | None:
        """Read and authenticate a persisted receipt against external-rooted state."""
