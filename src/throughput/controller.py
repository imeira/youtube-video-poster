"""Durable single-writer orchestration for explicitly deployed local adapters.

Configuration is operator authority, never supplied by a reviewer. Adapters consume
immutable input files and emit bound receipts; stdout and exit status are not QA.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

VALIDATOR_VERSION = "throughput-v2"
WINDOWS_REPLACE_ATTEMPTS = 5
WINDOWS_REPLACE_BACKOFF_SECONDS = 0.01
IS_WINDOWS = os.name == "nt"
REQUEST_KEYS = {
    "schema", "test_only", "episode_id", "request_id", "track", "frame",
    "revision", "source", "contract", "width", "height", "created_at",
    "expires_at", "authorization_path", "estimate", "predecessor",
    "verdict_path",
}
REQUEST_TRACKS = {"frames", "thumbnail", "final_video", "publication", "planning"}


def _now():
    return datetime.now(UTC).isoformat()


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _atomic_json(path, value):
    """Persist JSON with bounded recovery from a Windows reader sharing violation.

    Windows readers that omit delete sharing can transiently make ``os.replace``
    fail with Access Denied (5) or Sharing Violation (32).  We retain the same
    fully-written temp file and retry briefly; a persistent ACL/permission error
    is deliberately re-raised rather than being converted into a successful save.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(WINDOWS_REPLACE_ATTEMPTS):
            try:
                os.replace(name, path)
                break
            except PermissionError as error:
                # Do not mask a genuine denial: it is retried only within this
                # small bound, then the original exception reaches the caller.
                if (not IS_WINDOWS or getattr(error, "winerror", None) not in (5, 32)
                    or attempt + 1 == WINDOWS_REPLACE_ATTEMPTS):
                    raise
                time.sleep(WINDOWS_REPLACE_BACKOFF_SECONDS * (2 ** attempt))
    finally:
        Path(name).unlink(missing_ok=True)


def _time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.timestamp()


def _is_live_pid(pid: Any) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87  # Only INVALID_PARAMETER proves absent PID.
        try:
            code = wintypes.DWORD()
            return not kernel.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _pid_identity(pid: Any) -> str | None:
    """Return an OS creation marker, so a reused PID cannot own an old lease."""
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return None
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            created = wintypes.FILETIME()
            ignored = wintypes.FILETIME()
            if not kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(ignored),
                ctypes.byref(ignored), ctypes.byref(ignored)):
                return None
            return str((created.dwHighDateTime << 32) | created.dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    try:
        # Field 22 is Linux process start time. Split after ')' for names with spaces.
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
        return fields[19]
    except (IndexError, OSError):
        return None


def _lease_matches_process(lease: Any) -> bool:
    if not isinstance(lease, dict) or not _is_live_pid(lease.get("pid")):
        return False
    identity = lease.get("pid_identity")
    return isinstance(identity, str) and bool(identity) and identity == _pid_identity(lease["pid"])


def _active_operation_may_own_effects(lease: Any) -> bool:
    """Fence reclamation while a child may still be executing shared effects."""
    operation = lease.get("active_operation") if isinstance(lease, dict) else None
    if not isinstance(operation, dict):
        return False
    if operation.get("status") == "STARTING":
        # The controller may have died between process creation and persisting its
        # PID.  Only explicit operator reconciliation can prove no child exists.
        return True
    if operation.get("status") != "RUNNING":
        return False
    pid = operation.get("pid")
    if not _is_live_pid(pid):
        return False
    identity = operation.get("pid_identity")
    current = _pid_identity(pid)
    # Missing identity cannot safely disprove ownership; a mismatch does prove
    # PID reuse and permits recovery.
    return not isinstance(identity, str) or not current or identity == current


def _acquire_reclaim_guard(path: Path) -> int | None:
    """Acquire a crash-released OS lock; the guard file itself may persist."""
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
            os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        if IS_WINDOWS:
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except (OSError, BlockingIOError):
        os.close(fd)
        return None


def _release_reclaim_guard(fd: int) -> None:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        if IS_WINDOWS:
            import msvcrt
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


class Blocked(ValueError):
    def __init__(self, reason, status="BLOCKED_PREREQUISITE"):
        super().__init__(reason)
        self.status = status


class ThroughputController:
    def __init__(self, episode_root, *, config_path=None, test_mode=False, run_id=None):
        self.root = Path(episode_root).resolve()
        self.test_mode = test_mode
        self.run_id = run_id or uuid.uuid4().hex
        self.dir = self.root / "qa" / "throughput"
        self.queue_path = self.dir / "queue.json"
        self.config_path = Path(config_path) if config_path else None
        self.config = _read_json(self.config_path) if self.config_path else None
        self.config_hash = _sha256(self.config_path) if self.config else None
        lock_name = (self.config or {}).get("lock_name", ".ep8_episode_writer.lock" if self.root.name.startswith("EP8_") else ".episode_writer.lock")
        if Path(lock_name).name != lock_name or not lock_name.startswith("."):
            raise ValueError("lock must be an episode-root filename")
        self.lease_path = self.root / lock_name

    def validate_config(self):
        cfg = self.config
        if self.config_path and self.config_hash != _sha256(self.config_path):
            raise Blocked("deployment changed during worker lifetime")
        if (not cfg or cfg.get("schema") != "throughput-deployment/1"
            or Path(cfg["episode_root"]).resolve() != self.root
            or type(cfg.get("test_only")) is not bool or cfg["test_only"] != self.test_mode):
            raise Blocked("invalid deployment scope or TEST/live mode")
        if self.root.name.startswith("EP8_") and cfg["lock_name"] != ".ep8_episode_writer.lock":
            raise Blocked("EP8 requires the legacy episode lock")
        if self.test_mode:
            state = _read_json(self.root / "state.json")
            costs = _read_json(self.root / "costs.json")
            if (not self.root.name.startswith("TEST_") or not state or not costs
                or state.get("test_only") is not True or costs.get("test_only") is not True):
                raise Blocked("TEST isolation requires a TEST_* root and marked state/cost authority")
        for key in ("command_timeout", "review_timeout", "max_review_retries", "max_remediations"):
            value = cfg.get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise Blocked("invalid deployment numeric field: " + key)
        commands = cfg.get("commands", {})
        required = {"action", "internal_qa", "review", "transport", "promote", "successor", "remediation", "planning", "reconcile"}
        if set(commands) != required or not isinstance(cfg.get("requests"), list):
            raise Blocked("incomplete fixed adapter allowlist")
        for spec in commands.values():
            path = Path(spec["path"])
            if (not path.is_absolute() or path.suffix != ".py" or not path.is_file()
                or _sha256(path) != spec.get("sha256")
                or type(spec.get("test_only")) is not bool or spec["test_only"] != self.test_mode
                or type(spec.get("idempotent")) is not bool
                or spec.get("start_gate_protocol") is not True):
                raise Blocked("invalid allowlisted script path/hash/mode")
            if not self.test_mode and ("tests" in path.parts or b"TEST-only" in path.read_bytes()):
                raise Blocked("TEST adapter cannot execute live")
        return {"status": "VALID", "config_sha256": _sha256(self.config_path), "test_only": self.test_mode}

    def _lease(self) -> dict[str, Any] | None:
        return _read_json(self.lease_path)

    def _acquire(self) -> bool:
        # Serialize stale-owner reclamation too; two reclaimers must never unlink a new owner.
        guard = self.lease_path.with_suffix(self.lease_path.suffix + ".reclaim")
        guard_fd = _acquire_reclaim_guard(guard)
        if guard_fd is None:
            return False
        try:
            if self.lease_path.exists():
                old = self._lease()
                if not old or type(old.get("pid")) is not int or old["pid"] <= 0:
                    return False
                if _is_live_pid(old["pid"]):
                    # An identity mismatch proves PID reuse. A legacy/no-marker
                    # live owner stays protected because it cannot be identified safely.
                    if not isinstance(old.get("pid_identity"), str) or not _pid_identity(old["pid"]) or _lease_matches_process(old):
                        return False
                if _active_operation_may_own_effects(old):
                    return False
                self.lease_path.unlink()
            identity = _pid_identity(os.getpid())
            if not identity:
                return False
            try:
                fd = os.open(self.lease_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                return False
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"run_id": self.run_id, "pid": os.getpid(), "pid_identity": identity, "started_at": _now()}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            return True
        finally:
            _release_reclaim_guard(guard_fd)

    def _release(self) -> None:
        lease = self._lease()
        if (lease and lease.get("run_id") == self.run_id
            and not _active_operation_may_own_effects(lease)):
            self.lease_path.unlink(missing_ok=True)

    def _set_active_operation(self, operation: dict[str, Any] | None) -> None:
        lease = self._lease()
        if not lease or lease.get("run_id") != self.run_id or not _lease_matches_process(lease):
            raise Blocked("writer ownership lost while binding adapter process")
        if operation is None:
            lease.pop("active_operation", None)
        else:
            lease["active_operation"] = operation
        _atomic_json(self.lease_path, lease)


    def _queue(self):
        if not self.queue_path.exists():
            return {"schema": "throughput-queue/1", "jobs": [], "events": []}
        queue = _read_json(self.queue_path)
        if not queue or not isinstance(queue.get("jobs"), list):
            raise Blocked("corrupt queue; restore audit, never silently reset")
        return queue

    def _save(self):
        _atomic_json(self.queue_path, self.queue)

    def _binding(self, value):
        if (not isinstance(value, dict) or set(value) != {"path", "sha256"}
            or not isinstance(value["path"], str) or not isinstance(value["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", value["sha256"])):
            raise Blocked("invalid content binding")
        path = Path(value["path"]).resolve()
        if not path.is_relative_to(self.root) or _sha256(path) != value["sha256"]:
            raise Blocked("content binding mismatch")
        return path

    def validate_evidence(self, request):
        """Cache only immutable geometry evidence; rehash every binding on every use."""
        from PIL import Image
        if set(request) != REQUEST_KEYS:
            raise Blocked("request must use the closed throughput-request/1 schema")
        if request.get("track") not in REQUEST_TRACKS:
            raise Blocked("unsupported request track")
        for key in ("episode_id", "request_id", "track", "frame", "created_at",
                    "expires_at", "authorization_path", "verdict_path"):
            if not isinstance(request.get(key), str) or not request[key]:
                raise Blocked("invalid request string: " + key)
        for key in ("authorization_path", "verdict_path"):
            destination = Path(request[key]).resolve()
            if not destination.is_relative_to(self.root):
                raise Blocked(key + " must remain inside the episode root")
        if request.get("predecessor") is not None and (not isinstance(request["predecessor"], str) or not request["predecessor"]):
            raise Blocked("invalid predecessor")
        for key in ("revision", "width", "height"):
            if type(request.get(key)) is not int or request[key] <= 0:
                raise Blocked("invalid integer: " + key)
        estimate = request.get("estimate")
        if type(estimate) not in (int, float) or not math.isfinite(estimate) or estimate < 0:
            raise Blocked("invalid estimate")
        if request.get("schema") != "throughput-request/1" or request.get("test_only") is not self.test_mode:
            raise Blocked("invalid request schema/mode")
        source = self._binding(request["source"])
        contract_path = self._binding(request["contract"])
        contract = _read_json(contract_path)
        if not contract or any(type(contract.get(k)) is not int or contract[k] != request[k] for k in ("width", "height")):
            raise Blocked("effective contract geometry mismatch")
        key = _digest([request["source"]["sha256"], VALIDATOR_VERSION, request["contract"]["sha256"]])
        cache_path = self.dir / "cache" / (key + ".json")
        expected = {"cache_key": key, "validator_version": VALIDATOR_VERSION,
            "content_sha256": request["source"]["sha256"], "contract_sha256": request["contract"]["sha256"],
            "width": request["width"], "height": request["height"]}
        cached = _read_json(cache_path)
        if cached == expected:
            return dict(expected, cache_hit=True)
        with Image.open(source) as image:
            image.load()
            if image.size != (request["width"], request["height"]):
                raise Blocked("decoded geometry mismatch")
        _atomic_json(cache_path, expected)
        return dict(expected, cache_hit=False)

    def _artifact_key(self, request):
        """Identify one immutable artifact revision independently of its file bytes."""
        return _digest([
            request.get("episode_id"), request.get("track"),
            request.get("frame"), request.get("revision"),
        ])

    def _ingest(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise Blocked("request path must remain inside the episode root")
        request = _read_json(path)
        if not request or request.get("episode_id") != self.root.name:
            raise Blocked("unbound request")
        key = _sha256(path)
        artifact_key = self._artifact_key(request)
        for queued in self.queue["jobs"]:
            queued_artifact_key = queued.get("artifact_key") or self._artifact_key(queued["request"])
            if queued_artifact_key == artifact_key:
                if queued["id"] == key:
                    return
                raise Blocked("conflicting duplicate artifact revision")
        self.validate_evidence(request)
        self.queue["jobs"].append({"id": key, "artifact_key": artifact_key,
            "request_path": str(path), "request": request,
            "stage": "preflight_wait" if Path(request["verdict_path"]).exists() else "preflight_launch",
            "status": "READY_ACTION_NOT_CONSUMED", "created_at": _now(), "stage_at": _now(),
            "attempt": 0, "review_attempt": 0, "receipts": {}})
        self._save()

    def _event(self, job, stage, timing, operation_id):
        if any(e.get("operation_id") == operation_id for e in self.queue["events"]):
            return
        self.queue["events"].append({"job": job["id"], "frame": job["request"]["frame"],
            "track": job["request"]["track"], "stage": stage, "operation_id": operation_id, **timing})

    def _fresh_authority(self, job, *, authorize=False):
        from src.state.machine import EpisodeState, EpisodeStateStore
        req = job["request"]
        lease = self._lease()
        if not lease or lease.get("run_id") != self.run_id or not _lease_matches_process(lease):
            raise Blocked("writer ownership lost")
        if _sha256(job["request_path"]) != job["id"]:
            raise Blocked("request changed after enqueue")
        if _read_json(job["request_path"]) != req:
            raise Blocked("queued request snapshot tampered")
        for binding in job["receipts"].values():
            self._binding(binding)
        for phase in ("preflight", "qa"):
            if phase in job:
                self._binding(job[phase])
        if "candidate" in job:
            self._binding(job["candidate"])
        state = _read_json(self.root / "state.json")
        if not state or state.get("episode_id") != self.root.name:
            raise Blocked("state episode mismatch")
        store = EpisodeStateStore.from_dict(state)
        if authorize and store.current_state not in (EpisodeState.GENERATING_IMAGES, EpisodeState.VISUAL_QA):
            raise Blocked("episode state does not permit frame execution")
        if not authorize:
            return
        if req["track"] == "frames" and req.get("predecessor") and not self._dependency_ready(job):
            raise Blocked("predecessor not promoted")
        costs = _read_json(self.root / "costs.json")
        for key in ("spent", "projected", "hard_limit"):
            value = (costs or {}).get(key)
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise Blocked("invalid fresh ledger: " + key)
        cap = costs["hard_limit"]
        if "approved_maximum" in costs:
            maximum = costs["approved_maximum"]
            if type(maximum) not in (int, float) or not math.isfinite(maximum) or maximum < 0:
                raise Blocked("invalid approved cap")
            cap = min(cap, maximum)
        if sum(Decimal(str(v)) for v in (costs["spent"], costs["projected"], req["estimate"])) > Decimal(str(cap)):
            raise Blocked("spent + projected + estimate exceeds cap", "HUMAN_GATE")
        auth_path = Path(req["authorization_path"]).resolve()
        auth = _read_json(auth_path) if auth_path.is_relative_to(self.root) else None
        if (not auth or auth.get("schema") != "throughput-authorization/1" or auth.get("allowed") is not True
            or auth.get("episode_id") != self.root.name or auth.get("request_sha256") != job["id"]
            or auth.get("test_only") is not self.test_mode
            or min(_time(auth["expires_at"]), _time(req["expires_at"])) <= time.time()):
            raise Blocked("authorization missing, revoked, unbound or expired")

    def _dependency_ready(self, job):
        predecessor = job["request"].get("predecessor")
        if not predecessor:
            return True
        for prior in self.queue["jobs"]:
            if prior["request"]["request_id"] == predecessor and prior["status"] == "DONE" and "promote" in prior["receipts"]:
                self._binding(prior["receipts"]["promote"])
                self._binding(prior["candidate"])
                return True
        return False

    def _advance(self, job, stage):
        job.update(stage=stage, stage_at=_now(), status="READY_ACTION_NOT_CONSUMED", reason=None)
        self._save()

    def _review_binding(self, job, phase):
        req = job["request"]
        return {"request_id": req["request_id"], "request_sha256": job["id"], "phase": phase,
            "source_sha256": job.get("candidate", req["source"])["sha256"] if phase == "qa" else req["source"]["sha256"],
            "contract_sha256": req["contract"]["sha256"]}

    def _verdict_path(self, job, phase):
        path = Path(job["request"]["verdict_path"]) if phase == "preflight" else self.dir / "operations" / job["id"][:24] / "qa-verdict.json"
        return path.with_suffix(f".retry{job['review_attempt']}.json") if job["review_attempt"] else path

    def _verdict(self, job, phase):
        path = self._verdict_path(job, phase)
        data = _read_json(path)
        expected = self._review_binding(job, phase)
        if (not data or set(data) != set(expected) | {"decision", "timestamp", "test_only", "reviewer_origin"}
            or any(data.get(k) != v for k, v in expected.items()) or data.get("decision") not in ("PASS", "FAIL")
            or data.get("test_only") is not self.test_mode):
            raise Blocked("malformed_or_unbound_verdict", "WAITING_REVIEW")
        origin = data.get("reviewer_origin", {})
        route = self.config["commands"]["review"]
        if (not isinstance(origin, dict)
            or set(origin) != {"schema", "reviewer_id", "execution_context", "result_path", "result_sha256"}
            or any(not isinstance(v, str) or not v for v in origin.values())
            or origin.get("schema") != "reviewer-origin/1"
            or origin.get("reviewer_id") != route["reviewer_id"]
            or origin.get("execution_context") != route["execution_context"]):
            raise Blocked("invalid reviewer origin", "WAITING_REVIEW")
        proof = self._binding({"path": origin["result_path"], "sha256": origin["result_sha256"]})
        if _read_json(proof) != {k: v for k, v in data.items() if k != "reviewer_origin"}:
            raise Blocked("review proof mismatch", "WAITING_REVIEW")
        earliest = job["request"]["created_at"] if phase == "preflight" else job["internal_finished_at"]
        if not _time(earliest) <= _time(data["timestamp"]) <= time.time():
            raise Blocked("stale verdict", "WAITING_REVIEW")
        return data, {"path": str(path), "sha256": _sha256(path)}

    def _command(self, job, stage, **extra):
        spec = self.config["commands"][stage]
        script = Path(spec["path"]).resolve()
        if _sha256(script) != spec["sha256"]:
            raise Blocked("command hash mismatch")
        op = _digest([job["id"], stage, job["attempt"], job["review_attempt"], extra])
        directory = self.dir / "operations" / op[:32]
        input_path, receipt_path = directory / "input.json", directory / "receipt.json"
        timing_path = directory / "timing.json"
        intent_path = directory / "intent.json"
        start_claim_path = directory / "start-claim.json"
        start_gate_path = directory / "start-gate.json"
        payload = {"schema": "throughput-input/1", "test_only": self.test_mode,
            "episode_root": str(self.root), "operation_id": op, "stage": stage,
            "controller_lease_path": str(self.lease_path),
            "start_claim_path": str(start_claim_path),
            "start_gate_path": str(start_gate_path),
            "request": job["request"], **extra}
        if not input_path.exists():
            _atomic_json(input_path, payload)
        elif _read_json(input_path) != payload:
            raise Blocked("operation input tampered")
        started, clock = _now(), time.monotonic()
        if not receipt_path.exists():
            if intent_path.exists() and (not spec["idempotent"] or (stage == "action" and job["request"]["estimate"] > 0)):
                raise Blocked("ambiguous consumed submission: reconcile receipt/opaque ID, never resubmit")
            _atomic_json(intent_path, {"operation_id": op, "input_sha256": _sha256(input_path),
                "script_sha256": spec["sha256"], "started_at": started, "status": "CONSUMED_BEFORE_EXECUTION"})
            starting = {"status": "STARTING", "operation_id": op, "stage": stage,
                        "started_at": started}
            self._set_active_operation(starting)
            process = None
            child_pid = None
            try:
                start_claim_path.unlink(missing_ok=True)
                start_gate_path.unlink(missing_ok=True)
                process = subprocess.Popen(
                    [sys.executable, str(script), "--input", str(input_path), "--receipt", str(receipt_path)],
                    shell=False, cwd=self.root, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                )
                claim_deadline = time.monotonic() + min(5, self.config["command_timeout"])
                claim = None
                while time.monotonic() < claim_deadline:
                    claim = _read_json(start_claim_path)
                    if claim is not None or process.poll() is not None:
                        break
                    time.sleep(0.005)
                if (not claim or set(claim) != {"schema", "operation_id", "pid", "claimed_at"}
                    or claim.get("schema") != "throughput-start-claim/1"
                    or claim.get("operation_id") != op or type(claim.get("pid")) is not int
                    or not _is_live_pid(claim["pid"])):
                    raise Blocked("adapter failed to claim the start gate")
                child_pid = claim["pid"]
                identity = _pid_identity(child_pid)
                if not identity:
                    process.kill()
                    process.wait()
                    raise Blocked("adapter process identity unavailable")
                self._set_active_operation(dict(starting, status="RUNNING", pid=child_pid,
                                                pid_identity=identity))
                _atomic_json(start_gate_path, {"schema": "throughput-start-gate/1",
                    "operation_id": op, "child_pid": child_pid,
                    "child_pid_identity": identity, "opened_at": _now()})
                try:
                    _, stderr = process.communicate(timeout=self.config["command_timeout"])
                    returncode = process.returncode
                except subprocess.TimeoutExpired as error:
                    process.kill()
                    process.communicate()
                    status = "BLOCKED_PREREQUISITE" if (stage == "action" and job["request"]["estimate"] > 0) else "RETRY_SCHEDULED"
                    raise Blocked("adapter timeout; outcome must be reconciled before resubmission", status) from error
                if returncode:
                    detail = (stderr or "").strip()[-1000:] if self.test_mode else ""
                    raise Blocked("adapter failed" + (": " + detail if detail else ""))
            finally:
                # If this controller is terminated, this finally block does not run:
                # STARTING/RUNNING remains durable and stale-owner recovery fences
                # the potentially live child.
                if (process is None
                    or (child_pid is None and process.poll() is not None)
                    or (child_pid is not None and not _is_live_pid(child_pid))):
                    self._set_active_operation(None)
            _atomic_json(timing_path, {"started_at": started, "finished_at": _now(),
                "duration_seconds": time.monotonic() - clock, "clock": "monotonic"})
        receipt = _read_json(receipt_path)
        intent = _read_json(intent_path)
        if (not receipt or set(receipt) != {"schema", "test_only", "operation_id", "input_sha256", "stage", "started_at", "finished_at", "result"}
            or receipt.get("schema") != "throughput-command/1" or receipt.get("operation_id") != op
            or receipt.get("test_only") is not self.test_mode or not isinstance(receipt.get("result"), dict)
            or receipt.get("input_sha256") != _sha256(input_path) or receipt.get("stage") != stage
            or not intent or intent.get("operation_id") != op or intent.get("script_sha256") != spec["sha256"]
            or not _time(intent["started_at"]) <= _time(receipt["started_at"]) <= _time(receipt["finished_at"]) <= time.time()):
            raise Blocked("invalid command receipt")
        job["receipts"][stage] = {"path": str(receipt_path), "sha256": _sha256(receipt_path)}
        timing = _read_json(timing_path) or {"started_at": receipt["started_at"], "finished_at": receipt["finished_at"],
            "duration_seconds": _time(receipt["finished_at"]) - _time(receipt["started_at"]), "clock": "receipt-wall-recovered"}
        self._event(job, stage, timing, op)
        return receipt

    def _step(self, job):
        req, stage = job["request"], job["stage"]
        self.validate_evidence(req)
        self._fresh_authority(job, authorize=stage in ("action", "promote"))
        if req["track"] in ("final_video", "publication"):
            raise Blocked("explicit final human approval required: " + req["track"], "HUMAN_GATE")
        if req["track"] == "planning":
            before = [_sha256(self.root / p) for p in ("state.json", "costs.json")]
            self._command(job, "planning")
            if before != [_sha256(self.root / p) for p in ("state.json", "costs.json")]:
                raise Blocked("read-only planner modified shared authority")
            job["status"] = "DONE"
            self._save()
            return
        if stage in ("preflight_launch", "qa_launch"):
            phase = "qa" if stage == "qa_launch" else "preflight"
            transport = self._command(job, "transport")
            if transport["result"].get("available") is not True:
                raise Blocked("review transport unavailable", "RETRY_SCHEDULED")
            self._command(job, "review", review_binding=self._review_binding(job, phase),
                verdict_path=str(self._verdict_path(job, phase)))
            self._advance(job, phase + "_wait")
        elif stage in ("preflight_wait", "qa_wait"):
            phase = "qa" if stage == "qa_wait" else "preflight"
            try:
                verdict, proof = self._verdict(job, phase)
            except (ValueError, TypeError, KeyError, OSError) as error:
                if time.time() - _time(job["stage_at"]) >= self.config["review_timeout"]:
                    if job["review_attempt"] >= self.config["max_review_retries"]:
                        raise Blocked("review timeout; bounded routes exhausted") from error
                    job["review_attempt"] += 1
                    self._advance(job, phase + "_launch")
                    raise Blocked("fresh independent review scheduled", "RETRY_SCHEDULED") from error
                raise Blocked(str(error), "WAITING_REVIEW") from error
            job[phase] = proof
            if verdict["decision"] == "FAIL":
                self._advance(job, "remediation")
            else:
                self._advance(job, "action" if phase == "preflight" else "promote")
        elif stage == "action":
            receipt = self._command(job, "action")
            if receipt["result"].get("status") == "UNKNOWN_RECONCILIATION_REQUIRED":
                opaque = receipt["result"].get("request_id")
                if not isinstance(opaque, str) or not opaque:
                    raise Blocked("unknown submission without opaque ID; never resubmit")
                job["opaque_request_id"] = opaque
                self._advance(job, "reconcile")
                return
            job["candidate"] = receipt["result"]["candidate"]
            self._binding(job["candidate"])
            self._advance(job, "internal_qa")
        elif stage == "reconcile":
            receipt = self._command(job, "reconcile", opaque_request_id=job["opaque_request_id"])
            result = receipt["result"]
            if result.get("request_id") != job["opaque_request_id"]:
                raise Blocked("reconciliation ID mismatch")
            if result.get("status") != "COMPLETED":
                job["attempt"] += 1
                self._save()
                raise Blocked("opaque submission pending reconciliation", "RETRY_SCHEDULED")
            job["candidate"] = result["candidate"]
            self._binding(job["candidate"])
            self._advance(job, "internal_qa")
        elif stage == "internal_qa":
            receipt = self._command(job, "internal_qa", candidate=job["candidate"])
            result = receipt["result"]
            if (set(result) != {"decision", "source_sha256"} or result["decision"] not in ("PASS", "FAIL")
                or result["source_sha256"] != job["candidate"]["sha256"]):
                raise Blocked("invalid internal QA")
            if result["decision"] == "FAIL":
                job["failure"] = job["receipts"]["internal_qa"]
                self._advance(job, "remediation")
                return
            job["internal_finished_at"] = receipt["finished_at"]
            self._advance(job, "qa_launch")
        elif stage == "promote":
            self._verdict(job, "qa")
            if req["track"] == "thumbnail":
                raise Blocked("explicit final thumbnail approval required", "HUMAN_GATE")
            receipt = self._command(job, "promote", candidate=job["candidate"], qa=job["qa"])
            manifest = _read_json(self._binding(receipt["result"]["manifest"]))
            if manifest.get("candidate") != job["candidate"] or manifest.get("qa") != job["qa"]:
                raise Blocked("unbound promotion")
            state_path = self.root / "state.json"
            state = _read_json(state_path)
            approved = state.setdefault("checkpoint", {}).setdefault("approved_assets", [])
            if req["frame"] not in approved:
                approved.append(req["frame"])
                state["checkpoint"]["last_completed_scene"] = req["frame"]
                state.setdefault("state_history", []).append({"state": state["current_state"],
                    "timestamp": _now(), "agent": "throughput", "note": "promoted " + req["request_id"]})
                _atomic_json(state_path, state)
            self._advance(job, "successor")
        elif stage in ("successor", "remediation"):
            if stage == "remediation":
                attempts = sum("remediation" in j["receipts"] for j in self.queue["jobs"]
                    if (j["request"]["track"], j["request"]["frame"]) == (req["track"], req["frame"]))
                if attempts >= self.config["max_remediations"] and "remediation" not in job["receipts"]:
                    raise Blocked("remediation limit exhausted", "HUMAN_GATE")
            receipt = self._command(job, stage, **({"candidate": job["candidate"]} if stage == "successor" else {"failure": job.get("failure", job.get("qa", job.get("preflight")))}))
            self._ingest(self._binding(receipt["result"]["request"]))
            job["status"] = "DONE"
            self._save()
        else:
            raise Blocked("unsupported stage")

    def run_once(self):
        try:
            self.validate_config()
        except (ValueError, KeyError, TypeError, OSError) as error:
            return {"status": "BLOCKED_PREREQUISITE", "reason": str(error)}
        if not self._acquire():
            return {"status": "BLOCKED_PREREQUISITE", "reason": "episode writer held"}
        try:
            self.queue = self._queue()
            if self.queue.get("deployment_sha256", self.config_hash) != self.config_hash:
                return {"status": "BLOCKED_PREREQUISITE", "reason": "deployment changed; explicit migration required"}
            self.queue["deployment_sha256"] = self.config_hash
            try:
                for path in self.config["requests"]:
                    self._ingest(path)
            except Blocked as error:
                return {"status": error.status, "reason": str(error)}
            processed = set()
            while True:
                for deferred in self.queue["jobs"]:
                    if deferred.get("reason") == "predecessor not promoted" and self._dependency_ready(deferred):
                        processed.discard(deferred["id"])
                job = next((j for j in self.queue["jobs"] if j["id"] not in processed and j["status"] not in ("DONE", "HUMAN_GATE")), None)
                if job is None:
                    break
                processed.add(job["id"])
                while job["status"] != "DONE":
                    try:
                        self._step(job)
                    except Blocked as error:
                        job.update(status=error.status, reason=str(error))
                        self._save()
                        break
            return self.status()
        finally:
            self._release()

    consume_once = run_once

    def run(self, *, poll_interval=1, max_seconds=None):
        if not math.isfinite(poll_interval) or poll_interval <= 0 or (max_seconds is not None and (not math.isfinite(max_seconds) or max_seconds <= 0)):
            raise ValueError("positive finite polling interval/duration required")
        started = time.monotonic()
        while True:
            result = self.run_once()
            if result.get("reason") == "episode writer held" or not self.config:
                return result
            _atomic_json(self.dir / "heartbeat.json", {"pid": os.getpid(), "run_id": self.run_id,
                "updated_at": _now(), "worker_kind": "queue-consumer"})
            if max_seconds is not None and time.monotonic() - started >= max_seconds:
                return result
            time.sleep(min(poll_interval, 30))

    def report(self):
        queue = self._queue()
        totals = dict.fromkeys(("generation", "review", "remediation", "idle", "promotion"), 0.0)
        categories = {"action": "generation", "review": "review", "internal_qa": "review", "transport": "review",
            "remediation": "remediation", "promote": "promotion"}
        per_frame = {}
        for event in queue["events"]:
            category = categories.get(event["stage"], "idle")
            duration = event["duration_seconds"]
            totals[category] += duration
            frame = per_frame.setdefault(event["track"] + ":" + event["frame"], dict.fromkeys(totals, 0.0))
            frame[category] += duration
        return {**self.status(), "durations_seconds": totals, "per_frame_seconds": per_frame,
            "timing_sources": sorted({e.get("clock", "wall") for e in queue["events"]})}

    def status(self):
        queue = self._queue()
        lease = self._lease()
        pending = [j for j in queue["jobs"] if j["status"] != "DONE"]
        ready = [j for j in pending if j["status"] == "READY_ACTION_NOT_CONSUMED"]
        heartbeat = _read_json(self.dir / "heartbeat.json")
        return {"episode": self.root.name, "queue_jobs": len(queue["jobs"]),
            "status": pending[0]["status"] if pending else "IDLE",
            "jobs": [{"request_id": j["request"]["request_id"], "stage": j["stage"],
                "status": j["status"], "reason": j.get("reason")} for j in pending],
            "last_completed_action": queue["events"][-1]["stage"] if queue["events"] else None,
            "ready_jobs": len(ready),
            "oldest_ready_age_seconds": max((max(0, time.time() - _time(j["stage_at"])) for j in ready), default=0),
            "worker": {"live": bool(heartbeat and _is_live_pid(heartbeat.get("pid"))), "kind": "queue-consumer"},
            "lease": {"live": bool(lease and _is_live_pid(lease.get("pid"))), "owner": lease.get("run_id") if lease else None}}
