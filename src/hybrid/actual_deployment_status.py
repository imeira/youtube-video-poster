"""Bounded, filesystem-only deployment read model for actual Jacó evidence.

This publisher never opens either SQLite ledger, never writes a ledger/gateway/media
artifact, and deliberately does not claim CURRENT coordination.  It reports only
what the production evidence files currently prove.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
_SCHEMA = "episode-policy-status/v1"
_OUTPUT_FIELDS = frozenset({
    "schema", "status", "generation", "episode_id", "generated_at_utc", "sources",
    "evidence_binding_sha256", "live_dispatch", "actual_cost_usd", "remaining_usd",
    "visual_qa", "narrator_final_approved", "identity", "license", "thumbnail", "video",
    "publication", "pending_human_approvals", "snapshot_sha256",
})


class ActualDeploymentStatus:
    """Publish a non-authoritative status from immutable production evidence."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.output = self.root / "status" / "actual-deployment-status.json"
        self.inventory = self.root / "status" / "actual-deployment-status-sources.json"

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")

    @staticmethod
    def _strict(raw: bytes) -> dict[str, Any]:
        def pairs(items):
            out = {}
            for key, value in items:
                if key in out:
                    raise ValueError("duplicate JSON key")
                out[key] = value
            return out
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite")))
        if not isinstance(result, dict):
            raise TypeError("JSON object required")
        return result

    @staticmethod
    def _digest(raw: bytes) -> str:
        return sha256(raw).hexdigest()

    def _read(self, relative: str) -> tuple[Path, bytes, dict[str, Any] | None]:
        path = self.root / relative
        raw = path.read_bytes()
        return path, raw, self._strict(raw) if path.suffix == ".json" else None

    def _generation(self) -> int:
        prior = self.inspect()
        value = prior.get("generation")
        return value + 1 if type(value) is int and value >= 1 else 1

    def _atomic(self, path: Path, document: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("wb", dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
            stream.write(self._canonical(document)); stream.flush(); os.fsync(stream.fileno()); temporary = Path(stream.name)
        try:
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _stale(self) -> dict[str, Any]:
        return {"schema": _SCHEMA, "status": "STALE", "live_dispatch": False, "pending_human_approvals": False}

    def publish(self) -> dict[str, Any]:
        try:
            loaded = {name: self._read(name) for name in (
                "user-mandate.json", "execution/packet.json", "execution/provider-receipt.json",
                "execution/qa/provider-receipt.visual-qa.v1.json", "execution/provider-receipt.quarantine.png",
            )}
            mandate = loaded["user-mandate.json"][2]
            packet = loaded["execution/packet.json"][2]
            receipt = loaded["execution/provider-receipt.json"][2]
            qa = loaded["execution/qa/provider-receipt.visual-qa.v1.json"][2]
            assert mandate is not None and packet is not None and receipt is not None and qa is not None
            if mandate.get("request_id") != REQUEST_ID or packet.get("bindings", {}).get("request_id") != REQUEST_ID:
                raise ValueError("foreign request evidence")
            png_digest = self._digest(loaded["execution/provider-receipt.quarantine.png"][1])
            if receipt.get("sha256") != png_digest:
                raise ValueError("PNG receipt binding differs from source bytes")
            sources = {name: self._digest(raw) for name, (_path, raw, _doc) in loaded.items()}
            inventory = {
                "schema": "actual-deployment-status-sources/v1", "classification": "BOUNDED_OBSERVED_NO_WRITER_COORDINATION",
                "request_id": REQUEST_ID,
                "sources": {name: {"path": str(path.resolve()), "sha256": sources[name]} for name, (path, _raw, _doc) in loaded.items()},
            }
            inventory["inventory_sha256"] = self._digest(self._canonical(inventory))
            # The receipt explicitly says billing is unreconciled.  Never convert a
            # ceiling, claimed receipt, or balance calculation into actual spend.
            document = {
                "schema": _SCHEMA, "status": "BLOCKED_DELIVERY_READINESS", "generation": self._generation(),
                "episode_id": "JACO_GENESIS28", "generated_at_utc": datetime.now(UTC).isoformat(),
                "sources": sources, "evidence_binding_sha256": self._digest(self._canonical(sources)),
                "live_dispatch": False, "actual_cost_usd": "UNKNOWN", "remaining_usd": "UNKNOWN",
                "visual_qa": qa.get("verdict"), "narrator_final_approved": "UNKNOWN",
                "identity": "UNKNOWN", "license": mandate.get("license_status", "UNKNOWN"),
                "thumbnail": "UNKNOWN", "video": "UNKNOWN", "publication": "UNKNOWN",
                "pending_human_approvals": False,
            }
            document["snapshot_sha256"] = self._digest(self._canonical(document))
        except (AssertionError, OSError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return self._stale()
        self._atomic(self.output, document)
        self._atomic(self.inventory, inventory)
        return document

    def inspect(self) -> dict[str, Any]:
        try:
            document = self._strict(self.output.read_bytes())
            if set(document) != _OUTPUT_FIELDS or document.get("schema") != _SCHEMA:
                raise ValueError("closed status schema required")
            claimed = document["snapshot_sha256"]
            body = {key: value for key, value in document.items() if key != "snapshot_sha256"}
            if not isinstance(claimed, str) or claimed != self._digest(self._canonical(body)):
                raise ValueError("invalid status hash")
            return document
        except (OSError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return {"status": "STALE", "reason": "INVALID"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--inspect", action="store_true")
    args = parser.parse_args(argv)
    status = ActualDeploymentStatus(args.root)
    print(json.dumps(status.inspect() if args.inspect else status.publish(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
