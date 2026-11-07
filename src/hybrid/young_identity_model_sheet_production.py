"""Protected, unprovisioned LIVE composition for the young identity model sheet.

This is deliberately separate from the legacy TEST composition.  ``execute``
is a real LIVE route once an independently issued qualification is installed;
this repository ships no such authority.
"""
from __future__ import annotations

import json
import os
import re
import time
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path("C:/HermesStudio/production/jaco-flux3")
# `_002` was only an unprovisioned planning name.  This route is the protected
# three-view preflight for the requested `_001`; the separate composition
# module's `_001` remains an explicitly TEST-only fixture implementation.
OPERATION_ID = "JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001"
EPISODE_ID = "JACO_GENESIS28"
ENDPOINT = "blackforestlabs/flux-3/text-to-image"
REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
FINANCIAL_SHA256 = "916e8230db47406db96cbe774b9332c00ed24cf156c66d4d91d11a213b64f50d"
# The standing-operation digest is deliberately separate from the financial
# reconciliation digest.  It is supplied by the protected authority, never by
# a command-line caller or the quote path.
OPERATION_POLICY_SHA256_PREFIX = "46bb"
POLICY_PATH = ROOT / "standing-policy" / "JACO_GENESIS28_STANDING_OPERATION_POLICY.v1.json"
PREFLIGHT_PATH = ROOT / "preflight" / f"{OPERATION_ID}.preflight.json"
AUTHORITY_PATH = ROOT / "standing-policy" / f"{OPERATION_ID}.authority.v2.json"
QUALIFICATION_PATH = ROOT / "standing-policy" / f"{OPERATION_ID}.qualification.v2.json"
LEDGER_PATH = ROOT / "ledger" / "standing-budget.sqlite3"
FINANCIAL_SOURCE_PATH = ROOT / "financial-record-source.json"
CAP = Decimal("0.048")
EXPECTED_PAYLOAD = {
    "prompt": (
        "Young identity model sheet, exactly three labeled views in one 16:9 "
        "image: front view, profile view, and back view; consistent child-safe "
        "character design, neutral presentation, no extra poses or text."
    ),
    "aspect_ratio": "16:9", "resolution": "1k", "output_format": "png",
    "enable_prompt_expansion": False, "safety_tolerance": 2,
}


class ProductionAdmissionError(PermissionError):
    pass


_REVIEW_CAPABILITY_TOKEN = object()


class _ValidatedReviewCapability:
    """Non-caller-constructible, composition-owned independent-review proof."""

    __slots__ = ("run", "packet_sha256", "request_sha256", "quote_sha256", "openapi_sha256", "financial_sha256")

    def __init__(self, token, run, packet_sha256, request_sha256, quote_sha256, openapi_sha256, financial_sha256):
        if token is not _REVIEW_CAPABILITY_TOKEN:
            raise ProductionAdmissionError("independent review capability is composition-owned")
        self.run = run
        self.packet_sha256 = packet_sha256
        self.request_sha256 = request_sha256
        self.quote_sha256 = quote_sha256
        self.openapi_sha256 = openapi_sha256
        self.financial_sha256 = financial_sha256


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ProductionAdmissionError(f"{label} must be a lower-case SHA-256")
    return value


def _exact(document: object, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(document, dict) or set(document) != fields:
        raise ProductionAdmissionError(f"closed {label} schema required")
    return document


def _safe_path(path: Path, label: str) -> Path:
    # Check every existing ancestor before resolving it: resolve() alone hides a
    # Windows junction/reparse hop.
    absolute = path.absolute()
    for ancestor in (absolute, *absolute.parents):
        try:
            info = ancestor.lstat()
        except OSError:
            continue
        if ancestor.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & getattr(os, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise ProductionAdmissionError(f"{label} must not traverse a reparse point")
    return absolute.resolve()


def _load(path: Path, label: str) -> tuple[dict[str, Any], str]:
    try:
        safe = _safe_path(path, label)
        raw = safe.read_bytes(); value = json.loads(raw.decode(), object_pairs_hook=_no_duplicates)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ProductionAdmissionError(f"{label} is not strict readable JSON") from exc
    if not isinstance(value, dict): raise ProductionAdmissionError(f"{label} must be an object")
    return value, sha256(raw).hexdigest()


def _write_once(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); raw = _canonical(value)
    try: fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError as exc: raise ProductionAdmissionError("durable marker collision") from exc
    with os.fdopen(fd, "wb") as stream: stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def _money(value: object, label: str) -> Decimal:
    if not isinstance(value, str):
        raise ProductionAdmissionError(f"{label} must be a decimal string")
    try: amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc: raise ProductionAdmissionError(f"{label} is invalid") from exc
    if not amount.is_finite() or amount < 0: raise ProductionAdmissionError(f"{label} is invalid")
    return amount


def _canonical_consent(policy: dict[str, Any]) -> dict[str, Any]:
    """Use the production canonical-state verifier; never reimplement it here."""
    fields = {"schema", "session_id", "user_message_id", "adjacent_assistant_message_id",
              "user_message_sha256", "assistant_message_sha256", "financial_record_sha256",
              "review_source_sha256"}
    consent = _exact(policy["canonical_consent"], fields, "canonical consent")
    if consent["schema"] != "JACO_FLUX3_CANONICAL_TRANSCRIPT_PROVENANCE/1":
        raise ProductionAdmissionError("canonical consent provenance schema changed")
    for field in ("user_message_sha256", "assistant_message_sha256", "financial_record_sha256", "review_source_sha256"):
        _digest(consent[field], f"canonical consent {field}")
    try:
        # This is the protected implementation used by the canonical production
        # composition.  It opens the canonical state DB read-only and verifies
        # exact session, adjacent IDs, roles, and content hashes.
        from src.hybrid.jaco_flux3_production import _validate_canonical_transcript
        _validate_canonical_transcript(consent)
    except PermissionError as exc:
        raise ProductionAdmissionError("canonical transcript provenance rejected") from exc
    return consent


class YoungIdentityModelSheetProduction:
    """The deployment-pinned composition; no caller can select its authority."""
    def __init__(self) -> None:
        self.root, self.authority_path, self._test_only = ROOT.resolve(), AUTHORITY_PATH.resolve(), False

    @classmethod
    def _for_test(cls, root: Path, authority_path: Path):
        obj = object.__new__(cls); obj.root = Path(root).resolve(); obj.authority_path = Path(authority_path).resolve(); obj._test_only = True
        return obj

    def _authority(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        authority, _ = _load(self.authority_path, "protected young identity authority")
        _exact(authority, {"schema", "status", "operation_id", "episode_id", "predecessor_operation_id", "executor_predecessor_request_id", "executor_category", "migration_reason", "non_reuse", "policy_path", "policy_sha256", "preflight_path", "preflight_sha256", "maximum_next_cost_usd", "mode", "canonical_ledger", "deployment_qualification"}, "authority")
        if authority["schema"] != "jaco-derived-operation-authority/v2" or authority["status"] != "READY_FOR_INDEPENDENT_PARENT_REVIEW_NO_RESERVATION_NO_POST": raise ProductionAdmissionError("invalid authority status")
        if authority["operation_id"] != OPERATION_ID or authority["episode_id"] != EPISODE_ID or authority["mode"] not in {"TEST", "LIVE"}: raise ProductionAdmissionError("wrong operation scope")
        # `_001` has no predecessor execution.  It is a distinct three-view
        # preflight, not a claimed migration/rejection of any earlier work.
        if authority["predecessor_operation_id"] != "" or authority["executor_predecessor_request_id"] != "" or authority["executor_category"] != "first" or authority["migration_reason"] != "INITIAL_THREE_VIEW_PREFLIGHT" or authority["non_reuse"] is not True:
            raise ProductionAdmissionError("invalid initial-operation lineage")
        policy_path, preflight_path = _safe_path(Path(authority["policy_path"]), "policy path"), _safe_path(Path(authority["preflight_path"]), "preflight path")
        if policy_path != (self.root / "standing-policy" / POLICY_PATH.name).resolve() or preflight_path != (self.root / "preflight" / PREFLIGHT_PATH.name).resolve(): raise ProductionAdmissionError("caller-selected protected root")
        policy, policy_sha = _load(policy_path, "protected standing policy"); preflight, preflight_sha = _load(preflight_path, "protected preflight")
        _digest(authority["policy_sha256"], "policy hash"); _digest(authority["preflight_sha256"], "preflight hash")
        if authority["policy_sha256"] != policy_sha or authority["preflight_sha256"] != preflight_sha: raise ProductionAdmissionError("protected hash changed")
        _exact(policy, {"schema", "status", "episode_id", "canonical_consent", "financial_baseline"}, "standing policy")
        if policy["schema"] != "jaco-standing-episode-operation-policy/v2" or policy["status"] != "ACTIVE_FOR_DERIVATION_REQUIRES_INDEPENDENT_OPERATION_REVIEW" or policy["episode_id"] != EPISODE_ID: raise ProductionAdmissionError("standing policy changed")
        consent = _canonical_consent(policy)
        baseline = _exact(policy["financial_baseline"], {"currency", "total_episode_cap", "remaining_usd", "pending_usd", "source_sha256"}, "financial baseline")
        if baseline["currency"] != "USD" or _money(baseline["total_episode_cap"], "episode cap") != Decimal(10) or _money(baseline["remaining_usd"], "remaining") < CAP or _money(baseline["pending_usd"], "pending") < 0: raise ProductionAdmissionError("financial baseline changed")
        _digest(baseline["source_sha256"], "financial baseline source")
        _exact(preflight, {"schema", "status", "episode_id", "request_id", "operation", "replay_prohibition"}, "preflight")
        if preflight["schema"] != "jaco-episode-operation-preflight/v2" or preflight["status"] != "VALIDATED_PRE_DISPATCH_BLOCKED_MISSING_OPERATION_AUTHORIZATION" or preflight["episode_id"] != EPISODE_ID or preflight["request_id"] != REQUEST_ID or preflight["replay_prohibition"] is not True: raise ProductionAdmissionError("preflight binding changed")
        operation = _exact(preflight["operation"], {"id", "kind", "endpoint", "call_limit", "maximum_usd", "payload", "payload_sha256"}, "operation")
        if operation["id"] != OPERATION_ID or operation["kind"] != "MODEL_SHEET_THREE_VIEW" or operation["endpoint"] != ENDPOINT or type(operation["call_limit"]) is not int or operation["call_limit"] != 1: raise ProductionAdmissionError("preflight operation binding changed")
        payload = operation.get("payload")
        expected_payload_sha = operation.get("payload_sha256")
        if (payload != EXPECTED_PAYLOAD
                or _digest(expected_payload_sha, "payload hash") != expected_payload_sha
                or expected_payload_sha != sha256(_canonical(EXPECTED_PAYLOAD)).hexdigest()):
            raise ProductionAdmissionError("exact three-views payload required")
        if _money(operation["maximum_usd"], "operation cap") != CAP or _money(authority["maximum_next_cost_usd"], "authority cap") != CAP: raise ProductionAdmissionError("cap binding changed")
        # The transcript provenance is not merely syntactically valid: its two
        # non-message sources are bound into this protected chain as well.
        if (consent["financial_record_sha256"] != baseline["source_sha256"]
                or consent["review_source_sha256"] != authority["preflight_sha256"]):
            raise ProductionAdmissionError("canonical consent source bindings changed")
        return authority, policy, preflight

    def _qualification(self, authority: dict[str, Any]) -> dict[str, Any]:
        binding = authority.get("deployment_qualification")
        if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}: raise ProductionAdmissionError("protected qualification required")
        _digest(binding["sha256"], "qualification hash")
        path = _safe_path(Path(binding["path"]), "qualification path")
        if path != (self.root / "standing-policy" / QUALIFICATION_PATH.name).resolve(): raise ProductionAdmissionError("caller-selected qualification")
        q, digest = _load(path, "protected qualification")
        required = {"schema", "status", "operation_id", "episode_id", "policy_sha256", "preflight_sha256", "mode", "authorization", "review_packet_schema", "reviewer", "ci_digest_sha256", "execution_code_sha256", "operation_policy_sha256", "financial_policy_sha256"}
        test_required = required | {"consent_fixture"}; is_test = q.get("mode") == "TEST"
        if (set(q) != (test_required if is_test else required)
                or digest != binding["sha256"]
                or q.get("schema") != ("jaco-test-deployment-qualification/v2" if is_test else "jaco-protected-deployment-qualification/v2")
                or q.get("status") != "QUALIFIED" or q.get("mode") not in {"TEST", "LIVE"}
                or q.get("operation_id") != OPERATION_ID or q.get("episode_id") != EPISODE_ID
                or q.get("policy_sha256") != authority["policy_sha256"]
                or q.get("operation_policy_sha256") != authority["policy_sha256"]
                or q.get("preflight_sha256") != authority["preflight_sha256"]
                or q.get("financial_policy_sha256") != authority.get("canonical_ledger", {}).get("financial_policy_sha256")
                or q.get("review_packet_schema") != "jaco-fal-flux3-successor-benchmark/3"
                or q.get("reviewer") != "LOCAL_PROCESS_REVIEW_AUTOMATED_V1"
                or _digest(q.get("ci_digest_sha256"), "CI digest") != q["ci_digest_sha256"]
                or _digest(q.get("execution_code_sha256"), "execution code digest") != sha256(Path(__file__).read_bytes()).hexdigest()
                or q.get("authorization") != ("TEST_FIXTURE_CONSENT_ONLY" if is_test else "STANDING_USER_AUTHORIZATION_PROTECTED_POLICY")
                or (is_test and (not self._test_only or q.get("consent_fixture", {}).get("kind") != "TEST_ONLY_CONSENT_FIXTURE"))
                or (not is_test and self._test_only)):
            raise ProductionAdmissionError("invalid protected qualification")
        return q

    def _ledger_binding(self, authority: dict[str, Any]) -> tuple[Path, str, dict[str, Any]]:
        binding = authority.get("canonical_ledger", {})
        source = binding.get("financial_source", {}) if isinstance(binding, dict) else {}
        if (not isinstance(binding, dict)
                or set(binding) != {"path", "financial_policy_sha256", "operation_policy_sha256", "financial_source"}
                or _safe_path(Path(binding["path"]), "ledger path") != (self.root / "ledger" / LEDGER_PATH.name).resolve()
                or (not self._test_only and binding.get("financial_policy_sha256") != FINANCIAL_SHA256)
                or binding.get("operation_policy_sha256") != authority.get("policy_sha256")
                or not isinstance(source, dict) or set(source) != {"path", "sha256"}
                or _safe_path(Path(source["path"]), "financial source path") != (self.root / FINANCIAL_SOURCE_PATH.name).resolve()):
            raise ProductionAdmissionError("financial binding changed")
        _digest(binding["financial_policy_sha256"], "financial policy hash"); _digest(binding["operation_policy_sha256"], "operation policy hash"); _digest(source["sha256"], "financial source hash")
        financial_source, source_sha = _load(Path(source["path"]), "financial source")
        expected_financial_sha = source_sha if self._test_only else FINANCIAL_SHA256
        if source_sha != expected_financial_sha or source.get("sha256") != source_sha:
            raise ProductionAdmissionError("financial source changed")
        _exact(financial_source, {"schema", "request_hash", "revision", "currency", "total_episode_cap", "status", "approval_scope", "authorizes_generation"}, "financial source")
        if (financial_source["schema"] != "jaco-factory-financial-reconciliation/1" or financial_source["request_hash"] != REQUEST_ID
                or financial_source["currency"] != "USD" or financial_source["total_episode_cap"] != "10.00"
                or financial_source["status"] != "APPROVED_USD10" or financial_source["approval_scope"] != "FINANCIAL_ONLY"
                or financial_source["authorizes_generation"] is not False):
            raise ProductionAdmissionError("financial source scope changed")
        return Path(binding["path"]).resolve(), expected_financial_sha, financial_source

    def _validate_fresh_quote(self, quote: dict[str, Any], openapi: dict[str, Any]) -> None:
        quote_fields = {"schema", "endpoint_id", "currency", "maximum_usd", "unit_price_usd", "provider_unit", "semantic_unit", "quantity", "api_url", "raw_sanitized_sha256", "observed_at_epoch", "provider_expiry", "price_validity"}
        if not isinstance(quote, dict) or set(quote) != quote_fields:
            raise ProductionAdmissionError("closed authenticated quote schema required")
        if (quote["schema"] != "FAL_AUTHENTICATED_PRICE_CONSUMER_SNAPSHOT/2" or quote["endpoint_id"] != ENDPOINT
                or quote["currency"] != "USD" or quote["api_url"] != "https://api.fal.ai/v1/models/pricing"
                or quote["price_validity"] != "LOCAL_OBSERVED_AT_MAX_AGE_SECONDS_300_NOT_PROVIDER_EXPIRY"
                or type(quote["quantity"]) is not int or quote["quantity"] != 1
                or _digest(quote["raw_sanitized_sha256"], "quote digest") != quote["raw_sanitized_sha256"]
                or _money(quote["maximum_usd"], "fresh quote") != CAP
                or _money(quote["unit_price_usd"], "fresh unit price") != CAP
                or type(quote["observed_at_epoch"]) not in {int, float}
                or time.time() - quote["observed_at_epoch"] > 300):
            raise ProductionAdmissionError("fresh cap-bound quote required")
        try:
            metadata = openapi["info"]["x-fal-metadata"]
            schema = openapi["components"]["schemas"]["Flux3TextToImageInput"]
            server = openapi["servers"][0]["url"]
            post = openapi["paths"][f"/{ENDPOINT}"]["post"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProductionAdmissionError("official OpenAPI schema is incomplete") from exc
        expected = {"prompt", "aspect_ratio", "resolution", "output_format", "enable_prompt_expansion", "safety_tolerance"}
        if (not isinstance(openapi, dict) or metadata.get("endpointId") != ENDPOINT or server != "https://queue.fal.run"
                or not isinstance(schema.get("properties"), dict) or not expected.issubset(schema["properties"])
                or not isinstance(post, dict)):
            raise ProductionAdmissionError("OpenAPI endpoint or payload schema drifted")

    def _validated_review_capability(self, run, quote, openapi, financial_source):
        """Read back a reviewer run before it can become an admission proof."""
        from src.hybrid.local_process_review import validate_local_process_review_output

        run = _safe_path(Path(run), "independent review run")
        if run.parent != _safe_path(self.root / "operation-runs", "independent review root"):
            raise ProductionAdmissionError("independent review run is outside the fixed root")
        packet, packet_sha = _load(run / "packet.json", "review packet")
        request, request_sha = _load(run / "review-request.json", "review request")
        reviewed_quote, quote_sha = _load(run / "quote.json", "review quote")
        reviewed_openapi, openapi_sha = _load(run / "openapi.json", "review OpenAPI")
        reviewed_financial, financial_sha = _load(run / "financial-record.json", "review financial record")
        receipt, _receipt_sha = _load(run / "review.json", "review receipt")
        if reviewed_quote != quote or reviewed_openapi != openapi or reviewed_financial != financial_source:
            raise ProductionAdmissionError("review inputs are not the composed immutable inputs")
        payload = packet.get("candidate", {}).get("payload") if isinstance(packet, dict) else None
        if (payload != EXPECTED_PAYLOAD
                or packet.get("bindings", {}).get("request_id") != REQUEST_ID
                or packet.get("bindings", {}).get("endpoint") != ENDPOINT
                or packet.get("bindings", {}).get("payload_sha256") != sha256(_canonical(payload)).hexdigest()
                or request.get("packet", {}).get("sha256") != packet_sha
                or request.get("required_exact_bindings", {}).get("request_id") != REQUEST_ID
                or request.get("required_exact_bindings", {}).get("endpoint") != ENDPOINT):
            raise ProductionAdmissionError("review packet/request bindings changed")
        try:
            receipt = validate_local_process_review_output(
                receipt, packet_sha256=packet_sha, review_request_sha256=request_sha,
                quote_sha256=quote_sha, openapi_sha256=openapi_sha,
                financial_record_sha256=financial_sha,
            )
        except ValueError as exc:
            raise ProductionAdmissionError("independent review receipt is invalid") from exc
        reviewed_at = receipt["reviewed_at_epoch"]
        if reviewed_at > int(time.time()) or int(time.time()) - reviewed_at > 300:
            raise ProductionAdmissionError("independent review receipt is stale")
        return _ValidatedReviewCapability(
            _REVIEW_CAPABILITY_TOKEN, run, packet_sha, request_sha, quote_sha, openapi_sha, financial_sha,
        )

    def _review_for_execution(self, quote, openapi, financial_source):
        from src.hybrid.local_process_review import (
            build_local_process_review_policy,
            run_local_process_review,
        )
        authority, _, preflight = self._authority()
        self._qualification(authority)
        self._validate_fresh_quote(quote, openapi)
        run = self.root / "operation-runs" / f"{OPERATION_ID}-{time.time_ns()}"
        run.mkdir(parents=True, exist_ok=False)
        quote_path, openapi_path = run / "quote.json", run / "openapi.json"
        _write_once(quote_path, quote); _write_once(openapi_path, openapi)
        finance_path = run / "financial-record.json"; _write_once(finance_path, financial_source)
        payload = preflight["operation"]["payload"]
        payload_sha = sha256(_canonical(payload)).hexdigest()
        packet = run / "packet.json"
        _write_once(packet, {"schema": "jaco-fal-flux3-successor-benchmark/3", "kind": "BOUNDED_FAL_FLUX3_CANARY_PREGEN_PACKET", "bindings": {"request_id": REQUEST_ID, "endpoint": ENDPOINT, "payload_sha256": payload_sha, "authenticated_price_quote": {"sha256": sha256(quote_path.read_bytes()).hexdigest(), "bytes": quote_path.stat().st_size}, "openapi_snapshot": {"sha256": sha256(openapi_path.read_bytes()).hexdigest(), "bytes": openapi_path.stat().st_size}, "financial_approval": {"sha256": sha256(finance_path.read_bytes()).hexdigest(), "status": "APPROVED_USD10", "total_episode_cap_usd": "10.00"}}, "candidate": {"call_limit": 1, "single_output": True, "reference_inputs": [], "payload": payload}, "cost": {"approved_canary_maximum_usd": "0.048", "current_authenticated_quote_maximum_usd": "0.048", "actual_billed_amount": "UNKNOWN_UNTIL_PROVIDER_RECEIPT_OR_BILLING_RECONCILIATION"}})
        request = run / "review-request.json"
        _write_once(request, {"schema": "jaco-fal-flux3-independent-pre-generation-review-request/2", "kind": "INDEPENDENT_PREGEN_REVIEW_REQUEST", "packet": {"sha256": sha256(packet.read_bytes()).hexdigest()}, "required_exact_bindings": {"request_id": REQUEST_ID, "endpoint": ENDPOINT, "payload_sha256": payload_sha, "approved_canary_maximum_usd": "0.048", "successor_packet_sha256": sha256(packet.read_bytes()).hexdigest()}, "prohibitions": ["NO_FAL_PAID_POST", "NO_RESERVATION", "NO_GATEWAY_OR_OTHER_EP_WRITE", "NO_TEST_AS_LIVE"]})
        policy = build_local_process_review_policy(root=run, packet_path=packet, review_request_path=request, quote_path=quote_path, openapi_path=openapi_path, financial_record_path=finance_path, output_path=run / "review.json", request_id=REQUEST_ID, review_request_sha256=sha256(request.read_bytes()).hexdigest())
        run_local_process_review(policy)
        return self._validated_review_capability(run, quote, openapi, financial_source)

    async def _execute_prepared(self, *, quote: dict[str, Any], openapi: dict[str, Any], credential_loader=None, test_provider_factory=None, _review_capability=None) -> dict[str, Any]:
        from src.hybrid.execution import Authorization, Executor, Job, Price
        from src.hybrid.planner import Config
        from src.hybrid.standing_budget import StandingBudgetLedger
        authority, _, preflight = self._authority()
        qualification = self._qualification(authority)
        ledger_path, financial_sha, _financial_source = self._ledger_binding(authority)
        self._validate_fresh_quote(quote, openapi)
        # The independent review is the last read-only/pre-execution barrier.
        # No caller path is authority; only an internal, immediately read-back
        # capability may cross this boundary.
        execution = self.root / "execution" / OPERATION_ID
        unprepared_execution = not (execution / "executor.sqlite3").exists()
        if not isinstance(_review_capability, _ValidatedReviewCapability):
            raise ProductionAdmissionError("missing independent review for unprepared execution")
        revalidated_review = self._validated_review_capability(
            _review_capability.run, quote, openapi, _financial_source,
        )
        if tuple(getattr(_review_capability, name) for name in _ValidatedReviewCapability.__slots__) != tuple(
            getattr(revalidated_review, name) for name in _ValidatedReviewCapability.__slots__
        ):
            raise ProductionAdmissionError("independent review capability changed")
        job = Job(OPERATION_ID, authority["executor_category"], qualification["mode"], ENDPOINT, preflight["operation"]["payload"], None, CAP, predecessor=authority["executor_predecessor_request_id"], input_kind="TEXT_TO_IMAGE")
        scope = "TEST_LOCAL_ONLY" if qualification["mode"] == "TEST" else f"{OPERATION_ID}:{authority['policy_sha256']}"; marker_path = execution / "protected-qualification.json"
        def marker_for(bound):
            return {"schema": 2, "kind": "JACO_FLUX3_FINAL_ADMISSION_MARKER", "receipt_path": str(marker_path), "receipt_sha256": sha256(marker_path.read_bytes()).hexdigest(), "request_id": bound.request_id, "endpoint": ENDPOINT, "payload_sha256": bound.intent_input_sha256, "input_kind": "TEXT_TO_IMAGE", "auth_scope": scope, "reserved_usd": "0.048", "trust_identity": "YOUNG_IDENTITY_MODEL_SHEET_PROTECTED_QUALIFICATION"}
        def verify(bound, marker, claim):
            if (not marker_path.is_file()
                    or json.loads(marker_path.read_text(encoding="utf-8")) != qualification
                    or marker != marker_for(bound)
                    or claim.get("auth_scope") != scope
                    or claim.get("provider_request_id") in {None, ""}):
                raise ProductionAdmissionError("durable qualification marker changed")
        execution.mkdir(parents=True, exist_ok=True)
        ledger = StandingBudgetLedger(ledger_path, policy_sha256=financial_sha, episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode=qualification["mode"])
        executor = Executor(execution / "executor.sqlite3", Config(limit=Decimal(10)), standing_ledger=ledger, _jaco_final_admission_verifier=verify, _jaco_production_binding={"kind":"YOUNG_IDENTITY_MODEL_SHEET_PRODUCTION", "auth_scope":scope})
        if executor.inspect(job.request_id) is None:
            _write_once(marker_path, qualification)
            executor.prepare_intent(job, auth_scope=scope); claim = ledger.reserve(job.request_id, authority["predecessor_operation_id"] or EPISODE_ID, ENDPOINT, str(CAP), settlement_mode="FAL_READBACK_V2", auth_scope=scope, provider_request_id="UNASSIGNED", authority_mode=qualification["mode"])
            if claim.get("state") != "RESERVED": raise ProductionAdmissionError("canonical reservation rejected")
            executor._change(job.request_id, final_admission_marker=marker_for(job))
            for name in ("packet.json", "review.json", "quote.json", "openapi.json"):
                document, _document_sha = _load(_review_capability.run / name, f"review {name}")
                _write_once(execution / name, document)
        if self._test_only:
            if not callable(test_provider_factory): raise ProductionAdmissionError("private test requires fixture transport")
            provider = test_provider_factory(execution / "provider-receipt.json", openapi)
        else:
            if not callable(credential_loader):
                raise ProductionAdmissionError("credential loader required after review")
            from src.hybrid.jaco_flux3_production import _FixedFalProvider
            class OperationFixedFalProvider(_FixedFalProvider): mode = "LIVE"
            # Credentials are read only after both protected and independent
            # reviews have accepted the exact GET-only inputs.
            provider = OperationFixedFalProvider(execution / "provider-receipt.json", openapi, credential_loader())
        auth = None if qualification["mode"] == "TEST" else Authorization(job.request_id, qualification["authorization"], time.time()+300, CAP)
        price = None if qualification["mode"] == "TEST" else Price(ENDPOINT, job.request_id, CAP, time.time()+300, "fresh-cap-bound-quote")
        completed = await executor.run(job, provider, authorization=auth, price=price, settlement_checkpoint=lambda lid,rid: ledger.bind_provider_request_id(lid,rid), submit_prepared=True)
        return {"operation_id": OPERATION_ID, "status": completed["status"], "request_id": job.request_id, "mode": qualification["mode"]}

    async def _execute_with_composition(self, *, quote_and_openapi, credential_loader=None, test_provider_factory=None):
        authority, _, _ = self._authority(); qualification = self._qualification(authority); _, _, financial = self._ledger_binding(authority)
        # The first stage above is protected, local, and read-only.  A LIVE
        # credential may now be loaded only to authenticate the GET-only price
        # and OpenAPI collection; it is not retained or passed to execution.
        credential = "" if qualification["mode"] == "TEST" else (
            credential_loader() if callable(credential_loader) else (_ for _ in ()).throw(ProductionAdmissionError("credential unavailable"))
        )
        quote, openapi = await quote_and_openapi(credential)
        review_capability = self._review_for_execution(quote, openapi, financial)
        return await self._execute_prepared(quote=quote, openapi=openapi, credential_loader=(lambda: credential), test_provider_factory=test_provider_factory, _review_capability=review_capability)

    async def execute(self) -> dict[str, Any]:
        import asyncio

        from src.hybrid.fal_get_only_preflight import resolve_default_fal_credential
        from src.hybrid.jaco_flux3_production import (
            _FAL_OPENAPI_URL,
            _current_price_snapshot,
            _get_json,
            _official_openapi_endpoint,
        )
        async def inputs(credential):
            quote, openapi = await asyncio.gather(_current_price_snapshot(credential), asyncio.to_thread(_get_json, _FAL_OPENAPI_URL)); _official_openapi_endpoint(openapi); return quote, openapi
        return await self._execute_with_composition(quote_and_openapi=inputs, credential_loader=lambda: resolve_default_fal_credential()[0])
