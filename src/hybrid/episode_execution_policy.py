"""Immutable, non-dispatchable Jacó episode-operation policy compiler.

The compiler turns already-recorded failure evidence into exactly one bounded
quality-remediation preflight.  It intentionally never reserves money, creates
an executor, reads credentials, or authorizes a provider POST.  A caller must
supply a separately authenticated operation authority to any later dispatch
kernel; this module only makes absence of that authority explicit.
"""
from __future__ import annotations

import json
import os
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
EPISODE_ID = "JACO_GENESIS28"
OPERATION_ID = "JACO_GEN28_STYLE_REMEDIATION_001"
ENDPOINT = "blackforestlabs/flux-3/text-to-image"
MAXIMUM_USD = Decimal("0.048")
CAP_USD = Decimal("10.00")


class EpisodePolicyError(ValueError):
    """An immutable policy input or dispatch attempt is not eligible."""


def _strict_json(path: Path, label: str) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise EpisodePolicyError(f"{label} must be readable strict JSON") from error
    if not isinstance(value, dict):
        raise EpisodePolicyError(f"{label} must be a JSON object")
    return value, sha256(raw).hexdigest()


def _money(value: object, label: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise EpisodePolicyError(f"{label} is invalid") from error
    if not amount.is_finite() or amount < 0:
        raise EpisodePolicyError(f"{label} is invalid")
    return amount


def _pin(path: Path, label: str) -> dict[str, str]:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise EpisodePolicyError(f"{label} must be an absolute regular file")
    return {"path": str(path), "sha256": sha256(path.read_bytes()).hexdigest()}


class EpisodeExecutionPolicyCompiler:
    """Compile the one planned replacement without minting authority."""

    @staticmethod
    def _prompt() -> str:
        return (
            "Genesis 28 child-safe canonical quality-remediation image. Young adult Jacó "
            "sleeps outdoors beside one stone under a luminous stairway; dark wavy hair, "
            "short brown beard, cream tunic, blue mantle. Match the manifest-approved EP8 "
            "F001 child-stylized 3D illustrative animation: rounded expressive family-feature "
            "design, warm painterly cinematic light, readable 1920x1080 landscape composition; "
            "do not use realistic/cinematic photorealistic rendering. Depict exactly one coherent "
            "angel group with varied natural poses, including both ascent and descent only when "
            "spatially clear. No duplicated angels, repeated robes, repeated wings, copied faces, "
            "one-way directional reading, weak stair-to-earth integration, text, captions, logos, "
            "watermarks, modern objects, extra fingers, or physical depiction of God."
        )

    def compile_quality_remediation(
        self,
        *,
        request_id: str,
        prior_packet: Path | str,
        prior_receipt: Path | str,
        failed_candidate: Path | str,
        prior_qa: Path | str,
        ep8_manifest: Path | str,
        ep8_reference: Path | str,
        user_mandate: Path | str,
        actual_spend_usd: object,
    ) -> dict[str, Any]:
        if request_id != REQUEST_ID:
            raise EpisodePolicyError("request is not the Jacó literal request")
        packet_path, receipt_path, candidate_path = Path(prior_packet), Path(prior_receipt), Path(failed_candidate)
        qa_path, manifest_path, reference_path, mandate_path = map(Path, (prior_qa, ep8_manifest, ep8_reference, user_mandate))
        packet, packet_sha = _strict_json(packet_path, "prior packet")
        receipt, receipt_sha = _strict_json(receipt_path, "prior receipt")
        qa, qa_sha = _strict_json(qa_path, "prior QA")
        manifest, manifest_sha = _strict_json(manifest_path, "EP8 manifest")
        mandate, mandate_sha = _strict_json(mandate_path, "user mandate")
        candidate_pin, reference_pin = _pin(candidate_path, "failed candidate"), _pin(reference_path, "EP8 style reference")
        if packet.get("schema") != "jaco-fal-flux3-successor-benchmark/3" or packet.get("kind") != "BOUNDED_FAL_FLUX3_CANARY_PREGEN_PACKET" or packet.get("bindings", {}).get("request_id") != REQUEST_ID:
            raise EpisodePolicyError("prior packet request binding is invalid")
        if receipt.get("kind") != "PNG_RESULT" or receipt.get("sha256") != candidate_pin["sha256"]:
            raise EpisodePolicyError("prior receipt does not bind failed candidate")
        issues = qa.get("issues")
        if qa.get("kind") != "BOUNDED_INTERNAL_VISUAL_QA" or qa.get("verdict") != "NOT_PROMOTED" or qa.get("candidate", {}).get("sha256") != candidate_pin["sha256"] or not isinstance(issues, list) or "STYLE_MISMATCH_TO_EP8_APPROVED_REFERENCE" not in issues:
            raise EpisodePolicyError("prior QA is not the exact rejected style evidence")
        if manifest.get("episode_id") != "EP8_PROMISE_SON_20260901" or manifest.get("frame_id") != "F001" or manifest.get("status") != "APPROVED_FINAL_FRAME" or manifest.get("sha256") != reference_pin["sha256"]:
            raise EpisodePolicyError("EP8 approved-style binding is invalid")
        if mandate.get("schema") != "JACO_USER_EXECUTION_MANDATE/2" or mandate.get("request_id") != REQUEST_ID or mandate.get("budget_cap_usd") != "10.00" or mandate.get("call_limit") != 1 or mandate.get("scope") != "EVALUATION_GENERATION_ONLY" or mandate.get("release_authorization") != "NOT_AUTHORIZED":
            raise EpisodePolicyError("cap or mandate collision")
        actual = _money(actual_spend_usd, "actual spend")
        if actual + MAXIMUM_USD > CAP_USD:
            raise EpisodePolicyError("whole-episode cap would be exceeded")
        payload = {"prompt": self._prompt(), "aspect_ratio": "16:9", "resolution": "1k", "output_format": "png", "enable_prompt_expansion": False, "safety_tolerance": 2}
        payload_sha = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        return {
            "schema": "jaco-episode-operation-preflight/v1",
            "status": "VALIDATED_PRE_DISPATCH_BLOCKED_MISSING_OPERATION_AUTHORIZATION",
            "episode_id": EPISODE_ID,
            "request_id": REQUEST_ID,
            "authorization_provenance": "RECOVERED_CANONICAL_HISTORY_EVALUATION_WITHIN_USD10_NOT_DEPLOYMENT_DISPATCH_AUTHORIZATION",
            "operation": {"id": OPERATION_ID, "kind": "QUALITY_REMEDIATION", "endpoint": ENDPOINT, "call_limit": 1, "maximum_usd": "0.048", "payload": payload, "payload_sha256": payload_sha, "replay_prohibition": "ONE_CALL_CAP_CONSUMES_THIS_OPERATION_ID"},
            "prior_attempt": {"packet": {"path": str(packet_path), "sha256": packet_sha}, "receipt": {"path": str(receipt_path), "sha256": receipt_sha}, "failed_candidate": candidate_pin, "qa": {"path": str(qa_path), "sha256": qa_sha}, "promotion": "PERMANENTLY_REJECTED_NOT_REUSABLE", "consumed_authority": {"path": str(mandate_path), "sha256": mandate_sha}},
            "style_constraint": {"ep8_manifest": {"path": str(manifest_path), "sha256": manifest_sha}, "reference": reference_pin, "required_issue_remediations": ["STYLE_MISMATCH_TO_EP8_APPROVED_REFERENCE", "ANGEL_DUPLICATION", "ONE_WAY_DIRECTIONAL_READING"]},
            "budget": {"episode_cap_usd": "10.00", "actual_spend_usd": f"{actual:.3f}", "pending_usd": "0.000", "next_operation_maximum_usd": "0.048", "post_dispatch_exposure_usd": f"{actual + MAXIMUM_USD:.3f}", "reservation": "NOT_CREATED"},
            "human_gates": ["thumbnail", "final_video"],
            "publication": "EXPLICIT_SEPARATE_COMMAND_REQUIRED",
            "dispatch": {"permitted": False, "reason": "New operation-specific trusted authority, fresh provider price evidence, and independent pre-generation review are absent; no reservation or provider POST was created."},
        }

    @staticmethod
    def write_preflight(path: Path | str, preflight: dict[str, Any]) -> dict[str, Any]:
        """Persist a preflight once, then reopen exact bytes before returning it."""
        destination = Path(path)
        if not destination.is_absolute() or destination.is_symlink():
            raise EpisodePolicyError("preflight destination must be absolute regular path")
        if preflight.get("schema") != "jaco-episode-operation-preflight/v1":
            raise EpisodePolicyError("invalid preflight")
        encoded = json.dumps(preflight, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
        except FileExistsError as error:
            raise EpisodePolicyError("preflight is write-once") from error
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if destination.read_bytes() != encoded:
            raise EpisodePolicyError("preflight readback mismatch")
        document, _digest = _strict_json(destination, "preflight")
        if document != preflight:
            raise EpisodePolicyError("preflight readback document mismatch")
        return document

    @staticmethod
    def validate_for_dispatch(preflight: object, *, trust_root_kind: str, operation_id: str) -> None:
        if not isinstance(preflight, dict) or preflight.get("schema") != "jaco-episode-operation-preflight/v1":
            raise EpisodePolicyError("invalid preflight")
        operation = preflight.get("operation")
        if not isinstance(operation, dict) or operation_id != operation.get("id"):
            raise EpisodePolicyError("operation replay or collision")
        if trust_root_kind == "TEST":
            raise EpisodePolicyError("TEST trust root cannot authorize LIVE dispatch")
        if preflight.get("prior_attempt", {}).get("promotion") != "PERMANENTLY_REJECTED_NOT_REUSABLE":
            raise EpisodePolicyError("rejected asset cannot be promoted or reused")
        if preflight.get("dispatch", {}).get("permitted") is not True:
            raise EpisodePolicyError("operation authorization is missing")
