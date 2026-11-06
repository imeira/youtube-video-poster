"""Read-only policy-aware projection over existing episode evidence."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, ClassVar

from src.hybrid.locks import reject_reparse_path


class EpisodePolicyStatusProjection:
    """Join pinned JSON evidence without becoming an execution authority."""

    _CONFIG_FIELDS: ClassVar[frozenset[str]] = frozenset({"schema", "episode_id", "max_age_seconds", "sources"})
    _SOURCE_NAMES: ClassVar[frozenset[str]] = frozenset({"policy", "queue", "runner", "qa", "narrative", "assets", "billing"})
    _OUTPUT_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema", "status", "generation", "episode_id", "generated_at_utc", "sources",
        "evidence_binding_sha256", "live_dispatch", "actual_cost_usd", "remaining_usd",
        "visual_qa", "narrator_final_approved", "identity", "license", "thumbnail", "video",
        "publication", "pending_human_approvals", "snapshot_sha256",
    })

    def __init__(self, config_path: Path | str, output_path: Path | str, *, now=None) -> None:
        self.config_path = Path(config_path)
        self.output_path = Path(output_path)
        self._now = now or (lambda: datetime.now(UTC))

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

    @classmethod
    def _strict_json(cls, raw: bytes) -> dict[str, Any]:
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result

        def constant(_value):
            raise ValueError("non-finite JSON number")

        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(value, dict):
            raise TypeError("JSON object required")
        return value

    @staticmethod
    def _sha256(value: bytes) -> str:
        return sha256(value).hexdigest()

    def _read_pinned(self, path: Path, expected: str) -> tuple[dict[str, Any], str]:
        if not isinstance(expected, str) or len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
            raise ValueError("source hash is invalid")
        checked = reject_reparse_path(path)
        if not checked.is_absolute() or not checked.is_file():
            raise ValueError("source must be an absolute regular file")
        raw = checked.read_bytes()
        digest = self._sha256(raw)
        if digest != expected:
            raise ValueError("source bytes differ from hash pin")
        return self._strict_json(raw), digest

    def _load(self) -> tuple[dict[str, Any], dict[str, tuple[dict[str, Any], str]]]:
        config, _ = self._read_pinned(self.config_path, self._sha256(reject_reparse_path(self.config_path).read_bytes()))
        if set(config) != self._CONFIG_FIELDS or config.get("schema") != "episode-policy-status-config/v1":
            raise ValueError("closed deployment projection config is required")
        episode, age, declarations = config.get("episode_id"), config.get("max_age_seconds"), config.get("sources")
        if not isinstance(episode, str) or not episode or type(age) is not int or age <= 0 or not isinstance(declarations, dict) or set(declarations) != self._SOURCE_NAMES:
            raise ValueError("projection config is malformed")
        loaded = {}
        for name in sorted(self._SOURCE_NAMES):
            declaration = declarations[name]
            if not isinstance(declaration, dict) or set(declaration) != {"path", "sha256"} or not isinstance(declaration["path"], str):
                raise ValueError("source declaration is malformed")
            document, digest = self._read_pinned(Path(declaration["path"]), declaration["sha256"])
            if document.get("episode_id") != episode:
                raise ValueError("foreign or cross-request episode source")
            loaded[name] = (document, digest)
        queue = loaded["queue"][0]
        generated = queue.get("generated_at_utc")
        if not isinstance(generated, str):
            raise TypeError("queue timestamp is missing")
        stamp, now = datetime.fromisoformat(generated), self._now()
        if stamp.tzinfo is None or now.tzinfo is None or not 0 <= (now.astimezone(UTC) - stamp.astimezone(UTC)).total_seconds() <= age:
            raise ValueError("queue snapshot is stale or future")
        return config, loaded

    @staticmethod
    def _status(loaded: dict[str, tuple[dict[str, Any], str]]) -> str:
        policy, queue, runner = loaded["policy"][0], loaded["queue"][0], loaded["runner"][0]
        if queue.get("status") == "RUNNING" or runner.get("state") in {"RUNNING", "UNKNOWN", "UNKNOWN_REQUIRES_RECONCILIATION"}:
            return "UNKNOWN_REQUIRES_RECONCILIATION"
        if queue.get("status") == "WAITING":
            return "WAITING_HUMAN_GATE"
        if policy.get("mode") == "LIVE" and policy.get("external_trust") is not True:
            return "BLOCKED_QUALIFICATION"
        if queue.get("status") == "PENDING" and policy.get("mode") == "TEST":
            return "TEST_ELIGIBLE"
        return "BLOCKED_POLICY"

    def _generation(self) -> int:
        try:
            document = self._strict_json(reject_reparse_path(self.output_path).read_bytes())
            if set(document) == self._OUTPUT_FIELDS and type(document.get("generation")) is int and document["generation"] >= 1:
                return document["generation"] + 1
        except (OSError, ValueError, UnicodeDecodeError):
            pass
        return 1

    def _stale(self) -> dict[str, Any]:
        return {"schema": "episode-policy-status/v1", "status": "STALE", "live_dispatch": False, "pending_human_approvals": True}

    def publish(self) -> dict[str, Any]:
        try:
            config, loaded = self._load()
            status = self._status(loaded)
            billing, qa, narrative, assets = (loaded[name][0] for name in ("billing", "qa", "narrative", "assets"))
            binding = {name: digest for name, (_document, digest) in sorted(loaded.items())}
            document = {
                "schema": "episode-policy-status/v1", "status": status, "generation": self._generation(),
                "episode_id": config["episode_id"], "generated_at_utc": self._now().astimezone(UTC).isoformat(),
                "sources": binding, "evidence_binding_sha256": self._sha256(self._canonical(binding)),
                "live_dispatch": False,
                "actual_cost_usd": billing.get("actual_cost_usd") if billing.get("authenticated") is True else "UNKNOWN",
                "remaining_usd": billing.get("remaining_usd") if billing.get("authenticated") is True else "UNKNOWN",
                "visual_qa": qa.get("verdict"), "narrator_final_approved": narrative.get("final_approved"),
                "identity": assets.get("identity"), "license": assets.get("license"), "thumbnail": assets.get("thumbnail"),
                "video": assets.get("video"), "publication": assets.get("publication"),
                "pending_human_approvals": True,
            }
            document["snapshot_sha256"] = self._sha256(self._canonical(document))
        except (OSError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return self._stale()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        marker = {"schema": "episode-policy-status/v1", "status": "WRITER_IN_PROGRESS", "live_dispatch": False, "pending_human_approvals": True}
        self._atomic(marker)
        self._atomic(document)
        return document

    def _atomic(self, document: dict[str, Any]) -> None:
        with NamedTemporaryFile("wb", dir=self.output_path.parent, prefix=self.output_path.name + ".", delete=False) as output:
            output.write(self._canonical(document)); output.flush(); os.fsync(output.fileno()); temporary = Path(output.name)
        try:
            os.replace(temporary, self.output_path)
        finally:
            temporary.unlink(missing_ok=True)

    def inspect(self) -> dict[str, Any]:
        try:
            raw = reject_reparse_path(self.output_path).read_bytes()
            document = self._strict_json(raw)
            if set(document) != self._OUTPUT_FIELDS or document.get("schema") != "episode-policy-status/v1":
                raise ValueError
            claimed = document["snapshot_sha256"]
            body = {key: value for key, value in document.items() if key != "snapshot_sha256"}
            if not isinstance(claimed, str) or claimed != self._sha256(self._canonical(body)) or document["live_dispatch"] is not False or type(document["pending_human_approvals"]) is not bool:
                raise ValueError
            return document
        except (OSError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return self._stale()
