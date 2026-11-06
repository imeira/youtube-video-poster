"""TDD acceptance for the read-only episode policy status projection."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

EPISODE = "JACO_GENESIS28"


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _write(path: Path, document: dict) -> tuple[str, str]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(document))
    return str(path), hashlib.sha256(path.read_bytes()).hexdigest()


def _config(
    tmp_path: Path,
    *,
    queue_status: str = "WAITING",
    policy_mode: str = "LIVE",
    delivery_ready: bool = False,
) -> tuple[Path, Path]:
    now = datetime.now(UTC).isoformat()
    asset_state = "DELIVERED_FOR_REVIEW" if delivery_ready else "NONE"
    documents = {
        "policy": {"schema": "episode-policy/v1", "episode_id": EPISODE, "mode": policy_mode, "external_trust": False},
        "queue": {"schema": "episode-task-queue-snapshot-v1", "episode_id": EPISODE, "status": queue_status, "generated_at_utc": now},
        "runner": {"schema": "runner-receipt/v1", "episode_id": EPISODE, "state": "WAITING_HUMAN_THUMBNAIL"},
        "qa": {"schema": "visual-qa/v1", "episode_id": EPISODE, "verdict": "PROMOTED" if delivery_ready else "NOT_PROMOTED"},
        "narrative": {"schema": "narrative-status/v1", "episode_id": EPISODE, "final_approved": delivery_ready},
        "assets": {"schema": "asset-status/v1", "episode_id": EPISODE, "identity": "BOUND" if delivery_ready else "UNAVAILABLE", "license": "BOUND" if delivery_ready else "UNAVAILABLE", "thumbnail": asset_state, "video": "NONE", "publication": "NONE"},
        "billing": {"schema": "ledger-projection/v1", "episode_id": EPISODE, "settlement": "SETTLED", "actual_cost_usd": "0.048", "remaining_usd": "9.952", "authenticated": True},
    }
    sources = {}
    for name, document in documents.items():
        path, digest = _write(tmp_path / f"{name}.json", document)
        sources[name] = {"path": path, "sha256": digest}
    config = tmp_path / "config.json"
    _write(config, {"schema": "episode-policy-status-config/v1", "episode_id": EPISODE, "max_age_seconds": 300, "sources": sources})
    return config, tmp_path / "episode-policy-status.json"


def test_absent_media_identity_or_license_is_technical_block_not_human_wait(tmp_path: Path):
    from src.hybrid.episode_policy_status import EpisodePolicyStatusProjection

    config, output = _config(tmp_path)
    result = EpisodePolicyStatusProjection(config, output).publish()

    assert result["status"] == "BLOCKED_DELIVERY_READINESS"
    assert result["live_dispatch"] is False
    assert result["actual_cost_usd"] == "0.048"  # Synthetic fixture, not deployed status.
    assert result["remaining_usd"] == "9.952"
    assert result["visual_qa"] == "NOT_PROMOTED"
    assert result["narrator_final_approved"] is False
    assert result["identity"] == "UNAVAILABLE"
    assert result["license"] == "UNAVAILABLE"
    assert result["thumbnail"] == result["video"] == result["publication"] == "NONE"
    assert result["pending_human_approvals"] is True
    assert result["generation"] == 1
    assert result["status"] != "WAITING_HUMAN_GATE"


def test_waiting_human_gate_requires_a_delivery_ready_artifact(tmp_path: Path):
    from src.hybrid.episode_policy_status import EpisodePolicyStatusProjection

    config, output = _config(tmp_path, delivery_ready=True)

    assert EpisodePolicyStatusProjection(config, output).publish()["status"] == "WAITING_HUMAN_GATE"


def test_projection_rejects_tamper_foreign_episode_and_unknown_worker(tmp_path: Path):
    from src.hybrid.episode_policy_status import EpisodePolicyStatusProjection

    config, output = _config(tmp_path)
    projection = EpisodePolicyStatusProjection(config, output)
    assert projection.publish()["status"] == "BLOCKED_DELIVERY_READINESS"

    qa = tmp_path / "qa.json"
    qa.write_bytes(_canonical({"schema": "visual-qa/v1", "episode_id": EPISODE, "verdict": "PASS"}))
    assert projection.publish()["status"] == "STALE"

    config, output = _config(tmp_path / "foreign")
    foreign = tmp_path / "foreign" / "assets.json"
    foreign_doc = json.loads(foreign.read_text())
    foreign_doc["episode_id"] = "EP12"
    path, digest = _write(foreign, foreign_doc)
    config_doc = json.loads(config.read_text())
    config_doc["sources"]["assets"] = {"path": path, "sha256": digest}
    _write(config, config_doc)
    assert EpisodePolicyStatusProjection(config, output).publish()["status"] == "STALE"

    config, output = _config(tmp_path / "unknown", queue_status="RUNNING")
    assert EpisodePolicyStatusProjection(config, output).publish()["status"] == "UNKNOWN_REQUIRES_RECONCILIATION"


def test_projection_read_is_filesystem_only_and_does_not_open_sqlite(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from src.hybrid.episode_policy_status import EpisodePolicyStatusProjection

    config, output = _config(tmp_path)
    projection = EpisodePolicyStatusProjection(config, output)
    projection.publish()
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    monkeypatch.setattr(sqlite3, "connect", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("SQLite must not open")))
    assert projection.inspect()["status"] == "BLOCKED_DELIVERY_READINESS"
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert after == before


def test_worker_status_with_prebuilt_projection_never_opens_queue_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    from src.hybrid.autonomous_cli import main
    from src.hybrid.episode_policy_status import EpisodePolicyStatusProjection

    config, output = _config(tmp_path)
    EpisodePolicyStatusProjection(config, output).publish()
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("queue SQLite must not open")),
    )

    assert main([
        "worker-status", "--workspace", str(tmp_path), "--request-id", "request",
        "--policy-status-snapshot", str(output),
    ]) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED_DELIVERY_READINESS"
