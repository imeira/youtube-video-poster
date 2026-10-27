"""Immutable, offline pre-production request packets.

This is deliberately not a compiled episode.  A literal user prompt is evidence of
intent only; it cannot establish scripture, a production plan, asset availability,
or authority to spend/publish.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import asdict
from hashlib import sha256
from pathlib import Path
from typing import Any

from src.agents.script import ScriptAgent
from src.agents.script_qa import ScriptQAAgent
from src.agents.storyboard import StoryboardAgent
from src.hybrid.autonomous import _atomic_json, _digest_json
from src.hybrid.locks import file_lock, reject_reparse_path

DEPENDENCIES = (
    "SOURCE_REQUIRED",
    "PLANNING_REQUIRED",
    "ASSETS_REQUIRED",
    "AUTHORIZATION_REQUIRED",
)
_DOMAIN = b"offline-preproduction-request-v1\0"


def _request_id(prompt: bytes) -> str:
    return sha256(_DOMAIN + prompt).hexdigest()


def _root(workspace: Path, request_id: str) -> Path:
    return Path(workspace).absolute() / "PREPRODUCTION" / request_id


def _guarded_bytes(path: Path) -> bytes:
    """Read only after lexical reparse and ancestor rejection."""
    return reject_reparse_path(path).read_bytes()


def _validate_id(request_id: str) -> None:
    if len(request_id) != 64 or any(char not in "0123456789abcdef" for char in request_id):
        raise ValueError("request-id must be lowercase sha256")


def _public(root: Path, document: dict[str, Any]) -> dict[str, Any]:
    result = dict(document)
    result["prompt_path"] = str(root / "prompt.txt")
    return result


def _validate(root: Path, document: dict[str, Any], request_id: str, *, reject_path=None) -> None:
    reject_path = reject_path or reject_reparse_path
    root = reject_path(root)
    prompt_path = reject_path(root / "prompt.txt")
    prompt = prompt_path.read_bytes()
    if document.get("kind") != "preproduction-request":
        raise ValueError("not a preproduction request")
    if document.get("mode") != "PREPRODUCTION" or document.get("compiled") is not False:
        raise ValueError("preproduction request cannot be compiled media")
    if document.get("request_id") != request_id or request_id != _request_id(prompt):
        raise ValueError("stored prompt bytes hash mismatch")
    if document.get("prompt_sha256") != sha256(prompt).hexdigest():
        raise ValueError("stored prompt bytes hash mismatch")
    allowed_dependencies = {*DEPENDENCIES, "TTS_REQUIRED"}
    if not isinstance(document.get("dependencies"), list) or any(item not in allowed_dependencies for item in document["dependencies"]):
        raise ValueError("preproduction dependencies were altered")
    if document.get("budget_usd") is not None:
        raise ValueError("unevidenced preproduction plan or budget is not allowed")
    selection = document.get("selection_proposal")
    if not isinstance(selection, dict) or selection != {
        "status": "PLANNING_REQUIRED",
        "executable": False,
        "selected": [],
    }:
        raise ValueError("preproduction selection must remain a non-executable proposal")
    executed = document.get("execution")
    if executed is None:
        if document.get("dependencies") != list(DEPENDENCIES) or document.get("plan_proposal") is not None:
            raise ValueError("unevidenced preproduction plan or budget is not allowed")
    elif not isinstance(executed, dict) or executed.get("paid_execution") != "BLOCKED":
        raise ValueError("preproduction execution must keep paid work blocked")
    binding = dict(document)
    binding.pop("package_sha256", None)
    if document.get("package_sha256") != _digest_json(binding):
        raise ValueError("preproduction package hash mismatch")


def start_preproduction(workspace: Path, prompt: bytes) -> dict[str, Any]:
    """Persist a prompt-bound request without fabricating production inputs."""
    if not isinstance(prompt, bytes) or not prompt:
        raise ValueError("literal prompt bytes are required")
    try:
        prompt.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("literal prompt must be UTF-8") from error
    request_id = _request_id(prompt)
    root = _root(workspace, request_id)
    if root.exists():
        raise ValueError("request namespace already exists; never overwrite")
    root.mkdir(parents=True)
    (root / "prompt.txt").write_bytes(prompt)
    document: dict[str, Any] = {
        "kind": "preproduction-request",
        "mode": "PREPRODUCTION",
        "compiled": False,
        "request_id": request_id,
        "prompt_sha256": sha256(prompt).hexdigest(),
        "status": "BLOCKED",
        "dependencies": list(DEPENDENCIES),
        "budget_usd": None,
        "plan_proposal": None,
        "selection_proposal": {
            "status": "PLANNING_REQUIRED",
            "executable": False,
            "selected": [],
        },
        "note": "Offline intent packet only; not a source-verified plan, compiled episode, asset package, QA result, authorization, or publication request.",
    }
    document["package_sha256"] = _digest_json(document)
    _atomic_json(root / "request.json", document)
    return _public(root, document)


def load_preproduction(workspace: Path, request_id: str) -> dict[str, Any]:
    """Read a persisted pre-production request; resume never advances blocked work."""
    _validate_id(request_id)
    root = reject_reparse_path(_root(workspace, request_id))
    request_path = reject_reparse_path(root / "request.json")
    document = json.loads(request_path.read_text(encoding="utf-8"))
    _validate(root, document, request_id, reject_path=reject_reparse_path)
    return _public(root, document)


def _read_local_json(path: Path, label: str) -> tuple[dict[str, Any], str, bytes]:
    """Read explicit local evidence without following a link or a network route."""
    path = reject_reparse_path(Path(path))
    if not path.is_file():
        raise ValueError(f"local {label} file is required and cannot be a link")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid local {label} JSON") from error
    if not isinstance(value, dict):
        raise TypeError(f"local {label} must be a JSON object")
    return value, sha256(raw).hexdigest(), raw


def _read_local_audio(path: Path) -> tuple[bytes, str, float, str]:
    """Accept a real, decodable local narration recording, never synthesized fallback bytes."""
    path = reject_reparse_path(Path(path))
    if not path.is_file():
        raise ValueError("local audio file is required and cannot be a link")
    raw = path.read_bytes()
    if not raw:
        raise ValueError("local audio is empty")
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            check=True, capture_output=True, text=True,
        )
        duration = float(json.loads(probe.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError, subprocess.CalledProcessError) as error:
        raise ValueError("local audio must be ffprobe-measurable") from error
    if duration <= 0:
        raise ValueError("local audio duration must be positive")
    # A deliberately silent file is not narration evidence.  This is local inspection only.
    volume = subprocess.run(
        ["ffmpeg", "-v", "info", "-nostdin", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        check=True, capture_output=True, text=True,
    )
    marker = "max_volume:"
    values = [line.split(marker, 1)[1].strip().split(" ", 1)[0] for line in volume.stderr.splitlines() if marker in line]
    if not values or float(values[-1]) <= -80.0:
        raise ValueError("local audio is silent and cannot be called narration")
    return raw, sha256(raw).hexdigest(), duration, path.suffix.lower()


def _without_ambient_story_plan():
    """Do not let defaults consume an unbound environment-selected plan."""
    class _Context:
        def __enter__(self):
            self.value = os.environ.pop("STUDIO_SOL_PLAN_PATH", None)

        def __exit__(self, *_):
            if self.value is not None:
                os.environ["STUDIO_SOL_PLAN_PATH"] = self.value
    return _Context()


def _validate_evidence(value: dict[str, Any], *, schema: str, mode: str, label: str) -> None:
    if value.get("schema") != schema:
        raise ValueError(f"{label} schema mismatch")
    if value.get("mode") != mode:
        raise ValueError(f"{mode} {label} requires matching mode evidence")
    provenance = value.get("provenance")
    if not isinstance(provenance, dict) or not all(str(provenance.get(key, "")).strip() for key in ("kind", "origin", "retrieved_at")):
        raise ValueError(f"{label} provenance is required")
    if mode == "TEST" and provenance.get("kind") != "TEST_LOCAL_SOURCE":
        raise ValueError(f"TEST {label} requires clearly labelled TEST source provenance")
    if mode == "PREPRODUCTION" and str(provenance.get("kind")).startswith("TEST"):
        raise ValueError("TEST source cannot enter non-TEST preproduction")


def _contains_unqualified_price_claim(value: object) -> bool:
    """Candidates without live qualification must carry no direct or nested quote."""
    forbidden = ("price", "cost", "usd", "quote")
    if isinstance(value, dict):
        return any(
            any(token in str(key).lower() for token in forbidden)
            or _contains_unqualified_price_claim(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return any(_contains_unqualified_price_claim(child) for child in value)
    return False


def _storyboard_report(scenes: list[dict], segments: list[dict], duration: float) -> dict[str, Any]:
    if len(scenes) != len(segments) or not scenes:
        raise ValueError("timeline storyboard must cover every script segment")
    previous = 0.0
    for scene, segment in zip(scenes, segments):
        if scene.get("narration") != segment.get("narration"):
            raise ValueError("storyboard narration differs from QA-passed script")
        start, end = float(scene["start"]), float(scene["end"])
        if start != previous or end <= start:
            raise ValueError("storyboard timeline must be contiguous")
        previous = end
    if previous != duration:
        raise ValueError("storyboard must end at evidenced audio duration")
    report = {"status": "PASS", "scene_count": len(scenes), "duration_seconds": duration,
              "script_sha256": _digest_json(segments)}
    report["report_sha256"] = _digest_json(report)
    return report


def _verify_completed_stages(revision: Path, stages: dict[str, Any], source_sha: str, *, reject_path=None) -> None:
    """A resumed revision is immutable: never silently re-use changed source/script bytes."""
    reject_path = reject_path or reject_reparse_path
    revision = reject_path(revision)
    expected = {
        "research": revision / "research" / "sources.json",
        "plan": revision / "plan" / "editorial-plan.json",
        "script": revision / "script" / "script.json",
        "script_qa": revision / "qa" / "script-qa.json",
        "storyboard": revision / "storyboard" / "scenes.json",
    }
    storyboard = stages.get("storyboard", {})
    if storyboard.get("qa_sha256"):
        expected["storyboard_qa"] = revision / "qa" / "storyboard.json"
    source_copy = reject_path(revision / "inputs" / "source.json")
    if source_copy.exists() and sha256(source_copy.read_bytes()).hexdigest() != source_sha:
        raise ValueError("stored source bytes differ from active source revision")
    for name, path in expected.items():
        path = reject_path(path)
        stage = storyboard if name == "storyboard_qa" else stages.get(name, {})
        expected_sha = stage.get("qa_sha256") if name == "storyboard_qa" else stage.get("sha256")
        if stage and expected_sha and (
            not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected_sha
        ):
            raise ValueError(f"{name} artifact was altered; immutable revision is blocked")


def _verified_completed_storyboard(revision: Path, stages: dict[str, Any], *, reject_path=None) -> bool:
    """Return true only when a resumable storyboard still binds every local input."""
    reject_path = reject_path or reject_reparse_path
    revision = reject_path(revision)
    stage = stages.get("storyboard", {})
    if stage.get("status") != "PASS":
        return False
    required = {
        revision / "inputs" / "timeline.json": stage.get("timeline_sha256"),
        revision / "qa" / "storyboard.json": stage.get("qa_sha256"),
        revision / "storyboard" / "scenes.json": stage.get("sha256"),
    }
    audio_sha = stage.get("audio_sha256")
    inputs = reject_path(revision / "inputs")
    audio_files = [reject_path(path) for path in inputs.glob("audio.*")]
    if len(audio_files) != 1 or not isinstance(audio_sha, str):
        return False
    required[audio_files[0]] = audio_sha
    for path, expected in required.items():
        path = reject_path(path)
        if not isinstance(expected, str) or not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected:
            return False
    return True


def _reject_tree(root: Path, reject_path) -> None:
    """Reject every descendant before an existing packet is parsed or reused."""
    root = reject_path(root)
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except NotADirectoryError:
            continue
        for entry in entries:
            child = reject_path(Path(entry.path))
            if stat.S_ISDIR(os.lstat(child).st_mode):
                pending.append(child)


def _load_guarded_manifest(path: Path, reject_path):
    """Validate every implicit Manifest.load read before invoking that API."""
    from src.hybrid.artifacts import Manifest

    path = reject_path(path)
    document = json.loads(path.read_text(encoding="utf-8"))
    raw = document.get("manifest", {})
    assets = [*raw.get("assets", []), raw.get("sheet", {})] if isinstance(raw, dict) else []
    if not assets or not all(isinstance(item, dict) and item.get("path") for item in assets):
        raise ValueError("local TEST manifest assets are required")
    for item in assets:
        asset_path = reject_path(Path(item["path"]))
        reject_path(asset_path.with_suffix(".binding.json")) if item is raw.get("sheet") else None
    return Manifest.load(path), sha256(path.read_bytes()).hexdigest()


_DIRECTOR_IMPORT_OPERATIONS = {
    "provider_dispatch": "BLOCKED", "render": "BLOCKED", "telegram": "BLOCKED", "publication": "BLOCKED",
}
_DIRECTOR_IMPORT_AUTHORITY = {
    "qualification_status": "NOT_QUALIFIED",
    "reasons": ["TTS_AUTHENTICATED_RECEIPT_REQUIRED", "TRUST_ROOT_AUTHENTICATION_REQUIRED"],
    "operation_status": "BLOCKED",
}


def _fsync_directory(path: Path) -> None:
    """Durably flush a directory where the platform exposes that primitive.

    Windows does not expose POSIX directory ``fsync`` through Python's ``os``;
    file data is flushed there, but power-loss durability of a directory rename is
    filesystem/volume dependent and is never represented as a stronger guarantee.
    """
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _durable_write_bytes(path: Path, value: bytes) -> None:
    """Write immutable packet bytes and flush the file before publication."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def _durable_json(path: Path, value: dict[str, Any]) -> bytes:
    raw = json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8")
    _durable_write_bytes(path, raw)
    return raw


def _packet_files(root: Path) -> set[str]:
    files: set[str] = set()
    for current, _directories, names in os.walk(root):
        current_path = reject_reparse_path(Path(current))
        for name in names:
            path = reject_reparse_path(current_path / name)
            if not path.is_file():
                raise ValueError("materialized packet contains a non-regular file")
            files.add(path.relative_to(root).as_posix())
    return files


def _validate_packet_path(value: object) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise ValueError("materialized packet index path is invalid")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or str(path.as_posix()) != value:
        raise ValueError("materialized packet index path is invalid")
    return value


def verify_real_preproduction_import(packet_path: Path, request_id: str) -> dict[str, Any]:
    """Verify only materialized bytes; source paths are never opened on replay."""
    _validate_id(request_id)
    packet = reject_reparse_path(Path(packet_path))
    if not packet.is_dir():
        raise ValueError("materialized packet is missing")
    _reject_tree(packet, reject_reparse_path)
    receipt_path = reject_reparse_path(packet / "receipt.json")
    index_path = reject_reparse_path(packet / "index.json")
    if not receipt_path.is_file() or not index_path.is_file():
        raise ValueError("materialized packet receipt or index is missing")
    try:
        receipt = json.loads(receipt_path.read_bytes().decode("utf-8"))
        index_raw = index_path.read_bytes()
        index = json.loads(index_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("materialized packet receipt or index is invalid") from error
    receipt_binding = dict(receipt) if isinstance(receipt, dict) else {}
    if receipt_binding.pop("receipt_sha256", None) != _digest_json(receipt_binding):
        raise ValueError("materialized packet receipt hash mismatch")
    if (
        receipt.get("schema") != "preproduction-director-import-receipt-v2"
        or receipt.get("mode") != "PREPRODUCTION"
        or receipt.get("request_id") != request_id
        or receipt.get("operations") != _DIRECTOR_IMPORT_OPERATIONS
        or receipt.get("authority") != _DIRECTOR_IMPORT_AUTHORITY
        or receipt.get("packet_index_sha256") != sha256(index_raw).hexdigest()
    ):
        raise ValueError("materialized packet receipt contract mismatch")
    if not isinstance(index, dict) or set(index) != {"schema", "mode", "request_id", "entries", "asset_bindings"}:
        raise ValueError("materialized packet index schema mismatch")
    if index.get("schema") != "preproduction-director-import-index-v1" or index.get("mode") != "PREPRODUCTION" or index.get("request_id") != request_id:
        raise ValueError("materialized packet index contract mismatch")
    entries = index.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("materialized packet index entries are required")
    indexed: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size", "kind"}:
            raise ValueError("materialized packet index entry schema mismatch")
        relative = _validate_packet_path(entry["path"])
        if relative in indexed or not isinstance(entry["sha256"], str) or len(entry["sha256"]) != 64 or type(entry["size"]) is not int or entry["size"] < 0 or not isinstance(entry["kind"], str):
            raise ValueError("materialized packet index entry is invalid")
        indexed[relative] = entry
    if _packet_files(packet) != {*indexed, "index.json", "receipt.json"}:
        raise ValueError("materialized packet has missing or extra files")
    for relative, entry in indexed.items():
        raw = reject_reparse_path(packet / relative).read_bytes()
        if len(raw) != entry["size"] or sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("materialized packet bytes do not match index")
    bindings = index.get("asset_bindings")
    if not isinstance(bindings, dict) or set(bindings) != {"manifest", "assets", "sheet"}:
        raise ValueError("materialized packet asset bindings mismatch")
    manifest_entry = indexed.get("manifest.json")
    if manifest_entry is None or bindings.get("manifest") != manifest_entry["sha256"]:
        raise ValueError("materialized packet manifest binding mismatch")
    assets = bindings.get("assets")
    sheet = bindings.get("sheet")
    if not isinstance(assets, list) or not assets or not isinstance(sheet, dict):
        raise ValueError("materialized packet asset bindings mismatch")
    asset_hashes: list[str] = []
    for item in assets:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError("materialized packet asset binding mismatch")
        path = _validate_packet_path(item["path"])
        if indexed.get(path, {}).get("sha256") != item["sha256"]:
            raise ValueError("materialized packet asset binding mismatch")
        asset_hashes.append(item["sha256"])
    if set(sheet) != {"path", "binding_path", "sha256"} or indexed.get(sheet.get("path"), {}).get("sha256") != sheet.get("sha256"):
        raise ValueError("materialized packet sheet binding mismatch")
    binding_raw = reject_reparse_path(packet / _validate_packet_path(sheet["binding_path"])).read_bytes()
    try:
        sheet_binding = json.loads(binding_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("materialized packet sheet binding is invalid") from error
    if sheet_binding != {"assets": asset_hashes, "sheet": sheet["sha256"]}:
        raise ValueError("materialized packet sheet binding mismatch")
    return receipt


def _read_real_review_candidate(path: Path, source_sha256: str, script_sha256: str) -> dict[str, Any]:
    """Read the fixed active review as evidence, never as executable authority."""
    path = reject_reparse_path(path)
    if not path.is_file():
        raise ValueError("active independent review is not a regular local file")
    raw = path.read_bytes()
    try:
        review = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("active independent review is invalid") from error
    if not isinstance(review, dict):
        raise TypeError("active independent review must be an object")
    if review.get("schema") != "independent-editorial-review-record-v1":
        raise ValueError("active independent review schema mismatch")
    if review.get("review_type") != "independent_machine_review":
        raise ValueError("active independent review type mismatch")
    if not isinstance(review.get("reviewer"), str) or not review["reviewer"].strip():
        raise ValueError("active independent review reviewer is required")
    if review.get("editorial_verdict") != "NO_NARRATIVE_BLOCKER":
        raise ValueError("active independent review verdict is not eligible")
    if review.get("source_sha256") != source_sha256:
        raise ValueError("review source hash does not bind active source")
    if review.get("script_sha256") != script_sha256:
        raise ValueError("review script hash does not bind active script")
    return {
        "file_present": True,
        "path": "qa/independent-editorial-review-v4.json",
        "sha256": sha256(raw).hexdigest(),
        "source_sha256": source_sha256,
        "script_sha256": script_sha256,
        # This installation has no configured authenticated trust root.  A local
        # file can be useful editorial evidence but is never authority by itself.
        "authority": "NOT_QUALIFIED",
        "reason": "CONFIGURED_AUTHENTICATED_TRUST_ROOT_REQUIRED",
    }


def build_real_handoff_preflight(workspace: Path, request_id: str) -> dict[str, Any]:
    """Return a deterministic, read-only REAL preproduction handoff preflight.

    This consumer only reads the active immutable revision.  It deliberately does
    not open narration, timeline, or visual assets until independently rooted QA
    exists; it neither promotes inputs nor constructs Director, executor, budget,
    credential, provider, or delivery objects.
    """
    request = load_preproduction(workspace, request_id)
    execution = request.get("execution")
    if not isinstance(execution, dict) or execution.get("mode") != "PREPRODUCTION":
        raise ValueError("real handoff requires explicit PREPRODUCTION execution")
    if execution.get("paid_execution") != "BLOCKED" or execution.get("prices_usd") is not None or execution.get("providers_called") != []:
        raise ValueError("real handoff requires blocked preproduction execution")
    source_sha256 = execution.get("source_sha256")
    active_revision = execution.get("active_revision")
    if not isinstance(source_sha256, str) or len(source_sha256) != 64:
        raise ValueError("real handoff requires source hash binding")
    if active_revision != f"revisions/{source_sha256[:16]}":
        raise ValueError("active revision pointer is not hash-bound")

    root = reject_reparse_path(_root(workspace, request_id))
    revision = reject_reparse_path(root / active_revision)
    stages_path = reject_reparse_path(revision / "stages.json")
    source_path = reject_reparse_path(revision / "inputs" / "source.json")
    plan_path = reject_reparse_path(revision / "plan" / "editorial-plan.json")
    script_path = reject_reparse_path(revision / "script" / "script.json")
    script_qa_path = reject_reparse_path(revision / "qa" / "script-qa.json")
    review_path = reject_reparse_path(revision / "qa" / "independent-editorial-review-v4.json")
    for path in (stages_path, source_path, plan_path, script_path, script_qa_path):
        if not path.is_file():
            raise ValueError("active revision required artifact is missing")

    try:
        stages = json.loads(stages_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("active revision stages are invalid") from error
    if not isinstance(stages, dict):
        raise TypeError("active revision stages must be an object")
    _verify_completed_stages(revision, stages, source_sha256, reject_path=reject_reparse_path)
    source_raw = source_path.read_bytes()
    script_raw = script_path.read_bytes()
    plan_raw = plan_path.read_bytes()
    qa_raw = script_qa_path.read_bytes()
    if sha256(source_raw).hexdigest() != source_sha256:
        raise ValueError("active source bytes do not match request binding")
    hashes = {
        "source_sha256": source_sha256,
        "plan_sha256": sha256(plan_raw).hexdigest(),
        "script_sha256": sha256(script_raw).hexdigest(),
        "script_qa_sha256": sha256(qa_raw).hexdigest(),
    }
    for stage_name, binding_name in (("plan", "plan_sha256"), ("script", "script_sha256"), ("script_qa", "script_qa_sha256")):
        if stages.get(stage_name, {}).get("sha256") != hashes[binding_name]:
            raise ValueError(f"{stage_name} hash does not bind active artifact")

    review = {"file_present": False, "authority": "NOT_QUALIFIED", "reason": "INDEPENDENT_REVIEW_REQUIRED"}
    if review_path.exists():
        review = _read_real_review_candidate(review_path, hashes["source_sha256"], hashes["script_sha256"])

    import_status = {"status": "REQUIRED", "reason": "DIRECTOR_IMPORT_CONTRACT_REQUIRED"}
    imported = reject_reparse_path(revision / "director-import")
    if imported.exists():
        receipt = verify_real_preproduction_import(imported, request_id)
        # Freshness is intentionally distinct from packet integrity: the verifier
        # above reads only packet bytes, while this preflight checks active revision
        # bindings before treating an old packet as current.
        if any(receipt.get(key) != hashes[key] for key in ("source_sha256", "script_sha256", "script_qa_sha256")):
            raise ValueError("Director import receipt does not bind active revision")
        import_status = {"status": "MATERIALIZED", "receipt_sha256": receipt["receipt_sha256"]}

    dependencies = (
        ("TTS_AUTHENTICATED_RECEIPT_REQUIRED", "A configured authenticated trust-root receipt must bind this request and narration."),
        ("MEASURED_NON_SILENT_AUDIO_REQUIRED", "No qualified narration bytes were opened or promoted by this preflight."),
        ("COMPLETE_TIMELINE_RECEIPT_REQUIRED", "No complete measured timing receipt is available."),
        ("FROZEN_APPROVED_ASSET_MANIFEST_REQUIRED", "No frozen approved visual asset manifest is available."),
        ("PROVIDER_QUALIFICATION_REQUIRED", "No provider route is qualified by this preproduction request."),
        ("LIVE_PRICE_AND_OPERATION_AUTHORITY_REQUIRED", "No live price or operation authority exists; paid execution remains blocked."),
        ("DIRECTOR_IMPORT_CONTRACT_REQUIRED", "No PREPRODUCTION-to-Director import contract has materialized an episode."),
    )
    return {
        "mode": "PREPRODUCTION",
        "request_id": request_id,
        "status": "TTS_REQUIRED",
        "next_required_action": "OBTAIN_TTS_AUTHENTICATED_RECEIPT",
        "execution": {"paid_execution": "BLOCKED", "prices_usd": None, "providers_called": []},
        "bindings": hashes,
        "review": review,
        "live_authority": "NOT_QUALIFIED",
        "inventory": {
            "narration": "MEASURED_NON_SILENT_AUDIO_REQUIRED",
            "timing": "COMPLETE_TIMELINE_RECEIPT_REQUIRED",
            "assets": "FROZEN_APPROVED_ASSET_MANIFEST_REQUIRED",
        },
        "dependencies": [{"code": code, "status": import_status["status"] if code == "DIRECTOR_IMPORT_CONTRACT_REQUIRED" else "REQUIRED", "reason": import_status.get("reason", reason) if code == "DIRECTOR_IMPORT_CONTRACT_REQUIRED" else reason} for code, reason in dependencies],
        "director_import": import_status,
        "operations": {"provider_dispatch": "BLOCKED", "render": "BLOCKED", "telegram": "BLOCKED", "publication": "BLOCKED"},
        "note": "Read-only readiness only; this report does not qualify review authority, authorize LIVE work, or complete production.",
    }


def _real_import_evidence(workspace: Path, request_id: str, manifest_path: Path) -> dict[str, Any]:
    """Read and bind the local evidence needed by the non-activating importer."""
    preflight = build_real_handoff_preflight(workspace, request_id)
    root = reject_reparse_path(_root(workspace, request_id))
    execution = load_preproduction(workspace, request_id)["execution"]
    revision = reject_reparse_path(root / execution["active_revision"])
    stages = json.loads(reject_reparse_path(revision / "stages.json").read_text(encoding="utf-8"))
    paths = {
        "source": reject_reparse_path(revision / "inputs" / "source.json"),
        "script": reject_reparse_path(revision / "script" / "script.json"),
        "script_qa": reject_reparse_path(revision / "qa" / "script-qa.json"),
        "review": reject_reparse_path(revision / "qa" / "independent-editorial-review-v4.json"),
        "timeline": reject_reparse_path(revision / "inputs" / "timeline.json"),
        "storyboard": reject_reparse_path(revision / "storyboard" / "scenes.json"),
        "storyboard_qa": reject_reparse_path(revision / "qa" / "storyboard.json"),
    }
    audio_paths = [reject_reparse_path(path) for path in (revision / "inputs").glob("audio.*")] + [
        reject_reparse_path(path) for path in (revision / "inputs").glob("narration.*")
    ]
    audio_paths = list(dict.fromkeys(audio_paths))
    if len(audio_paths) != 1:
        raise ValueError("real import requires exactly one local narration file")
    paths["audio"] = audio_paths[0]
    if any(not path.is_file() for path in paths.values()):
        raise ValueError("real import required evidence is missing")
    raw = {name: path.read_bytes() for name, path in paths.items()}
    hashes = {f"{name}_sha256": sha256(value).hexdigest() for name, value in raw.items()}
    if hashes["source_sha256"] != preflight["bindings"]["source_sha256"] or hashes["script_sha256"] != preflight["bindings"]["script_sha256"]:
        raise ValueError("real import source/script binding changed")
    for name, stage_key in (("timeline", "timeline_sha256"), ("audio", "audio_sha256"), ("storyboard", "sha256"), ("storyboard_qa", "qa_sha256")):
        if stages.get("storyboard", {}).get(stage_key) != hashes[f"{name}_sha256"]:
            raise ValueError(f"real import {name} hash does not bind active stage")
    timeline = json.loads(raw["timeline"].decode("utf-8"))
    script = json.loads(raw["script"].decode("utf-8"))
    if timeline.get("mode") != "PREPRODUCTION" or timeline.get("audio_sha256") != hashes["audio_sha256"] or timeline.get("script_sha256") != hashes["script_sha256"] or timeline.get("source_sha256") != hashes["source_sha256"]:
        raise ValueError("real import timeline does not bind preproduction evidence")
    _audio_raw, _audio_sha, duration, _suffix = _read_local_audio(paths["audio"])
    if _audio_sha != hashes["audio_sha256"] or abs(float(timeline.get("audio_duration_s", -1)) - duration) > 0.02:
        raise ValueError("real import narration is not measured against its timeline")
    timestamps = timeline.get("sentence_timestamps")
    if not isinstance(timestamps, list) or len(timestamps) != len(script.get("segments", [])):
        raise ValueError("real import timeline must cover every script segment")
    previous = 0.0
    for timestamp, segment in zip(timestamps, script["segments"], strict=True):
        if not isinstance(timestamp, dict) or timestamp.get("text") != segment.get("narration"):
            raise ValueError("real import timeline narration differs from script")
        start, end = float(timestamp["start"]), float(timestamp["end"])
        if abs(start - previous) > 0.000001 or end <= start or abs(float(timestamp.get("duration", -1)) - (end - start)) > 0.000001:
            raise ValueError("real import timestamps must be contiguous measured intervals")
        previous = end
    if abs(previous - duration) > 0.02:
        raise ValueError("real import timestamps do not end at decoded audio duration")
    manifest_path = reject_reparse_path(Path(manifest_path))
    if not manifest_path.is_file():
        raise ValueError("real import manifest is required")
    manifest, manifest_sha = _load_guarded_manifest(manifest_path, reject_reparse_path)
    if manifest.mode != "LIVE" or "TEST" in manifest.reviewer.upper():
        raise ValueError("real import rejects TEST manifest evidence")
    for asset in (*manifest.assets, manifest.sheet):
        reject_reparse_path(asset.path)
        reject_reparse_path(asset.path.with_suffix(".binding.json")) if asset == manifest.sheet else None
        asset.verify("LIVE")
        if "TEST" in asset.reviewer.upper():
            raise ValueError("real import rejects TEST asset evidence")
    manifest.verify("LIVE")
    return {"root": root, "revision": revision, "paths": paths, "raw": raw, "hashes": hashes, "manifest": manifest, "manifest_sha256": manifest_sha, "request_id": request_id}


def import_real_preproduction_handoff(workspace: Path, request_id: str, manifest_path: Path) -> dict[str, Any]:
    """Atomically materialize verified PREPRODUCTION evidence for Director intake.

    This is deliberately only a local import contract. It neither instantiates a
    Director nor activates/render/dispatches anything.
    """
    _validate_id(request_id)
    evidence = _real_import_evidence(workspace, request_id, manifest_path)
    revision, raw = evidence["revision"], evidence["raw"]
    packet = reject_reparse_path(revision / "director-import")
    receipt_base = {
        "schema": "preproduction-director-import-receipt-v2", "mode": "PREPRODUCTION", "request_id": request_id,
        "source_sha256": evidence["hashes"]["source_sha256"], "script_sha256": evidence["hashes"]["script_sha256"],
        "script_qa_sha256": evidence["hashes"]["script_qa_sha256"], "review_sha256": evidence["hashes"]["review_sha256"],
        "timeline_sha256": evidence["hashes"]["timeline_sha256"], "audio_sha256": evidence["hashes"]["audio_sha256"],
        "storyboard_sha256": evidence["hashes"]["storyboard_sha256"], "storyboard_qa_sha256": evidence["hashes"]["storyboard_qa_sha256"],
        "manifest_sha256": evidence["manifest_sha256"], "active_revision": str(revision.relative_to(evidence["root"])).replace("\\", "/"),
        "authority": _DIRECTOR_IMPORT_AUTHORITY, "operations": _DIRECTOR_IMPORT_OPERATIONS,
    }

    def existing() -> dict[str, Any]:
        receipt = verify_real_preproduction_import(packet, request_id)
        if any(receipt.get(key) != value for key, value in receipt_base.items() if key not in {"packet_index_sha256", "receipt_sha256"}):
            raise ValueError("Director import conflicts with changed immutable evidence")
        return {
            "mode": "PREPRODUCTION", "packet_path": str(packet), "receipt_sha256": receipt["receipt_sha256"],
            "operations": receipt["operations"], "qualification_status": receipt["authority"]["qualification_status"],
        }

    with file_lock(revision / ".director-import.lock"):
        refreshed = _real_import_evidence(workspace, request_id, manifest_path)
        if refreshed["hashes"] != evidence["hashes"] or refreshed["manifest_sha256"] != evidence["manifest_sha256"]:
            raise ValueError("real import evidence changed before locked materialization")
        if packet.exists():
            return existing()
        for stale in revision.glob(".di-*"):
            _reject_tree(stale, reject_reparse_path)
            shutil.rmtree(stale)
        staging = Path(tempfile.mkdtemp(prefix=".di-", dir=revision))
        files: list[tuple[str, bytes, str]] = []
        for name in ("source", "script", "script_qa", "review", "timeline", "audio", "storyboard", "storyboard_qa"):
            relative = f"evidence/{name}.bin"
            _durable_write_bytes(staging / relative, raw[name])
            files.append((relative, raw[name], "evidence"))
        manifest_raw = reject_reparse_path(Path(manifest_path)).read_bytes()
        _durable_write_bytes(staging / "manifest.json", manifest_raw)
        files.append(("manifest.json", manifest_raw, "manifest"))
        assets = staging / "assets"
        assets.mkdir()
        asset_bindings: list[dict[str, str]] = []
        sheet_binding: dict[str, str] | None = None
        for index, asset in enumerate((*evidence["manifest"].assets, evidence["manifest"].sheet), 1):
            relative = f"assets/{index}-{asset.path.name}"
            asset_raw = reject_reparse_path(asset.path).read_bytes()
            _durable_write_bytes(staging / relative, asset_raw)
            if sha256(asset_raw).hexdigest() != asset.sha256:
                raise ValueError("Director import copied asset hash mismatch")
            files.append((relative, asset_raw, "asset" if asset != evidence["manifest"].sheet else "sheet"))
            if asset == evidence["manifest"].sheet:
                binding_relative = f"assets/{index}-{asset.path.with_suffix('.binding.json').name}"
                binding_raw = reject_reparse_path(asset.path.with_suffix(".binding.json")).read_bytes()
                _durable_write_bytes(staging / binding_relative, binding_raw)
                files.append((binding_relative, binding_raw, "sheet_binding"))
                sheet_binding = {"path": relative, "binding_path": binding_relative, "sha256": asset.sha256}
            else:
                asset_bindings.append({"path": relative, "sha256": asset.sha256})
        if sheet_binding is None:
            raise ValueError("Director import sheet binding is required")
        index = {
            "schema": "preproduction-director-import-index-v1", "mode": "PREPRODUCTION", "request_id": request_id,
            "entries": [
                {"path": relative, "sha256": sha256(value).hexdigest(), "size": len(value), "kind": kind}
                for relative, value, kind in sorted(files)
            ],
            "asset_bindings": {"manifest": sha256(manifest_raw).hexdigest(), "assets": asset_bindings, "sheet": sheet_binding},
        }
        index_raw = _durable_json(staging / "index.json", index)
        receipt_base["packet_index_sha256"] = sha256(index_raw).hexdigest()
        receipt_base["receipt_sha256"] = _digest_json(receipt_base)
        _durable_json(staging / "receipt.json", receipt_base)
        for directory in (staging / "evidence", staging / "assets", staging):
            _fsync_directory(directory)
        os.replace(staging, packet)
        _fsync_directory(revision)
        verified = verify_real_preproduction_import(packet, request_id)
    return {
        "mode": "PREPRODUCTION", "packet_path": str(packet), "receipt_sha256": verified["receipt_sha256"],
        "operations": verified["operations"], "qualification_status": verified["authority"]["qualification_status"],
    }


def advance_preproduction(
    workspace: Path,
    request_id: str,
    source_path: Path,
    *,
    mode: str,
    timeline_path: Path | None = None,
    audio_path: Path | None = None,
) -> dict[str, Any]:
    """Serialize a request transition and revalidate its state inside the lock."""
    _validate_id(request_id)
    root = reject_reparse_path(_root(workspace, request_id))
    with file_lock(root / ".advance.lock"):
        return _advance_preproduction_locked(
            workspace, request_id, source_path, mode=mode,
            timeline_path=timeline_path, audio_path=audio_path,
        )


def _advance_preproduction_locked(
    workspace: Path,
    request_id: str,
    source_path: Path,
    *,
    mode: str,
    timeline_path: Path | None = None,
    audio_path: Path | None = None,
) -> dict[str, Any]:
    """Run fixed, offline editorial adapters; no provider, spend, or publishing path.

    Source/timing evidence is always supplied as explicit local bytes. Completed
    stages are hash-bound and reused; a changed source gets a successor revision.
    """
    if mode not in {"TEST", "PREPRODUCTION"}:
        raise ValueError("mode must be TEST or PREPRODUCTION")
    _validate_id(request_id)
    root = reject_reparse_path(_root(workspace, request_id))
    request_path = reject_reparse_path(root / "request.json")
    document = json.loads(request_path.read_text(encoding="utf-8"))
    _validate(root, document, request_id, reject_path=reject_reparse_path)
    source, source_sha, source_raw = _read_local_json(source_path, "source")
    _validate_evidence(source, schema="local-biblical-source-v1", mode=mode, label="source")
    research = source.get("research")
    if not isinstance(research, dict) or not research.get("narrative_classification", {}).get("BIBLICAL_FACT"):
        raise ValueError("source must contain structured biblical research facts")
    revision = reject_reparse_path(root / "revisions" / source_sha[:16])
    revision.mkdir(parents=True, exist_ok=True)
    stages_path = reject_reparse_path(revision / "stages.json")
    stages = json.loads(stages_path.read_text(encoding="utf-8")) if stages_path.exists() else {}
    _verify_completed_stages(revision, stages, source_sha, reject_path=reject_reparse_path)
    source_copy = reject_reparse_path(revision / "inputs" / "source.json")
    if not source_copy.exists():
        source_copy.parent.mkdir(parents=True, exist_ok=True)
        source_copy.write_bytes(source_raw)

    if "script" not in stages:
        research_dir, plan_dir, script_dir, qa_dir = (revision / name for name in ("research", "plan", "script", "qa"))
        research_dir.mkdir(exist_ok=True)
        _atomic_json(research_dir / "sources.json", research)
        generated = asyncio.run(ScriptAgent(llm_provider=None).run(
            "PREPRODUCTION", research, target_duration_s=None, script_dir=str(script_dir)
        ))
        if not generated.success:
            raise ValueError("ScriptAgent rejected source evidence: " + str(generated.error))
        script_path = reject_reparse_path(script_dir / "script.json")
        script = json.loads(script_path.read_text(encoding="utf-8"))
        segments = script["segments"]
        source_segments = research.get("narrative_segments", [])
        if source_segments:
            if not isinstance(source_segments, list) or len(source_segments) != len(segments):
                raise ValueError("source narrative segments must cover every semantic script segment")
            segment_fact_matrix = [
                {"segment_id": item.get("id"), "kind": item.get("kind"), "verses": item.get("verses", ""),
                 "facts": item.get("facts"), "motion_need": item.get("motion_need")}
                for item in source_segments
            ]
            if any(not isinstance(item["motion_need"], str) or not item["motion_need"].strip() for item in segment_fact_matrix):
                raise ValueError("source narrative segment motion needs are required")
        else:
            motion_requirements = research.get("scene_motion_requirements", [])
            if not isinstance(motion_requirements, list) or (motion_requirements and len(motion_requirements) != len(segments)):
                raise ValueError("source motion requirements must cover every semantic script segment")
            segment_fact_matrix = motion_requirements
        provider_candidates = research.get("provider_candidates", [])
        if not isinstance(provider_candidates, list) or any(not isinstance(item, dict) for item in provider_candidates):
            raise ValueError("provider candidates must be a list of capability records")
        if any(
            item.get("qualification_status") != "UNQUALIFIED"
            or item.get("commercial_status") != "UNKNOWN"
            or _contains_unqualified_price_claim(item)
            for item in provider_candidates
        ):
            raise ValueError("provider candidates cannot claim unqualified prices")
        word_count = len(script["narration"].split())
        estimated_narration_seconds = (word_count / ScriptAgent.WORDS_PER_MINUTE) * 60
        recommendation = {
            "schema": "source-bound-editorial-plan-v1",
            "timing_policy": "semantic_segments_only",
            "word_count_measured": word_count,
            "words_per_minute_reference": ScriptAgent.WORDS_PER_MINUTE,
            "estimated_narration_seconds": estimated_narration_seconds,
            "semantic_segment_count": len(segments),
            "scene_count": len(segments),
            "biblical_event_count": len(research["narrative_classification"]["BIBLICAL_FACT"]),
            "duration_status": "ESTIMATE_PENDING_MEASURED_AUDIO",
            "segment_fact_matrix": segment_fact_matrix,
            "provider_candidates": provider_candidates,
            "references": research.get("references", []),
        }
        plan = {"adapter": "src.hybrid.preproduction:offline_source_bound_plan", "recommendation": recommendation,
                "prices_usd": None, "paid_execution": "BLOCKED", "source_sha256": source_sha}
        plan["plan_sha256"] = _digest_json(plan)
        plan_dir.mkdir(exist_ok=True)
        _atomic_json(plan_dir / "editorial-plan.json", plan)
        qa = ScriptQAAgent().review(script)
        qa_dir.mkdir(exist_ok=True)
        source_bound_review = bool(source_segments)
        qa_document = {
            "adapter": "src.agents.script_qa:ScriptQAAgent",
            "deterministic_checks_passed": qa.approved,
            "findings": list(qa.findings),
            "verdict": "PENDING_INDEPENDENT_EDITORIAL_REVIEW" if source_bound_review else ("PASS" if qa.approved else "FAIL"),
        }
        _atomic_json(qa_dir / "script-qa.json", qa_document)
        if not qa.approved:
            raise ValueError("ScriptQA rejected source-bound script: " + ", ".join(qa.findings))
        stages["research"] = {"status": "COMPLETE", "attempts": 1, "sha256": sha256(_guarded_bytes(research_dir / "sources.json")).hexdigest()}
        stages["plan"] = {"status": "COMPLETE", "attempts": 1, "sha256": sha256(_guarded_bytes(plan_dir / "editorial-plan.json")).hexdigest()}
        stages["script"] = {"status": "COMPLETE", "attempts": 1, "sha256": sha256(_guarded_bytes(script_dir / "script.json")).hexdigest()}
        stages["script_qa"] = {
            "status": "PENDING_INDEPENDENT_EDITORIAL_REVIEW" if source_bound_review else "PASS",
            "attempts": 1,
            "sha256": sha256(_guarded_bytes(qa_dir / "script-qa.json")).hexdigest(),
        }

    if timeline_path is None:
        if audio_path is not None:
            raise ValueError("audio requires hash-bound timeline evidence")
        if _verified_completed_storyboard(revision, stages):
            status = "PREPRODUCTION_VALIDATED"
        else:
            stages.setdefault("storyboard", {"status": "TTS_REQUIRED", "attempts": 0})
            status = "TTS_REQUIRED"
    else:
        # Stages are mutable progress bookkeeping, never review authority. Re-read
        # the immutable QA artifact and bind it to the recorded hash before any
        # audio/timeline bytes are consumed.
        qa_path = reject_reparse_path(revision / "qa" / "script-qa.json")
        qa_raw = qa_path.read_bytes()
        if sha256(qa_raw).hexdigest() != stages.get("script_qa", {}).get("sha256"):
            raise ValueError("script QA evidence hash does not match recorded stage")
        try:
            script_qa_evidence = json.loads(qa_raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("script QA evidence is invalid") from error
        if script_qa_evidence.get("verdict") == "PENDING_INDEPENDENT_EDITORIAL_REVIEW":
            raise ValueError("independent editorial review is pending; audio and storyboard remain blocked")
        # No self-authored stage string can stand in for independently rooted evidence.
        if stages.get("script_qa", {}).get("status") != "PASS":
            raise ValueError("independent editorial evidence has not been revalidated")
        if audio_path is None:
            raise ValueError("timeline evidence requires a local measured audio file")
        timeline, timeline_sha, timeline_raw = _read_local_json(timeline_path, "timeline evidence")
        _validate_evidence(timeline, schema="local-timeline-evidence-v1", mode=mode, label="timeline evidence")
        audio_raw, audio_sha, measured_duration, suffix = _read_local_audio(audio_path)
        script_path = reject_reparse_path(revision / "script" / "script.json")
        script_raw = script_path.read_bytes()
        script = json.loads(script_raw.decode("utf-8"))
        if timeline.get("audio_sha256") != audio_sha:
            raise ValueError("timeline audio hash does not bind supplied audio")
        if timeline.get("script_sha256") != sha256(script_raw).hexdigest():
            raise ValueError("timeline script hash does not bind QA-passed script")
        if timeline.get("source_sha256") != source_sha:
            raise ValueError("timeline source hash does not bind supplied source")
        if abs(float(timeline.get("audio_duration_s", -1)) - measured_duration) > 0.02:
            raise ValueError("timeline duration does not match measured audio")
        timestamps = timeline.get("sentence_timestamps")
        if not isinstance(timestamps, list) or len(timestamps) != len(script["segments"]):
            raise ValueError("timeline evidence must cover every script segment")
        previous = 0.0
        for timestamp, segment in zip(timestamps, script["segments"], strict=True):
            if not isinstance(timestamp, dict) or timestamp.get("text") != segment["narration"]:
                raise ValueError("timeline narration differs from QA-passed script")
            start, end = float(timestamp["start"]), float(timestamp["end"])
            if abs(start - previous) > 0.000001 or end <= start or abs(float(timestamp.get("duration", -1)) - (end - start)) > 0.000001:
                raise ValueError("timeline timestamps must be contiguous measured intervals")
            previous = end
        if abs(previous - measured_duration) > 0.02:
            raise ValueError("timeline timestamps do not end at measured audio duration")
        storyboard_input_sha = _digest_json({"timeline_sha256": timeline_sha, "audio_sha256": audio_sha, "script_sha256": sha256(script_raw).hexdigest(), "source_sha256": source_sha})
        if stages.get("storyboard", {}).get("input_sha256") != storyboard_input_sha:
            duration = timeline.get("audio_duration_s")
            with _without_ambient_story_plan():
                result = asyncio.run(StoryboardAgent(llm_provider=None).run("PREPRODUCTION", script["narration"], timestamps, float(duration), str(revision / "storyboard")))
            if not result.success:
                raise ValueError("StoryboardAgent rejected timeline evidence: " + str(result.error))
            report = _storyboard_report(result.data["scenes"], script["segments"], float(duration))
            qa_dir = revision / "qa"
            _atomic_json(qa_dir / "storyboard.json", report)
            inputs = revision / "inputs"
            inputs.mkdir(parents=True, exist_ok=True)
            (inputs / "timeline.json").write_bytes(timeline_raw)
            imported_audio = inputs / f"audio{suffix or '.bin'}"
            imported_audio.write_bytes(audio_raw)
            if sha256(_guarded_bytes(imported_audio)).hexdigest() != audio_sha:
                raise ValueError("imported audio hash mismatch")
            stages["storyboard"] = {"status": "PASS", "attempts": stages.get("storyboard", {}).get("attempts", 0) + 1,
                                   "input_sha256": storyboard_input_sha, "timeline_sha256": timeline_sha, "audio_sha256": audio_sha,
                                   "script_sha256": sha256(script_raw).hexdigest(), "source_sha256": source_sha,
                                   "sha256": sha256(_guarded_bytes(revision / "storyboard" / "scenes.json")).hexdigest(),
                                   "qa_sha256": sha256(_guarded_bytes(revision / "qa" / "storyboard.json")).hexdigest()}
        status = "PREPRODUCTION_VALIDATED"
    _atomic_json(stages_path, stages)
    document["status"] = status
    document["dependencies"] = ["ASSETS_REQUIRED", "AUTHORIZATION_REQUIRED"] if status == "PREPRODUCTION_VALIDATED" else ["TTS_REQUIRED", "ASSETS_REQUIRED", "AUTHORIZATION_REQUIRED"]
    document["execution"] = {"mode": mode, "source_sha256": source_sha, "active_revision": f"revisions/{source_sha[:16]}",
                             "paid_execution": "BLOCKED", "prices_usd": None, "providers_called": []}
    document["compilation"] = {
        "status": "ASSETS_REQUIRED" if status == "PREPRODUCTION_VALIDATED" else "TTS_REQUIRED",
        "compiled": False,
        "reason": "No approved visual assets or source manifest are present; compiled.json and manifest.json are deliberately absent.",
    }
    if status == "PREPRODUCTION_VALIDATED":
        compilation_input = {
            "schema": "preproduction-compiled-input-v1",
            "mode": mode,
            "source_sha256": source_sha,
            "script_sha256": stages["storyboard"]["script_sha256"],
            "timeline_sha256": stages["storyboard"]["timeline_sha256"],
            "audio_sha256": stages["storyboard"]["audio_sha256"],
            "storyboard_sha256": stages["storyboard"]["sha256"],
            "status": "ASSETS_REQUIRED",
        }
        compilation_input["input_sha256"] = _digest_json(compilation_input)
        _atomic_json(revision / "compilation-input.json", compilation_input)
    binding = dict(document)
    binding.pop("package_sha256", None)
    document["package_sha256"] = _digest_json(binding)
    _atomic_json(root / "request.json", document)
    result = _public(root, document)
    result.update({"active_revision_path": str(revision), "stages": stages, "prices_usd": None, "paid_execution": "BLOCKED"})
    return result


def compile_preproduction(workspace: Path, request_id: str, manifest_path: Path) -> dict[str, Any]:
    """Materialize a closed TEST compiled packet from validated offline evidence.

    This does not create assets, approvals, providers, or a LIVE policy.  It only
    copies already frozen local TEST assets and binds every consumed byte into a
    receipt that can be handed to the existing compiled TEST CLI adapter.
    """
    from src.hybrid.artifacts import FrozenAsset, Manifest
    from src.hybrid.autonomous_cli import (
        _reject_reparse_path,
        compiled_policy_document,
    )
    from src.hybrid.compiled import CompiledEpisode, compile_storyboard

    _validate_id(request_id)
    root = _reject_reparse_path(_root(workspace, request_id))
    request_path = _reject_reparse_path(root / "request.json")
    document = json.loads(request_path.read_text(encoding="utf-8"))
    _validate(root, document, request_id, reject_path=_reject_reparse_path)
    if document.get("status") != "PREPRODUCTION_VALIDATED":
        raise ValueError("compile requires PREPRODUCTION_VALIDATED request")
    execution = document.get("execution")
    if not isinstance(execution, dict) or execution.get("mode") != "TEST":
        raise ValueError("compile requires explicit TEST preproduction execution")
    active = execution.get("active_revision")
    if not isinstance(active, str) or active != f"revisions/{execution.get('source_sha256', '')[:16]}":
        raise ValueError("active revision pointer is not hash-bound")
    revision = _reject_reparse_path(root / active)
    source_sha = execution["source_sha256"]
    stages_path = _reject_reparse_path(revision / "stages.json")
    stages = json.loads(stages_path.read_text(encoding="utf-8"))
    for relative in (
        "inputs/source.json", "script/script.json", "qa/script-qa.json",
        "storyboard/scenes.json", "qa/storyboard.json", "inputs/timeline.json",
    ):
        _reject_reparse_path(revision / relative)
    _verify_completed_stages(revision, stages, source_sha, reject_path=_reject_reparse_path)
    if not _verified_completed_storyboard(revision, stages, reject_path=_reject_reparse_path):
        raise ValueError("validated storyboard inputs were altered")
    compilation_input_path = _reject_reparse_path(revision / "compilation-input.json")
    compilation_input = json.loads(compilation_input_path.read_text(encoding="utf-8"))
    expected_input = dict(compilation_input)
    input_sha = expected_input.pop("input_sha256", None)
    if input_sha != _digest_json(expected_input) or compilation_input.get("mode") != "TEST":
        raise ValueError("compilation input hash or mode mismatch")
    expected_hashes = {
        "source_sha256": source_sha,
        "script_sha256": stages["storyboard"]["script_sha256"],
        "timeline_sha256": stages["storyboard"]["timeline_sha256"],
        "audio_sha256": stages["storyboard"]["audio_sha256"],
        "storyboard_sha256": stages["storyboard"]["sha256"],
    }
    if any(compilation_input.get(key) != value for key, value in expected_hashes.items()):
        raise ValueError("compilation input does not bind validated evidence")
    required = {
        "source": (revision / "inputs" / "source.json", source_sha),
        "script": (revision / "script" / "script.json", stages["script"]["sha256"]),
        "script QA": (revision / "qa" / "script-qa.json", stages["script_qa"]["sha256"]),
        "storyboard": (revision / "storyboard" / "scenes.json", stages["storyboard"]["sha256"]),
        "timeline": (revision / "inputs" / "timeline.json", stages["storyboard"]["timeline_sha256"]),
    }
    audio_files = list((revision / "inputs").glob("audio.*"))
    if len(audio_files) != 1:
        raise ValueError("validated revision must contain one local audio input")
    _reject_reparse_path(audio_files[0])
    required["audio"] = (audio_files[0], stages["storyboard"]["audio_sha256"])
    for label, (path, expected) in required.items():
        path = _reject_reparse_path(path)
        if not path.is_file() or sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"{label} bytes do not match validated hash")

    manifest_path = _reject_reparse_path(Path(manifest_path))
    if not manifest_path.is_file():
        raise ValueError("local TEST manifest is required")
    manifest, manifest_input_sha = _load_guarded_manifest(manifest_path, _reject_reparse_path)
    if manifest.mode != "TEST" or "TEST" not in manifest.reviewer.upper():
        raise ValueError("manifest must be explicitly labelled local TEST evidence")
    for asset in (*manifest.assets, manifest.sheet):
        _reject_reparse_path(asset.path)
        asset.verify("TEST")
        if "TEST" not in asset.reviewer.upper():
            raise ValueError("manifest assets must be explicitly labelled TEST")

    packet = _reject_reparse_path(revision / "compiled-test")

    def existing_packet() -> dict[str, Any]:
        _reject_tree(packet, _reject_reparse_path)
        receipt_path = _reject_reparse_path(packet / "receipt.json")
        if not receipt_path.is_file():
            raise ValueError("compiled lineage exists without immutable receipt")
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        binding = dict(receipt)
        receipt_sha = binding.pop("receipt_sha256", None)
        if receipt_sha != _digest_json(binding):
            raise ValueError("compiled receipt hash mismatch")
        if receipt.get("compilation_input_sha256") != input_sha or receipt.get("manifest_input_sha256") != manifest_input_sha:
            raise ValueError("compiled lineage conflicts with changed inputs; never overwrite")
        compiled_path = _reject_reparse_path(packet / "compiled.json")
        packet_manifest, _ = _load_guarded_manifest(packet / "manifest.json", _reject_reparse_path)
        policy_path = _reject_reparse_path(packet / "policy.json")
        if (
            CompiledEpisode.load(compiled_path).checksum != receipt.get("compiled_sha256")
            or packet_manifest.checksum != receipt.get("manifest_sha256")
            or _digest_json(json.loads(policy_path.read_text(encoding="utf-8"))) != receipt.get("policy_sha256")
        ):
            raise ValueError("compiled lineage receipt does not bind packet bytes")
        return {"mode": "TEST", "packet_path": str(packet), "compilation_input_sha256": input_sha, "receipt_sha256": receipt_sha}

    with file_lock(revision / ".compiled-test.lock"):
        if packet.exists():
            return existing_packet()
        for stale in revision.glob(".compiled-test.staging-*"):
            _reject_tree(stale, _reject_reparse_path)
            shutil.rmtree(stale)
        staging = Path(tempfile.mkdtemp(prefix=".compiled-test.staging-", dir=revision))
        assets = staging / "assets"
        assets.mkdir()

        def copy_asset(asset: FrozenAsset, name: str) -> FrozenAsset:
            destination = assets / name
            shutil.copyfile(asset.path, destination)
            return FrozenAsset.approve(destination, asset.reviewer, "TEST")

        staged_assets = tuple(copy_asset(asset, f"reference-{index}{asset.path.suffix}") for index, asset in enumerate(manifest.assets, 1))
        staged_sheet = copy_asset(manifest.sheet, f"sheet{manifest.sheet.path.suffix}")
        shutil.copyfile(manifest.sheet.path.with_suffix(".binding.json"), staged_sheet.path.with_suffix(".binding.json"))
        local_assets = tuple(FrozenAsset(packet / "assets" / asset.path.name, asset.sha256, asset.reviewer, "TEST") for asset in staged_assets)
        local_sheet = FrozenAsset(packet / "assets" / staged_sheet.path.name, staged_sheet.sha256, staged_sheet.reviewer, "TEST")
        local_manifest = Manifest(local_assets, local_sheet, manifest.reviewer, "TEST")
        staged_audio = copy_asset(FrozenAsset.approve(audio_files[0], "TEST local preproduction audio", "TEST"), f"audio{audio_files[0].suffix}")
        local_audio = FrozenAsset(packet / "assets" / staged_audio.path.name, staged_audio.sha256, staged_audio.reviewer, "TEST")
        scenes_path = _reject_reparse_path(revision / "storyboard" / "scenes.json")
        scenes = json.loads(scenes_path.read_text(encoding="utf-8"))["scenes"]
        episode = compile_storyboard(f"TEST-PREPRODUCTION-{request_id[:16]}", staged_audio, scenes)
        episode = CompiledEpisode(episode.episode_id, local_audio, episode.frames, {
            "request_id": request_id, "source_sha256": source_sha, "script_sha256": stages["script"]["sha256"],
            "script_qa_sha256": stages["script_qa"]["sha256"], "storyboard_sha256": stages["storyboard"]["sha256"],
            "timeline_sha256": stages["storyboard"]["timeline_sha256"], "audio_sha256": stages["storyboard"]["audio_sha256"],
            "compilation_input_sha256": input_sha,
        })
        episode.save(staging / "compiled.json")
        manifest_document = asdict(local_manifest)
        for asset in [*manifest_document["assets"], manifest_document["sheet"]]:
            asset["path"] = str(asset["path"])
        _atomic_json(staging / "manifest.json", {"manifest": manifest_document, "checksum": local_manifest.checksum})
        _atomic_json(staging / "policy.json", compiled_policy_document())
        receipt = {
            "schema": "preproduction-compile-receipt-v1", "mode": "TEST", "request_id": request_id,
            "compilation_input_sha256": input_sha, "manifest_input_sha256": manifest_input_sha, "source_sha256": source_sha,
            "script_sha256": stages["script"]["sha256"], "script_qa_sha256": stages["script_qa"]["sha256"],
            "storyboard_sha256": stages["storyboard"]["sha256"], "timeline_sha256": stages["storyboard"]["timeline_sha256"],
            "audio_sha256": stages["storyboard"]["audio_sha256"], "compiled_sha256": episode.checksum,
            "manifest_sha256": local_manifest.checksum, "policy_sha256": _digest_json(compiled_policy_document()),
        }
        receipt["receipt_sha256"] = _digest_json(receipt)
        _atomic_json(staging / "receipt.json", receipt)
        os.replace(staging, packet)
        return {"mode": "TEST", "packet_path": str(packet), "compilation_input_sha256": input_sha, "receipt_sha256": receipt["receipt_sha256"]}
