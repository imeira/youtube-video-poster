"""Closed TEST-only successor for the Young Identity Model Sheet operation.

This intentionally does not import or extend the deployment-pinned legacy
factory.  The authority document is data, but its schema and paths are closed.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.hybrid.execution import Executor, Job
from src.hybrid.planner import Config
from src.hybrid.standing_budget import StandingBudgetLedger

OPERATION_ID = "JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001"
ENDPOINT = "blackforestlabs/flux-3/text-to-image"
EPISODE_ID = "JACO_GENESIS28"
STANDING_POLICY_SCHEMA = "jaco-young-identity-test-standing-policy/v1"
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class CompositionError(PermissionError):
    pass


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _load(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes(); value = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompositionError("protected manifest is unreadable") from exc
    if not isinstance(value, dict): raise CompositionError("protected manifest must be object")
    return value, sha256(raw).hexdigest()


def _hash(value: object) -> bool:
    return isinstance(value, str) and _SHA.fullmatch(value) is not None


def _cost(value: object) -> Decimal:
    if not isinstance(value, str): raise CompositionError("cost must be decimal string")
    try: result = Decimal(value)
    except InvalidOperation as exc: raise CompositionError("cost invalid") from exc
    if not result.is_finite() or result <= 0: raise CompositionError("cost invalid")
    return result


def _valid_standing_policy(policy: dict[str, Any], financial: dict[str, Any]) -> bool:
    """Validate the complete TEST-only standing policy, not just its consent ID."""
    if set(policy) != {
        "schema", "status", "episode_id", "canonical_consent", "financial_baseline",
    }:
        return False
    consent = policy["canonical_consent"]
    baseline = policy["financial_baseline"]
    return (
        policy["schema"] == STANDING_POLICY_SCHEMA
        and policy["status"] == "TEST_ONLY"
        and policy["episode_id"] == EPISODE_ID
        and isinstance(consent, dict)
        and set(consent) == {"consent_message_id"}
        and type(consent["consent_message_id"]) is int
        and consent["consent_message_id"] == 147620
        and isinstance(baseline, dict)
        and baseline == financial
    )


class YoungIdentityModelSheetComposition:
    _TEST_CONSTRUCTION_TOKEN = object()

    def __init__(self, root: Path, authority_path: Path, *, _token: object | None = None):
        if _token is not self._TEST_CONSTRUCTION_TOKEN:
            raise CompositionError("direct composition construction is forbidden")
        self.root, self.authority_path = Path(root).resolve(), Path(authority_path).resolve()
        if self.authority_path.parent != self.root: raise CompositionError("authority is not allowlisted")

    @classmethod
    def for_test(cls, root: Path, authority_path: Path):
        return cls(root, authority_path, _token=cls._TEST_CONSTRUCTION_TOKEN)

    def manifest(self) -> dict[str, Any]:
        authority, _ = _load(self.authority_path)
        if set(authority) != {"schema", "status", "operation_id", "predecessor_operation_id", "executor_predecessor_request_id", "executor_category", "policy_path", "policy_sha256", "preflight_path", "preflight_sha256", "financial_source", "financial_policy_sha256", "maximum_next_cost_usd", "mode"}:
            raise CompositionError("closed authority manifest required")
        if authority["schema"] != "jaco-derived-operation-authority/v1" or authority["status"] != "TEST_ONLY" or authority["operation_id"] != OPERATION_ID or not isinstance(authority["predecessor_operation_id"], str) or not authority["predecessor_operation_id"] or not isinstance(authority["executor_predecessor_request_id"], str) or authority["executor_category"] not in {"first", "alternative", "correction", "thumbnail"} or authority["mode"] != "TEST":
            raise CompositionError("unqualified LIVE or wrong operation")
        policy_path, preflight_path = Path(authority["policy_path"]).resolve(), Path(authority["preflight_path"]).resolve()
        financial = authority["financial_source"]
        if policy_path != self.root / "policy.json" or preflight_path != self.root / "preflight.json" or not isinstance(financial, dict) or set(financial) != {"path", "sha256"} or Path(financial["path"]).resolve() != self.root / "financial.json":
            raise CompositionError("arbitrary trust root forbidden")
        policy, psha = _load(policy_path); preflight, fsha = _load(preflight_path); source, source_sha = _load(self.root / "financial.json")
        if (not all(_hash(authority[k]) for k in ("policy_sha256", "preflight_sha256", "financial_policy_sha256")) or not _hash(financial["sha256"]) or authority["policy_sha256"] != psha or authority["preflight_sha256"] != fsha or financial["sha256"] != source_sha or authority["financial_policy_sha256"] != source_sha or authority["financial_policy_sha256"] == psha):
            raise CompositionError("financial and operation roots must be distinct hashes")
        if source != {"currency": "USD", "total_episode_cap": "10.00"} or not _valid_standing_policy(policy, source):
            raise CompositionError("standing consent or financial scope invalid")
        operation = preflight.get("operation")
        if not isinstance(operation, dict) or set(operation) != {"id", "kind", "endpoint", "call_limit", "maximum_usd", "payload", "payload_sha256"} or operation.get("id") != OPERATION_ID or operation.get("endpoint") != ENDPOINT or type(operation.get("call_limit")) is not int or operation["call_limit"] != 1 or not isinstance(operation.get("maximum_usd"), str) or not isinstance(operation.get("kind"), str) or not operation["kind"] or not isinstance(operation.get("payload"), dict) or not _hash(operation.get("payload_sha256")) or sha256(_canonical(operation["payload"])).hexdigest() != operation["payload_sha256"] or preflight.get("replay_prohibition") is not True:
            raise CompositionError("closed typed operation manifest invalid")
        cost = _cost(operation["maximum_usd"])
        if _cost(authority["maximum_next_cost_usd"]) != cost:
            raise CompositionError("authority cap does not bind operation")
        return {"authority": authority, "operation": operation, "financial_sha": source_sha, "cost": cost}

    async def execute(self, provider_factory):
        manifest = self.manifest(); operation = manifest["operation"]; cost = manifest["cost"]
        ledger = StandingBudgetLedger(self.root / "ledger.sqlite3", policy_sha256=manifest["financial_sha"], episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="TEST")
        # Executor's existing TEST admission contract fixes the runtime scope;
        # the distinct operation-policy hash remains sealed in the marker.
        scope = "TEST_LOCAL_ONLY"
        authority_sha = sha256(self.authority_path.read_bytes()).hexdigest()
        def marker_for(job):
            return {"schema": 2, "kind": "JACO_FLUX3_FINAL_ADMISSION_MARKER", "receipt_path": str(self.authority_path), "receipt_sha256": authority_sha, "request_id": job.request_id, "endpoint": operation["endpoint"], "payload_sha256": job.intent_input_sha256, "input_kind": "TEXT_TO_IMAGE", "auth_scope": scope, "reserved_usd": operation["maximum_usd"], "trust_identity": "TEST_CLOSED_YOUNG_IDENTITY_MANIFEST"}
        def verify(job, marker, claim):
            if marker != marker_for(job) or claim.get("auth_scope") != scope: raise CompositionError("durable admission binding changed")
        executor = Executor(self.root / "executor.sqlite3", Config(limit=Decimal(10)), standing_ledger=ledger, _jaco_final_admission_verifier=verify)
        # The predecessor is sealed in the authority but not supplied to
        # Executor's remediation-only predecessor mechanism.
        job = Job(operation["id"], manifest["authority"]["executor_category"], "TEST", operation["endpoint"], operation["payload"], None, cost, predecessor=manifest["authority"]["executor_predecessor_request_id"], input_kind="TEXT_TO_IMAGE")
        if executor.inspect(job.request_id) is None:
            executor.prepare_intent(job, auth_scope=scope)
            ledger.reserve(job.request_id, manifest["authority"]["predecessor_operation_id"], operation["endpoint"], str(cost), settlement_mode="FAL_READBACK_V2", auth_scope=scope, authority_mode="TEST")
            executor._change(job.request_id, final_admission_marker=marker_for(job))
        return await executor.run(job, provider_factory(self.root / "provider-result.bin"), settlement_checkpoint=lambda lid, rid: ledger.bind_provider_request_id(lid, rid), submit_prepared=True)

    def audit(self) -> dict[str, int]:
        ledger = StandingBudgetLedger(self.root / "ledger.sqlite3", policy_sha256=self.manifest()["financial_sha"], episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="TEST")
        with ledger._connect() as db: return {"claims": db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]}
