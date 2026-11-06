from __future__ import annotations

import json
import sqlite3
import threading
from hashlib import sha256
from pathlib import Path

import pytest

REQUEST = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
EPISODE = "JACO_GENESIS28"


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def _root(tmp_path: Path, records: list[dict], *, source_hash: str = "a" * 64) -> Path:
    root = tmp_path / "production" / "jaco-flux3"
    _write(root / "financial-record-source.json", {
        "schema": "jaco-factory-financial-reconciliation/1", "status": "APPROVED_USD10",
        "approval_scope": "FINANCIAL_ONLY", "authorizes_generation": False, "currency": "USD",
        "total_episode_cap": "10.00", "request_hash": REQUEST, "revision": "818556d97edb648f",
    })
    _write(root / "user-mandate.json", {"schema": "JACO_USER_EXECUTION_MANDATE/2"})
    classes = ("provider_receipts", "executor_intents", "executor_wal", "standing_ledger_claims", "billing_readbacks", "reconciliation_records")
    journals = []
    for record_class in classes:
        path = root / "journals" / f"{record_class}.json"
        _write(path, {"schema": "JACO_CANONICAL_FINANCIAL_JOURNAL/1", "request_id": REQUEST,
                      "episode_id": EPISODE, "records": records if record_class == "provider_receipts" else []})
        journals.append({"class": record_class, "path": str(path.relative_to(root)),
                         "schema": "JACO_CANONICAL_FINANCIAL_JOURNAL/1"})
    _write(root / "production-composition.json", {
        "schema": "JACO_FLUX3_FIXED_PRODUCTION_COMPOSITION/1", "financial_record": "financial-record-source.json",
        "canonical_financial_journals": journals,
        "protected_financial_sources": [{"path": "financial-record-source.json", "sha256": source_hash}],
    })
    # The test fixture pins the actual bytes, rather than treating the manifest field as decorative.
    config = json.loads((root / "production-composition.json").read_text())
    config["protected_financial_sources"][0]["sha256"] = sha256((root / "financial-record-source.json").read_bytes()).hexdigest()
    _write(root / "production-composition.json", config)
    return root


def test_complete_zero_history_bootstraps_one_immutable_usd10_ledger(tmp_path):
    from src.hybrid.jaco_flux3_budget_bootstrap import bootstrap_canonical_budget

    root = _root(tmp_path, [])
    result = bootstrap_canonical_budget(root=root)

    assert result["status"] == "PASS_ZERO_HISTORY"
    assert result["available_usd"] == "10.000000"
    assert (root / "ledger" / "standing-budget.sqlite3").exists()
    assert bootstrap_canonical_budget(root=root)["bootstrap_receipt_sha256"] == result["bootstrap_receipt_sha256"]


def test_concurrent_bootstrap_has_one_immutable_receipt(tmp_path):
    from src.hybrid.jaco_flux3_budget_bootstrap import bootstrap_canonical_budget

    root = _root(tmp_path, [])
    results: list[dict] = []

    def run() -> None:
        results.append(bootstrap_canonical_budget(root=root))

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 2
    assert {result["bootstrap_receipt_sha256"] for result in results}.__len__() == 1


@pytest.mark.parametrize("records, reason", [
    ([{"kind": "provider_receipt", "state": "SETTLED", "actual_usd": "0.010000"}], "prior paid record"),
    ([{"kind": "executor_wal", "state": "UNKNOWN_0_OR_1"}], "ambiguous or unsettled"),
])
def test_history_or_unknown_never_initializes_usd10(tmp_path, records, reason):
    from src.hybrid.jaco_flux3_budget_bootstrap import bootstrap_canonical_budget

    root = _root(tmp_path, records)
    result = bootstrap_canonical_budget(root=root)

    assert result["status"] == "BLOCKED_UNKNOWN"
    assert result["available_usd"] == "0.000000"
    assert reason in result["blocking_reason"]
    assert not (root / "ledger" / "standing-budget.sqlite3").exists()


def test_absent_configured_journal_is_incomplete_not_zero(tmp_path):
    from src.hybrid.jaco_flux3_budget_bootstrap import bootstrap_canonical_budget

    root = _root(tmp_path, [])
    missing = root / "journals" / "provider_receipts.json"
    missing.unlink()
    result = bootstrap_canonical_budget(root=root)

    assert result["status"] == "BLOCKED_UNKNOWN"
    assert result["missing_sources"] == [str(missing)]
    assert result["available_usd"] == "0.000000"


def test_changed_financial_source_collides_after_bootstrap(tmp_path):
    from src.hybrid.jaco_flux3_budget_bootstrap import (
        BootstrapCollision,
        bootstrap_canonical_budget,
    )

    root = _root(tmp_path, [])
    bootstrap_canonical_budget(root=root)
    source = json.loads((root / "financial-record-source.json").read_text())
    source["revision"] = "changed"
    _write(root / "financial-record-source.json", source)

    with pytest.raises(BootstrapCollision, match="financial source"):
        bootstrap_canonical_budget(root=root)


def test_existing_reserved_claim_reports_actual_remaining_budget_not_full_cap(tmp_path):
    from src.hybrid.jaco_flux3_budget_bootstrap import bootstrap_canonical_budget
    from src.hybrid.standing_budget import StandingBudgetLedger

    root = _root(tmp_path, [])
    first = bootstrap_canonical_budget(root=root)
    ledger = StandingBudgetLedger(
        first["ledger_path"], policy_sha256=first["policy_sha256"],
        episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="LIVE",
    )
    ledger.reserve("JACO_GEN28_CANONICAL_001", EPISODE, "blackforestlabs/flux-3/text-to-image", "0.048",
                   settlement_mode="FAL_READBACK_V2", auth_scope="JACO_TEST", authority_mode="LIVE")

    resumed = bootstrap_canonical_budget(root=root)

    assert resumed["status"] == "PASS_RECONCILED"
    assert resumed["available_usd"] == "9.952000"


def test_existing_own_claim_allows_get_only_recovery_without_second_claim(tmp_path):
    from src.hybrid.jaco_flux3_budget_bootstrap import bootstrap_canonical_budget
    from src.hybrid.standing_budget import StandingBudgetLedger

    root = _root(tmp_path, [])
    first = bootstrap_canonical_budget(root=root)
    ledger = StandingBudgetLedger(
        first["ledger_path"], policy_sha256=first["policy_sha256"],
        episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="LIVE",
    )
    original = ledger.reserve("JACO_GEN28_CANONICAL_001", EPISODE, "blackforestlabs/flux-3/text-to-image", "0.048",
                              settlement_mode="FAL_READBACK_V2", auth_scope="JACO_TEST", authority_mode="LIVE")

    recovered = bootstrap_canonical_budget(root=root)
    same = ledger.reserve("JACO_GEN28_CANONICAL_001", EPISODE, "blackforestlabs/flux-3/text-to-image", "0.048",
                          settlement_mode="FAL_READBACK_V2", auth_scope="JACO_TEST", authority_mode="LIVE")

    assert recovered["status"] == "PASS_RECONCILED"
    assert recovered["available_usd"] == "9.952000"
    assert same == original
    with sqlite3.connect(first["ledger_path"]) as db:
        assert db.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1


def test_unrecognized_financial_record_state_blocks_instead_of_counting_as_zero(tmp_path):
    from src.hybrid.jaco_flux3_budget_bootstrap import bootstrap_canonical_budget

    root = _root(tmp_path, [{"kind": "provider_receipt", "state": "COMPLETE"}])

    result = bootstrap_canonical_budget(root=root)

    assert result["status"] == "BLOCKED_UNKNOWN"
    assert "unrecognized record" in result["blocking_reason"]
