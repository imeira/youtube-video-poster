"""Scoped LOCAL_PROCESS_REVIEW for the Jacó FLUX 3 canary.

This is a deliberately narrow, provider-free pre-generation reviewer.  It runs
in a fresh subprocess with a scrubbed environment and only the exact immutable
inputs listed in a policy.  It has no imports for provider, budget, gateway, or
publication code.  Windows same-user subprocess isolation is not hostile-code
isolation; the receipt reports that limitation rather than asserting an
independent organization or security boundary.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
ENDPOINT = "blackforestlabs/flux-3/text-to-image"
_POLICY_KIND = "LOCAL_PROCESS_REVIEW_POLICY"
_POLICY_SCHEMA = 1
_OUTPUT_KIND = "LOCAL_PROCESS_REVIEW_OUTPUT"
_OUTPUT_SCHEMA = 1
_REQUIRED_PROHIBITIONS = [
    "NO_FAL_PAID_POST", "NO_RESERVATION", "NO_GATEWAY_OR_OTHER_EP_WRITE", "NO_TEST_AS_LIVE",
]


class LocalProcessReviewError(ValueError):
    """The local reviewer policy or immutable inputs are not eligible."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return sha256(value).hexdigest()


def _json_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise LocalProcessReviewError("duplicate JSON key")
        result[key] = value
    return result


def _load_json(path: Path, label: str) -> tuple[dict[str, Any], str, int]:
    try:
        raw = path.read_bytes()
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_json_pairs,
            parse_constant=lambda _constant: (_ for _ in ()).throw(LocalProcessReviewError("non-finite JSON value")),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LocalProcessReviewError(f"{label} is unreadable strict JSON") from error
    if not isinstance(value, dict):
        raise LocalProcessReviewError(f"{label} must be a JSON object")
    return value, _sha256_bytes(raw), len(raw)


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError as error:
        raise LocalProcessReviewError("review input is unavailable") from error
    attributes = getattr(info, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse)


def _regular_beneath(path: Path, root: Path, label: str, *, must_exist: bool = True) -> Path:
    if not path.is_absolute() or not root.is_absolute():
        raise LocalProcessReviewError(f"{label} must be absolute")
    # Check lexical ancestors before resolve so a link cannot escape the policy root.
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise LocalProcessReviewError(f"{label} escapes policy root") from error
    current = root
    for piece in relative.parts:
        if _is_reparse(current):
            raise LocalProcessReviewError(f"{label} has a reparse-point ancestor")
        current = current / piece
    if _is_reparse(root) or (must_exist and _is_reparse(path)):
        raise LocalProcessReviewError(f"{label} rejects reparse points")
    if must_exist and (not path.exists() or not path.is_file()):
        raise LocalProcessReviewError(f"{label} must be a regular file")
    return path


def _read_policy(policy_path: Path, *, clock: float) -> tuple[dict[str, Any], Path]:
    policy_path = Path(policy_path)
    if not policy_path.is_absolute():
        raise LocalProcessReviewError("policy path must be absolute")
    root = policy_path.parent
    _regular_beneath(policy_path, root, "policy")
    policy, digest, _size = _load_json(policy_path, "policy")
    required = {"schema", "kind", "scope", "root", "request_id", "review_request_sha256", "quote_expires_at", "inputs", "output_path", "policy_sha256"}
    if set(policy) != required or policy.get("schema") != _POLICY_SCHEMA or policy.get("kind") != _POLICY_KIND:
        raise LocalProcessReviewError("closed LOCAL_PROCESS_REVIEW policy required")
    body = {key: value for key, value in policy.items() if key != "policy_sha256"}
    if policy.get("policy_sha256") != _sha256_bytes(_canonical(body)) or digest != _sha256_bytes(_canonical(policy)):
        raise LocalProcessReviewError("policy hash mismatch")
    if policy.get("scope") != "JACO_FLUX3_ONE_CANARY_EVALUATION" or policy.get("request_id") != REQUEST_ID:
        raise LocalProcessReviewError("policy scope is not the Jacó one-canary scope")
    if policy.get("root") != str(root):
        raise LocalProcessReviewError("policy root mismatch")
    expiry = policy.get("quote_expires_at")
    if type(expiry) is not int or expiry <= clock:
        raise LocalProcessReviewError("quote expiry is stale")
    if expiry - clock > 300:
        raise LocalProcessReviewError("quote expiry exceeds bounded freshness")
    if not isinstance(policy.get("review_request_sha256"), str) or len(policy["review_request_sha256"]) != 64:
        raise LocalProcessReviewError("review request hash is invalid")
    if not isinstance(policy.get("inputs"), dict) or set(policy["inputs"]) != {"packet", "review_request", "quote", "openapi", "financial_record"}:
        raise LocalProcessReviewError("closed reviewer inputs required")
    for name, descriptor in policy["inputs"].items():
        if not isinstance(descriptor, dict) or set(descriptor) != {"path", "sha256", "bytes"}:
            raise LocalProcessReviewError(f"{name} input descriptor is malformed")
        candidate = Path(descriptor["path"])
        _regular_beneath(candidate, root, name)
        actual = _sha256_bytes(candidate.read_bytes())
        if actual != descriptor["sha256"] or candidate.stat().st_size != descriptor["bytes"]:
            raise LocalProcessReviewError(f"{name} input hash mismatch")
    review_descriptor = policy["inputs"]["review_request"]
    if review_descriptor["sha256"] != policy["review_request_sha256"]:
        raise LocalProcessReviewError("review request hash does not bind input")
    output = Path(policy["output_path"])
    _regular_beneath(output, root, "review output", must_exist=False)
    if output.exists():
        raise LocalProcessReviewError("review output is write-once")
    return policy, root


def build_local_process_review_policy(*, root: Path, packet_path: Path, review_request_path: Path,
                                      quote_path: Path, openapi_path: Path, financial_record_path: Path,
                                      output_path: Path, request_id: str, review_request_sha256: str) -> Path:
    """Create a closed policy; it grants review only and cannot grant LIVE dispatch."""
    root = Path(root)
    if not root.is_absolute() or not root.is_dir() or _is_reparse(root):
        raise LocalProcessReviewError("policy root must be an existing non-reparse directory")
    if request_id != REQUEST_ID:
        raise LocalProcessReviewError("literal Jacó request is required")
    sources = {"packet": packet_path, "review_request": review_request_path, "quote": quote_path,
               "openapi": openapi_path, "financial_record": financial_record_path}
    inputs: dict[str, dict[str, object]] = {}
    for name, source in sources.items():
        path = _regular_beneath(Path(source), root, name)
        raw = path.read_bytes()
        inputs[name] = {"path": str(path), "sha256": _sha256_bytes(raw), "bytes": len(raw)}
    if inputs["review_request"]["sha256"] != review_request_sha256:
        raise LocalProcessReviewError("review request hash mismatch")
    quote_document, _quote_digest, _quote_bytes = _load_json(Path(quote_path), "quote")
    provider_expiry = quote_document.get("valid_until_epoch")
    if type(provider_expiry) is not int:
        raise LocalProcessReviewError("provider-issued quote expiry is required; local TTL is not accepted")
    _regular_beneath(Path(output_path), root, "review output", must_exist=False)
    body: dict[str, object] = {
        "schema": _POLICY_SCHEMA, "kind": _POLICY_KIND, "scope": "JACO_FLUX3_ONE_CANARY_EVALUATION",
        "root": str(root), "request_id": request_id, "review_request_sha256": review_request_sha256,
        "quote_expires_at": provider_expiry, "inputs": inputs, "output_path": str(Path(output_path)),
    }
    policy = {**body, "policy_sha256": _sha256_bytes(_canonical(body))}
    path = root / "local-process-review-policy.json"
    if path.exists():
        raise LocalProcessReviewError("policy is write-once")
    _exclusive_json(path, policy)
    return path


def _exclusive_json(path: Path, document: dict[str, Any]) -> None:
    encoded = _canonical(document)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError as error:
        raise LocalProcessReviewError("review output is write-once") from error
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


def _expect(condition: bool, reason: str) -> None:
    if not condition:
        raise LocalProcessReviewError(reason)


def _review(policy: dict[str, Any]) -> dict[str, Any]:
    inputs = policy["inputs"]
    packet, packet_sha, _ = _load_json(Path(inputs["packet"]["path"]), "packet")
    request, request_sha, _ = _load_json(Path(inputs["review_request"]["path"]), "review request")
    quote, quote_sha, quote_bytes = _load_json(Path(inputs["quote"]["path"]), "quote")
    openapi, openapi_sha, openapi_bytes = _load_json(Path(inputs["openapi"]["path"]), "OpenAPI")
    finance, finance_sha, _ = _load_json(Path(inputs["financial_record"]["path"]), "financial record")
    _expect(packet_sha == inputs["packet"]["sha256"] and request_sha == policy["review_request_sha256"], "review packet/request changed")
    _expect(packet.get("schema") == "jaco-fal-flux3-successor-benchmark/3" and packet.get("kind") == "BOUNDED_FAL_FLUX3_CANARY_PREGEN_PACKET", "packet schema mismatch")
    bindings, candidate, cost = packet.get("bindings"), packet.get("candidate"), packet.get("cost")
    _expect(isinstance(bindings, dict) and isinstance(candidate, dict) and isinstance(cost, dict), "packet sections missing")
    _expect(bindings.get("request_id") == REQUEST_ID and bindings.get("endpoint") == ENDPOINT, "packet scope mismatch")
    _expect(request.get("schema") == "jaco-fal-flux3-independent-pre-generation-review-request/2" and request.get("kind") == "INDEPENDENT_PREGEN_REVIEW_REQUEST", "review request schema mismatch")
    exact = request.get("required_exact_bindings")
    _expect(isinstance(exact, dict) and exact.get("request_id") == REQUEST_ID and exact.get("endpoint") == ENDPOINT and exact.get("successor_packet_sha256") == packet_sha, "review request binding mismatch")
    _expect(request.get("packet", {}).get("sha256") == packet_sha, "review request packet hash mismatch")
    _expect(request.get("prohibitions") == _REQUIRED_PROHIBITIONS, "review request prohibitions mismatch")
    payload = candidate.get("payload")
    _expect(isinstance(payload, dict) and set(payload) == {"prompt", "aspect_ratio", "resolution", "output_format", "enable_prompt_expansion", "safety_tolerance"}, "closed payload required")
    _expect(isinstance(payload["prompt"], str) and len(payload["prompt"].strip()) >= 24, "semantic prompt is too short")
    _expect(payload["aspect_ratio"] == "4:3" and payload["resolution"] == "1k" and payload["output_format"] == "png" and payload["enable_prompt_expansion"] is False and type(payload["safety_tolerance"]) is int and 0 <= payload["safety_tolerance"] <= 4, "payload contract mismatch")
    _expect(candidate.get("call_limit") == 1 and candidate.get("single_output") is True and candidate.get("reference_inputs") == [], "one-canary scope mismatch")
    _expect(bindings.get("payload_sha256") == _sha256_bytes(_canonical(payload)) == exact.get("payload_sha256"), "payload hash mismatch")
    _expect(quote_sha == bindings.get("authenticated_price_quote", {}).get("sha256") and quote_bytes == bindings.get("authenticated_price_quote", {}).get("bytes"), "quote binding mismatch")
    _expect(openapi_sha == bindings.get("openapi_snapshot", {}).get("sha256") and openapi_bytes == bindings.get("openapi_snapshot", {}).get("bytes"), "OpenAPI binding mismatch")
    quote_endpoint = quote.get("endpoint_id", quote.get("endpoint"))
    quote_maximum = quote.get("maximum_cost_usd", quote.get("maximum_usd"))
    openapi_endpoint = openapi.get("endpoint")
    if openapi_endpoint is None:
        info = openapi.get("info")
        metadata = info.get("x-fal-metadata") if isinstance(info, dict) else None
        openapi_endpoint = metadata.get("endpointId") if isinstance(metadata, dict) else None
    _expect(quote_endpoint == ENDPOINT and quote.get("currency") == "USD" and quote_maximum == "0.048", "quote value mismatch")
    _expect(openapi_endpoint == ENDPOINT, "OpenAPI endpoint mismatch")
    _expect(cost.get("approved_canary_maximum_usd") == "0.048" and cost.get("current_authenticated_quote_maximum_usd") == "0.048" and cost.get("actual_billed_amount") == "UNKNOWN_UNTIL_PROVIDER_RECEIPT_OR_BILLING_RECONCILIATION", "cost contract mismatch")
    _expect(finance_sha == bindings.get("financial_approval", {}).get("sha256") and finance.get("request_hash") == REQUEST_ID and finance.get("currency") == "USD" and finance.get("total_episode_cap") == "10.00" and finance.get("authorizes_generation") is False, "financial record mismatch")
    output = {
        "schema": _OUTPUT_SCHEMA, "kind": _OUTPUT_KIND, "reviewer": "LOCAL_PROCESS_REVIEW_AUTOMATED_V1",
        "verdict": "PASS", "admission_state": "READY_FOR_PARENT_VERIFICATION_ONLY",
        "scope": "JACO_FLUX3_ONE_CANARY_EVALUATION", "request_id": REQUEST_ID, "endpoint": ENDPOINT,
        "packet_sha256": packet_sha, "review_request_sha256": request_sha,
        "quote_sha256": quote_sha, "openapi_sha256": openapi_sha, "financial_record_sha256": finance_sha,
        "prohibitions": _REQUIRED_PROHIBITIONS,
        "isolation": {"fresh_process": True, "environment": "SCRUBBED_MINIMAL", "provider_secrets_available": False,
                      "spend_credentials_available": False, "gateway_available": False, "publish_available": False,
                      "allowed_inputs": "READ_ONLY_HASH_BOUND_ALLOWLIST", "output": "WRITE_ONLY_REVIEW_OUTPUT",
                      "same_host_threat_model": "NOT_HOSTILE_CODE_ISOLATION"},
    }
    output["output_sha256"] = _sha256_bytes(_canonical(output))
    return output


def _reviewer_main(policy_path: str) -> int:
    try:
        policy, _root = _read_policy(Path(policy_path), clock=time.time())
        output = _review(policy)
        _exclusive_json(Path(policy["output_path"]), output)
        print(_canonical(output).decode("utf-8"))
        return 0
    except LocalProcessReviewError as error:
        print(f"LOCAL_PROCESS_REVIEW_BLOCKED:{error}", file=sys.stderr)
        return 2


def run_local_process_review(policy_path: Path, *, clock=time.time) -> dict[str, Any]:
    """Validate first, then run the reviewer with an empty credential environment."""
    policy, _root = _read_policy(Path(policy_path), clock=clock())
    environment = {"PYTHONIOENCODING": "utf-8"}
    if os.name == "nt" and os.environ.get("SYSTEMROOT"):
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    completed = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--reviewer", str(Path(policy_path))],
        cwd=str(Path(policy["output_path"]).parent), env=environment, check=False,
        capture_output=True, text=True, timeout=30,
    )
    if completed.returncode != 0:
        raise LocalProcessReviewError(f"reviewer process blocked: {completed.stderr.strip()}")
    output_path = Path(policy["output_path"])
    output, digest, _ = _load_json(output_path, "review output")
    body = {key: value for key, value in output.items() if key != "output_sha256"}
    if output.get("output_sha256") != _sha256_bytes(_canonical(body)) or digest != _sha256_bytes(_canonical(output)):
        raise LocalProcessReviewError("review output readback hash mismatch")
    if output.get("verdict") != "PASS" or output.get("admission_state") != "READY_FOR_PARENT_VERIFICATION_ONLY":
        raise LocalProcessReviewError("review output is not ready")
    return output


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--reviewer":
        raise SystemExit(_reviewer_main(sys.argv[2]))
    raise SystemExit("LOCAL_PROCESS_REVIEW runner requires --reviewer POLICY_PATH")
