"""Fixed production composition for the single Jacó FLUX 3 canary.

No caller selects a provider, ledger or executor.  The configuration is a
deployment artifact; it pins the mandate root and only supplies FAL GET URLs.
The only POST is delegated to the existing Executor after all durable gates.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import time
import urllib.request
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from src.hybrid.execution import Authorization, Executor, Job, Price, ProviderResult
from src.hybrid.fal_billing import AiohttpFalBillingClient
from src.hybrid.fal_get_only_preflight import resolve_default_fal_credential
from src.hybrid.jaco_flux3_budget_bootstrap import bootstrap_canonical_budget
from src.hybrid.jaco_flux3_final_admission import (
    ENDPOINT,
    MAX_USD,
    REQUEST_ID,
    FinalAdmissionKernel,
)
from src.hybrid.local_process_review import (
    build_local_process_review_policy,
    run_local_process_review,
    validate_local_process_review_output,
)
from src.hybrid.planner import Config
from src.hybrid.standing_budget import StandingBudgetLedger

_PAYLOAD = {"prompt": "Child-safe canonical Genesis 28 establishing image: young adult Jacó with dark wavy hair, short brown beard, cream tunic and blue mantle, sleeping outdoors beside a stone beneath a luminous stairway to heaven; no text, no modern objects.", "aspect_ratio": "4:3", "resolution": "1k", "output_format": "png", "enable_prompt_expansion": False, "safety_tolerance": 2}
# Deliberately outside both the caller working directory and review-output tree.
# This is an operator-installed same-host root, not an external signature root.
_PROVISIONED_ROOT = Path("C:/HermesStudio/production/jaco-flux3")
_SOURCE_EVIDENCE_ROOT = Path("C:/HermesStudio/review-evidence/jaco-flux3-canary-20261106")
_FAL_QUEUE_HOST = "queue.fal.run"
_FAL_API_HOST = "fal.ai"
_FAL_MEDIA_HOSTS = frozenset({"fal.media", "v3.fal.media"})
_FAL_PRICE_API_URL = "https://api.fal.ai/v1/models/pricing"
_FAL_OPENAPI_URL = "https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=blackforestlabs/flux-3/text-to-image"
_FAL_SUBMIT_URL = "https://queue.fal.run/blackforestlabs/flux-3/text-to-image"
_QUOTE_SCHEMA = "FAL_AUTHENTICATED_PRICE_CONSUMER_SNAPSHOT/2"
_CANONICAL_STATE_DB = Path("C:/Users/meira/AppData/Local/hermes/state.db")

class ProductionCompositionError(PermissionError): pass

def _canon(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
                      default=lambda item: str(item) if isinstance(item, Decimal) else (_ for _ in ()).throw(TypeError())).encode()

def _sha_path(path: Path) -> str: return sha256(path.read_bytes()).hexdigest()

def _write_once(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = _canon(value)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
    if path.read_bytes() != encoded: raise ProductionCompositionError("evidence readback mismatch")
    return path

def _load(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file(): raise ProductionCompositionError("trusted input must be a regular file")
    try: value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc: raise ProductionCompositionError("trusted input is invalid JSON") from exc
    if not isinstance(value, dict): raise ProductionCompositionError("trusted input must be an object")
    return value


def _validate_canonical_transcript(provenance: dict[str, Any]) -> None:
    """Bind local consent to two exact, adjacent canonical message records."""
    assistant_id, user_id = provenance["adjacent_assistant_message_id"], provenance["user_message_id"]
    if type(assistant_id) is not int or type(user_id) is not int or user_id != assistant_id + 1:
        raise ProductionCompositionError("canonical transcript message IDs are not adjacent")
    try:
        database = sqlite3.connect(f"file:{_CANONICAL_STATE_DB.as_posix()}?mode=ro", uri=True)
        try:
            rows = database.execute(
                "SELECT id, session_id, role, content FROM messages WHERE id IN (?, ?) ORDER BY id",
                (assistant_id, user_id),
            ).fetchall()
        finally:
            database.close()
    except sqlite3.Error as exc:
        raise ProductionCompositionError("canonical transcript is unavailable") from exc
    if len(rows) != 2:
        raise ProductionCompositionError("canonical transcript records are incomplete")
    expected = (
        (assistant_id, "assistant", provenance["assistant_message_sha256"]),
        (user_id, "user", provenance["user_message_sha256"]),
    )
    for row, (message_id, role, expected_sha) in zip(rows, expected, strict=True):
        actual_id, session_id, actual_role, content = row
        if (actual_id != message_id or session_id != provenance["session_id"] or actual_role != role
                or not isinstance(content, str) or sha256(content.encode("utf-8")).hexdigest() != expected_sha):
            raise ProductionCompositionError("canonical transcript content or role does not match provenance")


def _validate_mandate(root: Path) -> dict[str, Any]:
    """Load the fixed local mandate; no caller-selected root or fake signer.

    This deliberately describes same-host operator trust.  It validates canonical
    transcript provenance and immutable source bytes, but makes no claim of an
    external organization, cryptographic signature, or release authorization.
    """
    if root.resolve() != _PROVISIONED_ROOT.resolve():
        raise ProductionCompositionError("pinned deployment root is required")
    policy = _load(root / "parent-provisioned-policy.json")
    mandate = _load(root / "user-mandate.json")
    provenance = _load(root / "provenance.json")
    policy_required = {"schema", "trust_derivation", "mandate_sha256", "provenance_sha256", "source_evidence"}
    mandate_required = {"schema", "request_id", "revision", "endpoint", "payload_sha256", "maximum_usd", "call_limit", "budget_cap_usd", "scope", "release_authorization", "license_status"}
    provenance_required = {"schema", "session_id", "user_message_id", "adjacent_assistant_message_id", "user_message_sha256", "assistant_message_sha256", "financial_record_sha256", "review_source_sha256"}
    if (set(policy) != policy_required or policy.get("schema") != "JACO_FLUX3_LOCAL_OPERATOR_POLICY/1"
            or policy.get("trust_derivation") != "CANONICAL_USER_TRANSCRIPT_PLUS_TRUSTED_LOCAL_OPERATOR"
            or policy.get("mandate_sha256") != _sha_path(root / "user-mandate.json")
            or policy.get("provenance_sha256") != _sha_path(root / "provenance.json")):
        raise ProductionCompositionError("invalid pinned local operator policy")
    if (set(mandate) != mandate_required or mandate.get("schema") != "JACO_USER_EXECUTION_MANDATE/2"
            or mandate.get("request_id") != REQUEST_ID or mandate.get("revision") != "818556d97edb648f"
            or mandate.get("endpoint") != ENDPOINT or mandate.get("payload_sha256") != sha256(_canon(_PAYLOAD)).hexdigest()
            or mandate.get("maximum_usd") != MAX_USD or mandate.get("call_limit") != 1
            or mandate.get("budget_cap_usd") != "10.00" or mandate.get("scope") != "EVALUATION_GENERATION_ONLY"
            or mandate.get("release_authorization") != "NOT_AUTHORIZED"
            or mandate.get("license_status") != "EVALUATION_ONLY_CONDITIONALLY_SUPPORTABLE"):
        raise ProductionCompositionError("pinned mandate does not bind exact evaluation canary")
    if (set(provenance) != provenance_required or provenance.get("schema") != "JACO_FLUX3_CANONICAL_TRANSCRIPT_PROVENANCE/1"
            or provenance.get("session_id") != "local-712d007e4f3efba4d8c6cfc52f947e69023fed6f445a54206a6c3ede9b317fbb"
            or provenance.get("user_message_id") != 144800 or provenance.get("adjacent_assistant_message_id") != 144799):
        raise ProductionCompositionError("canonical transcript provenance is incomplete")
    _validate_canonical_transcript(provenance)
    evidence = policy.get("source_evidence")
    if not isinstance(evidence, dict) or set(evidence) != {"financial_record", "official_contract_review", "packet", "get_only_preflight"}:
        raise ProductionCompositionError("pinned policy source evidence is incomplete")
    for key, relative in {"financial_record": "financial-record.json", "official_contract_review": "official-contract-review/official-contract-review.md", "packet": "packet.json", "get_only_preflight": "get-only-current/preflight.json"}.items():
        expected = evidence[key]
        path = _SOURCE_EVIDENCE_ROOT / relative
        if (not isinstance(expected, str) or len(expected) != 64 or not path.is_file() or _sha_path(path) != expected):
            raise ProductionCompositionError(f"pinned source evidence changed: {key}")
    if provenance["financial_record_sha256"] != evidence["financial_record"] or provenance["review_source_sha256"] != evidence["official_contract_review"]:
        raise ProductionCompositionError("transcript provenance source bindings differ")
    return {"trust_derivation": policy["trust_derivation"], "license_status": mandate["license_status"], "release_authorization": mandate["release_authorization"], "mandate_sha256": policy["mandate_sha256"]}


def _fixed_https_url(url: str, *, hosts: frozenset[str]) -> str:
    """Reject provider-controlled routing before a credential can be attached."""
    parsed = urlparse(url)
    if (not isinstance(url, str) or parsed.scheme != "https" or parsed.username
            or parsed.password or parsed.port is not None or parsed.fragment
            or parsed.hostname not in hosts):
        raise ProductionCompositionError("unapproved FAL HTTPS origin")
    return url

def _get_json(url: str) -> dict[str, Any]:
    """Unauthenticated fixed-origin GET used solely for the public OpenAPI."""
    _fixed_https_url(url, hosts=frozenset({_FAL_API_HOST, _FAL_QUEUE_HOST}))
    request = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(request, timeout=20) as response:  # nosec - deployment-pinned URL
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict): raise ProductionCompositionError("FAL GET did not return an object")
    return value


def _official_openapi_endpoint(openapi: object) -> str:
    if not isinstance(openapi, dict):
        raise ProductionCompositionError("OpenAPI must be an object")
    info = openapi.get("info")
    metadata = info.get("x-fal-metadata") if isinstance(info, dict) else None
    endpoint = metadata.get("endpointId") if isinstance(metadata, dict) else None
    if endpoint != ENDPOINT:
        raise ProductionCompositionError("OpenAPI endpoint mismatch")
    return endpoint


def _opaque_remote_id(value: object) -> str:
    # The reviewed OpenAPI provides only `string`; do not invent an alphabet.
    if not isinstance(value, str) or not value or len(value) > 512 or any(ord(char) < 32 for char in value):
        raise ProductionCompositionError("FAL remote request ID is invalid")
    return value


def _queue_route(openapi: object, operation: str, remote_id: object) -> str:
    _official_openapi_endpoint(openapi)
    remote = _opaque_remote_id(remote_id)
    paths = openapi.get("paths") if isinstance(openapi, dict) else None
    suffix = "/status" if operation == "status" else "" if operation == "result" else None
    if suffix is None:
        raise ProductionCompositionError("unknown FAL recovery operation")
    expected = f"/{ENDPOINT}/requests/{{request_id}}{suffix}"
    if not isinstance(paths, dict) or expected not in paths or "get" not in paths[expected]:
        raise ProductionCompositionError("OpenAPI lacks documented FAL recovery route")
    return f"https://{_FAL_QUEUE_HOST}/{ENDPOINT}/requests/{quote(remote, safe='')}{suffix}"


def _price_snapshot(*, raw_price_page: object, observed_at_epoch: int) -> dict[str, object]:
    """Bind the authenticated GET consumer to the documented flat 1K image rate."""
    if type(observed_at_epoch) is not int or not isinstance(raw_price_page, dict):
        raise ProductionCompositionError("invalid authenticated price observation")
    if set(raw_price_page) != {"prices", "next_cursor", "has_more"} or raw_price_page.get("next_cursor") is not None or raw_price_page.get("has_more") is not False:
        raise ProductionCompositionError("authenticated price response has an incomplete or unknown page shape")
    prices = raw_price_page.get("prices")
    if not isinstance(prices, list):
        raise ProductionCompositionError("authenticated price response lacks prices")
    matches = [item for item in prices if isinstance(item, dict) and item.get("endpoint_id") == ENDPOINT]
    if len(matches) != 1 or set(matches[0]) != {"endpoint_id", "unit_price", "unit", "currency"}:
        raise ProductionCompositionError("authenticated price response lacks one exact endpoint quote")
    record = matches[0]
    provider_unit = record.get("unit")
    try:
        unit_price = Decimal(str(record.get("unit_price")))
    except Exception as error:
        raise ProductionCompositionError("authenticated price unit is invalid") from error
    if record.get("currency") != "USD" or not isinstance(provider_unit, str) or not provider_unit or unit_price != Decimal(MAX_USD):
        raise ProductionCompositionError("authenticated price does not affirm flat USD 0.048 1K image cost")
    return {
        "schema": _QUOTE_SCHEMA, "endpoint_id": ENDPOINT, "currency": "USD",
        "maximum_usd": MAX_USD, "unit_price_usd": MAX_USD,
        "provider_unit": provider_unit, "semantic_unit": "FLAT_1K_SINGLE_IMAGE", "quantity": 1,
        "api_url": _FAL_PRICE_API_URL, "raw_sanitized_sha256": sha256(_canon(raw_price_page)).hexdigest(),
        "observed_at_epoch": observed_at_epoch, "provider_expiry": "UNKNOWN_NOT_ASSERTED",
        "price_validity": "LOCAL_OBSERVED_AT_MAX_AGE_SECONDS_300_NOT_PROVIDER_EXPIRY",
    }


async def _current_price_snapshot(key: str | None = None) -> dict[str, object]:
    if key is None:
        key, _presence = resolve_default_fal_credential()
    raw_page = await AiohttpFalBillingClient(key).pricing({"endpoint_id": ENDPOINT})
    return _price_snapshot(raw_price_page=raw_page, observed_at_epoch=int(time.time()))


class _FixedFalProvider:
    """Dormant LIVE transport; construction belongs only to trusted deployment."""
    mode = "LIVE"

    def __init__(self, output: Path, openapi: dict[str, Any], credential: str = ""):
        self.output, self.openapi, self.credential = output, openapi, credential
        _official_openapi_endpoint(openapi)

    @staticmethod
    def _open(request):
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()

    @staticmethod
    def _queue_status(answer: object, remote: str) -> str:
        if not isinstance(answer, dict) or answer.get("request_id") != remote:
            raise ProductionCompositionError("FAL queue response does not bind checkpointed request ID")
        status = answer.get("status")
        if status not in {"IN_QUEUE", "IN_PROGRESS", "COMPLETED"}:
            raise ProductionCompositionError("FAL queue response has invalid status")
        return status

    def _quarantine_unknown(self, remote: str, phase: str) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        _write_once(self.output, {"kind": "UNKNOWN_PROVIDER_RESPONSE_QUARANTINE", "provider_request_id": remote,
                                   "phase": phase, "actual_billed_amount": "UNKNOWN_UNTIL_BILLING_RECONCILIATION",
                                   "qa_status": "FAIL_NOT_PROMOTABLE"})

    def _unexpected_output(self, remote: str, images: object) -> None:
        self.output.parent.mkdir(parents=True, exist_ok=True)
        _write_once(self.output, {"kind": "UNEXPECTED_PROVIDER_OUTPUT_QUARANTINE", "provider_request_id": remote,
                                   "image_count": len(images) if isinstance(images, list) else "NON_ARRAY",
                                   "actual_billed_amount": "UNKNOWN_UNTIL_BILLING_RECONCILIATION",
                                   "qa_status": "FAIL_NOT_PROMOTABLE"})

    async def submit(self, job, request_id, checkpoint):
        data = _canon(job.payload)
        req = urllib.request.Request(_FAL_SUBMIT_URL, data=data, method="POST", headers={"Content-Type": "application/json", "Authorization": f"Key {self.credential}"})
        answer = json.loads((await asyncio.to_thread(self._open, req)).decode())
        remote = _opaque_remote_id(answer.get("request_id") if isinstance(answer, dict) else None)
        self._queue_status(answer, remote)
        # Returned URLs are optional.  Persist either validated returned URLs or
        # the reviewed deterministic reconstruction *before* any recovery GET.
        status_url = answer.get("status_url") if isinstance(answer, dict) else None
        response_url = answer.get("response_url") if isinstance(answer, dict) else None
        if status_url is None:
            status_url = _queue_route(self.openapi, "status", remote)
        if response_url is None:
            response_url = _queue_route(self.openapi, "result", remote)
        _fixed_https_url(status_url, hosts=frozenset({_FAL_QUEUE_HOST}))
        _fixed_https_url(response_url, hosts=frozenset({_FAL_QUEUE_HOST}))
        recovery = self.output.with_suffix(".recovery.json")
        _write_once(recovery, {"schema": 1, "provider_request_id": remote,
                               "status_url": status_url, "response_url": response_url})
        checkpoint(provider_id=remote, partial=recovery)
        return await self.recover(job, request_id, remote, str(recovery), checkpoint)

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        remote = _opaque_remote_id(provider_id)
        if isinstance(partial, dict):
            # Compatibility for direct recovery callers: still materialize the
            # reconstructed routes before issuing a GET.
            recovery_path = self.output.with_suffix(".recovery.json")
            if not recovery_path.exists():
                _write_once(recovery_path, {"schema": 1, "provider_request_id": remote,
                                            "status_url": _queue_route(self.openapi, "status", remote),
                                            "response_url": _queue_route(self.openapi, "result", remote)})
            partial = str(recovery_path)
        recovery = _load(Path(partial))
        if (set(recovery) != {"schema", "provider_request_id", "status_url", "response_url"}
                or recovery.get("schema") != 1 or recovery.get("provider_request_id") != remote):
            raise ProductionCompositionError("recovery URLs are not durably bound to provider request ID")
        status_url, result_url = recovery["status_url"], recovery["response_url"]
        if not isinstance(status_url, str) or not isinstance(result_url, str):
            raise ProductionCompositionError("recovery URLs are invalid")
        _fixed_https_url(status_url, hosts=frozenset({_FAL_QUEUE_HOST}))
        _fixed_https_url(result_url, hosts=frozenset({_FAL_QUEUE_HOST}))
        status = json.loads((await asyncio.to_thread(self._open, urllib.request.Request(status_url, method="GET"))).decode())
        try:
            status_value = self._queue_status(status, remote)
        except ProductionCompositionError:
            self._quarantine_unknown(remote, "STATUS_GET")
            raise
        if status_value != "COMPLETED":
            raise ProductionCompositionError("FAL request is pending; preserve checkpoint and use only status/result GET recovery")
        result = json.loads((await asyncio.to_thread(self._open, urllib.request.Request(result_url, method="GET"))).decode())
        images = result.get("images") if isinstance(result, dict) else None
        if not isinstance(images, list) or len(images) != 1:
            self._unexpected_output(remote, images)
            raise ProductionCompositionError("FAL returned unexpected output cardinality; quarantined without promotion or retry")
        image_url = images[0].get("url") if isinstance(images[0], dict) else None
        if not isinstance(image_url, str):
            self._unexpected_output(remote, images)
            raise ProductionCompositionError("FAL result lacks the documented image URL; quarantined")
        _fixed_https_url(image_url, hosts=_FAL_MEDIA_HOSTS)
        body = await asyncio.to_thread(self._open, image_url)
        if not body.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ProductionCompositionError("FAL result is not PNG bytes")
        self.output.parent.mkdir(parents=True, exist_ok=True)
        quarantine_receipt = {"sha256": sha256(body).hexdigest(), "kind": "PNG_RESULT",
                              "actual_billed_amount": "UNKNOWN_UNTIL_BILLING_RECONCILIATION"}
        if self.output.exists():
            if _load(self.output) != quarantine_receipt:
                raise ProductionCompositionError("quarantine receipt changed during recovery")
        else:
            _write_once(self.output, quarantine_receipt)
        raw = self.output.with_suffix(".quarantine.png")
        if not raw.exists():
            fd = os.open(raw, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            with os.fdopen(fd, "wb") as stream:
                stream.write(body); stream.flush(); os.fsync(stream.fileno())
        if sha256(raw.read_bytes()).hexdigest() != sha256(body).hexdigest():
            raise ProductionCompositionError("quarantine download readback mismatch")
        # A rendered image is not a billing receipt.  Returning MAX_USD here
        # would silently convert a reserved cap into settled actual spend.
        raise ProductionCompositionError("image quarantined; authenticated billing reconciliation is required before completion")


def _production_final_receipt(*, packet: Path, review: Path, authorization: Path, quote: Path) -> dict[str, str | int]:
    """Revalidate the complete LOCAL_PROCESS_REVIEW output against current bytes."""
    packet_doc, review_doc, authorization_doc, quote_doc = (_load(packet), _load(review), _load(authorization), _load(quote))
    review_request, openapi, financial_record = (
        packet.parent / "review-request.json", packet.parent / "openapi.json", packet.parent / "financial-record.json",
    )
    review_request_doc, openapi_doc, financial_record_doc = (_load(review_request), _load(openapi), _load(financial_record))
    try:
        validate_local_process_review_output(review_doc, packet_sha256=_sha_path(packet),
            review_request_sha256=_sha_path(review_request), quote_sha256=_sha_path(quote),
            openapi_sha256=_sha_path(openapi), financial_record_sha256=_sha_path(financial_record))
    except Exception as exc:
        raise ProductionCompositionError("production final admission evidence does not bind exact canary") from exc
    payload_sha = sha256(_canon(_PAYLOAD)).hexdigest()
    required_auth = {"schema", "kind", "version", "request_id", "endpoint", "payload_sha256", "maximum_usd", "call_limit", "user_authorization_evidence"}
    if (packet_doc.get("bindings", {}).get("payload_sha256") != payload_sha
            or packet_doc.get("candidate", {}).get("payload") != _PAYLOAD
            or review_request_doc.get("required_exact_bindings", {}).get("payload_sha256") != payload_sha
            or review_request_doc.get("prohibitions") != ["NO_FAL_PAID_POST", "NO_RESERVATION", "NO_GATEWAY_OR_OTHER_EP_WRITE", "NO_TEST_AS_LIVE"]
            or _official_openapi_endpoint(openapi_doc) != ENDPOINT
            or financial_record_doc.get("request_hash") != REQUEST_ID
            or financial_record_doc.get("currency") != "USD"
            or financial_record_doc.get("total_episode_cap") != "10.00"
            or financial_record_doc.get("authorizes_generation") is not False
            or set(authorization_doc) != required_auth
            or authorization_doc.get("schema") != 1 or authorization_doc.get("kind") != "JACO_FLUX3_FINAL_ADMISSION_AUTHORIZATION"
            or authorization_doc.get("request_id") != REQUEST_ID or authorization_doc.get("endpoint") != ENDPOINT
            or authorization_doc.get("payload_sha256") != payload_sha or authorization_doc.get("maximum_usd") != MAX_USD
            or authorization_doc.get("call_limit") != 1
            or authorization_doc.get("user_authorization_evidence") != "USER_AUTHORIZED_LOCAL_QA_AND_ONE_ALTERNATIVE_IMAGE_EVAL_WITHIN_USD10"
            or set(quote_doc) != {"schema", "endpoint_id", "currency", "maximum_usd", "unit_price_usd", "provider_unit", "semantic_unit", "quantity", "api_url", "raw_sanitized_sha256", "observed_at_epoch", "provider_expiry", "price_validity"}
            or quote_doc.get("schema") != _QUOTE_SCHEMA or quote_doc.get("maximum_usd") != MAX_USD
            or quote_doc.get("unit_price_usd") != MAX_USD or quote_doc.get("endpoint_id") != ENDPOINT
            or quote_doc.get("currency") != "USD" or not isinstance(quote_doc.get("provider_unit"), str)
            or not quote_doc.get("provider_unit") or quote_doc.get("semantic_unit") != "FLAT_1K_SINGLE_IMAGE"
            or quote_doc.get("quantity") != 1 or quote_doc.get("api_url") != _FAL_PRICE_API_URL
            or not isinstance(quote_doc.get("raw_sanitized_sha256"), str) or len(quote_doc["raw_sanitized_sha256"]) != 64
            or type(quote_doc.get("observed_at_epoch")) is not int or quote_doc.get("provider_expiry") != "UNKNOWN_NOT_ASSERTED"
            or quote_doc.get("price_validity") != "LOCAL_OBSERVED_AT_MAX_AGE_SECONDS_300_NOT_PROVIDER_EXPIRY"):
        raise ProductionCompositionError("production final admission evidence does not bind exact canary")
    receipt: dict[str, str | int] = {"schema": 1, "kind": "JACO_FLUX3_FINAL_ADMISSION", "request_id": REQUEST_ID,
        "endpoint": ENDPOINT, "reserved_usd": MAX_USD, "call_limit": 1, "packet_sha256": _sha_path(packet),
        "review_sha256": _sha_path(review), "authorization_sha256": _sha_path(authorization), "quote_sha256": _sha_path(quote),
        "authorization_version": authorization_doc["version"], "validated_at_epoch": int(time.time())}
    return {**receipt, "receipt_sha256": sha256(_canon(receipt)).hexdigest()}

class FixedFalProductionFactory:
    """Composition-owned construction and receipt re-read for one exact job."""
    def __init__(self, root: Path):
        self.root = root.resolve(); self.config = _load(self.root / "production-composition.json")
        required = {"schema", "financial_record"}
        if set(self.config) != required or self.config.get("schema") != "JACO_FLUX3_FIXED_PRODUCTION_COMPOSITION/1": raise ProductionCompositionError("closed production composition config required")
        # A config cannot nominate its own root, hash, endpoints, or image input.
        # CLI construction is only from a deployment-provisioned location.
        if self.root != _PROVISIONED_ROOT.resolve():
            raise ProductionCompositionError("caller-selected production root is not authority")
        self.trust = _load(self.root / "parent-provisioned-policy.json")
    def _mandate(self) -> dict[str, Any]:
        # Re-open the pinned local authority at every composition boundary.
        return _validate_mandate(self.root)
    async def fresh_review(self) -> tuple[dict[str, Any], Path, Path]:
        self._mandate(); run = self.root / "runs" / str(int(time.time() * 1000000)); run.mkdir(parents=True)
        # Exactly one authenticated pricing GET uses the established consumer URL;
        # the public OpenAPI GET is separately persisted and hash-bound.
        quote, openapi = await asyncio.gather(_current_price_snapshot(), asyncio.to_thread(_get_json, _FAL_OPENAPI_URL))
        _official_openapi_endpoint(openapi)
        q, o = _write_once(run/"quote.json", quote), _write_once(run/"openapi.json", openapi)
        finance_source = self.root / self.config["financial_record"]
        finance = _write_once(run / "financial-record.json", _load(finance_source))
        payload_sha = sha256(_canon(_PAYLOAD)).hexdigest()
        packet = {"schema":"jaco-fal-flux3-successor-benchmark/3","kind":"BOUNDED_FAL_FLUX3_CANARY_PREGEN_PACKET","bindings":{"request_id":REQUEST_ID,"endpoint":ENDPOINT,"payload_sha256":payload_sha,"authenticated_price_quote":{"sha256":_sha_path(q),"bytes":q.stat().st_size},"openapi_snapshot":{"sha256":_sha_path(o),"bytes":o.stat().st_size},"financial_approval":{"sha256":_sha_path(finance),"status":"APPROVED_USD10","total_episode_cap_usd":"10.00"}},"candidate":{"call_limit":1,"single_output":True,"reference_inputs":[],"payload":_PAYLOAD},"cost":{"approved_canary_maximum_usd":MAX_USD,"current_authenticated_quote_maximum_usd":MAX_USD,"actual_billed_amount":"UNKNOWN_UNTIL_PROVIDER_RECEIPT_OR_BILLING_RECONCILIATION"}}
        p = _write_once(run/"packet.json", packet)
        request = {"schema":"jaco-fal-flux3-independent-pre-generation-review-request/2","kind":"INDEPENDENT_PREGEN_REVIEW_REQUEST","packet":{"sha256":_sha_path(p)},"required_exact_bindings":{"request_id":REQUEST_ID,"endpoint":ENDPOINT,"payload_sha256":payload_sha,"approved_canary_maximum_usd":MAX_USD,"successor_packet_sha256":_sha_path(p)},"prohibitions":["NO_FAL_PAID_POST","NO_RESERVATION","NO_GATEWAY_OR_OTHER_EP_WRITE","NO_TEST_AS_LIVE"]}
        r = _write_once(run/"review-request.json", request)
        policy = build_local_process_review_policy(root=run, packet_path=p, review_request_path=r, quote_path=q, openapi_path=o, financial_record_path=finance, output_path=run/"review.json", request_id=REQUEST_ID, review_request_sha256=_sha_path(r))
        return run_local_process_review(policy), run, q
    async def execute(self) -> dict:
        mandate = self._mandate()
        bootstrap = bootstrap_canonical_budget(root=self.root)
        if bootstrap.get("status") not in {"PASS_ZERO_HISTORY", "PASS_RECONCILED"}:
            # No ledger, intent, credential, provider, or network operation exists
            # on an absent/incomplete history. UNKNOWN is not zero spend.
            raise ProductionCompositionError(
                f"canonical budget bootstrap blocked: {bootstrap.get('blocking_reason', 'unknown reconciliation state')}"
            )
        execution = self.root / "execution"
        execution.mkdir(parents=True, exist_ok=True)
        ledger = StandingBudgetLedger(
            Path(bootstrap["ledger_path"]), policy_sha256=bootstrap["policy_sha256"],
            episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="LIVE",
        )
        binding = {"kind": "JACO_FLUX3_FIXED_PRODUCTION_COMPOSITION",
                   "auth_scope": f"JACO_FLUX3_FINAL_ADMISSION:{mandate['mandate_sha256']}"}
        receipt_path = execution / "final-admission-receipt.json"

        def verify_marker(job: Job, marker: dict[str, Any], claim: dict[str, Any]) -> None:
            # This is deliberately composition-owned and reopens the receipt;
            # a caller cannot satisfy it with self-consistent marker JSON.
            persisted = _load(receipt_path)
            if persisted.get("receipt_sha256") != marker["receipt_sha256"]:
                raise ProductionCompositionError("final admission receipt hash changed")
            approved = _production_final_receipt(packet=execution / "packet.json", review=execution / "review.json",
                                                  authorization=self.root / "final-admission-authorization.json", quote=execution / "quote.json")
            if persisted != approved or marker["receipt_sha256"] != approved["receipt_sha256"]:
                raise ProductionCompositionError("final admission receipt no longer validates")
            if claim.get("auth_scope") != binding["auth_scope"] or job.mode != "LIVE":
                raise ProductionCompositionError("final admission marker scope changed")

        executor = Executor(execution / "executor.sqlite3", Config(limit=Decimal(10)),
                            standing_ledger=ledger, _jaco_final_admission_verifier=verify_marker,
                            _jaco_production_binding=binding)
        job = Job("JACO_GEN28_CANONICAL_001", "first", "LIVE", ENDPOINT, _PAYLOAD, None,
                  Decimal(MAX_USD), input_kind="TEXT_TO_IMAGE")
        existing = executor.inspect(job.request_id)
        if existing is None:
            # Exactly one review is performed for a new one-shot intent.  A
            # checkpointed provider ID below follows only GET recovery.
            _review, run, _quote = await self.fresh_review()
            for name in ("packet.json", "review-request.json", "quote.json", "openapi.json", "financial-record.json", "review.json"):
                _write_once(execution / name, _load(run / name))
            approved = _production_final_receipt(packet=execution / "packet.json", review=execution / "review.json",
                                                  authorization=self.root / "final-admission-authorization.json", quote=execution / "quote.json")
            _write_once(receipt_path, approved)
            # Re-read before preparing any durable spend state.
            if _load(receipt_path) != approved:
                raise ProductionCompositionError("final admission receipt readback mismatch")
            executor.prepare_intent(job, auth_scope=binding["auth_scope"])
            claim = ledger.reserve(job.request_id, "JACO_GENESIS28", ENDPOINT, MAX_USD,
                                   settlement_mode="FAL_READBACK_V2", auth_scope=binding["auth_scope"],
                                   provider_request_id="UNASSIGNED", authority_mode="LIVE")
            if claim.get("state") != "RESERVED" or claim.get("reserved_usd") != "0.048000":
                raise ProductionCompositionError("standing budget claim does not bind canary")
            marker = {"schema": 2, "kind": "JACO_FLUX3_FINAL_ADMISSION_MARKER",
                      "receipt_path": str(receipt_path.resolve()), "receipt_sha256": approved["receipt_sha256"],
                      "request_id": job.request_id, "endpoint": ENDPOINT, "payload_sha256": job.intent_input_sha256,
                      "input_kind": "TEXT_TO_IMAGE", "auth_scope": binding["auth_scope"],
                      "reserved_usd": MAX_USD, "trust_identity": "FIXED_PRODUCTION_COMPOSITION"}
            executor._change(job.request_id, final_admission_marker=marker)
        credential, _presence = resolve_default_fal_credential()
        openapi = _load(execution / "openapi.json")
        provider = _FixedFalProvider(execution / "provider-receipt.json", openapi, credential)
        receipt = _load(receipt_path)
        authorization = Authorization(job.request_id, str(receipt["authorization_version"]),
                                      float(receipt["validated_at_epoch"]) + 300, Decimal(MAX_USD))
        price = Price(ENDPOINT, job.request_id, Decimal(MAX_USD),
                      float(receipt["validated_at_epoch"]) + 300, str(receipt["quote_sha256"]))
        completed = await executor.run(job, provider, authorization=authorization, price=price,
                                       settlement_checkpoint=lambda local_id, remote_id: ledger.bind_provider_request_id(local_id, remote_id),
                                       submit_prepared=True)
        return {"mode": "LIVE", "status": completed["status"], "request_id": job.request_id,
                "release_authorization": mandate["release_authorization"]}


def _test_config(path: str) -> tuple[Path, Path]:
    """Accept a caller fixture only in a permanently non-promotable TEST domain."""
    config_path = Path(path)
    if not config_path.is_absolute() or config_path.is_symlink() or not config_path.is_file():
        raise ProductionCompositionError("TEST configuration must be an absolute regular file")
    config = _load(config_path)
    required = {"schema", "mode", "domain", "episode_id", "output_root"}
    if (set(config) != required or config.get("schema") != "JACO_FLUX3_TEST_COMPOSITION/1"
            or config.get("mode") != "TEST" or config.get("domain") != "LOCAL_ONLY_NON_PROMOTABLE"
            or config.get("episode_id") != "JACO_GENESIS28" or not isinstance(config.get("output_root"), str)):
        raise ProductionCompositionError("closed non-promotable TEST configuration required")
    root = config_path.parent.resolve()
    output = Path(config["output_root"])
    if not output.is_absolute() or output.resolve().parent != root or output.is_symlink():
        raise ProductionCompositionError("TEST output root must be a direct child of its local policy root")
    return root, output.resolve()


def _test_receipt(*, packet_sha256: str, review_sha256: str, quote_sha256: str) -> dict[str, str | int]:
    """A mechanical TEST receipt, deliberately not a mandate or LIVE approval."""
    receipt: dict[str, str | int] = {
        "schema": 1, "kind": "JACO_FLUX3_FINAL_ADMISSION", "request_id": REQUEST_ID,
        "endpoint": ENDPOINT, "reserved_usd": MAX_USD, "call_limit": 1,
        "packet_sha256": packet_sha256, "review_sha256": review_sha256,
        "authorization_sha256": "0" * 64, "quote_sha256": quote_sha256,
        "authorization_version": "TEST_LOCAL_NON_AUTHORITY", "validated_at_epoch": int(time.time()),
    }
    return {**receipt, "receipt_sha256": sha256(_canon(receipt)).hexdigest()}


class _TestOnlyTransport:
    """Local test boundary: records one simulated POST then only simulated GET recovery."""
    mode = "TEST"

    def __init__(self, root: Path) -> None:
        self.root = root
        self.counts_path = root / "transport-counts.json"

    def _count(self, key: str) -> None:
        counts = json.loads(self.counts_path.read_text()) if self.counts_path.exists() else {"gets": 0, "posts": 0}
        counts[key] += 1
        self.counts_path.write_bytes(_canon(counts))

    async def submit(self, job, request_id, checkpoint):
        self._count("posts")
        checkpoint(provider_id="TEST_REMOTE_ID_001")
        raise RuntimeError("TEST_SUBMISSION_INTERRUPTED_AFTER_REMOTE_ID")

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        if provider_id != "TEST_REMOTE_ID_001":
            raise ProductionCompositionError("TEST recovery remote ID mismatch")
        self._count("gets")
        result = self.root / "recovered-test-result.bin"
        if not result.exists():
            _write_once(result, {"kind": "TEST_ONLY_NO_IMAGE", "request_id": request_id})
        return ProviderResult(result, Decimal(MAX_USD))


def _test_review(output: Path) -> tuple[dict[str, Any], Path, Path]:
    """Materialize the production packet shape, then use the actual subprocess reviewer."""
    run = output / "reviews" / str(time.time_ns())
    run.mkdir(parents=True)
    quote = _write_once(run / "quote.json", _price_snapshot(raw_price_page={
        "prices": [{"endpoint_id": ENDPOINT, "unit_price": 0.048, "unit": "image", "currency": "USD"}],
        "next_cursor": None, "has_more": False,
    }, observed_at_epoch=int(time.time())))
    # Copy reviewed official bytes; TEST never invents a simplified OpenAPI shape.
    official = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "fal_flux3_queue_openapi_20261106.json"
    openapi = run / "openapi.json"
    _write_once(openapi, json.loads(official.read_text(encoding="utf-8")))
    finance = _write_once(run / "financial-record.json", {
        "schema": "jaco-factory-financial-reconciliation/1", "request_hash": REQUEST_ID,
        "currency": "USD", "total_episode_cap": "10.00", "authorizes_generation": False,
    })
    payload_sha = sha256(_canon(_PAYLOAD)).hexdigest()
    packet = _write_once(run / "packet.json", {
        "schema": "jaco-fal-flux3-successor-benchmark/3", "kind": "BOUNDED_FAL_FLUX3_CANARY_PREGEN_PACKET",
        "bindings": {"request_id": REQUEST_ID, "endpoint": ENDPOINT, "payload_sha256": payload_sha,
                     "authenticated_price_quote": {"sha256": _sha_path(quote), "bytes": quote.stat().st_size},
                     "openapi_snapshot": {"sha256": _sha_path(openapi), "bytes": openapi.stat().st_size},
                     "financial_approval": {"sha256": _sha_path(finance), "status": "APPROVED_USD10", "total_episode_cap_usd": "10.00"}},
        "candidate": {"call_limit": 1, "single_output": True, "reference_inputs": [], "payload": _PAYLOAD},
        "cost": {"approved_canary_maximum_usd": MAX_USD, "current_authenticated_quote_maximum_usd": MAX_USD,
                 "actual_billed_amount": "UNKNOWN_UNTIL_PROVIDER_RECEIPT_OR_BILLING_RECONCILIATION"},
    })
    request = _write_once(run / "review-request.json", {
        "schema": "jaco-fal-flux3-independent-pre-generation-review-request/2", "kind": "INDEPENDENT_PREGEN_REVIEW_REQUEST",
        "packet": {"sha256": _sha_path(packet)},
        "required_exact_bindings": {"request_id": REQUEST_ID, "endpoint": ENDPOINT, "payload_sha256": payload_sha,
                                    "approved_canary_maximum_usd": MAX_USD, "successor_packet_sha256": _sha_path(packet)},
        "prohibitions": ["NO_FAL_PAID_POST", "NO_RESERVATION", "NO_GATEWAY_OR_OTHER_EP_WRITE", "NO_TEST_AS_LIVE"],
    })
    policy = build_local_process_review_policy(root=run, packet_path=packet, review_request_path=request,
        quote_path=quote, openapi_path=openapi, financial_record_path=finance, output_path=run / "review.json",
        request_id=REQUEST_ID, review_request_sha256=_sha_path(request))
    return run_local_process_review(policy), packet, quote


async def _run_test_composition(config_path: str) -> dict[str, Any]:
    _root, output = _test_config(config_path)
    output.mkdir(parents=True, exist_ok=True)
    review, packet, quote = _test_review(output)
    ledger = StandingBudgetLedger(output / "standing-budget.sqlite3", policy_sha256="a" * 64,
        episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="TEST")
    executor = Executor(output / "executor.sqlite3", Config(limit=Decimal(10)), standing_ledger=ledger)
    job = Job("JACO_GEN28_CANONICAL_001", "first", "TEST", ENDPOINT, _PAYLOAD, None,
              Decimal(MAX_USD), input_kind="TEXT_TO_IMAGE")
    receipt = _test_receipt(packet_sha256=_sha_path(packet), review_sha256=review["output_sha256"], quote_sha256=_sha_path(quote))
    completed = await FinalAdmissionKernel(executor=executor, ledger=ledger, episode_id="JACO_GENESIS28",
        provider=_TestOnlyTransport(output)).execute(job, receipt)
    return {"mode": "TEST", "domain": "LOCAL_ONLY_NON_PROMOTABLE", "status": completed["status"],
            "review_provenance": review["provenance"], "request_id": job.request_id}

def main(argv=None) -> int:
    parser=argparse.ArgumentParser(); parser.add_argument("--execute",action="store_true"); parser.add_argument("--test-config"); args=parser.parse_args(argv)
    if args.test_config:
        if not args.execute:
            raise ProductionCompositionError("TEST composition requires explicit --execute")
        print(json.dumps(asyncio.run(_run_test_composition(args.test_config)), sort_keys=True)); return 0
    factory=FixedFalProductionFactory(_PROVISIONED_ROOT)
    if not args.execute:
        review, run, _ = asyncio.run(factory.fresh_review()); print(json.dumps({"mode":"GET_ONLY_PREFLIGHT","run":str(run),"review":review["output_sha256"]},sort_keys=True)); return 0
    print(json.dumps(asyncio.run(factory.execute()),sort_keys=True)); return 0

if __name__ == "__main__": raise SystemExit(main())
