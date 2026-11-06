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


class FinalAdmissionKernel:
    """One TEST-only wiring of final admission to the durable FAL kernel."""

    def __init__(self, *, executor: object, ledger: object, episode_id: str, provider: object) -> None:
        if not isinstance(episode_id, str) or episode_id != "JACO_GENESIS28":
            raise FinalAdmissionError("exact Jacó budget scope is required")
        if getattr(provider, "mode", None) != "TEST":
            raise FinalAdmissionError("TEST kernel refuses non-TEST provider")
        for target, method in ((executor, "prepare_intent"), (executor, "inspect"), (executor, "run"),
                               (ledger, "reserve"), (ledger, "claim"), (ledger, "bind_provider_request_id")):
            if not callable(getattr(target, method, None)):
                raise TypeError(f"configured kernel lacks {method}")
        self.executor, self.ledger, self.episode_id, self.provider = executor, ledger, episode_id, provider

    @staticmethod
    def _verify_receipt(receipt: object) -> dict[str, str | int]:
        required = {"schema", "kind", "request_id", "endpoint", "reserved_usd", "call_limit", "packet_sha256",
                    "review_sha256", "authorization_sha256", "quote_sha256", "authorization_version",
                    "validated_at_epoch", "receipt_sha256"}
        if not isinstance(receipt, dict) or set(receipt) != required:
            raise FinalAdmissionError("closed final admission receipt required")
        body = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
        if (receipt.get("receipt_sha256") != sha256(_canonical(body)).hexdigest()
                or receipt.get("kind") != "JACO_FLUX3_FINAL_ADMISSION"
                or receipt.get("request_id") != REQUEST_ID or receipt.get("endpoint") != ENDPOINT
                or receipt.get("reserved_usd") != MAX_USD or receipt.get("call_limit") != 1):
            raise FinalAdmissionError("final admission receipt is invalid")
        return dict(receipt)

    async def execute(self, job: object, receipt: object) -> dict:
        """Prepare → reserve → marker → exactly-one submit/recovery."""
        approved = self._verify_receipt(receipt)
        if (getattr(job, "mode", None) != "TEST" or getattr(job, "endpoint", None) != ENDPOINT
                or str(getattr(job, "cost", "")) != MAX_USD):
            raise FinalAdmissionError("TEST canary job must exactly bind endpoint and USD 0.048")
        request_id = getattr(job, "request_id", None)
        if not isinstance(request_id, str) or not request_id:
            raise FinalAdmissionError("executor job identity is required")
        row = self.executor.inspect(request_id)
        if row is None:
            row = self.executor.prepare_intent(job)
        if row.get("status") in {"PREPARED", "INTENT"}:
            claim = self.ledger.reserve(
                request_id, self.episode_id, ENDPOINT, MAX_USD,
                settlement_mode="FAL_READBACK_V2", auth_scope=row["settlement_binding"]["auth_scope"],
                provider_request_id="UNASSIGNED", authority_mode="TEST",
            )
            if (claim.get("state") != "RESERVED" or claim.get("provider") != ENDPOINT
                    or claim.get("reserved_usd") != "0.048000"):
                raise FinalAdmissionError("standing budget claim does not bind canary")
            marker = {"schema": 1, "kind": "JACO_FLUX3_FINAL_ADMISSION_MARKER",
                      "receipt_sha256": approved["receipt_sha256"], "request_id": request_id,
                      "endpoint": ENDPOINT, "reserved_usd": MAX_USD}
            prior = row.get("final_admission_marker")
            if prior is not None and prior != marker:
                raise FinalAdmissionError("final admission marker is immutable")
            if prior is None:
                self.executor._change(request_id, final_admission_marker=marker)
        elif row.get("status") not in {"SUBMITTED", "SUBMITTING", "COMPLETE"}:
            raise FinalAdmissionError("executor intent cannot resume")
        # Executor recovery is GET/download-only after its durable provider ID;
        # this composition has no direct provider POST path.
        return await self.executor.run(
            job, self.provider,
            settlement_checkpoint=lambda local_id, remote_id: self.ledger.bind_provider_request_id(local_id, remote_id),
            submit_prepared=True,
        )


def main(argv: list[str] | None = None) -> int:
    """GET-only default CLI; --execute needs a configured external consent root."""
    import argparse

    parser = argparse.ArgumentParser(description="Jacó FLUX 3 final-admission preflight")
    parser.add_argument("--execute", action="store_true", help="attempt configured production execution")
    parser.add_argument("--packet")
    parser.add_argument("--review")
    parser.add_argument("--authorization")
    parser.add_argument("--quote")
    args = parser.parse_args(argv)
    if args.execute:
        raise FinalAdmissionError("production execution requires configured external consent root")
    if all((args.packet, args.review, args.authorization, args.quote)):
        print(json.dumps(validate_final_admission(packet=args.packet, review=args.review,
                                                  authorization=args.authorization, quote=args.quote), sort_keys=True))
    else:
        print(json.dumps({"mode": "GET_ONLY_PREFLIGHT", "execute": False, "post_count": 0}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
