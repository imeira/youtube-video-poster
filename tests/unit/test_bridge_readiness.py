"""Request-scoped readiness contracts for the disabled Hermes approval bridge."""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
REVISION_ID = "818556d97edb648f"


def _validator_module():
    path = Path(__file__).parents[2] / "integrations/hermes/hermes_studio_approval_bridge/deployment/validate_readiness.py"
    spec = importlib.util.spec_from_file_location("bridge_readiness", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _request_tree(tmp_path: Path) -> tuple[Path, Path]:
    request_root = tmp_path / "r"
    revision_root = request_root / "revisions" / REVISION_ID
    revision_root.mkdir(parents=True)
    source = revision_root / "inputs" / "source.json"
    source.parent.mkdir()
    source.write_text('{"story":"Jacó — Gênesis 28"}\n', encoding="utf-8")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    (request_root / "request.json").write_text(json.dumps({
        "request_id": REQUEST_ID,
        "mode": "PREPRODUCTION",
        "execution": {"active_revision": f"revisions/{REVISION_ID}", "source_sha256": source_hash},
    }), encoding="utf-8")
    gate = request_root / "derived" / f"financial_reconciliation_{REQUEST_ID}.json"
    gate.parent.mkdir(parents=True)
    gate.write_text(json.dumps({
        "request_hash": REQUEST_ID,
        "revision": REVISION_ID,
        "status": "APPROVED_USD10",
        "approval_scope": "FINANCIAL_ONLY",
        "authorizes_generation": False,
        "command_platform_message_id": "5046",
    }), encoding="utf-8")
    return request_root, source


def test_readiness_binds_literal_request_revision_and_financial_gate_only(tmp_path: Path):
    validator = _validator_module()
    request_root, source = _request_tree(tmp_path)
    config = {
        "request_id": REQUEST_ID,
        "revision_id": REVISION_ID,
        "request_root": str(request_root),
        "gate_artifact": str(request_root / "derived" / f"financial_reconciliation_{REQUEST_ID}.json"),
        "studio_config_path": str(tmp_path / "studio.yaml"),
        "journal_path": str(tmp_path / "state" / "journal.sqlite3"),
        "registry_path": str(tmp_path / "state" / "registry.sqlite3"),
        "chat_id": "141718934",
        "trusted_sender_ids": ["141718934"],
    }
    (tmp_path / "studio.yaml").write_text("approval_delivery:\n  chat_id: '141718934'\n  trusted_sender_ids: ['141718934']\n", encoding="utf-8")

    report = validator.build_report(config=config, hermes_home=tmp_path / "hermes")

    assert report["scope"]["request_id"] == REQUEST_ID
    assert report["scope"]["revision_id"] == REVISION_ID
    assert report["request_binding"]["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert report["financial_gate"]["status"] == "APPROVED_USD10"
    assert report["financial_gate"]["additional_approval_required"] is False
    assert report["future_media_gates"]["final_jaco_video_exists"] is False
    assert report["future_media_gates"]["deployment_blocking"] is False
    assert "5046" not in json.dumps(report["future_media_gates"])
    assert "EP12" not in json.dumps(report)
    assert "EP11" not in json.dumps(report)


def test_readiness_rejects_a_foreign_gate_artifact_even_when_it_is_approved(tmp_path: Path):
    validator = _validator_module()
    request_root, _ = _request_tree(tmp_path)
    foreign_gate = tmp_path / "foreign-approved.json"
    foreign_gate.write_text(json.dumps({
        "request_hash": "f" * 64,
        "revision": REVISION_ID,
        "status": "APPROVED_USD10",
        "approval_scope": "FINANCIAL_ONLY",
        "authorizes_generation": False,
    }), encoding="utf-8")
    config = {
        "request_id": REQUEST_ID,
        "revision_id": REVISION_ID,
        "request_root": str(request_root),
        "gate_artifact": str(foreign_gate),
        "studio_config_path": str(tmp_path / "studio.yaml"),
        "journal_path": str(tmp_path / "state" / "journal.sqlite3"),
        "registry_path": str(tmp_path / "state" / "registry.sqlite3"),
        "chat_id": "141718934",
        "trusted_sender_ids": ["141718934"],
    }
    (tmp_path / "studio.yaml").write_text("approval_delivery: {}\n", encoding="utf-8")

    report = validator.build_report(config=config, hermes_home=tmp_path / "hermes")

    assert report["financial_gate"]["valid"] is False
    assert report["financial_gate"]["reason"] == "gate_request_id_mismatch"



def test_disabled_deployment_plan_pins_the_ci_verified_studio_fix():
    config_path = (
        Path(__file__).parents[2]
        / "integrations/hermes/hermes_studio_approval_bridge/deployment"
        / "jaco-genesis28-bridge-config.json"
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    commit = "4cc03a48728e3b23d1626265c542b9a39ffa2c11"

    assert config["enabled"] is False
    assert config["studio_checkout_commit"] == commit
    assert config["studio_checkout_root"] == f"C:/HermesStudio/runtime/{commit}"
