from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _write(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))
    return sha256(path.read_bytes()).hexdigest()


def _fixture(root: Path, *, mode: str = "TEST") -> tuple[Path, dict]:
    operation_id = "JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001"
    payload = {
        "prompt": "test-only three-view young-adult Jacó model sheet",
        "aspect_ratio": "16:9",
        "resolution": "1k",
        "output_format": "png",
        "num_images": 1,
    }
    policy = root / "trusted-policy" / "operation-policy.json"
    financial = root / "trusted-policy" / "financial-policy.json"
    qualification = root / "trusted-policy" / "qualification.json"
    preflight = root / "preflight" / f"{operation_id}.json"
    financial_sha = _write(
        financial,
        {
            "schema": "financial-policy/v1",
            "role": "FINANCIAL_ONLY",
            "episode_cap_usd": "10.000000",
        },
    )
    policy_sha = _write(policy, {"schema": "operation-policy/v1", "operation_id": operation_id})
    qualification_sha = _write(qualification, {"schema": "qualification/v1", "mode": mode, "status": "QUALIFIED"})
    preflight_sha = _write(preflight, {
        "schema": "bounded-operation-preflight/v1", "operation_id": operation_id,
        "endpoint": "blackforestlabs/flux-3/text-to-image", "payload": payload,
        "views": ["FRONT_OPEN_EYE_HEAD_AND_SHOULDERS", "THREE_QUARTER_OPEN_EYE_HEAD_AND_SHOULDERS", "STANDING_FULL_BODY"],
    })
    ledger = root / "ledger" / "standing-budget.sqlite3"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(ledger) as db:
        db.execute("CREATE TABLE policy (id INTEGER PRIMARY KEY, binding TEXT NOT NULL)")
        db.execute(
            "CREATE TABLE claims (request_id TEXT PRIMARY KEY, state TEXT NOT NULL, "
            "episode_id TEXT NOT NULL, cap INTEGER NOT NULL, actual INTEGER)"
        )
        db.execute("CREATE TABLE settlement_receipts (request_id TEXT PRIMARY KEY)")
        db.execute("INSERT INTO policy VALUES (1, ?)", (json.dumps({"policy_sha256": financial_sha}),))
    manifest = {
        "schema": "bounded-operation-manifest/v1",
        "mode": mode,
        "status": "OFFLINE_ADMISSION_ONLY",
        "operation": {
            "id": operation_id, "episode_id": "JACO_GENESIS28",
            "type": "JACO_YOUNG_IDENTITY_MODEL_SHEET", "endpoint": "blackforestlabs/flux-3/text-to-image",
            "input_kind": "TEXT_TO_IMAGE", "maximum_cost_usd": "0.048000", "call_limit": 1,
            "payload": payload,
            "views": ["FRONT_OPEN_EYE_HEAD_AND_SHOULDERS", "THREE_QUARTER_OPEN_EYE_HEAD_AND_SHOULDERS", "STANDING_FULL_BODY"],
        },
        "bindings": {
            "financial_policy_sha256": financial_sha, "operation_policy_sha256": policy_sha,
            "preflight_sha256": preflight_sha, "qualification_sha256": qualification_sha,
        },
    }
    manifest_path = root / "operation-authorities" / f"{operation_id}.json"
    _write(manifest_path, manifest)
    return manifest_path, manifest


def test_three_view_manifest_admits_only_from_fixed_test_root_without_writes(tmp_path):
    from src.hybrid.bounded_operation_admission import BoundedOperationAdmission

    manifest_path, _manifest = _fixture(tmp_path)
    adapter = BoundedOperationAdmission._for_test(tmp_path)
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))

    plan = adapter.admit(operation_id="JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001")

    assert plan["mode"] == "TEST"
    assert plan["operation_id"] == "JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001"
    assert plan["action"] == "OFFLINE_ADMISSION_ONLY"
    assert plan["post_count"] == 0
    assert plan["reserve_count"] == 0
    assert manifest_path.is_file()
    assert before == sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))


@pytest.mark.parametrize("mutation", ["live", "endpoint", "extra_view", "foreign_ledger", "changed_hash"])
def test_adapter_fails_closed_before_ledger_mutation_or_transport(tmp_path, mutation):
    from src.hybrid.bounded_operation_admission import (
        BoundedAdmissionError,
        BoundedOperationAdmission,
    )

    manifest_path, manifest = _fixture(tmp_path, mode="LIVE" if mutation == "live" else "TEST")
    if mutation == "endpoint":
        manifest["operation"]["endpoint"] = "https://evil.example/post"
    elif mutation == "extra_view":
        manifest["operation"]["views"].append("BACK")
    elif mutation == "foreign_ledger":
        with sqlite3.connect(tmp_path / "ledger" / "standing-budget.sqlite3") as db:
            db.execute("UPDATE policy SET binding=? WHERE id=1", (json.dumps({"policy_sha256": "f" * 64}),))
    elif mutation == "changed_hash":
        manifest["bindings"]["preflight_sha256"] = "0" * 64
    _write(manifest_path, manifest)

    with pytest.raises(BoundedAdmissionError):
        BoundedOperationAdmission._for_test(tmp_path).admit(operation_id="JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001")
    assert not (tmp_path / "execution").exists()


def test_existing_claim_refuses_replay_and_recovery_is_get_only(tmp_path):
    from src.hybrid.bounded_operation_admission import (
        BoundedAdmissionError,
        BoundedOperationAdmission,
    )

    _manifest_path, _manifest = _fixture(tmp_path)
    adapter = BoundedOperationAdmission._for_test(tmp_path)
    plan = adapter.admit(operation_id="JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001")
    assert adapter.recovery_plan(plan, provider_request_id="test-remote-1") == {
        "action": "GET_ONLY_RECONCILE_EXISTING_REQUEST", "operation_id": plan["operation_id"],
        "provider_request_id": "test-remote-1", "post_count": 0, "reserve_count": 0,
    }
    with sqlite3.connect(tmp_path / "ledger" / "standing-budget.sqlite3") as db:
        db.execute(
            "INSERT INTO claims VALUES (?, 'RESERVED', 'JACO_GENESIS28', 48000, NULL)",
            (plan["request_id"],),
        )
    with pytest.raises(BoundedAdmissionError, match="replay"):
        adapter.admit(operation_id="JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001")


def test_cap_exposure_blocks_before_any_operation_artifact(tmp_path):
    from src.hybrid.bounded_operation_admission import (
        BoundedAdmissionError,
        BoundedOperationAdmission,
    )

    _fixture(tmp_path)
    with sqlite3.connect(tmp_path / "ledger" / "standing-budget.sqlite3") as db:
        db.execute(
            "INSERT INTO claims VALUES ('prior', 'SETTLED', 'JACO_GENESIS28', 1, 9_970_000)"
        )
    with pytest.raises(BoundedAdmissionError, match="cap exposure"):
        BoundedOperationAdmission._for_test(tmp_path).admit(
            operation_id="JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001"
        )
    assert not (tmp_path / "execution").exists()


def test_fresh_process_resume_fixture_has_no_post_or_reserve(tmp_path):
    """The offline recovery handoff cannot grow into a second submit path."""
    from src.hybrid.bounded_operation_admission import BoundedOperationAdmission

    _fixture(tmp_path)
    plan = BoundedOperationAdmission._for_test(tmp_path).admit(
        operation_id="JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001"
    )
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    script = tmp_path / "resume.py"
    script.write_text(
        """import json, sys
from pathlib import Path
from src.hybrid.bounded_operation_admission import BoundedOperationAdmission
root = Path(sys.argv[1])
plan = json.loads((root / 'plan.json').read_text(encoding='utf-8'))
print(json.dumps(BoundedOperationAdmission._for_test(root).recovery_plan(plan, provider_request_id='TEST-REMOTE-1'), sort_keys=True))
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(script), str(tmp_path)],
        cwd=Path.cwd(),
        env={**os.environ, "PYTHONPATH": str(Path.cwd())},
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    assert json.loads(completed.stdout) == {
        "action": "GET_ONLY_RECONCILE_EXISTING_REQUEST",
        "operation_id": "JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001",
        "provider_request_id": "TEST-REMOTE-1",
        "post_count": 0,
        "reserve_count": 0,
    }


def test_live_factory_is_permanently_unqualified_and_cli_has_no_execute_flag(tmp_path, capsys):
    from src.hybrid.bounded_operation_admission import (
        BoundedAdmissionError,
        BoundedOperationAdmission,
        main,
    )

    _fixture(tmp_path, mode="LIVE")
    with pytest.raises(BoundedAdmissionError, match="LIVE"):
        BoundedOperationAdmission._for_test(tmp_path).admit(operation_id="JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001")
    with pytest.raises(SystemExit) as help_exit:
        main(["--help"])
    assert help_exit.value.code == 0
    assert "--execute" not in capsys.readouterr().out
