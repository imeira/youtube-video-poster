"""Fail-closed bridge from FAL billing readback to the standing budget ledger.

It derives every settlement binding from the executor's durable receipt; caller
JSON is never authority."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import ClassVar

from src.hybrid.execution import Executor
from src.hybrid.fal_billing import (
    BillingReadbackRequest,
    BillingStatus,
    FalBillingConsumer,
)
from src.hybrid.standing_budget import BudgetDenied, StandingBudgetLedger


class SettlementDenied(ValueError):
    """A durable binding or authenticated billing requirement was not met."""


_MAX_FRESH_SECONDS = 300
_HASH = re.compile(r"[a-f0-9]{64}")


class JacoFinalAdmissionContractVerifier:
    """Verify the one deployed Jacó final-admission settlement contract.

    This is deliberately a typed, composition-created verifier rather than a
    scope-prefix exception.  Its root paths are protected composition inputs;
    reconciliation callers still supply only a local request ID and time window.
    """

    _MANDATE_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema", "request_id", "revision", "endpoint", "payload_sha256",
        "maximum_usd", "call_limit", "budget_cap_usd", "scope",
        "release_authorization", "license_status",
    })
    _POLICY_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema", "trust_derivation", "mandate_sha256", "provenance_sha256",
        "source_evidence",
    })
    _AUTHORIZATION_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema", "kind", "version", "request_id", "endpoint",
        "payload_sha256", "maximum_usd", "call_limit",
        "user_authorization_evidence",
    })
    _FINAL_RECEIPT_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema", "kind", "request_id", "endpoint", "reserved_usd",
        "call_limit", "packet_sha256", "review_sha256", "authorization_sha256",
        "quote_sha256", "authorization_version", "validated_at_epoch",
        "receipt_sha256",
    })
    _MARKER_FIELDS: ClassVar[frozenset[str]] = frozenset({
        "schema", "kind", "receipt_path", "receipt_sha256", "request_id",
        "endpoint", "payload_sha256", "input_kind", "auth_scope",
        "reserved_usd", "trust_identity",
    })

    def __init__(
        self, *, root, execution_root, style_authority_path=None,
        style_qualification_path=None, style_policy_path=None,
        financial_source_path=None, style_authority_sha256=None, execution_code_path=None,
    ) -> None:
        from pathlib import Path

        self.root = Path(root).resolve()
        self.execution_root = Path(execution_root).resolve()
        style_paths = (style_authority_path, style_qualification_path, style_policy_path, financial_source_path)
        if any(path is not None for path in style_paths) and not all(path is not None for path in style_paths):
            raise TypeError("complete typed style-remediation contract paths required")
        self.style_authority_path = Path(style_authority_path).resolve() if style_authority_path else None
        self.style_qualification_path = Path(style_qualification_path).resolve() if style_qualification_path else None
        self.style_policy_path = Path(style_policy_path).resolve() if style_policy_path else None
        self.financial_source_path = Path(financial_source_path).resolve() if financial_source_path else None
        if self.style_authority_path is not None and (not isinstance(style_authority_sha256, str) or not _HASH.fullmatch(style_authority_sha256)):
            raise TypeError("pinned style-remediation authority hash required")
        self.style_authority_sha256 = style_authority_sha256
        self.execution_code_path = Path(execution_code_path).resolve() if execution_code_path else None

    @staticmethod
    def _document(path, label: str) -> tuple[dict, str]:
        from pathlib import Path

        candidate = Path(path)
        try:
            raw = candidate.read_bytes()
            value = json.loads(raw.decode("utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SettlementDenied(f"final-admission {label} is unreadable") from exc
        if not isinstance(value, dict):
            raise SettlementDenied(f"final-admission {label} must be an object")
        return value, sha256(raw).hexdigest()

    def _policy_contract(self) -> tuple[dict, str, dict, str, dict]:
        policy, _ = self._document(self.root / "parent-provisioned-policy.json", "policy")
        mandate, mandate_sha = self._document(self.root / "user-mandate.json", "consent")
        authorization, authorization_sha = self._document(
            self.root / "final-admission-authorization.json", "authorization"
        )
        if (
            set(policy) != self._POLICY_FIELDS
            or policy.get("schema") != "JACO_FLUX3_LOCAL_OPERATOR_POLICY/1"
            or policy.get("trust_derivation")
            != "CANONICAL_USER_TRANSCRIPT_PLUS_TRUSTED_LOCAL_OPERATOR"
            or policy.get("mandate_sha256") != mandate_sha
            or not isinstance(policy.get("provenance_sha256"), str)
            or not _HASH.fullmatch(policy["provenance_sha256"])
            or not isinstance(policy.get("source_evidence"), dict)
            or set(policy["source_evidence"])
            != {"financial_record", "official_contract_review", "packet", "get_only_preflight"}
            or any(not isinstance(value, str) or not _HASH.fullmatch(value)
                   for value in policy["source_evidence"].values())
        ):
            raise SettlementDenied("final-admission protected policy is invalid")
        if (
            set(mandate) != self._MANDATE_FIELDS
            or mandate.get("schema") != "JACO_USER_EXECUTION_MANDATE/2"
            or mandate.get("request_id") != "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
            or mandate.get("endpoint") != "blackforestlabs/flux-3/text-to-image"
            or not isinstance(mandate.get("payload_sha256"), str)
            or not _HASH.fullmatch(mandate["payload_sha256"])
            or mandate.get("maximum_usd") != "0.048" or mandate.get("call_limit") != 1
            or mandate.get("budget_cap_usd") != "10.00"
            or mandate.get("scope") != "EVALUATION_GENERATION_ONLY"
            or mandate.get("release_authorization") != "NOT_AUTHORIZED"
            or mandate.get("license_status") != "EVALUATION_ONLY_CONDITIONALLY_SUPPORTABLE"
        ):
            raise SettlementDenied("final-admission protected consent is invalid")
        if (
            set(authorization) != self._AUTHORIZATION_FIELDS
            or authorization.get("schema") != 1
            or authorization.get("kind") != "JACO_FLUX3_FINAL_ADMISSION_AUTHORIZATION"
            or authorization.get("request_id") != mandate["request_id"]
            or authorization.get("endpoint") != mandate["endpoint"]
            or authorization.get("payload_sha256") != mandate["payload_sha256"]
            or authorization.get("maximum_usd") != mandate["maximum_usd"]
            or authorization.get("call_limit") != mandate["call_limit"]
            or authorization.get("user_authorization_evidence")
            != "USER_AUTHORIZED_LOCAL_QA_AND_ONE_ALTERNATIVE_IMAGE_EVAL_WITHIN_USD10"
            or authorization.get("version") != f"mandate-provenance-v1-{mandate_sha[:12]}"
        ):
            raise SettlementDenied("final-admission authorization is invalid")
        return mandate, mandate_sha, authorization, authorization_sha, policy

    def verify(self, receipt: dict, binding: dict, claim: dict) -> None:
        if self._is_style_scope(binding.get("auth_scope") if isinstance(binding, dict) else None):
            self._verify_style_remediation(receipt, binding, claim)
            return
        mandate, mandate_sha, authorization, authorization_sha, _policy = self._policy_contract()
        final_receipt, _ = self._document(
            self.execution_root / "final-admission-receipt.json", "receipt"
        )
        final_body = {key: value for key, value in final_receipt.items() if key != "receipt_sha256"}
        if (
            set(final_receipt) != self._FINAL_RECEIPT_FIELDS
            or final_receipt.get("receipt_sha256") != _canonical_hash(final_body)
            or final_receipt.get("schema") != 1
            or final_receipt.get("kind") != "JACO_FLUX3_FINAL_ADMISSION"
            or final_receipt.get("request_id") != mandate["request_id"]
            or final_receipt.get("endpoint") != mandate["endpoint"]
            or final_receipt.get("reserved_usd") != "0.048"
            or final_receipt.get("call_limit") != 1
            or final_receipt.get("authorization_sha256") != authorization_sha
            or final_receipt.get("authorization_version") != authorization["version"]
        ):
            raise SettlementDenied("final-admission receipt is invalid")
        scope = f"JACO_FLUX3_FINAL_ADMISSION:{mandate_sha}"
        marker = receipt.get("final_admission_marker") if isinstance(receipt, dict) else None
        if (
            receipt.get("mode") != "LIVE" or receipt.get("request_id") != binding["local_request_id"]
            or receipt.get("endpoint") != mandate["endpoint"]
            or receipt.get("input_contract") != {
                "schema": 1,
                "input_kind": "TEXT_TO_IMAGE",
                "payload_sha256": mandate["payload_sha256"],
            }
            or not isinstance(marker, dict) or set(marker) != self._MARKER_FIELDS
            or marker.get("schema") != 2 or marker.get("kind") != "JACO_FLUX3_FINAL_ADMISSION_MARKER"
            or marker.get("receipt_path") != str((self.execution_root / "final-admission-receipt.json").resolve())
            or marker.get("receipt_sha256") != final_receipt["receipt_sha256"]
            or marker.get("request_id") != binding["local_request_id"]
            or marker.get("endpoint") != mandate["endpoint"]
            or marker.get("payload_sha256") != mandate["payload_sha256"]
            or marker.get("input_kind") != "TEXT_TO_IMAGE" or marker.get("auth_scope") != scope
            or marker.get("reserved_usd") != "0.048"
            or marker.get("trust_identity") != "FIXED_PRODUCTION_COMPOSITION"
            or binding.get("auth_scope") != scope or binding.get("authority_mode") != "LIVE"
            or claim.get("request_id") != binding["local_request_id"]
            or claim.get("episode_id") != "JACO_GENESIS28"
            or claim.get("provider") != mandate["endpoint"]
            or claim.get("reserved_usd") != "0.048000" or claim.get("state") != "RESERVED"
            or claim.get("actual_usd") is not None
            or claim.get("settlement_mode") != "FAL_READBACK_V2"
            or claim.get("auth_scope") != scope
            or claim.get("provider_request_id") != binding["provider_request_id"]
            or claim.get("authority_mode") != "LIVE"
        ):
            raise SettlementDenied("final-admission binding mismatch")

    def _is_style_scope(self, scope: object) -> bool:
        """Accept only the one protected operation scope, never a prefix."""
        if self.style_policy_path is None or not isinstance(scope, str):
            return False
        _policy, policy_sha = self._document(self.style_policy_path, "standing policy")
        return scope == f"JACO_GEN28_STYLE_REMEDIATION_001:{policy_sha}"

    def _verify_style_remediation(self, receipt: dict, binding: dict, claim: dict) -> None:
        """Verify the already-submitted successor, never a caller-selected operation."""
        assert self.style_authority_path and self.style_qualification_path
        assert self.style_policy_path and self.financial_source_path
        authority, authority_sha = self._document(self.style_authority_path, "style authority")
        qualification, qualification_sha = self._document(self.style_qualification_path, "style qualification")
        policy, policy_sha = self._document(self.style_policy_path, "standing policy")
        financial, financial_sha = self._document(self.financial_source_path, "financial source")
        operation = "JACO_GEN28_STYLE_REMEDIATION_001"
        local_id = "27ef6a66be109984c208daa6e053e4a9d4f51a0202b46d624ea964336f74d052"
        remote_id = "01a1b4a6-65e9-7e31-815f-1726452a3693"
        endpoint = "blackforestlabs/flux-3/text-to-image"
        ledger = authority.get("canonical_ledger")
        consent = policy.get("canonical_consent") if isinstance(policy, dict) else None
        marker = receipt.get("final_admission_marker") if isinstance(receipt, dict) else None
        expected_scope = f"{operation}:{policy_sha}"
        if (
            authority_sha != self.style_authority_sha256
            or authority.get("schema") != "jaco-derived-operation-authority/v1"
            or authority.get("status") != "READY_FOR_INDEPENDENT_PARENT_REVIEW_NO_RESERVATION_NO_POST"
            or authority.get("operation_id") != operation or authority.get("episode_id") != "JACO_GENESIS28"
            or authority.get("policy_sha256") != policy_sha
            or not isinstance(ledger, dict) or ledger.get("financial_policy_sha256") != financial_sha
            or not isinstance(financial, dict) or financial.get("currency") != "USD"
            or policy.get("schema") != "jaco-standing-episode-operation-policy/v1"
            or policy.get("episode_id") != "JACO_GENESIS28" or not isinstance(consent, dict)
            or not isinstance(consent.get("session_id"), str) or not consent["session_id"]
            or type(consent.get("consent_message_id")) is not int
            or type(consent.get("prompt_message_id")) is not int
            or qualification.get("schema") != "jaco-protected-deployment-qualification/v2"
            or qualification.get("status") != "QUALIFIED" or qualification.get("operation_id") != operation
            or qualification.get("episode_id") != "JACO_GENESIS28" or qualification.get("mode") != "LIVE"
            or qualification.get("authorization") != "STANDING_USER_AUTHORIZATION_PROTECTED_POLICY"
            or qualification.get("policy_sha256") != policy_sha
            or qualification.get("operation_policy_sha256") != policy_sha
            or qualification.get("financial_policy_sha256") != financial_sha
            or (self.execution_code_path is not None and qualification.get("execution_code_sha256") != sha256(self.execution_code_path.read_bytes()).hexdigest())
            or not isinstance(receipt, dict) or receipt.get("receipt_schema") != 2
            or receipt.get("request_id") != local_id or receipt.get("scene") != operation
            or receipt.get("endpoint") != endpoint or receipt.get("mode") != "LIVE"
            or receipt.get("input_contract") != {"schema": 1, "input_kind": "TEXT_TO_IMAGE", "payload_sha256": marker.get("payload_sha256") if isinstance(marker, dict) else None}
            or not isinstance(marker, dict)
            or marker != {"schema": 2, "kind": "JACO_STYLE_REMEDIATION_QUALIFICATION_MARKER", "qualification_sha256": qualification_sha, "request_id": local_id, "endpoint": endpoint, "payload_sha256": marker.get("payload_sha256"), "input_kind": "TEXT_TO_IMAGE", "auth_scope": expected_scope, "reserved_usd": "0.048", "trust_identity": "PROTECTED_DEPLOYMENT_QUALIFICATION"}
            or not isinstance(marker.get("payload_sha256"), str) or not _HASH.fullmatch(marker["payload_sha256"])
            or binding != {"schema": 2, "local_request_id": local_id, "provider_request_id": remote_id, "endpoint_id": endpoint, "auth_scope": expected_scope, "authority_mode": "LIVE"}
            or not isinstance(claim, dict) or claim.get("request_id") != local_id
            or claim.get("episode_id") != "JACO_GENESIS28" or claim.get("provider") != endpoint
            or claim.get("reserved_usd") != "0.048000" or claim.get("state") != "RESERVED"
            or claim.get("actual_usd") is not None or claim.get("settlement_mode") != "FAL_READBACK_V2"
            or claim.get("auth_scope") != expected_scope or claim.get("provider_request_id") != remote_id
            or claim.get("authority_mode") != "LIVE" or claim.get("currency", "USD") != "USD"
        ):
            raise SettlementDenied("final-admission style-remediation binding mismatch")

    def scope(self) -> str:
        """Return the single exact scope after reopening protected policy."""
        _mandate, mandate_sha, _authorization, _authorization_sha, _policy = self._policy_contract()
        return f"JACO_FLUX3_FINAL_ADMISSION:{mandate_sha}"

    def matches_scope(self, scope: object) -> bool:
        """Identify the composition's one explicitly provisioned authority."""
        if not isinstance(scope, str):
            return False
        if self.style_authority_path is not None:
            return self._is_style_scope(scope)
        return scope == self.scope()


class ModelSheetV3BillingVerifier(JacoFinalAdmissionContractVerifier):
    """Typed, fixed-scope verifier for the completed three-view model sheet.

    The production companion passes only protected paths and the already
    validated deployment-code hash.  This class has no fallback to labels,
    prefixes, or a generic LIVE authorization field.
    """

    _OPERATION = "JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001"
    _LOCAL_ID = "c81bec2124005d306d2f843ac7b5d5c747feb254d090610582d79099f0535cc1"
    _REMOTE_ID = "01a1b7bf-ef29-77c1-ad38-5214baa197de"
    _ENDPOINT = "blackforestlabs/flux-3/text-to-image"

    def __init__(self, *, root, execution_root, authority_path, policy_path,
                 preflight_path, qualification_path, financial_source_path,
                 authority_sha256: str, execution_code_sha256: str) -> None:
        super().__init__(root=root, execution_root=execution_root)
        from pathlib import Path

        self.authority_path = Path(authority_path).resolve()
        self.policy_path = Path(policy_path).resolve()
        self.preflight_path = Path(preflight_path).resolve()
        self.qualification_path = Path(qualification_path).resolve()
        self.financial_source_path = Path(financial_source_path).resolve()
        if (not isinstance(authority_sha256, str) or not _HASH.fullmatch(authority_sha256)
                or not isinstance(execution_code_sha256, str) or not _HASH.fullmatch(execution_code_sha256)):
            raise TypeError("model-sheet contract hashes must be lower-case SHA-256")
        self.authority_sha256 = authority_sha256
        self.execution_code_sha256 = execution_code_sha256

    def _contract(self) -> tuple[dict, dict, dict, dict, str]:
        authority, authority_sha = self._document(self.authority_path, "model-sheet authority")
        policy, policy_sha = self._document(self.policy_path, "model-sheet policy")
        preflight, preflight_sha = self._document(self.preflight_path, "model-sheet preflight")
        qualification, qualification_sha = self._document(self.qualification_path, "model-sheet qualification")
        _financial, financial_sha = self._document(self.financial_source_path, "model-sheet financial source")
        ledger = authority.get("canonical_ledger")
        deployment = authority.get("deployment_qualification")
        operation = preflight.get("operation") if isinstance(preflight, dict) else None
        amendment = qualification.get("technical_deployment_amendment") if isinstance(qualification, dict) else None
        if (
            authority_sha != self.authority_sha256
            or authority.get("schema") != "jaco-derived-operation-authority/v2"
            or authority.get("status") != "READY_FOR_INDEPENDENT_PARENT_REVIEW_NO_RESERVATION_NO_POST"
            or authority.get("operation_id") != self._OPERATION
            or authority.get("episode_id") != "JACO_GENESIS28"
            or authority.get("policy_path") != str(self.policy_path)
            or authority.get("policy_sha256") != policy_sha
            or authority.get("preflight_path") != str(self.preflight_path)
            or authority.get("preflight_sha256") != preflight_sha
            or not isinstance(ledger, dict)
            or ledger.get("financial_policy_sha256") != financial_sha
            or ledger.get("financial_source") != {"path": str(self.financial_source_path), "sha256": financial_sha}
            or not isinstance(deployment, dict)
            or deployment != {"path": str(self.qualification_path), "sha256": qualification_sha}
            or policy.get("schema") != "jaco-standing-episode-operation-policy/v1"
            or policy.get("episode_id") != "JACO_GENESIS28"
            or preflight.get("schema") != "jaco-episode-operation-preflight/v3"
            or preflight.get("episode_id") != "JACO_GENESIS28"
            or not isinstance(operation, dict)
            or operation.get("id") != self._OPERATION
            or operation.get("endpoint") != self._ENDPOINT
            or operation.get("call_limit") != 1
            or not isinstance(operation.get("payload_sha256"), str)
            or not _HASH.fullmatch(operation["payload_sha256"])
            or qualification.get("schema") != "jaco-protected-deployment-qualification/v3"
            or qualification.get("status") != "QUALIFIED"
            or qualification.get("operation_id") != self._OPERATION
            or qualification.get("episode_id") != "JACO_GENESIS28"
            or qualification.get("mode") != "LIVE"
            or qualification.get("authorization") != "STANDING_USER_AUTHORIZATION_PROTECTED_POLICY"
            or qualification.get("policy_sha256") != policy_sha
            or qualification.get("operation_policy_sha256") != policy_sha
            or qualification.get("financial_policy_sha256") != financial_sha
            or qualification.get("preflight_sha256") != preflight_sha
            or qualification.get("execution_code_sha256") != self.execution_code_sha256
            or not isinstance(amendment, dict)
            or amendment.get("schema") != "jaco-technical-deployment-amendment/v1"
        ):
            raise SettlementDenied("model-sheet protected contract is invalid")
        return authority, preflight, qualification, operation, qualification_sha

    def matches_scope(self, scope: object) -> bool:
        if not isinstance(scope, str):
            return False
        authority, _preflight, _qualification, _operation, _qualification_sha = self._contract()
        return scope == f"{self._OPERATION}:{authority['policy_sha256']}"

    def verify(self, receipt: dict, binding: dict, claim: dict) -> None:
        authority, _preflight, _qualification, operation, qualification_sha = self._contract()
        scope = f"{self._OPERATION}:{authority['policy_sha256']}"
        marker = receipt.get("final_admission_marker") if isinstance(receipt, dict) else None
        expected_marker = {
            "schema": 2, "kind": "JACO_FLUX3_FINAL_ADMISSION_MARKER",
            "receipt_path": str((self.execution_root / "protected-qualification.v3.json").resolve()),
            "receipt_sha256": qualification_sha, "request_id": self._LOCAL_ID,
            "endpoint": self._ENDPOINT, "payload_sha256": operation["payload_sha256"],
            "input_kind": "TEXT_TO_IMAGE", "auth_scope": scope,
            "reserved_usd": "0.048",
            "trust_identity": "YOUNG_IDENTITY_MODEL_SHEET_PROTECTED_QUALIFICATION",
        }
        expected_binding = {"schema": 2, "local_request_id": self._LOCAL_ID,
                            "provider_request_id": self._REMOTE_ID, "endpoint_id": self._ENDPOINT,
                            "auth_scope": scope, "authority_mode": "LIVE"}
        if (
            not isinstance(receipt, dict) or receipt.get("receipt_schema") != 2
            or receipt.get("request_id") != self._LOCAL_ID or receipt.get("scene") != self._OPERATION
            or receipt.get("endpoint") != self._ENDPOINT or receipt.get("mode") != "LIVE"
            or receipt.get("input_contract") != {"schema": 1, "input_kind": "TEXT_TO_IMAGE", "payload_sha256": operation["payload_sha256"]}
            or marker != expected_marker or binding != expected_binding
            or not isinstance(claim, dict) or claim.get("request_id") != self._LOCAL_ID
            or claim.get("episode_id") != "JACO_GENESIS28" or claim.get("provider") != self._ENDPOINT
            or claim.get("reserved_usd") != "0.048000" or claim.get("state") != "RESERVED"
            or claim.get("actual_usd") is not None or claim.get("settlement_mode") != "FAL_READBACK_V2"
            or claim.get("auth_scope") != scope or claim.get("provider_request_id") != self._REMOTE_ID
            or claim.get("authority_mode") != "LIVE" or claim.get("currency", "USD") != "USD"
        ):
            raise SettlementDenied("model-sheet v3 settlement binding mismatch")


def _canonical_hash(document: dict) -> str:
    return sha256(
        json.dumps(
            document, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def _utc(value: object, label: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != UTC.utcoffset(value)
    ):
        raise SettlementDenied(f"{label} must be timezone-aware UTC")
    return value


def _unknown(ledger: StandingBudgetLedger, request_id: str, reason: str, *, coordinator=None) -> dict:
    try:
        claim = ledger.claim(request_id)
        if claim["actual_usd"] is None:
            if coordinator is None:
                claim = ledger.mark_unknown(request_id)
            else:
                return coordinator.mark_unknown(request_id, reason)
    except BudgetDenied as exc:
        raise SettlementDenied("reservation cannot be preserved as UNKNOWN") from exc
    return {
        "request_id": request_id,
        "status": "UNKNOWN",
        "reason": reason,
        "claim": claim,
    }


class FalLedgerSettlementService:
    """Explicitly reconcile one persisted executor claim using bounded FAL GET readback."""

    def __init__(
        self,
        ledger: StandingBudgetLedger,
        executor: Executor,
        consumer: FalBillingConsumer,
        *,
        clock: Callable[[], datetime] | None = None,
        max_fresh_seconds: int = _MAX_FRESH_SECONDS,
        coordinator=None,
        jaco_final_admission_verifier: JacoFinalAdmissionContractVerifier | None = None,
    ) -> None:
        if (
            not isinstance(ledger, StandingBudgetLedger)
            or not isinstance(executor, Executor)
            or not isinstance(consumer, FalBillingConsumer)
        ):
            raise TypeError("standing ledger, executor, and FAL consumer are required")
        if (
            isinstance(max_fresh_seconds, bool)
            or not isinstance(max_fresh_seconds, int)
            or not 1 <= max_fresh_seconds <= 3600
        ):
            raise ValueError("invalid readback freshness bound")
        self.ledger = ledger
        self.executor = executor
        self.consumer = consumer
        self.clock = clock or (lambda: datetime.now(UTC))
        self.max_fresh_seconds = max_fresh_seconds
        if (
            jaco_final_admission_verifier is not None
            and not isinstance(jaco_final_admission_verifier, JacoFinalAdmissionContractVerifier)
        ):
            raise TypeError("Jaco final-admission verifier must be trusted composition")
        self.jaco_final_admission_verifier = jaco_final_admission_verifier
        if coordinator is not None:
            from src.hybrid.billing_snapshot import BillingSnapshotCoordinator
            if not isinstance(coordinator, BillingSnapshotCoordinator):
                raise TypeError("billing snapshot coordinator must be trusted composition")
            if coordinator.executor is not executor or coordinator.ledger is not ledger:
                raise ValueError("billing snapshot coordinator must bind this executor and ledger")
        self.coordinator = coordinator

    def admit(self, job, *, episode_id: str, experiment: bool = False) -> dict:
        """The configured coordinator is the only FAL admission writer path."""
        if self.coordinator is None:
            raise SettlementDenied("configured billing snapshot coordinator required for admission")
        return self.coordinator.prepare_and_reserve(job, episode_id=episode_id, experiment=experiment)

    async def run_admitted(self, job, provider, *, authorization=None, price=None) -> dict:
        """Factory-owned wiring; callers cannot supply a checkpoint callback."""
        if self.coordinator is None:
            raise SettlementDenied("configured billing snapshot coordinator required for FAL submit")
        return await self.executor.run(
            job, provider, authorization=authorization, price=price,
            settlement_checkpoint=self.coordinator.execution_capability(job.request_id), submit_prepared=True,
        )

    def _binding(self, local_request_id: str, *, require_provider_id: bool = True) -> tuple[dict, dict]:
        if not isinstance(local_request_id, str) or not local_request_id:
            raise SettlementDenied("local request id required")
        receipt = self.executor.inspect(local_request_id)
        if not isinstance(receipt, dict) or receipt.get("receipt_schema") != 2:
            raise SettlementDenied("executor receipt schema-v2 binding required")
        binding = receipt.get("settlement_binding")
        required = {
            "schema", "local_request_id", "provider_request_id", "endpoint_id",
            "auth_scope", "authority_mode",
        }
        if not isinstance(binding, dict) or set(binding) != required:
            raise SettlementDenied("executor settlement binding is missing or malformed")
        if binding.get("schema") != 2 or binding.get("local_request_id") != local_request_id:
            raise SettlementDenied("executor settlement binding local request mismatch")
        if not all(isinstance(binding.get(key), str) and binding[key] and len(binding[key]) <= 512
                   for key in ("provider_request_id", "endpoint_id", "auth_scope", "authority_mode")):
            raise SettlementDenied("executor settlement binding is malformed")
        if binding["provider_request_id"] == "UNASSIGNED" and require_provider_id:
            raise SettlementDenied("remote provider request id is not durable")
        if receipt.get("request_id") != local_request_id or receipt.get("endpoint") != binding["endpoint_id"]:
            raise SettlementDenied("executor receipt does not bind settlement endpoint")
        if receipt.get("mode") == "TEST":
            expected_scope = "TEST_LOCAL_ONLY"
        elif receipt.get("mode") == "LIVE":
            jaco_scope = self.jaco_final_admission_verifier
            if jaco_scope is not None and jaco_scope.matches_scope(binding["auth_scope"]):
                expected_scope = binding["auth_scope"]
            else:
                authorization = receipt.get("authorization")
                if not isinstance(authorization, dict) or authorization.get("request_id") != local_request_id:
                    raise SettlementDenied("LIVE executor receipt authorization binding is missing")
                expected_scope = f"LIVE_AUTHORIZATION_REQUEST:{local_request_id}"
        else:
            raise SettlementDenied("executor receipt mode is invalid")
        if binding["auth_scope"] != expected_scope or binding["authority_mode"] != receipt["mode"]:
            raise SettlementDenied("executor settlement auth scope mismatch")
        return receipt, binding

    def reserve_from_executor(self, local_request_id: str, *, episode_id: str,
                              quoted_cap_usd: object, experiment: bool = False) -> dict:
        """Reserve only from the same durable receipt that later authorizes readback."""
        _, binding = self._binding(local_request_id, require_provider_id=False)
        try:
            return self.ledger.reserve(
                local_request_id, episode_id, binding["endpoint_id"], quoted_cap_usd,
                experiment=experiment, settlement_mode="FAL_READBACK_V2",
                auth_scope=binding["auth_scope"],
                provider_request_id=binding["provider_request_id"],
                authority_mode=binding["authority_mode"],
            )
        except BudgetDenied as exc:
            raise SettlementDenied("FAL reservation rejected") from exc

    async def reconcile(
        self, local_request_id: str, *, start: datetime, end: datetime
    ) -> dict:
        receipt, binding = self._binding(local_request_id)
        try:
            claim = self.ledger.claim(local_request_id)
        except BudgetDenied as exc:
            raise SettlementDenied(
                "matching standing-ledger reservation required"
            ) from exc
        if claim["provider"] != binding["endpoint_id"]:
            raise SettlementDenied(
                "standing-ledger provider does not bind executor endpoint"
            )
        if (claim["settlement_mode"], claim["auth_scope"],
                claim["provider_request_id"], claim["authority_mode"]) != (
                "FAL_READBACK_V2", binding["auth_scope"],
                binding["provider_request_id"], binding["authority_mode"]):
            raise SettlementDenied("standing-ledger settlement binding mismatch")
        if (
            self.jaco_final_admission_verifier is not None
            and self.jaco_final_admission_verifier.matches_scope(binding["auth_scope"])
        ):
            self.jaco_final_admission_verifier.verify(receipt, binding, claim)
        transport_authority = getattr(self.consumer._transport, "authority", None)
        if transport_authority == "TEST_NOT_AUTHORITY":
            if self.ledger.binding.get("mode") != "TEST" or receipt["mode"] != "TEST":
                raise SettlementDenied("TEST fixture cannot settle a LIVE claim")
        elif transport_authority != "TRANSPORT_AUTHENTICATED_FAL_READBACK":
            raise SettlementDenied(
                "configured FAL billing transport authority required"
            )

        try:
            window_start, window_end, now = (
                _utc(start, "start"),
                _utc(end, "end"),
                _utc(self.clock(), "clock"),
            )
        except SettlementDenied:
            return _unknown(self.ledger, local_request_id, "invalid readback window", coordinator=self.coordinator)
        if (
            window_start >= window_end
            or window_end > now
            or (now - window_end).total_seconds() > self.max_fresh_seconds
        ):
            return _unknown(
                self.ledger, local_request_id, "readback window is not fresh", coordinator=self.coordinator
            )

        readback = await self.consumer.readback(
            BillingReadbackRequest(
                binding["provider_request_id"],
                binding["endpoint_id"],
                window_start,
                window_end,
            )
        )
        if readback.status is not BillingStatus.BILLED:
            return _unknown(
                self.ledger,
                local_request_id,
                "billing readback is not one complete billed event",
                coordinator=self.coordinator,
            )
        if (
            readback.authority != transport_authority
            or readback.request_id != binding["provider_request_id"]
            or readback.endpoint_id != binding["endpoint_id"]
        ):
            return _unknown(
                self.ledger, local_request_id, "billing readback binding mismatch", coordinator=self.coordinator
            )
        actual = readback.actual_cost_usd
        if not isinstance(actual, Decimal):
            return _unknown(
                self.ledger, local_request_id, "billing money is not exact micro-USD", coordinator=self.coordinator
            )
        try:
            micros = actual * Decimal(1_000_000)
            if (
                not actual.is_finite()
                or actual < 0
                or micros != micros.to_integral_value()
                or micros > 2**63 - 1
            ):
                raise ValueError
        except (InvalidOperation, ValueError):
            return _unknown(
                self.ledger, local_request_id, "billing money is not exact micro-USD", coordinator=self.coordinator
            )

        settlement_receipt = {
            "schema": 2,
            "authority": transport_authority,
            "local_request_id": local_request_id,
            "provider_request_id": binding["provider_request_id"],
            "endpoint_id": binding["endpoint_id"],
            "auth_scope": binding["auth_scope"],
            "authority_mode": binding["authority_mode"],
            "currency": "USD",
            "cost_total_micro_usd": str(int(micros)),
            "evidence_hash": _canonical_hash({
                "authority": transport_authority, "provider_request_id": readback.request_id,
                "endpoint_id": readback.endpoint_id, "cost_total": str(actual),
            }),
            "provenance": "FAL_BILLING_READBACK",
        }
        receipt_body = json.dumps(settlement_receipt, sort_keys=True, separators=(",", ":"), allow_nan=False)
        receipt_hash = sha256(receipt_body.encode("utf-8")).hexdigest()
        try:
            if self.coordinator is None:
                settled = self.ledger._settle_verified_receipt(receipt_body)
            else:
                settled = self.coordinator.settle_verified_receipt(receipt_body, settlement_receipt)["claim"]
        except BudgetDenied as exc:
            raise SettlementDenied("authenticated ledger settlement rejected") from exc
        return {
            "request_id": local_request_id,
            "status": "SETTLED",
            "receipt_sha256": receipt_hash,
            "claim": settled,
        }
