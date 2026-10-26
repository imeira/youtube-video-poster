"""Read-only operational handoff from a REAL preproduction revision."""

from __future__ import annotations

import json
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
