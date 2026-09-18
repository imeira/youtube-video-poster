"""Append-only structured production events with a secret-free schema."""

from __future__ import annotations

import json
import os
import re
import threading
import time
from decimal import Decimal
from pathlib import Path

_ALLOWED_FIELDS = frozenset(
    {
        "status",
        "episode",
        "revision",
        "scene",
        "agent",
        "stage",
        "provider",
        "model",
        "version",
        "prompt_hash",
        "seed",
        "resolution",
        "queued_at",
        "started_at",
        "ended_at",
        "worker",
        "attempt",
        "recovery",
        "bytes",
        "cost",
        "result_sha256",
        "error",
        "request_id",
        "category",
        "cache_hit",
    }
)
_URL = re.compile(r"https?://\S+", re.IGNORECASE)
_AUTHORIZATION = re.compile(
    r'''\bauthorization\b\s*[:=]\s*["']?(?:(?:bearer|basic|digest)\s+)?[^"'\s,}\]]+["']?''',
    re.IGNORECASE,
)
_NAMED_CREDENTIAL = re.compile(
    r'''["']?\b(?:token|secret|api[_ -]?key|password|passwd)\b["']?\s*[:=]\s*["']?[^"'\s,}\]]+["']?''',
    re.IGNORECASE,
)
_CREDENTIAL = re.compile(
    r'''\b(?:bearer|basic)\b\s+["']?[^"'\s,}\]]+["']?''',
    re.IGNORECASE,
)
_CREDENTIAL_KEY = re.compile(
    r"^(?:token|secret|api[_ -]?key|authorization|password|passwd)$",
    re.IGNORECASE,
)


def _safe_text(value: str) -> str:
    value = _URL.sub("[REDACTED_URL]", value)
    value = _AUTHORIZATION.sub("[REDACTED_CREDENTIAL]", value)
    value = _NAMED_CREDENTIAL.sub("[REDACTED_CREDENTIAL]", value)
    return _CREDENTIAL.sub("[REDACTED_CREDENTIAL]", value)


def _json_value(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Path):
        return value.name
    if isinstance(value, str):
        return _safe_text(value)
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED_CREDENTIAL]"
                if _CREDENTIAL_KEY.fullmatch(str(key))
                else _json_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class StructuredEventLog:
    """Write only allowlisted operational metadata; prompts and URLs are dropped."""

    def __init__(self, path: Path, *, clock=time.time):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self._lock = threading.Lock()

    def emit(self, **fields) -> dict:
        status = fields.get("status")
        if status not in {"QUEUED", "RUNNING", "COMPLETE", "FAILED"}:
            raise ValueError("event status must be QUEUED/RUNNING/COMPLETE/FAILED")
        event = {
            key: _json_value(value)
            for key, value in fields.items()
            if key in _ALLOWED_FIELDS and value is not None
        }
        event["timestamp"] = self.clock()
        encoded = json.dumps(
            event,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        with self._lock:
            descriptor = os.open(
                self.path,
                os.O_APPEND | os.O_CREAT | os.O_WRONLY,
                0o600,
            )
            try:
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return event
