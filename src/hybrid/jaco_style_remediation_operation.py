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
import sqlite3
import time
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

ROOT = Path("C:/HermesStudio/production/jaco-flux3")
AUTHORITY_PATH = ROOT / "standing-policy" / "JACO_GEN28_STYLE_REMEDIATION_001.authority.v2.json"
POLICY_PATH = ROOT / "standing-policy" / "JACO_GENESIS28_STANDING_OPERATION_POLICY.v1.json"
PREFLIGHT_PATH = ROOT / "preflight" / "JACO_GEN28_STYLE_REMEDIATION_001.preflight.json"
LEDGER_PATH = ROOT / "ledger" / "standing-budget.sqlite3"
FINANCIAL_SOURCE_PATH = ROOT / "financial-record-source.json"
OPERATION_ID = "JACO_GEN28_STYLE_REMEDIATION_001"
EPISODE_ID = "JACO_GENESIS28"
REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
ENDPOINT = "blackforestlabs/flux-3/text-to-image"
CAP = Decimal("10.000000")
PRIOR_ACTUAL = Decimal("0.048000")
NEXT_CAP = Decimal("0.048000")
APPROVED_PREFLIGHT_SHA256 = "7b4699ce1ad1379984f1c87f45d9c0896fae502cef3c276762bd56f3b230a6e4"
APPROVED_FINANCIAL_SOURCE_SHA256 = "916e8230db47406db96cbe774b9332c00ed24cf156c66d4d91d11a213b64f50d"
FAL_FLUX3_ASPECT_RATIOS = frozenset({
    "auto", "21:9", "2:1", "16:9", "3:2", "7:5", "4:3", "5:4", "1:1",
    "4:5", "3:4", "5:7", "2:3", "9:16", "1:2",
})


class OperationAdmissionError(PermissionError):
    """The protected standing authority cannot admit this operation."""


def _sha_path(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


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


def _write_once(path: Path, body: dict[str, Any]) -> Path:
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
    return path


def _financial_source_is_canonical(source: dict[str, Any]) -> bool:
    return source == {
        "schema": "jaco-factory-financial-reconciliation/1",
        "request_hash": REQUEST_ID,
        "revision": "818556d97edb648f",
        "currency": "USD",
        "total_episode_cap": "10.00",
        "status": "APPROVED_USD10",
        "approval_scope": "FINANCIAL_ONLY",
        "authorizes_generation": False,
    }


def _validate_geometry(payload: object) -> str:
    # The immutable preflight is a 16:9 operation.  The provider enum confirms
    # support; it never grants a caller authority to select another geometry.
    if not isinstance(payload, dict) or payload.get("aspect_ratio") != "16:9":
        raise OperationAdmissionError("protected payload geometry drifted")
    if payload["aspect_ratio"] not in FAL_FLUX3_ASPECT_RATIOS:
        raise OperationAdmissionError("protected payload geometry is unsupported")
    return "16:9"


def probe_deployment_contract() -> dict[str, str]:
    """Read only the protected production contract; never create or reserve state."""
    policy, policy_sha = _load(POLICY_PATH, "protected standing policy")
    preflight, preflight_sha = _load(PREFLIGHT_PATH, "protected successor preflight")
    financial_source, financial_sha = _load(FINANCIAL_SOURCE_PATH, "canonical financial source")
    if (preflight_sha != APPROVED_PREFLIGHT_SHA256
            or preflight.get("schema") != "jaco-episode-operation-preflight/v1"
            or preflight.get("status") != "VALIDATED_PRE_DISPATCH_BLOCKED_MISSING_OPERATION_AUTHORIZATION"
            or preflight.get("episode_id") != EPISODE_ID
            or preflight.get("operation", {}).get("id") != OPERATION_ID
            or preflight["operation"].get("endpoint") != ENDPOINT
            or preflight["operation"].get("call_limit") != 1):
        raise OperationAdmissionError("protected successor preflight contract changed")
    geometry = _validate_geometry(preflight["operation"].get("payload"))
    if (policy.get("schema") != "jaco-standing-episode-operation-policy/v1"
            or policy.get("status") != "ACTIVE_FOR_DERIVATION_REQUIRES_INDEPENDENT_OPERATION_REVIEW"
            or policy.get("episode_id") != EPISODE_ID
            or financial_sha != APPROVED_FINANCIAL_SOURCE_SHA256
            or not _financial_source_is_canonical(financial_source)):
        raise OperationAdmissionError("protected policy or financial source changed")
    try:
        with sqlite3.connect(f"file:{LEDGER_PATH.as_posix()}?mode=ro", uri=True) as ledger:
            tables = {row[0] for row in ledger.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error as exc:
        raise OperationAdmissionError("canonical ledger is not readable") from exc
    if not {"policy", "claims", "settlement_receipts"}.issubset(tables):
        raise OperationAdmissionError("canonical ledger schema is incompatible")
    return {
        "status": "NEEDS_QUALIFICATION",
        "operation_id": OPERATION_ID,
        "policy_sha256": policy_sha,
        "preflight_sha256": preflight_sha,
        "financial_source_sha256": financial_sha,
        "geometry": geometry,
        "ledger_path": str(LEDGER_PATH),
    }


class OperationAdmissionFactory:
    """Binds fresh review inputs to one protected successor operation only."""

    def __init__(self) -> None:
        """Open only the deployment-pinned production authority."""
        self.root = ROOT.resolve()
        self.authority_path = AUTHORITY_PATH.resolve()
        self._test_only = False

    @classmethod
    def _for_test(cls, *, root: Path, authority_path: Path) -> OperationAdmissionFactory:
        """Create an explicitly non-production fixture composition for offline tests."""
        factory = object.__new__(cls)
        factory.root = Path(root).resolve()
        factory.authority_path = Path(authority_path).resolve()
        factory._test_only = True
        return factory

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
        if policy_path != (self.root / "standing-policy" / POLICY_PATH.name).resolve() or preflight_path != (self.root / "preflight" / PREFLIGHT_PATH.name).resolve():
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
        return probe_deployment_contract()

    def _qualification(self, authority: dict[str, Any]) -> dict[str, Any]:
        """Open only the authority-pinned deployment contract, never CLI data."""
        binding = authority.get("deployment_qualification")
        if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
            raise OperationAdmissionError("protected deployment qualification contract is required")
        path = Path(binding["path"]).resolve()
        expected = (self.root / "standing-policy" / "JACO_GEN28_STYLE_REMEDIATION_001.qualification.v1.json").resolve()
        if path != expected:
            raise OperationAdmissionError("caller-selected deployment qualification is forbidden")
        contract, digest = _load(path, "protected deployment qualification")
        required = {
            "schema", "status", "operation_id", "episode_id", "policy_sha256", "preflight_sha256",
            "mode", "authorization", "review_packet_schema", "reviewer", "ci_digest_sha256",
            "execution_code_sha256",
        }
        test_required = required | {"consent_fixture"}
        is_test_fixture = contract.get("mode") == "TEST"
        if (digest != binding["sha256"]
                or set(contract) != (test_required if is_test_fixture else required)
                or contract.get("schema") != (
                    "jaco-test-deployment-qualification/v1" if is_test_fixture
                    else "jaco-protected-deployment-qualification/v1"
                )
                or contract.get("status") != "QUALIFIED" or contract.get("operation_id") != OPERATION_ID
                or contract.get("episode_id") != EPISODE_ID or contract.get("policy_sha256") != authority.get("policy_sha256")
                or contract.get("preflight_sha256") != authority.get("preflight_sha256")
                or contract.get("mode") not in {"TEST", "LIVE"}
                or contract.get("review_packet_schema") != "jaco-fal-flux3-successor-benchmark/3"
                or contract.get("reviewer") != "LOCAL_PROCESS_REVIEW_AUTOMATED_V1"
                or not isinstance(contract.get("ci_digest_sha256"), str)
                or len(contract["ci_digest_sha256"]) != 64
                or contract.get("execution_code_sha256") != _sha_path(Path(__file__))
                or contract.get("authorization") != (
                    "TEST_FIXTURE_CONSENT_ONLY" if is_test_fixture
                    else "STANDING_USER_AUTHORIZATION_PROTECTED_POLICY"
                )
                or (is_test_fixture and (
                    not self._test_only
                    or not isinstance(contract.get("consent_fixture"), dict)
                    or contract["consent_fixture"].get("kind") != "TEST_ONLY_CONSENT_FIXTURE"
                ))
                or (not is_test_fixture and self._test_only)):
            raise OperationAdmissionError("invalid protected deployment qualification")
        return contract

    def _review_for_execution(
        self, quote: dict[str, Any], openapi: dict[str, Any], financial_source: dict[str, Any]
    ) -> tuple[dict[str, Any], Path]:
        from src.hybrid.local_process_review import (
            build_local_process_review_policy,
            run_local_process_review,
        )
        authority, _policy, preflight = self._authority()
        self._qualification(authority)
        payload = preflight["operation"]["payload"]
        _validate_geometry(payload)
        run = self.root / "operation-runs" / f"{OPERATION_ID}-{time.time_ns()}"
        run.mkdir(parents=True, exist_ok=False)
        quote_path, openapi_path = run / "quote.json", run / "openapi.json"
        _write_once(quote_path, quote); _write_once(openapi_path, openapi)
        finance = _write_once(run / "financial-record.json", financial_source)
        payload_sha = sha256(_canonical(payload)).hexdigest()
        packet = _write_once(run / "packet.json", {"schema": "jaco-fal-flux3-successor-benchmark/3", "kind": "BOUNDED_FAL_FLUX3_CANARY_PREGEN_PACKET", "bindings": {"request_id": REQUEST_ID, "endpoint": ENDPOINT, "payload_sha256": payload_sha, "authenticated_price_quote": {"sha256": _sha_path(quote_path), "bytes": quote_path.stat().st_size}, "openapi_snapshot": {"sha256": _sha_path(openapi_path), "bytes": openapi_path.stat().st_size}, "financial_approval": {"sha256": _sha_path(finance), "status": "APPROVED_USD10", "total_episode_cap_usd": "10.00"}}, "candidate": {"call_limit": 1, "single_output": True, "reference_inputs": [], "payload": payload}, "cost": {"approved_canary_maximum_usd": "0.048", "current_authenticated_quote_maximum_usd": "0.048", "actual_billed_amount": "UNKNOWN_UNTIL_PROVIDER_RECEIPT_OR_BILLING_RECONCILIATION"}})
        request = _write_once(run / "review-request.json", {"schema": "jaco-fal-flux3-independent-pre-generation-review-request/2", "kind": "INDEPENDENT_PREGEN_REVIEW_REQUEST", "packet": {"sha256": _sha_path(packet)}, "required_exact_bindings": {"request_id": REQUEST_ID, "endpoint": ENDPOINT, "payload_sha256": payload_sha, "approved_canary_maximum_usd": "0.048", "successor_packet_sha256": _sha_path(packet)}, "prohibitions": ["NO_FAL_PAID_POST", "NO_RESERVATION", "NO_GATEWAY_OR_OTHER_EP_WRITE", "NO_TEST_AS_LIVE"]})
        policy = build_local_process_review_policy(root=run, packet_path=packet, review_request_path=request, quote_path=quote_path, openapi_path=openapi_path, financial_record_path=finance, output_path=run / "review.json", request_id=REQUEST_ID, review_request_sha256=_sha_path(request))
        return run_local_process_review(policy), run

    async def _execute_prepared(
        self, *, quote: dict[str, Any], openapi: dict[str, Any], credential: str,
        test_provider_factory=None,
    ) -> dict[str, Any]:
        """Composition-only continuation after qualification and fresh inputs."""
        from src.hybrid.execution import Authorization, Executor, Job, Price
        from src.hybrid.jaco_flux3_production import _FixedFalProvider
        from src.hybrid.planner import Config
        from src.hybrid.standing_budget import StandingBudgetLedger
        class OperationFixedFalProvider(_FixedFalProvider):
            def __init__(self, output, schema, key, mode):
                if mode not in {"TEST", "LIVE"}: raise OperationAdmissionError("qualified provider mode is invalid")
                super().__init__(output, schema, key); self.mode = mode
        authority, _policy, preflight = self._authority()
        qualification = self._qualification(authority)
        ledger_binding = authority.get("canonical_ledger")
        if not isinstance(ledger_binding, dict) or set(ledger_binding) != {"path", "policy_sha256", "financial_source"}:
            raise OperationAdmissionError("canonical shared ledger binding is required")
        ledger_path = Path(ledger_binding["path"]).resolve()
        expected_ledger_path = (self.root / "ledger" / LEDGER_PATH.name).resolve()
        if ledger_path != expected_ledger_path or ledger_binding["policy_sha256"] != authority["policy_sha256"]:
            raise OperationAdmissionError("per-job ledger reset is forbidden")
        financial_binding = ledger_binding["financial_source"]
        if not isinstance(financial_binding, dict) or set(financial_binding) != {"path", "sha256"}:
            raise OperationAdmissionError("canonical financial source binding is required")
        financial_path = Path(financial_binding["path"]).resolve()
        expected_financial_path = (self.root / FINANCIAL_SOURCE_PATH.name).resolve()
        financial_source, financial_sha = _load(financial_path, "canonical financial source")
        if (financial_path != expected_financial_path
                or financial_sha != financial_binding["sha256"]
                or not _financial_source_is_canonical(financial_source)):
            raise OperationAdmissionError("canonical financial source changed")
        execution = self.root / "execution" / OPERATION_ID; execution.mkdir(parents=True, exist_ok=True)
        job = Job(OPERATION_ID, "first", qualification["mode"], ENDPOINT, preflight["operation"]["payload"], None, Decimal("0.048"), input_kind="TEXT_TO_IMAGE")
        qualification_path = execution / "protected-qualification.json"
        scope = (
            "TEST_LOCAL_ONLY" if qualification["mode"] == "TEST"
            else f"{OPERATION_ID}:{authority['policy_sha256']}"
        )
        def verify_marker(bound_job, marker, claim):
            if (not qualification_path.is_file() or json.loads(qualification_path.read_text(encoding="utf-8")) != qualification
                    or marker.get("qualification_sha256") != sha256(_canonical(qualification)).hexdigest()
                    or bound_job.request_id != job.request_id or claim.get("auth_scope") != scope):
                raise OperationAdmissionError("protected qualification marker changed")
        # A fresh quote/review can reject (for example, stale or unknown price)
        # without creating a ledger database or reservation.  Recovery always
        # reopens the durable executor state and never creates a second review.
        review_run: Path | None = None
        if not (execution / "executor.sqlite3").exists():
            _review, review_run = self._review_for_execution(quote, openapi, financial_source)
        ledger = StandingBudgetLedger(ledger_path, policy_sha256=authority["policy_sha256"], episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode=qualification["mode"])
        executor = Executor(execution / "executor.sqlite3", Config(limit=Decimal(10)), standing_ledger=ledger, _jaco_final_admission_verifier=verify_marker, _jaco_production_binding={"kind": "JACO_FLUX3_FIXED_PRODUCTION_COMPOSITION", "auth_scope": scope})
        if executor.inspect(job.request_id) is None:
            if review_run is None:
                raise OperationAdmissionError("missing immutable review for unprepared execution")
            _write_once(qualification_path, qualification)
            executor.prepare_intent(job, auth_scope=scope)
            claim = ledger.reserve(job.request_id, EPISODE_ID, ENDPOINT, "0.048", settlement_mode="FAL_READBACK_V2", auth_scope=scope, provider_request_id="UNASSIGNED", authority_mode=qualification["mode"])
            if claim.get("state") != "RESERVED" or claim.get("reserved_usd") != "0.048000": raise OperationAdmissionError("canonical ledger reservation rejected")
            executor._change(job.request_id, final_admission_marker={"schema": 2, "kind": "JACO_STYLE_REMEDIATION_QUALIFICATION_MARKER", "qualification_sha256": sha256(_canonical(qualification)).hexdigest(), "request_id": job.request_id, "endpoint": ENDPOINT, "payload_sha256": job.intent_input_sha256, "input_kind": "TEXT_TO_IMAGE", "auth_scope": scope, "reserved_usd": "0.048", "trust_identity": "PROTECTED_DEPLOYMENT_QUALIFICATION"})
            for name in ("packet.json", "review.json", "quote.json", "openapi.json"): _write_once(execution / name, json.loads((review_run / name).read_text(encoding="utf-8")))
        schema = json.loads((execution / "openapi.json").read_text(encoding="utf-8"))
        if qualification["mode"] == "TEST":
            if not self._test_only or not callable(test_provider_factory):
                raise OperationAdmissionError("TEST execution requires private fixture transport")
            provider = test_provider_factory(execution / "provider-receipt.json", schema)
            if getattr(provider, "mode", None) != "TEST":
                raise OperationAdmissionError("TEST fixture transport mode mismatch")
        else:
            if self._test_only:
                raise OperationAdmissionError("private fixture composition cannot execute LIVE")
            provider = OperationFixedFalProvider(execution / "provider-receipt.json", schema, credential, qualification["mode"])
        auth = None if qualification["mode"] == "TEST" else Authorization(job.request_id, qualification["authorization"], time.time()+300, Decimal("0.048"))
        price = None if qualification["mode"] == "TEST" else Price(ENDPOINT, job.request_id, Decimal("0.048"), time.time()+300, "protected-fresh-quote")
        completed = await executor.run(job, provider, authorization=auth, price=price, settlement_checkpoint=lambda local_id, remote_id: ledger.bind_provider_request_id(local_id, remote_id), submit_prepared=True)
        return {"operation_id": OPERATION_ID, "status": completed["status"], "request_id": job.request_id, "mode": qualification["mode"]}

    async def _execute_with_composition(
        self, *, quote_and_openapi, credential_loader, test_provider_factory=None
    ) -> dict[str, Any]:
        """Qualify the pinned root before credential resolution or remote reads."""
        authority, _policy, _preflight = self._authority()
        qualification = self._qualification(authority)
        quote, openapi = await quote_and_openapi()
        if qualification["mode"] == "TEST":
            if not self._test_only or not callable(test_provider_factory):
                raise OperationAdmissionError("TEST execution requires private fixture transport")
            credential = ""
        else:
            credential = credential_loader()
        return await self._execute_prepared(
            quote=quote,
            openapi=openapi,
            credential=credential,
            test_provider_factory=test_provider_factory,
        )

    async def execute(self) -> dict[str, Any]:
        """Run the fixed production composition; callers cannot supply authority inputs."""
        import asyncio

        from src.hybrid.fal_get_only_preflight import (
            resolve_default_fal_credential,
        )
        from src.hybrid.jaco_flux3_production import (
            _FAL_OPENAPI_URL,
            _current_price_snapshot,
            _get_json,
            _official_openapi_endpoint,
        )

        credential: str | None = None

        async def quote_and_openapi() -> tuple[dict[str, Any], dict[str, Any]]:
            nonlocal credential
            credential, _presence = resolve_default_fal_credential()
            quote, openapi = await asyncio.gather(
                _current_price_snapshot(credential),
                asyncio.to_thread(_get_json, _FAL_OPENAPI_URL),
            )
            _official_openapi_endpoint(openapi)
            return quote, openapi

        def resolved_credential() -> str:
            if credential is None:
                raise OperationAdmissionError("credential resolution did not complete")
            return credential

        return await self._execute_with_composition(
            quote_and_openapi=quote_and_openapi,
            credential_loader=resolved_credential,
        )

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
        import asyncio

        # ``execute`` validates the pinned authority and qualification before it
        # resolves a credential or starts either remote read.
        print(json.dumps(asyncio.run(OperationAdmissionFactory().execute()), sort_keys=True))
        return 0
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
