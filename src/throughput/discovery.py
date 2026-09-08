"""Read-only EP8 discovery plus an explicitly TEST-only clone bridge.

Discovery never turns legacy artifacts into execution authority.  The clone bridge
normalizes the real R027 review shape only inside a new TEST_* directory and runs
it through the ordinary controller with the repository's TEST fixture worker.
"""
from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .controller import Blocked, ThroughputController, _atomic_json, _read_json, _sha256

REQUEST = "R027_zero_cost_R025_lineage_source_crop_parent_independent_preflight_review_request_v1.json"
HANDOFF = "R027_parent_hermes_independent_preflight_transport_recovery_v2.json"
WAITER = "r027_verdict_waiter_v2.py"
PREFLIGHT_BUILDER = "r026_promote_r027_zero_cost_preflight_v1.py"
FAIL_RECOVERY = "r027_fail_recovery_acquire_lease_v1.py"
LIVE_CONTRACT = "ep8_r027_live_handler_contract_v1.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _binding(path: Path) -> dict[str, str]:
    return {"path": str(path.resolve()), "sha256": _sha256(path)}


def _bound_path(root: Path, value: dict, *, expected: Path | None = None) -> Path:
    if not isinstance(value, dict) or set(value) != {"path", "sha256"}:
        raise Blocked("invalid legacy content binding")
    target = Path(value["path"])
    target = target if target.is_absolute() else root / target
    target = target.resolve()
    if not target.is_relative_to(root) or not target.is_file() or _sha256(target) != value["sha256"]:
        raise Blocked("legacy content binding mismatch")
    if expected is not None and target != expected.resolve():
        raise Blocked("legacy content binding points at an unexpected artifact")
    return target


def _script(root: Path, name: str, *, required: bool) -> tuple[dict[str, str] | None, str | None]:
    path = root / "scripts" / name
    if not path.is_file():
        if required:
            raise Blocked("missing required legacy script: " + name)
        return None, None
    text = path.read_text(encoding="utf-8")
    if ".ep8_episode_writer.lock" not in text:
        raise Blocked("legacy episode writer lock not verified: " + name)
    return _binding(path), text


def _waiter_analysis(binding: dict[str, str], text: str) -> dict:
    terminal_sleep = "while True" in text and "VERDICT" in text
    return {
        "script": binding,
        "verdict_arrival_detected": "VERDICT" in text,
        "consumes_terminal_verdict": not terminal_sleep and "run_once" in text,
        "status": "STALLS_AFTER_VERDICT" if terminal_sleep else "UNVERIFIED_CONSUMER",
        "blockers": (["waiter enters an unconditional loop after verdict arrival and invokes no PASS/FAIL handler"]
                     if terminal_sleep else ["waiter has no verified durable controller handoff"]),
    }


def _handler_analysis(root: Path, fail_binding: dict[str, str] | None, fail_text: str | None) -> dict:
    contract_path = root / "qa" / "throughput" / LIVE_CONTRACT
    if contract_path.exists():
        # A sidecar may describe a future integration, but discovery deliberately
        # does not bless it merely by name.  Full controller command contracts and
        # production verdict evidence still have to be validated before activation.
        contract = _read_json(contract_path)
        contract_binding = _binding(contract_path)
    else:
        contract = None
        contract_binding = None

    fail_blockers: list[str] = []
    if fail_text is not None:
        if "os.O_EXCL" in fail_text and ".ep8_episode_writer.lock" in fail_text:
            fail_blockers.append("reacquires the episode lock")
        if "--input" not in fail_text or "--receipt" not in fail_text or "throughput-command/1" not in fail_text:
            fail_blockers.append("lacks the throughput input/receipt protocol")
        if "--dry-run" not in fail_text:
            fail_blockers.append("has no side-effect-free dry-run mode")
        if "replace(STATE" in fail_text or "replace(COSTS" in fail_text:
            fail_blockers.append("writes mutable episode authority directly")
        # The real recovery script only acquires/relabels a lease and announces a
        # next action; it creates no reviewed remediation plan/request.
        fail_blockers.append("does not materialize the promised remediation preflight")

    return {
        "contract": contract_binding,
        "contract_present": contract is not None,
        "PASS": {
            "status": "MISSING",
            "script": None,
            "blockers": ["no R027 PASS handler exists for crop, QA, promotion and successor handoff"],
        },
        "FAIL": {
            "status": "UNSAFE_INCOMPLETE_LEGACY_HANDLER" if fail_binding else "MISSING",
            "script": fail_binding,
            "blockers": fail_blockers if fail_binding else ["no R027 FAIL handler exists"],
        },
    }


def discover(episode_root):
    """Bind the current R027 legacy boundary without writing to the episode."""
    root = Path(episode_root).resolve()
    request_path = root / "qa" / REQUEST
    handoff_path = root / "qa" / HANDOFF
    request = _read_json(request_path)
    handoff = _read_json(handoff_path)
    if (not request or not handoff
        or any(value.get("episode_id") != root.name or value.get("frame_id") != "R027"
               for value in (request, handoff))
        or request.get("allowed_decisions") != ["PASS_PREFLIGHT_ONLY", "FAIL_PREFLIGHT"]
        or handoff.get("decision") not in ("PASS_PREFLIGHT_ONLY", "FAIL_PREFLIGHT")):
        raise Blocked("missing or unbound R027 request/verdict handoff")
    review_binding = handoff.get("bindings", {}).get("review_request")
    _bound_path(root, review_binding, expected=request_path)

    waiter_binding, waiter_text = _script(root, WAITER, required=True)
    builder_binding, _ = _script(root, PREFLIGHT_BUILDER, required=True)
    fail_binding, fail_text = _script(root, FAIL_RECOVERY, required=False)
    waiter = _waiter_analysis(waiter_binding, waiter_text)
    handlers = _handler_analysis(root, fail_binding, fail_text)
    selected = [waiter_binding, builder_binding]
    if fail_binding:
        selected.append(fail_binding)

    verdict_is_full = handoff.get("record_type") != "PARENT_PERSISTED_EXTRACT_OF_INDEPENDENT_FAIL_NOT_FULL_VERDICT"
    blockers = [
        "persisted R027 handoff is an extract, not the full reviewer verdict" if not verdict_is_full
        else "production reviewer origin/receipt contract is not validated",
        *waiter["blockers"],
        *handlers["PASS"]["blockers"],
        *handlers["FAIL"]["blockers"],
        "no production deployment contract was generated or activated",
    ]
    return {
        "schema": "throughput-discovery/2",
        "read_only": True,
        "episode_root": str(root),
        "request": _binding(request_path),
        "handoff": _binding(handoff_path),
        "decision": handoff["decision"],
        "verdict_evidence": {
            "full_reviewer_verdict": verdict_is_full,
            "record_type": handoff.get("record_type"),
            "production_authority": False,
        },
        "blocking_findings": handoff.get("blocking_findings", []),
        "lock_path": str(root / ".ep8_episode_writer.lock"),
        "selected_legacy_scripts": selected,
        "waiter": waiter,
        "handlers": handlers,
        "runnable": False,
        "status": "BLOCKED_PREREQUISITE",
        "blockers": blockers,
    }


def _clone_source(request: dict, source_root: Path) -> Path:
    bindings = request.get("bindings", {})
    path_value = bindings.get("R025_source_path")
    expected_hash = bindings.get("R025_source_sha256")
    if not isinstance(path_value, str) or not isinstance(expected_hash, str):
        raise Blocked("R027 request lacks the exact R025 source binding")
    path = Path(path_value)
    path = path if path.is_absolute() else source_root / path
    path = path.resolve()
    if not path.is_relative_to(source_root) or not path.is_file() or _sha256(path) != expected_hash:
        raise Blocked("R027 source binding mismatch")
    return path


def run_test_clone(episode_root, destination, *, fixture_worker):
    """Exercise the real legacy review shape in a new, unmistakable TEST clone.

    No source file is opened for writing.  The legacy scripts are discovered and
    hash-bound but never executed because they do not satisfy the controller
    command protocol.
    """
    source_root = Path(episode_root).resolve()
    clone_root = Path(destination).resolve()
    worker = Path(fixture_worker).resolve()
    if not clone_root.name.startswith("TEST_"):
        raise ValueError("test clone destination basename must start with TEST_")
    if clone_root.exists():
        raise ValueError("test clone destination must not already exist")
    if clone_root == source_root or clone_root.is_relative_to(source_root):
        raise ValueError("test clone destination must be outside the source episode")
    if not worker.is_file() or "tests" not in worker.parts or b"TEST-only" not in worker.read_bytes():
        raise ValueError("fixture worker must be the explicit TEST-only repository adapter")

    inventory = discover(source_root)
    request = _read_json(Path(inventory["request"]["path"]))
    handoff = _read_json(Path(inventory["handoff"]["path"]))
    source_image = _clone_source(request, source_root)
    state = _read_json(source_root / "state.json")
    costs = _read_json(source_root / "costs.json")
    if not state or not costs:
        raise Blocked("test clone requires readable state and costs snapshots")

    # All writes begin only after read-only source validation succeeds.
    test_dir = clone_root / "TEST_ONLY"
    test_dir.mkdir(parents=True)
    copied_source = test_dir / "R025-source.png"
    shutil.copyfile(source_image, copied_source)
    state = dict(state, episode_id=clone_root.name, test_only=True,
                 lease={"status": "TEST_CLONE_INACTIVE", "pid": None})
    costs = dict(costs, test_only=True)
    _atomic_json(clone_root / "state.json", state)
    _atomic_json(clone_root / "costs.json", costs)
    _atomic_json(test_dir / "source-inventory.json", {
        "schema": "ep8-r027-test-clone-source/1", "test_only": True,
        "source_episode": str(source_root), "source_discovery": inventory,
    })

    from PIL import Image
    with Image.open(copied_source) as image:
        image.load()
        width, height = image.size
    contract_path = test_dir / "contract.json"
    _atomic_json(contract_path, {
        "schema": "ep8-r027-test-clone-contract/1", "test_only": True,
        "width": width, "height": height,
        "source_decision": handoff["decision"],
        "blocking_findings": handoff.get("blocking_findings", []),
    })
    created_at = _now()
    authorization_path = test_dir / "authorization.json"
    verdict_path = test_dir / "normalized-verdict.json"
    normalized_request = {
        "schema": "throughput-request/1", "test_only": True,
        "episode_id": clone_root.name, "request_id": "R027-legacy-review-test-bridge-v1",
        "track": "frames", "frame": "R027", "revision": 1,
        "source": _binding(copied_source), "contract": _binding(contract_path),
        "width": width, "height": height, "created_at": created_at,
        "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "authorization_path": str(authorization_path), "estimate": 0,
        "predecessor": None, "verdict_path": str(verdict_path),
    }
    request_path = test_dir / "normalized-request.json"
    _atomic_json(request_path, normalized_request)
    request_hash = _sha256(request_path)
    normalized_decision = {"PASS_PREFLIGHT_ONLY": "PASS", "FAIL_PREFLIGHT": "FAIL"}[handoff["decision"]]
    proof = {
        "request_id": normalized_request["request_id"], "request_sha256": request_hash,
        "phase": "preflight", "source_sha256": normalized_request["source"]["sha256"],
        "contract_sha256": normalized_request["contract"]["sha256"],
        "decision": normalized_decision, "timestamp": _now(), "test_only": True,
    }
    proof_path = test_dir / "normalized-verdict-proof.json"
    _atomic_json(proof_path, proof)
    _atomic_json(verdict_path, dict(proof, reviewer_origin={
        "schema": "reviewer-origin/1", "reviewer_id": "ep8-legacy-handoff-test-bridge",
        "execution_context": "isolated-test-clone", "result_path": str(proof_path),
        "result_sha256": _sha256(proof_path),
    }))
    _atomic_json(authorization_path, {
        "schema": "throughput-authorization/1", "episode_id": clone_root.name,
        "request_sha256": request_hash, "allowed": True,
        "expires_at": normalized_request["expires_at"], "test_only": True,
    })
    command = {
        "path": str(worker), "sha256": _sha256(worker), "test_only": True,
        "idempotent": True, "start_gate_protocol": True,
        "reviewer_id": "ep8-legacy-handoff-test-bridge",
        "execution_context": "isolated-test-clone",
    }
    deployment_path = test_dir / "deployment.json"
    _atomic_json(deployment_path, {
        "schema": "throughput-deployment/1", "episode_root": str(clone_root),
        "test_only": True, "lock_name": ".episode_writer.lock",
        "requests": [str(request_path)],
        "commands": {stage: dict(command) for stage in (
            "action", "internal_qa", "review", "transport", "promote",
            "successor", "remediation", "planning", "reconcile")},
        "command_timeout": 10, "review_timeout": 60,
        "max_review_retries": 1, "max_remediations": 2,
    })
    controller = ThroughputController(
        clone_root, config_path=deployment_path, test_mode=True,
        run_id="TEST-EP8-R027-CLONE",
    )
    run_result = controller.run_once()
    # run_once computes status while it still owns the short critical-section
    # lease.  Re-read after return so the delivered TEST report reflects the
    # externally visible, released state rather than that internal instant.
    result = controller.status()
    result["status"] = run_result["status"]
    if "reason" in run_result:
        result["reason"] = run_result["reason"]
    return {
        **result, "test_only": True, "clone_root": str(clone_root),
        "source_decision": handoff["decision"],
        "normalized_decision": normalized_decision,
        "production_activated": False,
    }
