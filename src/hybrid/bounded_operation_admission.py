"""Offline-only, data-driven admission for bounded typed operations.

This adapter deliberately neither creates execution state nor constructs a
provider.  A production composition may consume a returned plan only after a
separately provisioned LIVE qualification; this module rejects LIVE itself.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from pathlib import Path
from typing import Any

_TRUSTED_ROOT = Path("C:/HermesStudio/production/jaco-flux3")
_OPERATION_ID = "JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001"
_ENDPOINT = "blackforestlabs/flux-3/text-to-image"
_VIEWS = (
    "FRONT_OPEN_EYE_HEAD_AND_SHOULDERS",
    "THREE_QUARTER_OPEN_EYE_HEAD_AND_SHOULDERS",
    "STANDING_FULL_BODY",
)


class BoundedAdmissionError(PermissionError):
    """A protected operation cannot be admitted by the offline adapter."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise BoundedAdmissionError("duplicate JSON key")
        result[key] = value
    return result


def _read_json(path: Path, label: str) -> tuple[dict[str, Any], str]:
    try:
        info = path.lstat()
        if path.is_symlink() or bool(getattr(info, "st_file_attributes", 0) & getattr(os, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)):
            raise BoundedAdmissionError(f"{label} must not be a reparse point")
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(BoundedAdmissionError("non-finite JSON")))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoundedAdmissionError(f"{label} is not strict readable JSON") from exc
    if not isinstance(value, dict):
        raise BoundedAdmissionError(f"{label} must be an object")
    return value, sha256(raw).hexdigest()


def _amount(value: object) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BoundedAdmissionError("maximum cost must be finite") from exc
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -6:
        raise BoundedAdmissionError("maximum cost must be positive USD with at most six decimals")
    return amount


@dataclass(frozen=True)
class _Composition:
    root: Path
    test_only: bool

    @property
    def authority_dir(self) -> Path:
        return self.root / "operation-authorities"

    @property
    def ledger_path(self) -> Path:
        return self.root / "ledger" / "standing-budget.sqlite3"

    def manifest_path(self, operation_id: str) -> Path:
        if operation_id != _OPERATION_ID:
            raise BoundedAdmissionError("operation type is not provisioned")
        return self.authority_dir / f"{operation_id}.json"


class BoundedOperationAdmission:
    """Read a composition-selected manifest and return a non-effecting plan."""

    def __init__(self) -> None:
        self._composition = _Composition(_TRUSTED_ROOT.resolve(), False)

    @classmethod
    def _for_test(cls, root: Path) -> BoundedOperationAdmission:
        adapter = object.__new__(cls)
        adapter._composition = _Composition(Path(root).resolve(), True)
        return adapter

    def _manifest(self, operation_id: str) -> tuple[dict[str, Any], str]:
        path = self._composition.manifest_path(operation_id)
        if path.parent != self._composition.authority_dir:
            raise BoundedAdmissionError("caller-selected authority path is forbidden")
        manifest, digest = _read_json(path, "protected operation manifest")
        required = {"schema", "mode", "status", "operation", "bindings"}
        if set(manifest) != required or manifest.get("schema") != "bounded-operation-manifest/v1":
            raise BoundedAdmissionError("closed operation manifest required")
        if manifest.get("status") != "OFFLINE_ADMISSION_ONLY":
            raise BoundedAdmissionError("operation manifest is not offline-admissible")
        if manifest.get("mode") == "LIVE":
            raise BoundedAdmissionError("LIVE operation remains unqualified in offline adapter")
        if manifest.get("mode") != "TEST" or not self._composition.test_only:
            raise BoundedAdmissionError("only private TEST composition is supported")
        return manifest, digest

    def _validate(self, manifest: dict[str, Any]) -> tuple[dict[str, Any], str]:
        operation = manifest.get("operation")
        bindings = manifest.get("bindings")
        operation_required = {"id", "episode_id", "type", "endpoint", "input_kind", "maximum_cost_usd", "call_limit", "payload", "views"}
        binding_required = {"financial_policy_sha256", "operation_policy_sha256", "preflight_sha256", "qualification_sha256"}
        if not isinstance(operation, dict) or set(operation) != operation_required or not isinstance(bindings, dict) or set(bindings) != binding_required:
            raise BoundedAdmissionError("closed typed operation and bindings required")
        if (operation.get("id") != _OPERATION_ID or operation.get("episode_id") != "JACO_GENESIS28"
                or operation.get("type") != "JACO_YOUNG_IDENTITY_MODEL_SHEET" or operation.get("endpoint") != _ENDPOINT
                or operation.get("input_kind") != "TEXT_TO_IMAGE" or operation.get("call_limit") != 1
                or tuple(operation.get("views", ())) != _VIEWS):
            raise BoundedAdmissionError("operation type, endpoint, or three-view design changed")
        payload = operation.get("payload")
        if not isinstance(payload, dict) or set(payload) != {"prompt", "aspect_ratio", "resolution", "output_format", "num_images"}:
            raise BoundedAdmissionError("closed text-to-image payload required")
        if (not isinstance(payload.get("prompt"), str) or not payload["prompt"].strip()
                or payload.get("aspect_ratio") != "16:9" or payload.get("resolution") != "1k"
                or payload.get("output_format") != "png" or payload.get("num_images") != 1):
            raise BoundedAdmissionError("protected payload changed")
        _amount(operation.get("maximum_cost_usd"))
        if any(not isinstance(value, str) or len(value) != 64 for value in bindings.values()):
            raise BoundedAdmissionError("hash bindings are malformed")
        return operation, sha256(_canonical(operation)).hexdigest()

    def _validate_bound_evidence(
        self, manifest: dict[str, Any], operation: dict[str, Any]
    ) -> dict[str, Any]:
        bindings = manifest["bindings"]
        root = self._composition.root
        expected = {
            "financial_policy_sha256": root / "trusted-policy" / "financial-policy.json",
            "operation_policy_sha256": root / "trusted-policy" / "operation-policy.json",
            "qualification_sha256": root / "trusted-policy" / "qualification.json",
            "preflight_sha256": root / "preflight" / f"{_OPERATION_ID}.json",
        }
        documents: dict[str, dict[str, Any]] = {}
        for key, path in expected.items():
            document, digest = _read_json(path, key)
            if digest != bindings[key]:
                raise BoundedAdmissionError(f"{key} changed")
            documents[key] = document
        financial = documents["financial_policy_sha256"]
        policy = documents["operation_policy_sha256"]
        qualification = documents["qualification_sha256"]
        preflight = documents["preflight_sha256"]
        if financial != {
            "schema": "financial-policy/v1",
            "role": "FINANCIAL_ONLY",
            "episode_cap_usd": "10.000000",
        }:
            raise BoundedAdmissionError("financial policy is not financial-only")
        if policy != {"schema": "operation-policy/v1", "operation_id": _OPERATION_ID}:
            raise BoundedAdmissionError("operation policy binding changed")
        if qualification != {"schema": "qualification/v1", "mode": "TEST", "status": "QUALIFIED"}:
            raise BoundedAdmissionError("TEST qualification changed")
        if preflight != {
            "schema": "bounded-operation-preflight/v1", "operation_id": _OPERATION_ID,
            "endpoint": _ENDPOINT, "payload": operation["payload"], "views": list(_VIEWS),
        }:
            raise BoundedAdmissionError("preflight payload or geometry binding changed")
        return financial

    def _ledger_is_fresh(
        self,
        request_id: str,
        financial_policy_sha256: str,
        *,
        next_cap_usd: Decimal,
        episode_cap_usd: Decimal,
    ) -> None:
        ledger_path = self._composition.ledger_path
        try:
            with sqlite3.connect(f"file:{ledger_path.as_posix()}?mode=ro", uri=True) as db:
                tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if not {"policy", "claims", "settlement_receipts"}.issubset(tables):
                    raise BoundedAdmissionError("canonical ledger schema is incompatible")
                row = db.execute("SELECT binding FROM policy WHERE id=1").fetchone()
                binding = json.loads(row[0]) if row else None
                claimed = db.execute("SELECT 1 FROM claims WHERE request_id=?", (request_id,)).fetchone()
                claims = db.execute(
                    "SELECT episode_id, cap, actual FROM claims WHERE episode_id=?",
                    ("JACO_GENESIS28",),
                ).fetchall()
        except (sqlite3.Error, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BoundedAdmissionError("canonical ledger is unreadable") from exc
        if not isinstance(binding, dict) or binding.get("policy_sha256") != financial_policy_sha256:
            raise BoundedAdmissionError("foreign ledger or financial policy binding")
        if claimed:
            raise BoundedAdmissionError("operation replay/collision already has a ledger claim")
        exposure_micro = 0
        for episode_id, cap, actual in claims:
            if episode_id != "JACO_GENESIS28" or type(cap) is not int or cap < 0:
                raise BoundedAdmissionError("canonical ledger claim is malformed")
            if actual is not None and (type(actual) is not int or actual < 0):
                raise BoundedAdmissionError("canonical ledger settlement is malformed")
            exposure_micro += actual if actual is not None else cap
        next_micro = int(next_cap_usd * 1_000_000)
        cap_micro = int(episode_cap_usd * 1_000_000)
        if exposure_micro + next_micro > cap_micro:
            raise BoundedAdmissionError("canonical ledger cap exposure is exhausted")

    def admit(self, *, operation_id: str) -> dict[str, str | int]:
        """Validate all bound bytes before returning a plan; performs no writes."""
        manifest, manifest_sha = self._manifest(operation_id)
        operation, operation_sha = self._validate(manifest)
        financial = self._validate_bound_evidence(manifest, operation)
        request_id = sha256(_canonical({"operation": operation, "manifest_sha256": manifest_sha})).hexdigest()
        self._ledger_is_fresh(
            request_id,
            manifest["bindings"]["financial_policy_sha256"],
            next_cap_usd=_amount(operation["maximum_cost_usd"]),
            episode_cap_usd=_amount(financial["episode_cap_usd"]),
        )
        return {
            "action": "OFFLINE_ADMISSION_ONLY", "mode": "TEST", "operation_id": operation_id,
            "request_id": request_id, "operation_sha256": operation_sha,
            "manifest_sha256": manifest_sha, "post_count": 0, "reserve_count": 0,
        }

    def recovery_plan(self, plan: dict[str, object], *, provider_request_id: str) -> dict[str, str | int]:
        """Return only the fixed typed GET reconciliation instruction, never POST."""
        required = {"action", "mode", "operation_id", "request_id", "operation_sha256", "manifest_sha256", "post_count", "reserve_count"}
        if (not isinstance(plan, dict) or set(plan) != required or plan.get("action") != "OFFLINE_ADMISSION_ONLY"
                or plan.get("mode") != "TEST" or plan.get("operation_id") != _OPERATION_ID
                or not isinstance(plan.get("request_id"), str) or len(plan["request_id"]) != 64
                or not isinstance(provider_request_id, str) or not provider_request_id or len(provider_request_id) > 512):
            raise BoundedAdmissionError("closed admitted operation plan required")
        return {"action": "GET_ONLY_RECONCILE_EXISTING_REQUEST", "operation_id": _OPERATION_ID,
                "provider_request_id": provider_request_id, "post_count": 0, "reserve_count": 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline bounded-operation admission probe")
    parser.add_argument("--operation", required=True, help="fixed provisioned operation ID")
    parser.add_argument("--probe", action="store_true", help="read-only protected binding probe")
    args = parser.parse_args(argv)
    if not args.probe:
        parser.error("--probe is required; no execution command exists")
    print(json.dumps(BoundedOperationAdmission().admit(operation_id=args.operation), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
