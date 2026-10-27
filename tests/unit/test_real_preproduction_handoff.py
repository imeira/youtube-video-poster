"""Read-only operational handoff from a REAL preproduction revision."""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest


def _source(path: Path, *, mode: str = "PREPRODUCTION") -> Path:
    source = {
        "schema": "local-biblical-source-v1",
        "mode": mode,
        "provenance": {
            "kind": "LOCAL_SOURCE" if mode == "PREPRODUCTION" else "TEST_LOCAL_SOURCE",
            "origin": "unit-test",
            "retrieved_at": "offline",
        },
        "research": {
            "story": "Jacó em Betel",
            "references": [{"book": "Gênesis", "chapter": 28, "verses": "10-22"}],
            "narrative_classification": {"BIBLICAL_FACT": ["Jacó dormiu e recebeu uma promessa."]},
            "narrative_segments": [
                {"id": "S001", "kind": "biblical_paraphrase", "verses": "Gênesis 28:10-15", "facts": ["sonho"], "narration": "Jacó dormiu e recebeu uma promessa de cuidado.", "motion_need": "sonho sereno"},
                {"id": "S002", "kind": "family_reflection", "facts": ["reflexão"], "narration": "Conversem em família sobre confiar no cuidado de Deus.", "motion_need": "fechamento editorial"},
            ],
            "provider_candidates": [{"route": "unqualified", "capability": "image", "qualification_status": "UNQUALIFIED", "commercial_status": "UNKNOWN"}],
        },
    }
    path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    return path


def _fingerprint(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)).replace("\\", "/"): sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def _prepare(tmp_path: Path):
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    created = start_preproduction(tmp_path, "Jacó em Betel\n".encode())
    source = _source(tmp_path / "source.json")
    request = advance_preproduction(tmp_path, created["request_id"], source, mode="PREPRODUCTION")
    revision = Path(request["active_revision_path"])
    review = {
        "schema": "independent-editorial-review-record-v1",
        "review_type": "independent_machine_review",
        "reviewer": "independent-fixture",
        "editorial_verdict": "NO_NARRATIVE_BLOCKER",
        "code_verdict": "PASS",
        "source_sha256": sha256((revision / "inputs" / "source.json").read_bytes()).hexdigest(),
        "script_sha256": sha256((revision / "script" / "script.json").read_bytes()).hexdigest(),
        "human_approval": False,
        "paid_authorization": False,
        "audio_measured": False,
        "visual_approval": False,
        "publication_authorization": False,
    }
    review_path = revision / "qa" / "independent-editorial-review-v4.json"
    review_path.write_text(json.dumps(review), encoding="utf-8")
    return created, source, revision, review_path


def _director_import_inputs(tmp_path: Path):
    """Build explicit local PREPRODUCTION evidence; TEST evidence is separate."""
    import math
    import wave

    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet

    created, source, revision, _review = _prepare(tmp_path)
    script_raw = (revision / "script" / "script.json").read_bytes()
    script = json.loads(script_raw)
    audio_path = revision / "inputs" / "narration.wav"
    audio_path.parent.mkdir(exist_ok=True)
    sample_rate, duration = 8000, len(script["segments"]) * 2
    samples = b"".join(
        int(9000 * math.sin(2 * math.pi * 440 * index / sample_rate)).to_bytes(2, "little", signed=True)
        for index in range(sample_rate * duration)
    )
    with wave.open(str(audio_path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples)
    audio_sha = sha256(audio_path.read_bytes()).hexdigest()
    source_sha = sha256(source.read_bytes()).hexdigest()
    timeline = {
        "schema": "local-timeline-evidence-v1", "mode": "PREPRODUCTION",
        "provenance": {"kind": "LOCAL_SOURCE", "origin": "unit-test", "retrieved_at": "offline"},
        "audio_sha256": audio_sha, "script_sha256": sha256(script_raw).hexdigest(), "source_sha256": source_sha,
        "audio_duration_s": duration,
        "sentence_timestamps": [{"start": index * 2, "end": (index + 1) * 2, "duration": 2, "text": segment["narration"]} for index, segment in enumerate(script["segments"])],
    }
    timeline_path = revision / "inputs" / "timeline.json"
    timeline_path.write_text(json.dumps(timeline, ensure_ascii=False), encoding="utf-8")
    scenes = {"scenes": [{"scene_id": f"S{index + 1:03}", "start": index * 2, "end": (index + 1) * 2, "narration": segment["narration"], "image_prompt": "local verified frame", "visual_action": "quiet scene"} for index, segment in enumerate(script["segments"])]}
    storyboard_path = revision / "storyboard" / "scenes.json"
    storyboard_path.parent.mkdir(exist_ok=True)
    storyboard_path.write_text(json.dumps(scenes, ensure_ascii=False), encoding="utf-8")
    report_path = revision / "qa" / "storyboard.json"
    report_path.write_text(json.dumps({"status": "PASS", "scene_count": len(scenes["scenes"]), "duration_seconds": duration}), encoding="utf-8")
    assets = tmp_path / "real-assets"
    assets.mkdir()
    image = assets / "reference.png"
    Image.new("RGB", (64, 64), "navy").save(image)
    frozen = FrozenAsset.approve(image, "independent local review", "LIVE")
    sheet = assets / "sheet.png"
    contact_sheet((frozen,), sheet)
    manifest_path = assets / "manifest.json"
    Manifest.freeze((frozen,), FrozenAsset.approve(sheet, "independent local review", "LIVE"), "independent local review", "LIVE").save(manifest_path)
    stages_path = revision / "stages.json"
    stages = json.loads(stages_path.read_text(encoding="utf-8"))
    stages["storyboard"] = {"status": "PASS", "audio_sha256": audio_sha, "timeline_sha256": sha256(timeline_path.read_bytes()).hexdigest(), "script_sha256": sha256(script_raw).hexdigest(), "source_sha256": source_sha, "sha256": sha256(storyboard_path.read_bytes()).hexdigest(), "qa_sha256": sha256(report_path.read_bytes()).hexdigest()}
    stages_path.write_text(json.dumps(stages), encoding="utf-8")
    return created, revision, manifest_path


def test_real_import_materializes_hash_bound_preproduction_packet_without_activation(tmp_path):
    from src.hybrid.preproduction import import_real_preproduction_handoff

    created, revision, manifest_path = _director_import_inputs(tmp_path)
    result = import_real_preproduction_handoff(tmp_path, created["request_id"], manifest_path)

    assert result["mode"] == "PREPRODUCTION"
    assert result["operations"] == {"provider_dispatch": "BLOCKED", "render": "BLOCKED", "telegram": "BLOCKED", "publication": "BLOCKED"}
    packet = revision / "director-import"
    assert (packet / "receipt.json").is_file()
    assert import_real_preproduction_handoff(tmp_path, created["request_id"], manifest_path) == result


@pytest.mark.parametrize("relative", [
    "assets/1-reference.png", "manifest.json", "assets/2-sheet.binding.json", "index.json",
])
def test_real_import_replay_rejects_tampered_or_missing_materialized_bytes_without_source(tmp_path, relative):
    from src.hybrid.preproduction import (
        import_real_preproduction_handoff,
        verify_real_preproduction_import,
    )

    created, revision, manifest_path = _director_import_inputs(tmp_path)
    import_real_preproduction_handoff(tmp_path, created["request_id"], manifest_path)
    packet = revision / "director-import"
    target = packet / relative
    if relative == "index.json":
        target.write_text("{}", encoding="utf-8")
    else:
        target.unlink()
    # An integrity replay must use only the materialized packet, not the source
    # manifest or source asset tree, which may no longer be available.
    shutil.rmtree(tmp_path / "real-assets")

    with pytest.raises(ValueError, match="(materialized|index|packet)"):
        verify_real_preproduction_import(packet, created["request_id"])


@pytest.mark.parametrize("relative", [
    "assets/1-reference.png", "manifest.json", "assets/2-sheet.png", "assets/2-sheet.binding.json", "index.json",
])
def test_real_import_replay_rejects_adulterated_materialized_asset_manifest_sheet_binding_or_index(tmp_path, relative):
    from src.hybrid.preproduction import (
        import_real_preproduction_handoff,
        verify_real_preproduction_import,
    )

    created, revision, manifest_path = _director_import_inputs(tmp_path)
    import_real_preproduction_handoff(tmp_path, created["request_id"], manifest_path)
    (revision / "director-import" / relative).write_bytes(b"adulterated")

    with pytest.raises(ValueError, match="(materialized|index|packet|binding)"):
        verify_real_preproduction_import(revision / "director-import", created["request_id"])


def test_real_import_replay_rejects_extra_materialized_file(tmp_path):
    from src.hybrid.preproduction import (
        import_real_preproduction_handoff,
        verify_real_preproduction_import,
    )

    created, revision, manifest_path = _director_import_inputs(tmp_path)
    import_real_preproduction_handoff(tmp_path, created["request_id"], manifest_path)
    (revision / "director-import" / "extra.bin").write_bytes(b"unexpected")

    with pytest.raises(ValueError, match="missing or extra"):
        verify_real_preproduction_import(revision / "director-import", created["request_id"])


def test_real_import_receipt_is_explicitly_not_qualified_and_cannot_be_promoted(tmp_path):
    from src.hybrid.preproduction import (
        import_real_preproduction_handoff,
        verify_real_preproduction_import,
    )

    created, revision, manifest_path = _director_import_inputs(tmp_path)
    result = import_real_preproduction_handoff(tmp_path, created["request_id"], manifest_path)
    receipt = json.loads((revision / "director-import" / "receipt.json").read_text(encoding="utf-8"))

    assert result["qualification_status"] == "NOT_QUALIFIED"
    assert receipt["authority"]["qualification_status"] == "NOT_QUALIFIED"
    assert set(receipt["authority"]["reasons"]) >= {
        "TTS_AUTHENTICATED_RECEIPT_REQUIRED", "TRUST_ROOT_AUTHENTICATION_REQUIRED",
    }
    receipt["mode"] = "LIVE"
    (revision / "director-import" / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ValueError, match="receipt"):
        verify_real_preproduction_import(revision / "director-import", created["request_id"])


def test_materialized_import_replays_without_any_origin_files(tmp_path):
    from src.hybrid.preproduction import (
        import_real_preproduction_handoff,
        verify_real_preproduction_import,
    )

    created, revision, manifest_path = _director_import_inputs(tmp_path)
    result = import_real_preproduction_handoff(tmp_path, created["request_id"], manifest_path)
    shutil.rmtree(tmp_path / "real-assets")
    (revision / "inputs" / "source.json").unlink()

    replay = verify_real_preproduction_import(revision / "director-import", created["request_id"])
    assert replay["receipt_sha256"] == result["receipt_sha256"]


def test_post_rename_validation_failure_never_returns_success_and_packet_is_replayable(tmp_path, monkeypatch):
    from src.hybrid import preproduction

    created, revision, manifest_path = _director_import_inputs(tmp_path)
    original = preproduction.verify_real_preproduction_import
    calls = 0

    def crash_after_rename(packet, request_id):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("simulated crash after rename")
        return original(packet, request_id)

    monkeypatch.setattr(preproduction, "verify_real_preproduction_import", crash_after_rename)
    with pytest.raises(RuntimeError, match="after rename"):
        preproduction.import_real_preproduction_handoff(tmp_path, created["request_id"], manifest_path)
    assert (revision / "director-import").is_dir()
    monkeypatch.setattr(preproduction, "verify_real_preproduction_import", original)
    assert original(revision / "director-import", created["request_id"])["mode"] == "PREPRODUCTION"


def test_director_import_facade_keeps_all_irreversible_operations_blocked(tmp_path):
    from src.agents.director import DirectorAgent

    created, _revision, manifest_path = _director_import_inputs(tmp_path)
    result = DirectorAgent.__new__(DirectorAgent).import_preproduction_handoff(tmp_path, created["request_id"], manifest_path)

    assert result["mode"] == "PREPRODUCTION"
    assert set(result["operations"].values()) == {"BLOCKED"}


def test_real_import_rejects_explicit_test_manifest_without_creating_packet(tmp_path):
    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.preproduction import import_real_preproduction_handoff

    created, revision, _manifest_path = _director_import_inputs(tmp_path)
    fixture = tmp_path / "TEST-fixture"
    fixture.mkdir()
    image = fixture / "asset.png"
    Image.new("RGB", (32, 32), "black").save(image)
    asset = FrozenAsset.approve(image, "TEST fixture", "TEST")
    sheet = fixture / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = fixture / "manifest.json"
    Manifest.freeze((asset,), FrozenAsset.approve(sheet, "TEST fixture", "TEST"), "TEST fixture", "TEST").save(manifest)

    with pytest.raises(ValueError, match="TEST manifest"):
        import_real_preproduction_handoff(tmp_path, created["request_id"], manifest)
    assert not (revision / "director-import").exists()


def test_real_import_preserves_prior_receipt_on_valid_manifest_conflict(tmp_path):
    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.preproduction import import_real_preproduction_handoff

    created, revision, manifest_path = _director_import_inputs(tmp_path)
    first = import_real_preproduction_handoff(tmp_path, created["request_id"], manifest_path)
    prior = (revision / "director-import" / "receipt.json").read_bytes()
    image = tmp_path / "real-assets" / "alternate.png"
    Image.new("RGB", (64, 64), "green").save(image)
    asset = FrozenAsset.approve(image, "independent local review", "LIVE")
    sheet = tmp_path / "real-assets" / "alternate-sheet.png"
    contact_sheet((asset,), sheet)
    alternate = tmp_path / "real-assets" / "alternate-manifest.json"
    Manifest.freeze((asset,), FrozenAsset.approve(sheet, "independent local review", "LIVE"), "independent local review", "LIVE").save(alternate)

    with pytest.raises(ValueError, match="conflicts"):
        import_real_preproduction_handoff(tmp_path, created["request_id"], alternate)
    assert (revision / "director-import" / "receipt.json").read_bytes() == prior
    assert first["receipt_sha256"] == json.loads(prior)["receipt_sha256"]


def test_real_import_recovers_staged_crash_without_overwrite(tmp_path, monkeypatch):
    from src.hybrid import preproduction
    from src.hybrid.preproduction import import_real_preproduction_handoff

    created, revision, manifest_path = _director_import_inputs(tmp_path)
    original_replace = preproduction.os.replace

    def crash_publish(source, destination):
        if Path(destination).name == "director-import":
            raise RuntimeError("simulated import crash")
        return original_replace(source, destination)

    monkeypatch.setattr(preproduction.os, "replace", crash_publish)
    with pytest.raises(RuntimeError, match="simulated import crash"):
        import_real_preproduction_handoff(tmp_path, created["request_id"], manifest_path)
    assert not (revision / "director-import").exists()
    assert list(revision.glob(".di-*"))
    monkeypatch.setattr(preproduction.os, "replace", original_replace)
    resumed = import_real_preproduction_handoff(tmp_path, created["request_id"], manifest_path)
    assert (revision / "director-import" / "receipt.json").is_file()
    assert import_real_preproduction_handoff(tmp_path, created["request_id"], manifest_path) == resumed


def test_real_import_rejects_missing_manifest_before_packet_mutation(tmp_path):
    from src.hybrid.preproduction import import_real_preproduction_handoff

    created, revision, _manifest_path = _director_import_inputs(tmp_path)
    with pytest.raises(ValueError, match="manifest"):
        import_real_preproduction_handoff(tmp_path, created["request_id"], tmp_path / "missing-manifest.json")
    assert not (revision / "director-import").exists()


def test_real_handoff_preflight_reports_evidenced_import_without_clearing_other_blockers(tmp_path):
    from src.hybrid.preproduction import (
        build_real_handoff_preflight,
        import_real_preproduction_handoff,
    )

    created, _revision, manifest_path = _director_import_inputs(tmp_path)
    imported = import_real_preproduction_handoff(tmp_path, created["request_id"], manifest_path)
    report = build_real_handoff_preflight(tmp_path, created["request_id"])

    assert report["director_import"] == {"status": "MATERIALIZED", "receipt_sha256": imported["receipt_sha256"]}
    assert next(item for item in report["dependencies"] if item["code"] == "DIRECTOR_IMPORT_CONTRACT_REQUIRED")["status"] == "MATERIALIZED"
    assert report["status"] == "TTS_REQUIRED"
    assert report["operations"] == imported["operations"]


def test_real_handoff_preflight_rejects_tampered_import_receipt(tmp_path):
    from src.hybrid.preproduction import (
        build_real_handoff_preflight,
        import_real_preproduction_handoff,
    )

    created, revision, manifest_path = _director_import_inputs(tmp_path)
    import_real_preproduction_handoff(tmp_path, created["request_id"], manifest_path)
    receipt = revision / "director-import" / "receipt.json"
    receipt.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="receipt hash"):
        build_real_handoff_preflight(tmp_path, created["request_id"])


def test_real_handoff_reports_real_preproduction_readiness_without_authority_or_writes(tmp_path):
    from src.hybrid.preproduction import build_real_handoff_preflight

    created, _source_path, _revision, _review = _prepare(tmp_path)
    root = tmp_path / "PREPRODUCTION" / created["request_id"]
    before = _fingerprint(root)

    result = build_real_handoff_preflight(tmp_path, created["request_id"])

    assert result["status"] == "TTS_REQUIRED"
    assert result["mode"] == "PREPRODUCTION"
    assert result["execution"] == {"paid_execution": "BLOCKED", "prices_usd": None, "providers_called": []}
    assert result["review"]["file_present"] is True
    assert result["review"]["authority"] == "NOT_QUALIFIED"
    assert result["live_authority"] == "NOT_QUALIFIED"
    assert result["inventory"] == {
        "narration": "MEASURED_NON_SILENT_AUDIO_REQUIRED",
        "timing": "COMPLETE_TIMELINE_RECEIPT_REQUIRED",
        "assets": "FROZEN_APPROVED_ASSET_MANIFEST_REQUIRED",
    }
    assert [item["code"] for item in result["dependencies"]] == [
        "TTS_AUTHENTICATED_RECEIPT_REQUIRED",
        "MEASURED_NON_SILENT_AUDIO_REQUIRED",
        "COMPLETE_TIMELINE_RECEIPT_REQUIRED",
        "FROZEN_APPROVED_ASSET_MANIFEST_REQUIRED",
        "PROVIDER_QUALIFICATION_REQUIRED",
        "LIVE_PRICE_AND_OPERATION_AUTHORITY_REQUIRED",
        "DIRECTOR_IMPORT_CONTRACT_REQUIRED",
    ]
    assert result["next_required_action"] == "OBTAIN_TTS_AUTHENTICATED_RECEIPT"
    assert result["bindings"]["source_sha256"] == result["review"]["source_sha256"]
    assert result["bindings"]["script_sha256"] == result["review"]["script_sha256"]
    assert "independent-fixture" not in json.dumps(result)
    assert _fingerprint(root) == before


def test_real_handoff_cli_is_read_only_and_explicitly_not_live_authority(tmp_path):
    from tests.unit.test_autonomous_cli import run_cli

    created, _source_path, _revision, _review = _prepare(tmp_path)
    root = tmp_path / "PREPRODUCTION" / created["request_id"]
    before = _fingerprint(root)

    code, result = run_cli(
        "real-handoff-preflight", "--workspace", str(tmp_path), "--request-id", created["request_id"]
    )

    assert code == 0, result
    assert result["status"] == "TTS_REQUIRED"
    assert result["review"]["authority"] == "NOT_QUALIFIED"
    assert result["operations"] == {"provider_dispatch": "BLOCKED", "render": "BLOCKED", "telegram": "BLOCKED", "publication": "BLOCKED"}
    assert _fingerprint(root) == before


def test_real_handoff_rejects_test_request_before_review_or_asset_reads(tmp_path, monkeypatch):
    from src.hybrid.autonomous import _digest_json
    from src.hybrid.preproduction import build_real_handoff_preflight

    created, _source_path, revision, review = _prepare(tmp_path)
    root = tmp_path / "PREPRODUCTION" / created["request_id"]
    request_path = root / "request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["execution"]["mode"] = "TEST"
    binding = dict(request)
    binding.pop("package_sha256")
    request["package_sha256"] = _digest_json(binding)
    request_path.write_text(json.dumps(request), encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def forbid_review_or_asset(self, *args, **kwargs):
        if self == review or self.parent.name in {"assets", "audio", "storyboard"}:
            raise AssertionError("TEST input reached review or asset inspection")
        return original_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", forbid_review_or_asset)
    with pytest.raises(ValueError, match="PREPRODUCTION"):
        build_real_handoff_preflight(tmp_path, created["request_id"])
    assert not (revision / "audio").exists()


def test_real_handoff_rejects_forged_active_source_before_review_reads(tmp_path, monkeypatch):
    from src.hybrid.preproduction import build_real_handoff_preflight

    created, _source_path, revision, review_path = _prepare(tmp_path)
    source_path = revision / "inputs" / "source.json"
    source_path.write_bytes(b"forged source")
    original_read_bytes = Path.read_bytes

    def forbid_review(self, *args, **kwargs):
        if self == review_path:
            raise AssertionError("review was read after source hash mismatch")
        return original_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", forbid_review)
    with pytest.raises(ValueError, match="stored source bytes differ"):
        build_real_handoff_preflight(tmp_path, created["request_id"])


def test_real_handoff_rejects_forged_review_hash_before_inventory_reads(tmp_path, monkeypatch):
    from src.hybrid.preproduction import build_real_handoff_preflight

    created, _source_path, revision, review_path = _prepare(tmp_path)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["script_sha256"] = "0" * 64
    review_path.write_text(json.dumps(review), encoding="utf-8")
    original_scandir = __import__("os").scandir

    def forbid_inventory(path):
        if Path(path) == revision / "inputs":
            raise AssertionError("inventory inspected before forged review rejection")
        return original_scandir(path)

    monkeypatch.setattr("src.hybrid.preproduction.os.scandir", forbid_inventory)
    with pytest.raises(ValueError, match="review script hash"):
        build_real_handoff_preflight(tmp_path, created["request_id"])


def test_real_handoff_rejects_mismatched_active_revision_path(tmp_path):
    from src.hybrid.autonomous import _digest_json
    from src.hybrid.preproduction import build_real_handoff_preflight

    created, _source_path, _revision, _review = _prepare(tmp_path)
    request_path = tmp_path / "PREPRODUCTION" / created["request_id"] / "request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["execution"]["active_revision"] = "revisions/" + "0" * 16
    binding = dict(request)
    binding.pop("package_sha256")
    request["package_sha256"] = _digest_json(binding)
    request_path.write_text(json.dumps(request), encoding="utf-8")

    with pytest.raises(ValueError, match="active revision pointer"):
        build_real_handoff_preflight(tmp_path, created["request_id"])


def test_real_handoff_rejects_reparse_review_path_before_read(tmp_path, monkeypatch):
    from src.hybrid.preproduction import build_real_handoff_preflight

    created, _source_path, _revision, review_path = _prepare(tmp_path)
    outside = tmp_path / "outside-review.json"
    outside.write_text("{}", encoding="utf-8")
    review_path.unlink()
    review_path.symlink_to(outside)
    original_read_bytes = Path.read_bytes

    def forbid_linked_review(self, *args, **kwargs):
        if self in {review_path, outside}:
            raise AssertionError("linked review bytes were read")
        return original_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", forbid_linked_review)
    with pytest.raises(ValueError, match="reparse"):
        build_real_handoff_preflight(tmp_path, created["request_id"])


def test_fresh_processes_concurrently_materialize_one_real_import_and_one_verified_replay(tmp_path):
    """The OS lock serializes two fresh-process imports without test doubles."""
    created, revision, manifest_path = _director_import_inputs(tmp_path)
    packet = revision / "director-import"
    audit = tmp_path / "replace-audit"
    audit.mkdir()
    child = '''
import json
import os
import socket
import sys
from pathlib import Path
from src.hybrid import preproduction

workspace, request_id, manifest, port, audit_dir, published_packet = sys.argv[1:]
audit_dir = Path(audit_dir)
published_packet = Path(published_packet)
original_replace = preproduction.os.replace
publication_count = 0

def audited_replace(source, destination):
    global publication_count
    result = original_replace(source, destination)
    if Path(destination) == published_packet:
        publication_count += 1
        marker = audit_dir / f"{os.getpid()}-{publication_count}.replace"
        with open(marker, "xb"):
            pass
    return result

preproduction.os.replace = audited_replace
with socket.create_connection(("127.0.0.1", int(port)), timeout=20) as barrier:
    barrier.settimeout(20)
    barrier.sendall(b"ready")
    if barrier.recv(2) != b"go":
        raise RuntimeError("barrier release failed")
result = preproduction.import_real_preproduction_handoff(Path(workspace), request_id, Path(manifest))
print(json.dumps({
    "mode": result["mode"],
    "receipt_sha256": result["receipt_sha256"],
    "qualification_status": result["qualification_status"],
}, sort_keys=True))
'''
    children = []
    peers = []
    outputs = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(2)
            listener.settimeout(30)
            port = listener.getsockname()[1]
            command = [
                sys.executable, "-c", child, str(tmp_path), created["request_id"],
                str(manifest_path), str(port), str(audit), str(packet),
            ]
            children = [
                subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for _ in range(2)
            ]
            for _ in children:
                peer, _address = listener.accept()
                peer.settimeout(20)
                peers.append(peer)
            for peer in peers:
                assert peer.recv(5) == b"ready"
            for peer in peers:
                peer.sendall(b"go")

        for process in children:
            stdout, stderr = process.communicate(timeout=30)
            # Do not expose a child traceback (which could carry a filesystem path or
            # provider-derived data) if this assertion fails.
            assert process.returncode == 0, (
                f"import child failed: exit={process.returncode}, "
                f"stdout_sha256={sha256(stdout.encode()).hexdigest()}, "
                f"stderr_sha256={sha256(stderr.encode()).hexdigest()}"
            )
            assert stderr == ""
            assert "independent-fixture" not in stdout
            assert str(tmp_path) not in stdout
            outputs.append(json.loads(stdout))
    finally:
        for peer in peers:
            try:
                peer.close()
            except OSError:
                pass
        for process in children:
            if process.poll() is None:
                process.kill()
        for process in children:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)

    assert all(output["mode"] == "PREPRODUCTION" for output in outputs)
    assert all(output["qualification_status"] == "NOT_QUALIFIED" for output in outputs)
    assert len({output["receipt_sha256"] for output in outputs}) == 1
    # The pass-through audit records each successful production os.replace only
    # when its destination is the published director-import directory.
    assert len(list(audit.glob("*.replace"))) == 1
    assert packet.is_dir()
    assert [path.name for path in revision.iterdir() if path.name == "director-import"] == ["director-import"]
    assert not list(revision.glob(".di-*"))
    receipt = json.loads((packet / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["receipt_sha256"] == outputs[0]["receipt_sha256"]
    # Both child outcomes are independently replayed from the published bytes.
    from src.hybrid.preproduction import verify_real_preproduction_import

    assert verify_real_preproduction_import(packet, created["request_id"])["receipt_sha256"] == outputs[0]["receipt_sha256"]
