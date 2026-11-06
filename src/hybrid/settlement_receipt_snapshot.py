"""Read-only export of a verified canonical settlement receipt from SQLite.

This is deliberately a distinct publisher action.  It opens the standing ledger
read-only, reads one SQLite snapshot transaction, validates the immutable receipt,
and atomically writes a bounded observation JSON read model.  Status consumers
must read only that JSON file and never open the ledger.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

OUTER_REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
_SCHEMA = "jaco-canonical-settlement-receipt-observed/v1"
_FIELDS = frozenset({
    "schema", "status", "classification", "generation", "observed_at_utc",
    "outer_request_id", "local_request_id", "remote_request_id", "endpoint_id",
    "auth_scope", "authority_mode", "claim_state", "actual_micro_usd",
    "provenance", "canonical_receipt", "canonical_receipt_sha256", "snapshot_sha256",
})


class SettlementReceiptSnapshotPublisher:
    """Publish bounded observed evidence; it never mutates a ledger or queue."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.ledger = self.root / "ledger" / "standing-budget.sqlite3"
        self.output = self.root / "status" / "canonical-settlement-receipt.json"

    @staticmethod
    def _canonical(value: object) -> bytes:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")

    @staticmethod
    def _strict(raw: bytes) -> dict[str, Any]:
        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate JSON key")
                result[key] = value
            return result
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-finite JSON")))
        if not isinstance(value, dict):
            raise TypeError("JSON object required")
        return value

    @classmethod
    def _digest(cls, value: object) -> str:
        return sha256(cls._canonical(value)).hexdigest()

    def _generation(self) -> int:
        try:
            previous = self._strict(self.output.read_bytes())
            value = previous.get("generation")
            return value + 1 if type(value) is int and value >= 1 else 1
        except (OSError, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return 1

    def _atomic(self, document: dict[str, Any]) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("wb", dir=self.output.parent, prefix=f".{self.output.name}.", delete=False) as stream:
            stream.write(self._canonical(document))
            stream.flush()
            os.fsync(stream.fileno())
            temporary = Path(stream.name)
        try:
            os.replace(temporary, self.output)
        finally:
            temporary.unlink(missing_ok=True)

    def _outer_binding(self) -> tuple[str, str]:
        packet = self._strict((self.root / "execution" / "packet.json").read_bytes())
        admission = self._strict((self.root / "final-admission-authorization.json").read_bytes())
        packet_binding = packet.get("bindings")
        if (not isinstance(packet_binding, dict)
                or packet_binding.get("request_id") != OUTER_REQUEST_ID
                or admission.get("request_id") != OUTER_REQUEST_ID):
            raise ValueError("outer request binding is foreign")
        endpoint = packet_binding.get("endpoint")
        if not isinstance(endpoint, str) or not endpoint or admission.get("endpoint") != endpoint:
            raise ValueError("outer request endpoint binding is invalid")
        return OUTER_REQUEST_ID, endpoint

    def _read_receipt(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.ledger.is_file():
            raise ValueError("ledger is unavailable")
        uri = f"file:{self.ledger.resolve().as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute("BEGIN")
            rows = db.execute(
                "SELECT c.request_id, c.provider, c.state, c.actual, c.auth_scope, c.provider_request_id, "
                "c.authority_mode, s.receipt_sha256, s.actual AS receipt_actual, s.receipt_body, s.provenance "
                "FROM claims AS c JOIN settlement_receipts AS s ON s.request_id=c.request_id "
                "WHERE c.state='SETTLED'"
            ).fetchall()
        if len(rows) != 1:
            raise ValueError("exactly one settled canonical receipt is required")
        row = dict(rows[0])
        receipt_body = row["receipt_body"]
        if not isinstance(receipt_body, str):
            raise ValueError("canonical receipt body is invalid")
        receipt = self._strict(receipt_body.encode("utf-8"))
        canonical_body = self._canonical(receipt).decode("utf-8")
        required = {"schema", "authority", "local_request_id", "provider_request_id", "endpoint_id", "auth_scope", "authority_mode", "currency", "cost_total_micro_usd", "evidence_hash", "provenance"}
        if (set(receipt) != required or receipt.get("schema") != 2 or canonical_body != receipt_body
                or not isinstance(row["receipt_sha256"], str)
                or sha256(receipt_body.encode("utf-8")).hexdigest() != row["receipt_sha256"]):
            raise ValueError("canonical receipt integrity is invalid")
        if (receipt.get("authority") != "TRANSPORT_AUTHENTICATED_FAL_READBACK"
                or receipt.get("currency") != "USD" or receipt.get("provenance") != "FAL_BILLING_READBACK"
                or receipt.get("provenance") != row["provenance"]):
            raise ValueError("canonical receipt provenance is invalid")
        amount = receipt.get("cost_total_micro_usd")
        if not isinstance(amount, str) or not amount.isascii() or not amount.isdecimal() or (len(amount) > 1 and amount.startswith("0")):
            raise ValueError("canonical receipt amount is invalid")
        if (receipt["local_request_id"], receipt["provider_request_id"], receipt["endpoint_id"], receipt["auth_scope"], receipt["authority_mode"], int(amount)) != (
                row["request_id"], row["provider_request_id"], row["provider"], row["auth_scope"], row["authority_mode"], row["actual"]):
            raise ValueError("canonical receipt does not bind settled claim")
        if row["receipt_actual"] != row["actual"] or row["actual"] < 0:
            raise ValueError("settled actual is inconsistent")
        return row, receipt

    def publish(self) -> dict[str, Any]:
        try:
            outer_request_id, outer_endpoint = self._outer_binding()
            row, receipt = self._read_receipt()
            if receipt["endpoint_id"] != outer_endpoint:
                raise ValueError("receipt endpoint is foreign to outer request")
            document: dict[str, Any] = {
                "schema": _SCHEMA, "status": "BOUNDED_OBSERVED", "classification": "BOUNDED_OBSERVED_NO_WRITER_COORDINATION",
                "generation": self._generation(), "observed_at_utc": datetime.now(UTC).isoformat(),
                "outer_request_id": outer_request_id, "local_request_id": row["request_id"],
                "remote_request_id": row["provider_request_id"], "endpoint_id": row["provider"],
                "auth_scope": row["auth_scope"], "authority_mode": row["authority_mode"], "claim_state": row["state"],
                "actual_micro_usd": str(row["actual"]), "provenance": row["provenance"],
                "canonical_receipt": receipt, "canonical_receipt_sha256": row["receipt_sha256"],
            }
            document["snapshot_sha256"] = self._digest(document)
        except (OSError, sqlite3.Error, ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            return {"schema": _SCHEMA, "status": "STALE", "classification": "BOUNDED_OBSERVED_NO_WRITER_COORDINATION"}
        self._atomic(document)
        return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args(argv)
    print(json.dumps(SettlementReceiptSnapshotPublisher(args.root).publish(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
