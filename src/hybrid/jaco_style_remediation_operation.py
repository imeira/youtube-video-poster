"""Operation-scoped admission for the bounded Jacó style-remediation successor.

This module deliberately has no provider transport.  It only produces a fresh,
write-once review packet from the protected standing authority.  Reservation and
POST remain unavailable until parent review and CI provide a separately-bound
admission receipt.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path("C:/HermesStudio/production/jaco-flux3")
AUTHORITY_PATH = ROOT / "standing-policy" / "JACO_GEN28_STYLE_REMEDIATION_001.authority.v1.json"
POLICY_PATH = ROOT / "standing-policy" / "JACO_GENESIS28_STANDING_OPERATION_POLICY.v1.json"
OPERATION_ID = "JACO_GEN28_STYLE_REMEDIATION_001"
EPISODE_ID = "JACO_GENESIS28"
REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
ENDPOINT = "blackforestlabs/flux-3/text-to-image"
CAP = Decimal("10.000000")
PRIOR_ACTUAL = Decimal("0.048000")
NEXT_CAP = Decimal("0.048000")


class OperationAdmissionError(PermissionError):
    """The protected standing authority cannot admit this operation."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _load(path: Path, label: str) -> tuple[dict[str, Any], str]:
    try:
        info = path.lstat()
        if path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & getattr(os, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise OperationAdmissionError(f"{label} must not be a reparse point")
        raw = path.read_bytes()
        doc = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperationAdmissionError(f"{label} is not strict readable JSON") from exc
    if not isinstance(doc, dict):
        raise OperationAdmissionError(f"{label} must be an object")
    return doc, sha256(raw).hexdigest()


def _money(value: object, label: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise OperationAdmissionError(f"{label} is invalid") from exc
    if not amount.is_finite() or amount < 0:
        raise OperationAdmissionError(f"{label} is invalid")
    return amount


def _write_once(path: Path, body: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = _canonical(body)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError as exc:
        raise OperationAdmissionError("operation artifact replay/collision") from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    if path.read_bytes() != encoded:
        raise OperationAdmissionError("operation artifact readback mismatch")


class OperationAdmissionFactory:
    """Binds fresh review inputs to one protected successor operation only."""

    def __init__(self, *, root: Path = ROOT, authority_path: Path = AUTHORITY_PATH) -> None:
        self.root = Path(root).resolve()
        self.authority_path = Path(authority_path).resolve()

    def _authority(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        authority, _authority_sha = _load(self.authority_path, "protected successor authority")
        if authority.get("schema") != "jaco-derived-operation-authority/v1":
            raise OperationAdmissionError("unsupported successor authority")
        if authority.get("status") != "READY_FOR_INDEPENDENT_PARENT_REVIEW_NO_RESERVATION_NO_POST":
            raise OperationAdmissionError("consumed or non-ready successor authority")
        if authority.get("operation_id") != OPERATION_ID or authority.get("episode_id") != EPISODE_ID:
            raise OperationAdmissionError("wrong operation scope")
        policy_path = Path(authority.get("policy_path", "")).resolve()
        preflight_path = Path(authority.get("preflight_path", "")).resolve()
        if policy_path != (self.root / "standing-policy" / POLICY_PATH.name).resolve() or preflight_path != (self.root / "preflight" / "JACO_GEN28_STYLE_REMEDIATION_001.preflight.json").resolve():
            raise OperationAdmissionError("caller-selected policy or preflight is forbidden")
        policy, policy_sha = _load(policy_path, "protected standing policy")
        preflight, preflight_sha = _load(preflight_path, "protected successor preflight")
        if authority.get("policy_sha256") != policy_sha or authority.get("preflight_sha256") != preflight_sha:
            raise OperationAdmissionError("protected policy or preflight hash changed")
        if (preflight.get("schema") != "jaco-episode-operation-preflight/v1" or preflight.get("episode_id") != EPISODE_ID
                or preflight.get("request_id") != REQUEST_ID or preflight.get("operation", {}).get("id") != OPERATION_ID
                or preflight["operation"].get("endpoint") != ENDPOINT or preflight["operation"].get("call_limit") != 1):
            raise OperationAdmissionError("preflight does not bind successor operation")
        consent = policy.get("canonical_consent", {})
        if (policy.get("episode_id") != EPISODE_ID or consent.get("session_id") != "local-712d007e4f3efba4d8c6cfc52f947e69023fed6f445a54206a6c3ede9b317fbb"
                or consent.get("consent_message_id") != 147620 or consent.get("prompt_message_id") != 147618):
            raise OperationAdmissionError("same-host canonical consent binding changed")
        budget = authority.get("budget_check")
        if not isinstance(budget, dict):
            raise OperationAdmissionError("authority budget is missing")
        actual = _money(budget.get("actual_usd"), "actual spend")
        pending = _money(budget.get("pending_usd"), "pending spend")
        next_cap = _money(budget.get("next_cap_usd"), "next cap")
        episode_cap = _money(budget.get("episode_cap_usd"), "episode cap")
        if actual != PRIOR_ACTUAL or pending != Decimal(0) or next_cap != NEXT_CAP or episode_cap != CAP or actual + pending + next_cap > episode_cap:
            raise OperationAdmissionError("shared canonical ledger budget cannot be reset per job")
        if _money(authority.get("maximum_next_cost_usd"), "maximum next cost") != NEXT_CAP:
            raise OperationAdmissionError("operation cap changed")
        return authority, policy, preflight

    def build_review_packet(self, *, quote: dict[str, Any], openapi: dict[str, Any]) -> dict[str, Any]:
        """Create a new immutable review packet; caller data cannot choose payload/scope."""
        authority, _policy, preflight = self._authority()
        if not isinstance(quote, dict) or not isinstance(openapi, dict):
            raise OperationAdmissionError("fresh quote and OpenAPI objects are required")
        payload = preflight["operation"].get("payload")
        if not isinstance(payload, dict) or set(payload) != {"prompt", "aspect_ratio", "resolution", "output_format", "enable_prompt_expansion", "safety_tolerance"}:
            raise OperationAdmissionError("closed successor payload required")
        payload_sha = sha256(_canonical(payload)).hexdigest()
        runs_root = self.root / "operation-runs"
        if runs_root.exists() and any(runs_root.glob("*/review-packet.json")):
            raise OperationAdmissionError("operation artifact replay/collision")
        run = runs_root / f"{OPERATION_ID}-{time.time_ns()}"
        packet_path = run / "review-packet.json"
        quote_path, openapi_path = run / "quote.json", run / "openapi.json"
        _write_once(quote_path, quote)
        _write_once(openapi_path, openapi)
        packet = {
            "schema": "jaco-operation-scoped-independent-review-packet/v1",
            "status": "READY_FOR_INDEPENDENT_PARENT_REVIEW_NO_RESERVATION_NO_POST",
            "operation_id": OPERATION_ID,
            "episode_id": EPISODE_ID,
            "request_id": REQUEST_ID,
            "endpoint": ENDPOINT,
            "call_limit": 1,
            "payload": payload,
            "payload_sha256": payload_sha,
            "standing_authority_sha256": sha256(self.authority_path.read_bytes()).hexdigest(),
            "standing_policy_sha256": authority["policy_sha256"],
            "successor_preflight_sha256": authority["preflight_sha256"],
            "quote_sha256": sha256(quote_path.read_bytes()).hexdigest(),
            "openapi_sha256": sha256(openapi_path.read_bytes()).hexdigest(),
            "budget": {"actual_usd": "0.048000", "pending_usd": "0.000000", "next_cap_usd": "0.048000", "episode_cap_usd": "10.000000", "post_reservation_exposure_usd": "0.096000"},
            "prohibitions": ["NO_RESERVATION", "NO_PROVIDER_POST", "NO_SECOND_LEDGER", "NO_REPLAY"],
        }
        _write_once(packet_path, packet)
        return {"status": packet["status"], "operation_id": OPERATION_ID, "packet_path": str(packet_path), "packet_sha256": sha256(packet_path.read_bytes()).hexdigest(), "budget": packet["budget"]}

    def verify(self) -> dict[str, str]:
        authority, _policy, _preflight = self._authority()
        return {"status": authority["status"], "operation_id": OPERATION_ID, "authority_sha256": sha256(self.authority_path.read_bytes()).hexdigest(), "preflight_sha256": authority["preflight_sha256"]}

    async def fresh_review(self) -> dict[str, Any]:
        """Fetch only the fixed authenticated quote/OpenAPI, then seal a new packet.

        This is intentionally not an execution path: it makes no reservation,
        constructs no provider, and cannot POST.
        """
        import asyncio

        from src.hybrid.jaco_flux3_production import (
            _FAL_OPENAPI_URL,
            _current_price_snapshot,
            _get_json,
            _official_openapi_endpoint,
        )

        quote, openapi = await asyncio.gather(
            _current_price_snapshot(), asyncio.to_thread(_get_json, _FAL_OPENAPI_URL)
        )
        _official_openapi_endpoint(openapi)
        return self.build_review_packet(quote=quote, openapi=openapi)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Operation-scoped Jacó successor admission")
    parser.add_argument("--operation", required=True)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--fresh-review", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    if args.operation != OPERATION_ID:
        raise OperationAdmissionError("fixed approved operation selection required")
    if args.execute:
        raise OperationAdmissionError("parent review and CI admission are required before reservation or provider POST")
    if args.verify == args.fresh_review:
        raise OperationAdmissionError("select exactly one of --verify or --fresh-review")
    factory = OperationAdmissionFactory()
    if args.verify:
        result: dict[str, Any] = factory.verify()
    else:
        import asyncio

        result = asyncio.run(factory.fresh_review())
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
