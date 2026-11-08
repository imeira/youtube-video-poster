from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

# TEST-ONLY immutable fixture manifest.  These pins are deliberately literals:
# fixture maintenance must review an intentional update when the protected module
# or fixture payload changes.  They are not LIVE authority or production config.
TEST_FIXTURE_MEDIA_BYTES = b"hermetic canonical model-sheet media"
TEST_FIXTURE_MEDIA_SHA256 = "bbac93dee93c278c2f6e7cd59c82aea5f07eb5623332a168cc6d0efc441c85fd"
TEST_FIXTURE_PRODUCTION_SOURCE_SHA256 = "6b7c4aeb15e1bcee528e69905a52c04182fa686d82915d84dd8f536171c66d12"
TEST_FIXTURE_AUTH_SCOPE = "JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001:c997b15604f763b9e02832b4b84a01ce89bf66b43916da9803a67dc5928848bd"
TEST_FIXTURE_PROVIDER_RECEIPT_JSON = ("{\"actual_billed_amount\":\"UNKNOWN_UNTIL_BILLING_RECONCILIATION\",\"kind\":\"PNG_RESULT\",\"sha256\":\"bbac93dee93c278c2f6e7cd59c82aea5f07eb5623332a168cc6d0efc441c85fd\"}")
TEST_FIXTURE_SETTLEMENT_RECEIPT_JSON = ("{\"auth_scope\":\"JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001:c997b15604f763b9e02832b4b84a01ce89bf66b43916da9803a67dc5928848bd\",\"authority\":\"TRANSPORT_AUTHENTICATED_FAL_READBACK\",\"authority_mode\":\"LIVE\",\"cost_total_micro_usd\":\"48000\",\"currency\":\"USD\",\"endpoint_id\":\"blackforestlabs/flux-3/text-to-image\",\"evidence_hash\":\"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\",\"local_request_id\":\"c81bec2124005d306d2f843ac7b5d5c747feb254d090610582d79099f0535cc1\",\"provenance\":\"FAL_BILLING_READBACK\",\"provider_request_id\":\"01a1b7bf-ef29-77c1-ad38-5214baa197de\",\"schema\":2}")
TEST_FIXTURE_SETTLEMENT_RECEIPT_SHA256 = "624a59731fa4df41778ee7656be63f712e835aef673611b3d9382db822bd04a0"


def test_failed_readonly_guard_never_constructs_ledger_or_resolves_credential(monkeypatch, tmp_path):
    """A missing durable DB blocks before any mutable constructor or credential."""
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    calls = []
    monkeypatch.setattr(settlement, "_readonly_snapshot", lambda: (_ for _ in ()).throw(
        settlement.ProductionAdmissionError("durable model-sheet execution receipt is unavailable")
    ))
    monkeypatch.setattr(settlement, "StandingBudgetLedger", lambda *a, **kw: calls.append("ledger"))
    monkeypatch.setattr(settlement, "Executor", lambda *a, **kw: calls.append("executor"))
    monkeypatch.setattr(settlement, "resolve_default_fal_credential", lambda: calls.append("credential"))
    monkeypatch.setattr(settlement, "AiohttpFalBillingClient", lambda *a, **kw: calls.append("http"))

    with pytest.raises(settlement.ProductionAdmissionError, match="durable"):
        asyncio.run(settlement.reconcile())

    assert calls == []
    assert not (tmp_path / "ledger.sqlite3").exists()
    assert not (tmp_path / "executor.sqlite3").exists()


def _live_completion_job(settlement):
    return SimpleNamespace(
        scene="JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001",
        request_id=settlement.LOCAL_REQUEST_ID,
        mode="LIVE",
        endpoint="blackforestlabs/flux-3/text-to-image",
    )


def test_completion_rejects_an_identical_copy_before_opening_it(tmp_path, monkeypatch):
    """The completion raster has one canonical protected name, never a caller path."""
    import src.hybrid.young_identity_model_sheet_production as production
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    monkeypatch.setattr(production, "ROOT", tmp_path)
    alternate = tmp_path / "same-bytes-copy.png"
    canonical = tmp_path / "execution" / production.OPERATION_ID / "provider-receipt.quarantine.png"
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"canonical-fixture-media")
    # The alternate is byte-identical to the fixture's immutable media;
    # identity is still the canonical path, checked before any file read.
    alternate.write_bytes(canonical.read_bytes())
    reads = []
    original = Path.read_bytes

    def reject_alternate_read(path):
        if path == alternate:
            reads.append(path)
            raise AssertionError("alternate media was opened")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", reject_alternate_read)
    with pytest.raises(PermissionError, match="fixed canonical quarantine path"):
        settlement.validate_model_sheet_completion(
            tmp_path / "execution" / production.OPERATION_ID / "executor.sqlite3",
            tmp_path / "ledger" / "standing-budget.sqlite3",
            _live_completion_job(settlement),
            alternate,
        )
    assert reads == []


def test_completion_rejects_a_symlink_at_the_canonical_quarantine_path_before_open(tmp_path, monkeypatch):
    """A canonical spelling cannot conceal a symlink or Windows reparse hop."""
    import src.hybrid.young_identity_model_sheet_production as production
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    monkeypatch.setattr(production, "ROOT", tmp_path)
    canonical = tmp_path / "execution" / production.OPERATION_ID / "provider-receipt.quarantine.png"
    canonical.parent.mkdir(parents=True)
    target = tmp_path / "copied-result.png"
    target.write_bytes(b"identical-but-linked")
    try:
        os.symlink(target, canonical)
    except OSError as exc:
        pytest.skip(f"symlink unavailable in this Windows test environment: {exc}")
    reads = []
    original = Path.read_bytes

    def reject_canonical_read(path):
        if path == canonical:
            reads.append(path)
            raise AssertionError("reparse-point media was opened")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", reject_canonical_read)
    with pytest.raises(PermissionError, match="fixed canonical quarantine path"):
        settlement.validate_model_sheet_completion(
            tmp_path / "execution" / production.OPERATION_ID / "executor.sqlite3",
            tmp_path / "ledger" / "standing-budget.sqlite3",
            _live_completion_job(settlement),
            canonical,
        )
    assert reads == []


def test_recovery_rejects_symlinked_public_companion_receipt_before_open(tmp_path, monkeypatch):
    """Recovery validates its public receipt companion before its first read."""
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    execution = tmp_path / "execution" / settlement.OPERATION_ID
    execution.mkdir(parents=True)
    outside = tmp_path / "outside-provider-receipt.json"
    outside.write_text("{}", encoding="utf-8")
    companion = execution / "provider-receipt.json"
    try:
        os.symlink(outside, companion)
    except OSError as exc:
        pytest.skip(f"symlink unavailable in this Windows test environment: {exc}")
    reads = []
    original = Path.read_text

    def reject_companion_read(path, *args, **kwargs):
        if path == companion:
            reads.append(path)
            raise AssertionError("symlinked companion was opened")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_companion_read)
    snapshot = SimpleNamespace(
        factory=SimpleNamespace(_authority=lambda: ({}, {}, {"operation": {"endpoint": "ignored", "payload": {}}})),
        execution=execution,
        ledger_path=tmp_path / "ledger.sqlite3",
        financial_sha256="0" * 64,
    )
    with pytest.raises(settlement.ProductionAdmissionError, match="quarantined model-sheet media"):
        asyncio.run(settlement._recover_after_settlement(snapshot))
    assert reads == []


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions are native reparse points")
def test_recovery_rejects_public_companion_in_native_junction_before_open(tmp_path, monkeypatch):
    """The public recovery companion cannot cross a native Windows junction."""
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    target = tmp_path / "target-execution"
    target.mkdir()
    (target / "provider-receipt.json").write_text("{}", encoding="utf-8")
    junction = tmp_path / "execution" / settlement.OPERATION_ID
    junction.parent.mkdir()
    made = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        check=False,
    )
    if made.returncode:
        pytest.skip("native junction unavailable")
    companion = junction / "provider-receipt.json"
    reads = []
    original = Path.read_text

    def reject_companion_read(path, *args, **kwargs):
        if path == companion:
            reads.append(path)
            raise AssertionError("junction companion was opened")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", reject_companion_read)
    snapshot = SimpleNamespace(
        factory=SimpleNamespace(_authority=lambda: ({}, {}, {"operation": {"endpoint": "ignored", "payload": {}}})),
        execution=junction,
        ledger_path=tmp_path / "ledger.sqlite3",
        financial_sha256="0" * 64,
    )
    with pytest.raises(settlement.ProductionAdmissionError, match="quarantined model-sheet media"):
        asyncio.run(settlement._recover_after_settlement(snapshot))
    assert reads == []


def test_reconcile_uses_durable_submission_timestamp_and_current_utc_end(monkeypatch):
    """The caller cannot choose a narrow billing window that omits the claim."""
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    submitted = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    now = submitted + timedelta(minutes=2)
    observed = {}

    class Service:
        async def reconcile(self, request_id, *, start, end):
            observed.update(request_id=request_id, start=start, end=end)
            return {"status": "SETTLED"}

    snapshot = SimpleNamespace(submission_at=submitted, ledger_path="ignored")
    monkeypatch.setattr(settlement, "_readonly_snapshot", lambda: snapshot)
    monkeypatch.setattr(settlement, "_readonly_ledger_snapshot", lambda _path: object())
    monkeypatch.setattr(settlement, "_prove_settlement", lambda _snapshot, _before: None)
    monkeypatch.setattr(settlement, "_construct_service", lambda value, credential: Service())
    monkeypatch.setattr(settlement, "resolve_default_fal_credential", lambda: ("non-secret", "CONFIGURED"))
    monkeypatch.setattr(settlement, "_utc_now", lambda: now)

    async def recover(_value):
        return {"status": "COMPLETE"}

    monkeypatch.setattr(settlement, "_recover_after_settlement", recover)

    result = asyncio.run(settlement.reconcile())

    assert result["status"] == "SETTLED"
    assert observed == {
        "request_id": settlement.LOCAL_REQUEST_ID,
        "start": submitted,
        "end": now,
    }


def test_settlement_runs_only_existing_get_recovery_and_reports_unknown_completion(monkeypatch):
    """Settlement never marks the executor COMPLETE itself or invokes POST/review."""
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    submitted = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    now = submitted + timedelta(minutes=1)
    calls = []

    class Service:
        async def reconcile(self, *_args, **_kwargs):
            calls.append("billing-get")
            return {"status": "SETTLED", "claim": {"state": "SETTLED"}}

    snapshot = SimpleNamespace(submission_at=submitted, ledger_path="ignored")
    monkeypatch.setattr(settlement, "_readonly_snapshot", lambda: snapshot)
    monkeypatch.setattr(settlement, "_readonly_ledger_snapshot", lambda _path: object())
    monkeypatch.setattr(settlement, "_prove_settlement", lambda _snapshot, _before: None)
    monkeypatch.setattr(settlement, "_construct_service", lambda value, credential: Service())
    monkeypatch.setattr(settlement, "resolve_default_fal_credential", lambda: ("non-secret", "CONFIGURED"))
    monkeypatch.setattr(settlement, "_utc_now", lambda: now)

    async def recover(_value):
        calls.append("recovery-get")
        return {"status": "SUBMITTED"}

    monkeypatch.setattr(settlement, "_recover_after_settlement", recover)

    result = asyncio.run(settlement.reconcile())

    assert calls == ["billing-get", "recovery-get"]
    assert result["completion"] == {"status": "SUBMITTED"}


def test_settled_service_return_without_canonical_write_never_reaches_recovery(monkeypatch):
    """A successful service return is not settlement proof; readback must be durable."""
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    submitted = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    calls = []

    class Service:
        async def reconcile(self, *_args, **_kwargs):
            calls.append("billing-get")
            return {"status": "SETTLED"}

    snapshot = SimpleNamespace(submission_at=submitted, ledger_path="ignored")
    monkeypatch.setattr(settlement, "_readonly_snapshot", lambda: snapshot)
    monkeypatch.setattr(settlement, "_readonly_ledger_snapshot", lambda _path: object())
    monkeypatch.setattr(settlement, "_construct_service", lambda _value, _credential: Service())
    monkeypatch.setattr(settlement, "resolve_default_fal_credential", lambda: ("non-secret", "CONFIGURED"))
    monkeypatch.setattr(settlement, "_utc_now", lambda: submitted + timedelta(minutes=1))
    monkeypatch.setattr(settlement, "_prove_settlement", lambda _snapshot, _before: (_ for _ in ()).throw(
        settlement.ProductionAdmissionError("canonical current settlement receipt is unavailable")
    ))

    async def forbidden_recovery(_snapshot):
        calls.append("recovery-get")
        raise AssertionError("recovery must not run")

    monkeypatch.setattr(settlement, "_recover_after_settlement", forbidden_recovery)
    with pytest.raises(settlement.ProductionAdmissionError, match="canonical"):
        asyncio.run(settlement.reconcile())
    assert calls == ["billing-get"]



def test_verified_settled_replay_has_no_credential_or_billing_transport(monkeypatch):
    """A canonical SETTLED replay is local completion recovery, never a new GET."""
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    submitted = datetime(2026, 11, 7, 10, 0, tzinfo=UTC)
    snapshot = SimpleNamespace(submission_at=submitted, ledger_path="ignored", already_settled=True)
    calls = []
    monkeypatch.setattr(settlement, "_readonly_snapshot", lambda: snapshot)
    monkeypatch.setattr(settlement, "_readonly_ledger_snapshot", lambda _path: object())
    monkeypatch.setattr(settlement, "_prove_settlement", lambda _snapshot, _before: calls.append("proof"))
    monkeypatch.setattr(settlement, "resolve_default_fal_credential", lambda: calls.append("credential"))
    monkeypatch.setattr(settlement, "_construct_service", lambda *_: calls.append("service"))

    async def recover(_snapshot):
        calls.append("recovery")
        return {"status": "COMPLETE", "result_sha256": "a" * 64}

    monkeypatch.setattr(settlement, "_recover_after_settlement", recover)
    result = asyncio.run(settlement.reconcile())

    assert result["status"] == "SETTLED"
    assert result["completion"]["status"] == "COMPLETE"
    assert calls == ["proof", "recovery"]


def _test_only_settlement_pins_child() -> str:
    """Return the isolated child bootstrap for this fixed, non-secret fixture."""
    return f"""
import sys
from hashlib import sha256
from pathlib import Path
from scripts.run_offline_tests import audit
sys.addaudithook(audit)
root = Path(sys.argv[1])
import src.hybrid.jaco_flux3_production as canonical
import src.hybrid.young_identity_model_sheet_production as production
import src.hybrid.young_identity_model_sheet_settlement as settlement
# TEST-backed LIVE-mode codepath simulation only: fixed fixture pins isolate the
# production module without supplying LIVE authority, roots, credentials, or I/O.
assert sha256(Path(production.__file__).read_bytes()).hexdigest() == {TEST_FIXTURE_PRODUCTION_SOURCE_SHA256!r}
assert sha256((root / 'execution' / production.OPERATION_ID / 'provider-receipt.quarantine.png').read_bytes()).hexdigest() == {TEST_FIXTURE_MEDIA_SHA256!r}
with __import__('sqlite3').connect(root / 'ledger' / 'standing-budget.sqlite3') as db:
    assert db.execute('SELECT receipt_sha256 FROM settlement_receipts WHERE request_id=?', (settlement.LOCAL_REQUEST_ID,)).fetchone()[0] == {TEST_FIXTURE_SETTLEMENT_RECEIPT_SHA256!r}
production.ROOT = root
production.AUTHORITY_PATH = root / 'standing-policy' / 'JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001.authority.v2.json'
production.FINANCIAL_SHA256 = sha256((root / 'financial-record-source.json').read_bytes()).hexdigest()
canonical._CANONICAL_STATE_DB = root / 'canonical-state.sqlite3'
settlement.APPROVED_EXECUTION_CODE_SHA256 = {TEST_FIXTURE_PRODUCTION_SOURCE_SHA256!r}
settlement.APPROVED_QUARANTINED_PNG_SHA256 = {TEST_FIXTURE_MEDIA_SHA256!r}
settlement.APPROVED_SETTLEMENT_RECEIPT_SHA256 = {TEST_FIXTURE_SETTLEMENT_RECEIPT_SHA256!r}
raise SystemExit(settlement.main(['--reconcile']))
"""


def test_public_live_fixture_settles_then_completes_in_guarded_fresh_cli_process(tmp_path):
    """Fixed test evidence simulates a LIVE-mode recovery with audit guards, not LIVE qualification."""
    import src.hybrid.young_identity_model_sheet_production as production
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    assert sha256(Path(production.__file__).read_bytes()).hexdigest() == TEST_FIXTURE_PRODUCTION_SOURCE_SHA256
    module = "tests.unit.test_young_identity_model_sheet_production"
    submit = [sys.executable, "-c", f"from {module} import _amended_live_recovery_child as f; import sys; f(sys.argv[1], sys.argv[2], sys.argv[3])", str(tmp_path), "submit", settlement.REMOTE_REQUEST_ID]
    first = subprocess.run(submit, text=True, capture_output=True, check=False, timeout=60)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout)["result"] == "CHECKPOINTED"

    execution = tmp_path / "execution" / settlement.OPERATION_ID
    media = execution / "provider-receipt.quarantine.png"
    media.write_bytes(TEST_FIXTURE_MEDIA_BYTES)
    (execution / "provider-receipt.json").write_text(TEST_FIXTURE_PROVIDER_RECEIPT_JSON, encoding="utf-8")
    (execution / "provider-receipt.recovery.json").write_text(json.dumps({"schema": 1, "provider_request_id": settlement.REMOTE_REQUEST_ID, "status_url": f"https://queue.fal.run/blackforestlabs/flux-3/requests/{settlement.REMOTE_REQUEST_ID}/status", "response_url": f"https://queue.fal.run/blackforestlabs/flux-3/requests/{settlement.REMOTE_REQUEST_ID}"}, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    assert sha256(media.read_bytes()).hexdigest() == TEST_FIXTURE_MEDIA_SHA256
    assert sha256((execution / "provider-receipt.json").read_bytes()).hexdigest() == sha256(TEST_FIXTURE_PROVIDER_RECEIPT_JSON.encode()).hexdigest()
    assert sha256(TEST_FIXTURE_SETTLEMENT_RECEIPT_JSON.encode()).hexdigest() == TEST_FIXTURE_SETTLEMENT_RECEIPT_SHA256
    ledger_path = tmp_path / "ledger" / "standing-budget.sqlite3"
    with sqlite3.connect(ledger_path) as db:
        scope = db.execute("SELECT auth_scope FROM claims WHERE request_id=?", (settlement.LOCAL_REQUEST_ID,)).fetchone()[0]
        assert scope == TEST_FIXTURE_AUTH_SCOPE
        db.execute("UPDATE claims SET state='SETTLED', actual=48000 WHERE request_id=?", (settlement.LOCAL_REQUEST_ID,))
        db.execute("INSERT INTO settlement_receipts VALUES(?,?,?,?,?)", (settlement.LOCAL_REQUEST_ID, TEST_FIXTURE_SETTLEMENT_RECEIPT_SHA256, 48000, TEST_FIXTURE_SETTLEMENT_RECEIPT_JSON, "FAL_BILLING_READBACK"))

    replay = subprocess.run([sys.executable, "-c", _test_only_settlement_pins_child(), str(tmp_path)], text=True, capture_output=True, check=False, timeout=60, env={**os.environ, "PYTHONPATH": str(Path.cwd())})
    assert replay.returncode == 0, replay.stderr
    result = json.loads(replay.stdout)
    assert result == {"completion": {"request_id": settlement.LOCAL_REQUEST_ID, "result_sha256": TEST_FIXTURE_MEDIA_SHA256, "status": "COMPLETE"}, "request_id": settlement.LOCAL_REQUEST_ID, "status": "SETTLED"}
    with sqlite3.connect(execution / "executor.sqlite3") as db:
        row = json.loads(db.execute("SELECT data FROM jobs").fetchone()[0])
    assert row["status"] == "COMPLETE"
    assert row["result_sha256"] == TEST_FIXTURE_MEDIA_SHA256


def test_joint_media_and_receipt_mutation_cannot_rebind_fixed_test_manifest(tmp_path, monkeypatch):
    """Matching mutated companion data still fails the fixed test fixture pin."""
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    execution = tmp_path / "execution" / settlement.OPERATION_ID
    execution.mkdir(parents=True)
    mutated_media = b"mutated hermetic canonical model-sheet media"
    (execution / "provider-receipt.quarantine.png").write_bytes(mutated_media)
    (execution / "provider-receipt.json").write_text(json.dumps({"sha256": sha256(mutated_media).hexdigest(), "kind": "PNG_RESULT", "actual_billed_amount": "UNKNOWN_UNTIL_BILLING_RECONCILIATION"}, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    monkeypatch.setattr(settlement, "APPROVED_QUARANTINED_PNG_SHA256", TEST_FIXTURE_MEDIA_SHA256)
    monkeypatch.setattr(settlement, "StandingBudgetLedger", lambda *_args, **_kwargs: pytest.fail("ledger opened after immutable fixture mismatch"))
    snapshot = SimpleNamespace(factory=SimpleNamespace(_authority=lambda: ({"executor_category": "first"}, {}, {"operation": {"endpoint": "ignored", "payload": {}}})), execution=execution, ledger_path=tmp_path / "ledger.sqlite3", financial_sha256="0" * 64)

    with pytest.raises(settlement.ProductionAdmissionError, match="media changed"):
        asyncio.run(settlement._recover_after_settlement(snapshot))


def test_main_rejects_caller_selected_billing_window():
    import src.hybrid.young_identity_model_sheet_settlement as settlement

    with pytest.raises(SystemExit):
        settlement.main(["--reconcile", "--start", "2026-11-07T10:00:00+00:00"])
