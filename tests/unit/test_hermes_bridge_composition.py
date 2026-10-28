"""TDD contract for the single-owner, in-process Hermes bridge composition."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from src.approval.hermes_inbound_bridge import HermesBridgeConfig


def _raw(*, reply_to: int = 42) -> bytes:
    return json.dumps({"update_id": 1, "message": {
        "message_id": 99, "from": {"id": 20002, "is_bot": False},
        "chat": {"id": 10001, "type": "private"},
        "reply_to_message": {"message_id": reply_to}, "text": "APROVAR THUMBNAIL EP8",
    }}, separators=(",", ":")).encode()


def test_composition_recognizes_only_an_intact_delivery_at_its_active_gate(tmp_path: Path):
    from src.approval.hermes_bridge_composition import HermesApprovalBridgeComposition

    artifact = tmp_path / "thumbnail.png"
    artifact.write_bytes(b"thumbnail")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    service = SimpleNamespace(
        _enabled=True,
        _settings=SimpleNamespace(webhook_secret="studio-webhook", registry_path=tmp_path / "registry.sqlite3"),
        _director=SimpleNamespace(config=SimpleNamespace(episodes_dir=tmp_path / "episodes")),
        _lookup=lambda *, chat_id, message_id: ({
            "episode_id": "EP8", "artifact_kind": "thumbnail", "artifact_path": str(artifact),
            "artifact_sha256": digest,
        } if (chat_id, message_id) == ("10001", 42) else None),
    )
    from src.state.machine import EpisodeState, EpisodeStateStore

    (tmp_path / "episodes" / "EP8").mkdir(parents=True)
    state_path = tmp_path / "episodes" / "EP8" / "state.json"
    state = EpisodeStateStore.load_or_create(state_path, "EP8")
    state.current_state = EpisodeState.WAITING_THUMBNAIL_APPROVAL
    state.save(state_path)

    composition = HermesApprovalBridgeComposition(
        config=HermesBridgeConfig("instance", "bridge-hmac", "10001", frozenset({"20002"})),
        service=service,
    )

    assert composition.recognize(_raw()) is True
    assert composition.recognize(_raw(reply_to=777)) is False
    artifact.write_bytes(b"changed")
    assert composition.recognize(_raw()) is False
