"""Composition-owned final admission for the one Jacó FLUX 3 canary.

This validates immutable local evidence only.  It does not reserve, construct a
provider, read credentials, or submit a request; those effects remain behind the
existing executor/ledger kernel after this receipt has been consumed.
"""
from __future__ import annotations

import json
import math
import stat
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
ENDPOINT = "blackforestlabs/flux-3/text-to-image"
MAX_USD = "0.048"
MAX_AGE_SECONDS = 300


class FinalAdmissionError(PermissionError):
    """Evidence cannot admit a paid FAL submission."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise FinalAdmissionError("duplicate JSON key")
        result[key] = value
    return result


def _read(path: Path | str, label: str) -> tuple[dict[str, Any], str]:
    candidate = Path(path)
    try:
        info = candidate.lstat()
        if candidate.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise FinalAdmissionError(f"{label} must not be a reparse point")
        raw = candidate.read_bytes()
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
            parse_constant=lambda _: (_ for _ in ()).throw(FinalAdmissionError("non-finite JSON value")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FinalAdmissionError(f"{label} is not strict readable JSON") from error
    if not isinstance(document, dict):
        raise FinalAdmissionError(f"{label} must be an object")
    return document, sha256(raw).hexdigest()


def _fresh(value: object, now: float, label: str) -> None:
    if type(value) is not int or not math.isfinite(now) or value > now or now - value > MAX_AGE_SECONDS:
        raise FinalAdmissionError(f"{label} is stale")


def validate_final_admission(*, packet: Path | str, review: Path | str, authorization: Path | str,
                             quote: Path | str, clock=time.time) -> dict[str, str | int]:
    """Return a hash-bound, non-effecting receipt for exactly one submit attempt."""
    now = clock()
    packet_doc, packet_sha = _read(packet, "packet")
    review_doc, review_sha = _read(review, "local review")
    authorization_doc, authorization_sha = _read(authorization, "final admission authorization")
    quote_doc, quote_sha = _read(quote, "price snapshot")
    if set(packet_doc) != {"endpoint", "payload_sha256", "maximum_usd"} or packet_doc.get("endpoint") != ENDPOINT or packet_doc.get("maximum_usd") != MAX_USD or not isinstance(packet_doc.get("payload_sha256"), str) or len(packet_doc["payload_sha256"]) != 64:
        raise FinalAdmissionError("closed canary packet is required")
    if (review_doc.get("schema") != 1 or review_doc.get("kind") != "LOCAL_PROCESS_REVIEW_OUTPUT"
            or review_doc.get("verdict") != "PASS" or review_doc.get("admission_state") != "READY_FOR_PARENT_VERIFICATION_ONLY"
            or review_doc.get("request_id") != REQUEST_ID or review_doc.get("endpoint") != ENDPOINT
            or review_doc.get("packet_sha256") != packet_sha):
        raise FinalAdmissionError("local review does not bind the exact packet")
    _fresh(review_doc.get("reviewed_at_epoch"), now, "local review")
    required_authorization = {"schema", "kind", "version", "request_id", "endpoint", "payload_sha256", "maximum_usd", "call_limit", "user_authorization_evidence"}
    if (set(authorization_doc) != required_authorization or authorization_doc.get("schema") != 1
            or authorization_doc.get("kind") != "JACO_FLUX3_FINAL_ADMISSION_AUTHORIZATION"
            or not isinstance(authorization_doc.get("version"), str) or not authorization_doc["version"]
            or authorization_doc.get("request_id") != REQUEST_ID or authorization_doc.get("endpoint") != ENDPOINT
            or authorization_doc.get("payload_sha256") != packet_doc["payload_sha256"]
            or authorization_doc.get("maximum_usd") != MAX_USD or authorization_doc.get("call_limit") != 1
            or authorization_doc.get("user_authorization_evidence") != "USER_AUTHORIZED_LOCAL_QA_AND_ONE_ALTERNATIVE_IMAGE_EVAL_WITHIN_USD10"):
        raise FinalAdmissionError("final admission authorization does not bind this canary")
    required_quote = {"schema", "endpoint_id", "currency", "maximum_usd", "unit", "unit_price_usd", "observed_at_epoch", "provider_expiry", "price_validity"}
    if (set(quote_doc) != required_quote or quote_doc.get("schema") != "FAL_AUTHENTICATED_CATALOG_PRICE_SNAPSHOT/1"
            or quote_doc.get("endpoint_id") != ENDPOINT or quote_doc.get("currency") != "USD"
            or quote_doc.get("maximum_usd") != MAX_USD or quote_doc.get("unit") != "megapixels"
            or quote_doc.get("unit_price_usd") != MAX_USD or quote_doc.get("provider_expiry") is not None
            or quote_doc.get("price_validity") != "CATALOG_PRICE_NOT_TRANSACTION_QUOTE"):
        raise FinalAdmissionError("fresh authenticated price changed")
    _fresh(quote_doc.get("observed_at_epoch"), now, "price snapshot")
    receipt: dict[str, str | int] = {"schema": 1, "kind": "JACO_FLUX3_FINAL_ADMISSION", "request_id": REQUEST_ID,
        "endpoint": ENDPOINT, "reserved_usd": MAX_USD, "call_limit": 1, "packet_sha256": packet_sha,
        "review_sha256": review_sha, "authorization_sha256": authorization_sha, "quote_sha256": quote_sha,
        "authorization_version": authorization_doc["version"], "validated_at_epoch": int(now)}
    receipt["receipt_sha256"] = sha256(_canonical(receipt)).hexdigest()
    return receipt


def prepare_execution_intent(receipt: dict[str, str | int], path: Path | str) -> dict[str, str | int]:
    """Consume one verified final admission into an fsynced write-once intent.

    Reservation and Executor submission deliberately happen after this durable
    barrier. A crash or timeout after it is an ambiguous one-shot state, never a
    reason to overwrite the intent or make another queue POST.
    """
    required = {"schema", "kind", "request_id", "endpoint", "reserved_usd", "call_limit", "packet_sha256", "review_sha256", "authorization_sha256", "quote_sha256", "authorization_version", "validated_at_epoch", "receipt_sha256"}
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise FinalAdmissionError("closed final admission receipt required")
    body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if receipt.get("receipt_sha256") != sha256(_canonical(body)).hexdigest() or receipt.get("request_id") != REQUEST_ID or receipt.get("endpoint") != ENDPOINT or receipt.get("reserved_usd") != MAX_USD or receipt.get("call_limit") != 1:
        raise FinalAdmissionError("final admission receipt is invalid")
    target = Path(path)
    intent = {"schema": 1, "kind": "JACO_FLUX3_EXECUTION_INTENT", "state": "PREPARED", "request_id": REQUEST_ID,
              "endpoint": ENDPOINT, "reserved_usd": MAX_USD, "remote_request_id": "UNASSIGNED",
              "final_admission_sha256": receipt["receipt_sha256"]}
    encoded = _canonical(intent)
    import os
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError as error:
        raise FinalAdmissionError("final admission already consumed") from error
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    if target.read_bytes() != encoded:
        raise FinalAdmissionError("execution intent readback mismatch")
    return intent
