"""Closed, TEST-only publication-plan contract for the two-gate runner."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA = "two-gate-publication/v2"


class PublicationPublisher(Protocol):
    mode: str

    def upload_private(self, package: dict[str, Any]) -> dict[str, Any]: ...
    def configure(self, video_id: str, package: dict[str, Any]) -> dict[str, Any]: ...
    def readback(self, video_id: str) -> dict[str, Any]: ...
    def release(self, video_id: str, package: dict[str, Any]) -> dict[str, Any]: ...
    def recover_upload(self, package: dict[str, Any]) -> dict[str, Any] | None: ...


def validate_plan(value: dict[str, Any], *, episode_id: str, channel: str) -> dict[str, Any]:
    """Return an immutable-by-convention normalized v2 plan or fail closed."""
    if not isinstance(value, dict) or set(value) != {
        "schema", "episode_id", "channel", "playlist_id", "visibility", "publish_at", "timezone"
    }:
        raise ValueError("publication plan must use the closed v2 schema")
    if value.get("schema") != SCHEMA or value.get("episode_id") != episode_id:
        raise ValueError("publication plan schema or episode binding is invalid")
    if value.get("channel") != channel:
        raise ValueError("publication plan channel does not match policy")
    if not all(isinstance(value.get(key), str) and value[key].strip() for key in ("channel", "playlist_id", "timezone")):
        raise ValueError("publication plan requires channel, playlist_id and timezone")
    visibility = value.get("visibility")
    publish_at = value.get("publish_at")
    if visibility not in {"public", "scheduled"}:
        raise ValueError("publication plan visibility must be public or scheduled")
    if visibility == "public" and publish_at is not None:
        raise ValueError("public publication plans cannot specify publish_at")
    if visibility == "scheduled":
        if not isinstance(publish_at, str):
            raise ValueError("scheduled publication plans require an ISO publish_at")
        try:
            parsed = datetime.fromisoformat(publish_at)
        except ValueError as error:
            raise ValueError("scheduled publish_at must be ISO-8601") from error
        if parsed.tzinfo is None:
            raise ValueError("scheduled publish_at must include an offset")
    try:
        ZoneInfo(value["timezone"])
    except ZoneInfoNotFoundError as error:
        raise ValueError("publication plan timezone is unknown") from error
    return json.loads(json.dumps(value, sort_keys=True))
