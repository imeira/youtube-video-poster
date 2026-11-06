"""Bounded, filesystem-only deployment read model for actual Jacó evidence.

This publisher never opens either SQLite ledger, never writes a ledger/gateway/media
artifact, and deliberately does not claim CURRENT coordination.  It reports only
what the production evidence files currently prove.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
_SCHEMA = "episode-policy-status/v1"
_OUTPUT_FIELDS = frozenset({
    "schema", "status", "generation", "episode_id", "generated_at_utc", "sources",
    "evidence_binding_sha256", "live_dispatch", "actual_cost_usd", "remaining_usd",
    "financial_status", "financial_observation", "visual_qa", "narrator_final_approved", "identity", "license", "thumbnail", "video",
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

    def _financial(self, *, endpoint: object) -> tuple[str, str, str, str]:
        """Consume only a fresh, hash-bound settlement JSON read model.

        This adapter deliberately does not import or open the SQLite publisher's
        ledger. A bounded observation is evidence of the read time, not a
        writer-coordinated CURRENT claim.
        """
        try:
            snapshot = self._strict((self.root / "status" / "canonical-settlement-receipt.json").read_bytes())
            expected = {
                "schema", "status", "classification", "generation", "observed_at_utc", "outer_request_id",
                "local_request_id", "remote_request_id", "endpoint_id", "auth_scope", "authority_mode",
                "claim_state", "actual_micro_usd", "provenance", "canonical_receipt", "canonical_receipt_sha256",
                "snapshot_sha256",
            }
            text_fields = ("outer_request_id", "local_request_id", "remote_request_id", "endpoint_id", "auth_scope", "authority_mode", "claim_state", "actual_micro_usd", "provenance", "canonical_receipt_sha256", "observed_at_utc")
            if (set(snapshot) != expected or snapshot.get("schema") != "jaco-canonical-settlement-receipt-observed/v1"
                    or type(snapshot.get("generation")) is not int or snapshot["generation"] < 1
                    or any(not isinstance(snapshot.get(key), str) or not snapshot[key] or len(snapshot[key]) > 512 for key in text_fields)
                    or snapshot.get("authority_mode") != "LIVE"):
                raise ValueError("closed settlement observation required")
            claimed = snapshot.pop("snapshot_sha256")
            if (not isinstance(claimed, str) or len(claimed) != 64
                    or any(character not in "0123456789abcdef" for character in claimed)
                    or claimed != self._digest(self._canonical(snapshot))):
                raise ValueError("settlement observation hash invalid")
            observed = datetime.fromisoformat(snapshot["observed_at_utc"])
            now = datetime.now(UTC)
            if observed.tzinfo is None or not timedelta(0) <= now - observed.astimezone(UTC) <= timedelta(minutes=5):
                raise ValueError("settlement observation is stale")
            receipt = snapshot["canonical_receipt"]
            required = {"schema", "authority", "local_request_id", "provider_request_id", "endpoint_id", "auth_scope", "authority_mode", "currency", "cost_total_micro_usd", "evidence_hash", "provenance"}
            bindings = (snapshot["local_request_id"], snapshot["remote_request_id"], snapshot["endpoint_id"], snapshot["auth_scope"], snapshot["authority_mode"])
            if (not isinstance(receipt, dict) or set(receipt) != required or type(receipt.get("schema")) is not int
                    or receipt.get("schema") != 2
                    or not isinstance(receipt.get("evidence_hash"), str)
                    or len(receipt["evidence_hash"]) != 64
                    or any(character not in "0123456789abcdef" for character in receipt["evidence_hash"])
                    or any(not isinstance(receipt.get(key), str) or not receipt[key] or len(receipt[key]) > 512 for key in ("local_request_id", "provider_request_id", "endpoint_id", "auth_scope", "authority_mode"))
                    or self._digest(self._canonical(receipt)) != snapshot["canonical_receipt_sha256"]
                    or snapshot["outer_request_id"] != REQUEST_ID or snapshot["endpoint_id"] != endpoint
                    or snapshot["status"] != "BOUNDED_OBSERVED"
                    or snapshot["classification"] != "BOUNDED_OBSERVED_NO_WRITER_COORDINATION"
                    or snapshot["claim_state"] != "SETTLED" or snapshot["provenance"] != "FAL_BILLING_READBACK"
                    or receipt.get("authority") != "TRANSPORT_AUTHENTICATED_FAL_READBACK"
                    or receipt.get("currency") != "USD" or receipt.get("authority_mode") != "LIVE"
                    or receipt.get("provenance") != snapshot["provenance"]
                    or (receipt.get("local_request_id"), receipt.get("provider_request_id"), receipt.get("endpoint_id"), receipt.get("auth_scope"), receipt.get("authority_mode")) != bindings):
                raise ValueError("settlement receipt binding invalid")
            actual_micro = snapshot["actual_micro_usd"]
            if not isinstance(actual_micro, str) or not actual_micro.isdecimal() or (len(actual_micro) > 1 and actual_micro.startswith("0")) or receipt.get("cost_total_micro_usd") != actual_micro:
                raise ValueError("settlement amount invalid")
            financial = self._strict((self.root / "financial-record-source.json").read_bytes())
            if financial.get("request_hash") != REQUEST_ID or financial.get("status") != "APPROVED_USD10":
                raise ValueError("financial authority binding invalid")
            cap = Decimal(financial["total_episode_cap"])
            actual = Decimal(actual_micro) / Decimal(1_000_000)
            if not cap.is_finite() or cap < actual:
                raise ValueError("financial amount exceeds validated cap")
            return "SETTLED", "BOUNDED_OBSERVED", format(actual, "f"), format(cap - actual, "f")
        except (OSError, ValueError, TypeError, KeyError, InvalidOperation, UnicodeDecodeError, json.JSONDecodeError):
            return "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN"

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
            financial_status, financial_observation, actual_cost, remaining = self._financial(
                endpoint=packet.get("bindings", {}).get("endpoint")
            )
            document = {
                "schema": _SCHEMA, "status": "BLOCKED_DELIVERY_READINESS", "generation": self._generation(),
                "episode_id": "JACO_GENESIS28", "generated_at_utc": datetime.now(UTC).isoformat(),
                "sources": sources, "evidence_binding_sha256": self._digest(self._canonical(sources)),
                "live_dispatch": False, "actual_cost_usd": actual_cost, "remaining_usd": remaining,
                "financial_status": financial_status, "financial_observation": financial_observation,
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
            if (type(document.get("generation")) is not int or document["generation"] < 1
                    or type(document.get("live_dispatch")) is not bool or document["live_dispatch"] is not False
                    or type(document.get("pending_human_approvals")) is not bool or document["pending_human_approvals"] is not False
                    or document.get("status") != "BLOCKED_DELIVERY_READINESS"
                    or document.get("episode_id") != "JACO_GENESIS28"
                    or not isinstance(document.get("generated_at_utc"), str)
                    or not isinstance(document.get("sources"), dict)
                    or set(document["sources"]) != {"user-mandate.json", "execution/packet.json", "execution/provider-receipt.json", "execution/qa/provider-receipt.visual-qa.v1.json", "execution/provider-receipt.quarantine.png"}
                    or any(not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value) for value in document["sources"].values())
                    or not isinstance(document.get("evidence_binding_sha256"), str)
                    or document["evidence_binding_sha256"] != self._digest(self._canonical(document["sources"]))):
                raise ValueError("status values are invalid")
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
