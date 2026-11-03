from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REQUEST_ID = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
REVISION = "818556d97edb648f"
EPISODE = "JACO_GENESIS28"


def _write(path: Path, body: dict) -> Path:
    path.write_text(json.dumps(body, sort_keys=True), encoding="utf-8")
    return path


def _source(tmp_path: Path, name: str, body: dict) -> dict:
    path = _write(tmp_path / f"{name}.json", body)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _inputs(tmp_path: Path) -> dict:
    scope = {"request_id": REQUEST_ID, "active_revision": REVISION, "episode": EPISODE}
    return {
        "manifest": _source(tmp_path, "manifest", {"schema": "manifest/v1", "scope": scope, "assets": []}),
        "policy_qa": _source(tmp_path, "policy", {"schema": "policy-qa/v1", "scope": scope, "verdict": "PASS"}),
        "ledger": _source(tmp_path, "ledger", {"schema": "ledger/v1", "scope": scope, "actual_cost_usd": None, "pending_cost_usd": None}),
        "provider_eligibility": _source(tmp_path, "provider", {"schema": "provider-eligibility/v1", "scope": scope, "status": "BLOCKED"}),
    }


def test_canonical_projection_joins_exact_jaco_sources_and_moves_legacy_status_to_pointer(tmp_path: Path):
    from src.hybrid.canonical_status import CanonicalStatusProducer

    sources = _inputs(tmp_path)
    old_root = tmp_path / "PREPRODUCTION" / REQUEST_ID
    old_root.mkdir(parents=True)
    historic = old_root / "status.json"
    historic.write_text('{"historic":true}', encoding="utf-8")
    result = CanonicalStatusProducer(tmp_path).publish(sources)

    document = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
    assert document["scope"] == {"request_id": REQUEST_ID, "active_revision": REVISION, "episode": EPISODE}
    assert document["canonical_status"] == "BLOCKED"
    assert set(document["sources"]) == {"manifest", "policy_qa", "ledger", "provider_eligibility"}
    assert historic.read_text(encoding="utf-8") == '{"historic":true}'
    pointer = json.loads((old_root / "canonical-status-pointer.json").read_text(encoding="utf-8"))
    assert pointer["canonical_status_path"] == result["path"]


@pytest.mark.parametrize("mutate", [
    lambda sources: sources["manifest"].update({"sha256": "0" * 64}),
    lambda sources: json.loads(Path(sources["policy_qa"]["path"]).read_text()).update({"ignored": "no-op"}),
])
def test_canonical_projection_rejects_modified_source_bytes(tmp_path: Path, mutate):
    from src.hybrid.canonical_status import CanonicalStatusProducer

    sources = _inputs(tmp_path)
    if mutate.__code__.co_consts[-1] == "no-op":
        body = json.loads(Path(sources["policy_qa"]["path"]).read_text())
        body["verdict"] = "FAIL"
        _write(Path(sources["policy_qa"]["path"]), body)
    else:
        mutate(sources)
    with pytest.raises(ValueError, match="hash"):
        CanonicalStatusProducer(tmp_path).publish(sources)


def test_canonical_projection_rejects_foreign_ep12_scope_missing_field_and_source_conflict(tmp_path: Path):
    from src.hybrid.canonical_status import CanonicalStatusProducer

    for mutation, message in (
        (lambda values: _write(Path(values["manifest"]["path"]), {"schema": "manifest/v1", "scope": {"request_id": REQUEST_ID, "active_revision": REVISION, "episode": "EP12"}, "assets": []}), "foreign"),
        (lambda values: _write(Path(values["ledger"]["path"]), {"schema": "ledger/v1", "scope": {"request_id": REQUEST_ID, "active_revision": REVISION, "episode": EPISODE}, "actual_cost_usd": None}), "missing"),
        (lambda values: _write(Path(values["provider_eligibility"]["path"]), {"schema": "provider-eligibility/v1", "scope": {"request_id": REQUEST_ID, "active_revision": "other", "episode": EPISODE}, "status": "BLOCKED"}), "source scope conflict"),
    ):
        sources = _inputs(tmp_path)
        mutation(sources)
        # These are authenticated source declarations: update the declared byte
        # hash so this slice reaches the semantic scope/field validation.
        for declaration in sources.values():
            path = Path(declaration["path"])
            declaration["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        with pytest.raises(ValueError, match=message):
            CanonicalStatusProducer(tmp_path).publish(sources)


def test_projection_is_deterministic_and_rejects_stale_source_status(tmp_path: Path):
    from src.hybrid.canonical_status import CanonicalStatusProducer

    sources = _inputs(tmp_path)
    producer = CanonicalStatusProducer(tmp_path, max_source_age_seconds=60)
    first = producer.project(sources, generated_at=datetime(2026, 11, 3, tzinfo=UTC))
    second = producer.project(sources, generated_at=datetime(2026, 11, 3, tzinfo=UTC))
    assert first == second
    stale = json.loads(Path(sources["provider_eligibility"]["path"]).read_text())
    stale["generated_at_utc"] = (datetime.now(UTC) - timedelta(seconds=61)).isoformat()
    _write(Path(sources["provider_eligibility"]["path"]), stale)
    sources["provider_eligibility"]["sha256"] = hashlib.sha256(Path(sources["provider_eligibility"]["path"]).read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="stale"):
        producer.project(sources, now=datetime.now(UTC))


def test_metrics_only_report_actual_measured_fields_and_unknowns(tmp_path: Path):
    from src.hybrid.status_metrics import StatusMetricsLedger

    ledger = StatusMetricsLedger(tmp_path / "metrics.sqlite3")
    assert ledger.record({"event_id": "a", "request_id": REQUEST_ID, "stage": "image", "queue_wait_s": 2.0, "stage_latency_s": 3.0, "actual_cost_usd": None, "accepted_asset_count": 1, "retries": 0, "tokens_input": 5, "quota_units": 7, "status": "PENDING"})
    assert not ledger.record({"event_id": "a", "request_id": REQUEST_ID, "stage": "image", "queue_wait_s": 2.0, "stage_latency_s": 3.0, "actual_cost_usd": None, "accepted_asset_count": 1, "retries": 0, "tokens_input": 5, "quota_units": 7, "status": "PENDING"})
    report = ledger.report()
    assert report["actual_cost_usd"] is None
    assert report["pending_requests"] == 1
    assert report["stage_latency_s"] == 3.0
    assert report["queue_wait_s"] == 2.0
    assert report["accepted_asset_count"] == 1
    assert report["tokens_input"] == 5
    assert report["quota_units"] == 7


def test_test_only_benchmark_labels_fake_latency_and_shows_rolling_cache_effect():
    from src.hybrid.status_metrics import benchmark_rolling_vs_barrier

    result = benchmark_rolling_vs_barrier([0.01, 0.03, 0.02], cache_hits={1})
    assert result["mode"] == "TEST_ONLY_FAKE_PROVIDER_LATENCY"
    assert result["real_cost_gain"] == "NOT_MEASURED"
    assert result["rolling_elapsed_s"] < result["barrier_elapsed_s"]
    assert result["cache_hits"] == 1


def test_canonical_status_consumer_reads_only_published_json(tmp_path: Path):
    from src.hybrid.canonical_status import (
        CanonicalStatusConsumer,
        CanonicalStatusProducer,
    )

    sources = _inputs(tmp_path)
    published = CanonicalStatusProducer(tmp_path).publish(sources)
    assert CanonicalStatusConsumer.inspect(Path(published["path"]))["canonical_status"] == "BLOCKED"
    assert CanonicalStatusConsumer.inspect(tmp_path / "missing.json") == {"status": "STALE", "reason": "MISSING"}


def test_production_facade_projects_exact_executor_receipts_not_caller_pass_flags(tmp_path: Path):
    """The façade derives Jacó status/metrics from the durable executor ledger."""
    from src.hybrid.jaco_production_facade import JacoProductionFacade

    request_id = "a" * 64
    receipt = {
        "request_id": request_id,
        "status": "COMPLETE",
        "mode": "LIVE",
        "result_sha256": "b" * 64,
        "actual_cost": "0.04",
        "charged": "0.04",
        "authorization": {"reviewer": "CURRENT_POLICY_MACHINE_ADMISSION"},
    }

    class Executor:
        def inspect(self, candidate):
            assert candidate == request_id
            return receipt

    class Pipeline:
        run = type("Run", (), {"executor": Executor()})()

        @staticmethod
        def baseline_jobs():
            return (type("Job", (), {"request_id": request_id})(),)

    published = JacoProductionFacade(tmp_path).publish_from_executor(Pipeline())

    snapshot = json.loads(Path(published["status"]["path"]).read_text(encoding="utf-8"))
    assert snapshot["canonical_status"] == "BLOCKED"  # QA is not fabricated.
    assert snapshot["sources"]["ledger"]["schema"] == "ledger/v1"
    assert published["metrics"]["actual_cost_usd"] == "0.04"
    assert published["metrics"]["pending_requests"] == 0


def test_production_facade_fails_closed_for_unknown_executor_cost(tmp_path: Path):
    from src.hybrid.jaco_production_facade import JacoProductionFacade

    request_id = "c" * 64

    class Executor:
        @staticmethod
        def inspect(_candidate):
            return {
                "request_id": request_id, "status": "COMPLETE", "mode": "LIVE",
                "result_sha256": "d" * 64, "actual_cost": None, "charged": "0.04",
            }

    class Pipeline:
        run = type("Run", (), {"executor": Executor()})()

        @staticmethod
        def baseline_jobs():
            return (type("Job", (), {"request_id": request_id})(),)

    with pytest.raises(ValueError, match="actual_cost"):
        JacoProductionFacade(tmp_path).publish_from_executor(Pipeline())
