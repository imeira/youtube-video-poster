"""Protected, unprovisioned LIVE composition for the young identity model sheet.

This is deliberately separate from the legacy TEST composition.  ``execute``
is a real LIVE route once an independently issued qualification is installed;
this repository ships no such authority.
"""
from __future__ import annotations

import json
import os
import time
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path("C:/HermesStudio/production/jaco-flux3")
OPERATION_ID = "JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_002"
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


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _load(path: Path, label: str) -> tuple[dict[str, Any], str]:
    try:
        info = path.lstat()
        if path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & getattr(os, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise ProductionAdmissionError(f"{label} must not be a reparse point")
        raw = path.read_bytes(); value = json.loads(raw.decode())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionAdmissionError(f"{label} is not strict readable JSON") from exc
    if not isinstance(value, dict): raise ProductionAdmissionError(f"{label} must be an object")
    return value, sha256(raw).hexdigest()


def _write_once(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); raw = _canonical(value)
    try: fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    except FileExistsError as exc: raise ProductionAdmissionError("durable marker collision") from exc
    with os.fdopen(fd, "wb") as stream: stream.write(raw); stream.flush(); os.fsync(stream.fileno())


def _money(value: object, label: str) -> Decimal:
    try: amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc: raise ProductionAdmissionError(f"{label} is invalid") from exc
    if not amount.is_finite() or amount < 0: raise ProductionAdmissionError(f"{label} is invalid")
    return amount


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
        if authority.get("schema") != "jaco-derived-operation-authority/v1" or authority.get("status") != "READY_FOR_INDEPENDENT_PARENT_REVIEW_NO_RESERVATION_NO_POST": raise ProductionAdmissionError("consumed or invalid authority")
        if authority.get("operation_id") != OPERATION_ID or authority.get("episode_id") != EPISODE_ID: raise ProductionAdmissionError("wrong operation scope")
        policy_path, preflight_path = Path(authority.get("policy_path", "")).resolve(), Path(authority.get("preflight_path", "")).resolve()
        if policy_path != (self.root / "standing-policy" / POLICY_PATH.name).resolve() or preflight_path != (self.root / "preflight" / PREFLIGHT_PATH.name).resolve(): raise ProductionAdmissionError("caller-selected protected root")
        policy, policy_sha = _load(policy_path, "protected standing policy"); preflight, preflight_sha = _load(preflight_path, "protected preflight")
        if authority.get("policy_sha256") != policy_sha or authority.get("preflight_sha256") != preflight_sha: raise ProductionAdmissionError("protected hash changed")
        consent = policy.get("canonical_consent", {})
        if (not isinstance(policy, dict) or not isinstance(preflight, dict)
                or policy.get("episode_id") != EPISODE_ID
                or preflight.get("request_id") != REQUEST_ID
                or consent.get("consent_message_id") != 147620):
            raise ProductionAdmissionError("request, episode, or standing consent binding changed")
        operation = preflight.get("operation", {})
        if operation.get("id") != OPERATION_ID or operation.get("endpoint") != ENDPOINT or operation.get("call_limit") != 1: raise ProductionAdmissionError("preflight operation binding changed")
        payload = operation.get("payload")
        expected_payload_sha = operation.get("payload_sha256")
        if (payload != EXPECTED_PAYLOAD
                or not isinstance(expected_payload_sha, str)
                or expected_payload_sha != sha256(_canonical(EXPECTED_PAYLOAD)).hexdigest()):
            raise ProductionAdmissionError("exact three-views payload required")
        if _money(operation.get("maximum_usd"), "operation cap") != CAP or _money(authority.get("maximum_next_cost_usd"), "authority cap") != CAP: raise ProductionAdmissionError("cap binding changed")
        return authority, policy, preflight

    def _qualification(self, authority: dict[str, Any]) -> dict[str, Any]:
        binding = authority.get("deployment_qualification")
        if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}: raise ProductionAdmissionError("protected qualification required")
        path = Path(binding["path"]).resolve()
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
                or not isinstance(q.get("ci_digest_sha256"), str) or len(q["ci_digest_sha256"]) != 64
                or q.get("execution_code_sha256") != sha256(Path(__file__).read_bytes()).hexdigest()
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
                or Path(binding["path"]).resolve() != (self.root / "ledger" / LEDGER_PATH.name).resolve()
                or (not self._test_only and binding.get("financial_policy_sha256") != FINANCIAL_SHA256)
                or binding.get("operation_policy_sha256") != authority.get("policy_sha256")
                or not isinstance(source, dict) or set(source) != {"path", "sha256"}
                or Path(source["path"]).resolve() != (self.root / FINANCIAL_SOURCE_PATH.name).resolve()):
            raise ProductionAdmissionError("financial binding changed")
        financial_source, source_sha = _load(Path(source["path"]), "financial source")
        expected_financial_sha = source_sha if self._test_only else FINANCIAL_SHA256
        if source_sha != expected_financial_sha or source.get("sha256") != source_sha:
            raise ProductionAdmissionError("financial source changed")
        return Path(binding["path"]).resolve(), expected_financial_sha, financial_source

    def _validate_fresh_quote(self, quote: dict[str, Any], openapi: dict[str, Any]) -> None:
        if (not isinstance(quote, dict) or not isinstance(openapi, dict)
                or quote.get("endpoint_id") != ENDPOINT
                or _money(quote.get("maximum_usd"), "fresh quote") > CAP
                or time.time() - float(quote.get("observed_at_epoch", 0)) > 300):
            raise ProductionAdmissionError("fresh cap-bound quote required")

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
        return run_local_process_review(policy), run

    async def _execute_prepared(self, *, quote: dict[str, Any], openapi: dict[str, Any], credential: str, test_provider_factory=None) -> dict[str, Any]:
        from src.hybrid.execution import Authorization, Executor, Job, Price
        from src.hybrid.planner import Config
        from src.hybrid.standing_budget import StandingBudgetLedger
        authority, _, preflight = self._authority()
        qualification = self._qualification(authority)
        ledger_path, financial_sha, financial_source = self._ledger_binding(authority)
        self._validate_fresh_quote(quote, openapi)
        execution = self.root / "execution" / OPERATION_ID; execution.mkdir(parents=True, exist_ok=True)
        unprepared_execution = not (execution / "executor.sqlite3").exists()
        job = Job(OPERATION_ID, "first", qualification["mode"], ENDPOINT, preflight["operation"]["payload"], None, CAP, input_kind="TEXT_TO_IMAGE")
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
        ledger = StandingBudgetLedger(ledger_path, policy_sha256=financial_sha, episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode=qualification["mode"])
        executor = Executor(execution / "executor.sqlite3", Config(limit=Decimal(10)), standing_ledger=ledger, _jaco_final_admission_verifier=verify, _jaco_production_binding={"kind":"YOUNG_IDENTITY_MODEL_SHEET_PRODUCTION", "auth_scope":scope})
        review_run = None
        if unprepared_execution:
            _, review_run = self._review_for_execution(quote, openapi, financial_source)
        if executor.inspect(job.request_id) is None:
            if review_run is None:
                raise ProductionAdmissionError("missing immutable review for unprepared execution")
            _write_once(marker_path, qualification)
            executor.prepare_intent(job, auth_scope=scope); claim = ledger.reserve(job.request_id, EPISODE_ID, ENDPOINT, str(CAP), settlement_mode="FAL_READBACK_V2", auth_scope=scope, provider_request_id="UNASSIGNED", authority_mode=qualification["mode"])
            if claim.get("state") != "RESERVED": raise ProductionAdmissionError("canonical reservation rejected")
            executor._change(job.request_id, final_admission_marker=marker_for(job))
            for name in ("packet.json", "review.json", "quote.json", "openapi.json"):
                _write_once(execution / name, json.loads((review_run / name).read_text(encoding="utf-8")))
        if self._test_only:
            if not callable(test_provider_factory): raise ProductionAdmissionError("private test requires fixture transport")
            provider = test_provider_factory(execution / "provider-receipt.json", openapi)
        else:
            from src.hybrid.jaco_flux3_production import _FixedFalProvider
            class OperationFixedFalProvider(_FixedFalProvider): mode = "LIVE"
            provider = OperationFixedFalProvider(execution / "provider-receipt.json", openapi, credential)
        auth = None if qualification["mode"] == "TEST" else Authorization(job.request_id, qualification["authorization"], time.time()+300, CAP)
        price = None if qualification["mode"] == "TEST" else Price(ENDPOINT, job.request_id, CAP, time.time()+300, "fresh-cap-bound-quote")
        completed = await executor.run(job, provider, authorization=auth, price=price, settlement_checkpoint=lambda lid,rid: ledger.bind_provider_request_id(lid,rid), submit_prepared=True)
        return {"operation_id": OPERATION_ID, "status": completed["status"], "request_id": job.request_id, "mode": qualification["mode"]}

    async def _execute_with_composition(self, *, quote_and_openapi, credential_loader, test_provider_factory=None):
        authority, _, _ = self._authority(); qualification = self._qualification(authority); self._ledger_binding(authority)
        quote, openapi = await quote_and_openapi(); credential = "" if qualification["mode"] == "TEST" else credential_loader()
        return await self._execute_prepared(quote=quote, openapi=openapi, credential=credential, test_provider_factory=test_provider_factory)

    async def execute(self) -> dict[str, Any]:
        import asyncio

        from src.hybrid.fal_get_only_preflight import resolve_default_fal_credential
        from src.hybrid.jaco_flux3_production import (
            _FAL_OPENAPI_URL,
            _current_price_snapshot,
            _get_json,
            _official_openapi_endpoint,
        )
        credential: str | None = None
        async def inputs():
            nonlocal credential
            credential, _ = resolve_default_fal_credential(); quote, openapi = await asyncio.gather(_current_price_snapshot(credential), asyncio.to_thread(_get_json, _FAL_OPENAPI_URL)); _official_openapi_endpoint(openapi); return quote, openapi
        return await self._execute_with_composition(quote_and_openapi=inputs, credential_loader=lambda: credential or (_ for _ in ()).throw(ProductionAdmissionError("credential unavailable")))
