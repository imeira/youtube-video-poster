"""Single fail-closed canonical status projection for the Jacó request."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, ClassVar

from src.hybrid.jaco_asset_integration import EPISODE, REQUEST_ID, REVISION


class CanonicalStatusConsumer:
    """Filesystem-only consumer for the producer's immutable JSON projection."""

    @staticmethod
    def inspect(path: Path | str) -> dict[str, Any]:
        target = Path(path)
        if not target.is_file():
            return {"status": "STALE", "reason": "MISSING"}
        try:
            raw = target.read_bytes()
            document = CanonicalStatusProducer._strict_json(raw)
            claimed = document.pop("snapshot_sha256")
            if (
                document.get("schema") != "jaco-canonical-request-status/v1"
                or not isinstance(claimed, str)
                or claimed != sha256(CanonicalStatusProducer._canonical(document)).hexdigest()
            ):
                raise ValueError("invalid canonical status snapshot")
            document["snapshot_sha256"] = claimed
            return document
        except (OSError, UnicodeDecodeError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            return {"status": "STALE", "reason": "INVALID"}


class CanonicalStatusProducer:
    """Join immutable source declarations into one non-authoritative status file."""

    _REQUIRED: ClassVar = {
        "manifest": {"schema": "manifest/v1", "fields": {"assets"}},
        "policy_qa": {"schema": "policy-qa/v1", "fields": {"verdict"}},
        "ledger": {"schema": "ledger/v1", "fields": {"actual_cost_usd", "pending_cost_usd"}},
        "provider_eligibility": {"schema": "provider-eligibility/v1", "fields": {"status"}},
    }

    def __init__(self, factory_root: Path | str, *, max_source_age_seconds: int = 300):
        self.factory_root = Path(factory_root)
        if type(max_source_age_seconds) is not int or max_source_age_seconds <= 0:
            raise ValueError("positive source age is required")
        self.max_source_age_seconds = max_source_age_seconds

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

    @staticmethod
    def _strict_json(raw: bytes) -> dict[str, Any]:
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate source JSON key")
                result[key] = value
            return result

        def reject_constant(_value):
            raise ValueError("non-finite source JSON number")

        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise TypeError("source JSON object required")
        return value

    def _source(self, name: str, declaration: object, *, now: datetime | None) -> dict[str, Any]:
        if not isinstance(declaration, dict) or set(declaration) != {"path", "sha256"}:
            raise ValueError(f"{name} source declaration is closed")
        path_value, expected = declaration["path"], declaration["sha256"]
        if not isinstance(path_value, str) or not isinstance(expected, str) or len(expected) != 64:
            raise ValueError(f"{name} source hash declaration is required")
        path = Path(path_value)
        if not path.is_file():
            raise ValueError(f"{name} source is missing")
        raw = path.read_bytes()
        actual = sha256(raw).hexdigest()
        if actual != expected:
            raise ValueError(f"{name} source hash mismatch")
        document = self._strict_json(raw)
        expected_contract = self._REQUIRED[name]
        if document.get("schema") != expected_contract["schema"]:
            raise ValueError(f"{name} source schema is invalid")
        if not expected_contract["fields"].issubset(document):
            raise ValueError(f"{name} source missing required field")
        scope = document.get("scope")
        expected_scope = {"request_id": REQUEST_ID, "active_revision": REVISION, "episode": EPISODE}
        if not isinstance(scope, dict) or set(scope) != set(expected_scope):
            raise ValueError(f"{name} source scope missing required field")
        if scope.get("request_id") != REQUEST_ID or scope.get("episode") != EPISODE:
            raise ValueError(f"{name} source has foreign scope")
        if scope.get("active_revision") != REVISION:
            raise ValueError("source scope conflict")
        generated = document.get("generated_at_utc")
        if generated is not None:
            if not isinstance(generated, str):
                raise ValueError(f"{name} source timestamp invalid")
            try:
                instant = datetime.fromisoformat(generated)
            except ValueError as error:
                raise ValueError(f"{name} source timestamp invalid") from error
            current = datetime.now(UTC) if now is None else now
            if instant.tzinfo is None or current.tzinfo is None or not 0 <= (current.astimezone(UTC) - instant.astimezone(UTC)).total_seconds() <= self.max_source_age_seconds:
                raise ValueError(f"{name} source is stale")
        return {"path": str(path), "sha256": actual, "schema": document["schema"], "document": document}

    def project(self, sources: dict[str, object], *, generated_at: datetime | None = None, now: datetime | None = None) -> dict[str, Any]:
        if not isinstance(sources, dict) or set(sources) != set(self._REQUIRED):
            raise ValueError("all canonical sources are required")
        loaded = {name: self._source(name, sources[name], now=now) for name in sorted(self._REQUIRED)}
        provider = loaded["provider_eligibility"]["document"]["status"]
        policy = loaded["policy_qa"]["document"]["verdict"]
        if provider != "ELIGIBLE" or policy != "PASS":
            canonical_status = "BLOCKED"
        else:
            canonical_status = "PENDING_READBACK_REQUIRED"
        instant = datetime.now(UTC) if generated_at is None else generated_at
        if instant.tzinfo is None:
            raise ValueError("generated timestamp must be timezone-aware")
        projection = {
            "schema": "jaco-canonical-request-status/v1",
            "scope": {"request_id": REQUEST_ID, "active_revision": REVISION, "episode": EPISODE},
            "canonical_status": canonical_status,
            "authority": "LOCAL_STATUS_ONLY",
            "generated_at_utc": instant.astimezone(UTC).isoformat(),
            "sources": {
                name: {key: value for key, value in loaded[name].items() if key != "document"}
                for name in sorted(loaded)
            },
        }
        projection["snapshot_sha256"] = sha256(self._canonical(projection)).hexdigest()
        return projection

    def publish(self, sources: dict[str, object]) -> dict[str, str]:
        document = self.project(sources)
        # Reuse the established Jacó integration root; do not create a second
        # PREPRODUCTION/requests authority for the same literal request identity.
        root = self.factory_root / "requests" / REQUEST_ID / "jaco-asset-integration"
        root.mkdir(parents=True, exist_ok=True)
        target = root / "canonical-status.json"
        with NamedTemporaryFile("wb", dir=root, prefix=".canonical-status.", delete=False) as stream:
            stream.write(self._canonical(document)); stream.flush(); os.fsync(stream.fileno()); temporary = Path(stream.name)
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        # Keep historic PREPRODUCTION status immutable; point legacy readers at this sole projection.
        historic = self.factory_root / "PREPRODUCTION" / REQUEST_ID
        if historic.is_dir():
            pointer = historic / "canonical-status-pointer.json"
            pointer_document = {"schema": "canonical-status-pointer/v1", "canonical_status_path": str(target), "canonical_status_sha256": sha256(target.read_bytes()).hexdigest()}
            with NamedTemporaryFile("wb", dir=historic, prefix=".canonical-pointer.", delete=False) as stream:
                stream.write(self._canonical(pointer_document)); stream.flush(); os.fsync(stream.fileno()); temporary = Path(stream.name)
            try:
                os.replace(temporary, pointer)
            finally:
                temporary.unlink(missing_ok=True)
        return {"path": str(target), "sha256": sha256(target.read_bytes()).hexdigest()}
