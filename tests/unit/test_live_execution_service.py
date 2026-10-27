from __future__ import annotations

import asyncio
import base64
import json
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.execution import Job


def live_job(tmp_path: Path) -> Job:
    reference = tmp_path / "reference.png"
    Image.new("RGB", (64, 36), "green").save(reference)
    asset = FrozenAsset.approve(reference, "identity-review", "LIVE")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze(
        (asset,), FrozenAsset.approve(sheet, "assets-review", "LIVE"),
        "assets-review", "LIVE",
    )
    return Job(
        "SC001", "first", "LIVE", "fal-ai/flux-2/klein/9b/edit",
        {"prompt": "Calm child-safe scene", "image_urls": [str(reference)],
         "image_size": {"width": 64, "height": 36}}, manifest, Decimal("0.02"),
    )


def policy() -> dict:
    return {
        "schema": 2, "version": "external-live-v1", "mode": "LIVE",
        "episode_cap_usd": "1.00", "month_cap_usd": "2.00",
        "experiment_cap_usd": "0.50", "allowed_adapters": ["fal_flux"],
        "publication": {"enabled": False},
    }


def persisted_qa(path: Path, job: Job, kind: str, *, evidence: dict | None = None) -> Path:
    path.write_text(json.dumps({
        "schema": 1, "kind": kind, "approved": True,
        "request_id": job.request_id, "manifest_sha256": job.manifest.checksum,
        "reviewer": f"independent-{kind}",
        "evidence": evidence or {"method": "external-review", "result": "PASS"},
    }), encoding="utf-8")
    return path


class QuoteResolver:
    def __init__(self, *, valid_until: float = 4_102_444_120):
        self.valid_until = valid_until
        self.calls = 0

    def quote(self, job: Job) -> dict:
        self.calls += 1
        return {
            "endpoint": job.endpoint, "amount": str(job.cost),
            "valid_until": self.valid_until,
            "evidence": {"source": "authenticated-billing-export", "currency": "USD"},
        }


class ForbiddenExecutor:
    def __init__(self):
        self.calls = 0

    async def run(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("disabled service must never invoke executor")


class ForbiddenLedger:
    def __init__(self):
        self.calls = []
        self.binding = {}

    def __getattr__(self, name):
        def forbidden(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError("disabled service must never mutate the ledger")
        return forbidden


def service(tmp_path: Path, *, resolver: QuoteResolver | None = None):
    from src.hybrid.live_execution import LiveExecutionPolicy, LiveExecutionService

    trust_root = tmp_path / "external-trust-root"
    trust_root.mkdir()
    trust_root.joinpath("live-trust-root.json").write_text(json.dumps({
        "schema": 1, "kind": "LIVE_TRUST_ROOT", "issuer": "external-operator",
        "verification": "UNAVAILABLE",
    }), encoding="utf-8")
    parsed = LiveExecutionPolicy.from_dict(policy())
    executor, ledger = ForbiddenExecutor(), ForbiddenLedger()
    return (
        LiveExecutionService(
            executor=executor, ledger=ledger, policy=parsed,
            quote_resolver=resolver or QuoteResolver(), trust_root=trust_root,
            clock=lambda: 4_102_444_000,
        ), executor, ledger, trust_root,
    )


def test_preflight_is_write_once_and_explicitly_not_qualified(tmp_path):
    resolver = QuoteResolver()
    service_, _executor, _ledger, trust_root = service(tmp_path, resolver=resolver)
    job = live_job(tmp_path)
    identity = persisted_qa(trust_root / "identity.json", job, "IDENTITY_QA")
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")
    receipt_path = tmp_path / "preflight.json"

    qualification = service_.preflight(
        job, adapter="fal_flux", identity_qa_path=identity, assets_qa_path=assets,
        receipt_path=receipt_path,
    )

    assert qualification.execution_state == "BLOCKED_NOT_QUALIFIED"
    assert qualification.authorization is None and qualification.price is None
    persisted = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert persisted["execution_state"] == "BLOCKED_NOT_QUALIFIED"
    assert persisted["trust_root_verification"] == "UNAVAILABLE"
    assert persisted["billing_bridge"] == "NOT_CONNECTED"
    assert persisted["billing_status"] == "NOT_AUTHENTICATED"
    assert resolver.calls == 0
    assert service_.preflight(
        job, adapter="fal_flux", identity_qa_path=identity, assets_qa_path=assets,
        receipt_path=receipt_path,
    ).document_sha256 == qualification.document_sha256
    receipt_path.write_text('{"different": true}', encoding="utf-8")
    with pytest.raises(ValueError, match="write-once"):
        service_.preflight(
            job, adapter="fal_flux", identity_qa_path=identity, assets_qa_path=assets,
            receipt_path=receipt_path,
        )


def test_dispatch_is_hard_blocked_before_executor_or_ledger_even_after_preflight(tmp_path):
    service_, executor, ledger, trust_root = service(tmp_path)
    job = live_job(tmp_path)
    identity = persisted_qa(trust_root / "identity.json", job, "IDENTITY_QA")
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")
    receipt_path = tmp_path / "preflight.json"
    service_.preflight(job, adapter="fal_flux", identity_qa_path=identity,
                       assets_qa_path=assets, receipt_path=receipt_path)

    with pytest.raises(PermissionError, match="disabled until external trust"):
        asyncio.run(service_.dispatch(job, preflight_path=receipt_path))
    # Replays after any hypothetical reserve/submit/receipt/settle crash cannot
    # enter a recovery or resubmit branch: this service has no such branch.
    with pytest.raises(PermissionError, match="disabled until external trust"):
        asyncio.run(service_.dispatch(job, preflight_path=receipt_path))

    assert executor.calls == 0
    assert ledger.calls == []


def test_package_exposes_no_live_factory_or_test_transport_selector():
    from src.hybrid import live_execution

    assert not hasattr(live_execution, "LiveAdapterFactory")


def test_dispatch_revalidates_preflight_hash_before_hard_block(tmp_path):
    service_, executor, _ledger, trust_root = service(tmp_path)
    job = live_job(tmp_path)
    identity = persisted_qa(trust_root / "identity.json", job, "IDENTITY_QA")
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")
    receipt_path = tmp_path / "preflight.json"
    service_.preflight(job, adapter="fal_flux", identity_qa_path=identity,
                       assets_qa_path=assets, receipt_path=receipt_path)
    receipt_path.write_text('{"tampered":true}', encoding="utf-8")

    with pytest.raises(ValueError, match="preflight receipt hash mismatch"):
        asyncio.run(service_.dispatch(job, preflight_path=receipt_path))
    assert executor.calls == 0


def test_inline_self_attested_qa_and_bearer_evidence_are_rejected(tmp_path):
    service_, _executor, _ledger, trust_root = service(tmp_path)
    job = live_job(tmp_path)
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")

    with pytest.raises(ValueError, match="persisted"):
        service_.preflight(job, adapter="fal_flux", identity_qa_path={},
                           assets_qa_path=assets, receipt_path=tmp_path / "p.json")

    identity = persisted_qa(
        trust_root / "identity.json", job, "IDENTITY_QA",
        evidence={"method": "external-review", "note": "Bearer secret-value"},
    )
    with pytest.raises(ValueError, match="sensitive"):
        service_.preflight(job, adapter="fal_flux", identity_qa_path=identity,
                           assets_qa_path=assets, receipt_path=tmp_path / "p.json")


def test_qa_receipt_rejects_integer_instead_of_boolean_approval(tmp_path):
    service_, _executor, _ledger, trust_root = service(tmp_path)
    job = live_job(tmp_path)
    identity = persisted_qa(trust_root / "identity.json", job, "IDENTITY_QA")
    receipt = json.loads(identity.read_text(encoding="utf-8"))
    receipt["approved"] = 1
    identity.write_text(json.dumps(receipt), encoding="utf-8")
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")

    with pytest.raises(ValueError, match="does not bind"):
        service_.preflight(job, adapter="fal_flux", identity_qa_path=identity,
                           assets_qa_path=assets, receipt_path=tmp_path / "p.json")


def test_preflight_hashes_the_same_qa_bytes_it_strictly_validates_after_path_swap(tmp_path, monkeypatch):
    from src.hybrid import live_execution

    service_, _executor, _ledger, trust_root = service(tmp_path)
    job = live_job(tmp_path)
    identity = persisted_qa(trust_root / "identity.json", job, "IDENTITY_QA")
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")
    accepted_bytes = identity.read_bytes()
    outside = tmp_path / "outside.json"
    persisted_qa(outside, job, "IDENTITY_QA", evidence={"method": "outside", "result": "FAIL"})
    original_load_json = live_execution._load_json
    original_read_bytes = Path.read_bytes
    swapped = False

    def swap_identity_after_parse(path, label, **kwargs):
        nonlocal swapped
        parsed = original_load_json(path, label, **kwargs)
        if not swapped and label == "IDENTITY_QA receipt":
            swapped = True
            identity.unlink()
            identity.symlink_to(outside)
        return parsed

    def forbid_reopened_identity(self, *args, **kwargs):
        if self == identity:
            raise AssertionError("outside QA bytes must not be reopened")
        return original_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(live_execution, "_load_json", swap_identity_after_parse)
    monkeypatch.setattr(Path, "read_bytes", forbid_reopened_identity)

    result = service_.preflight(job, adapter="fal_flux", identity_qa_path=identity,
                                assets_qa_path=assets, receipt_path=tmp_path / "p.json")

    assert swapped
    assert result.document["identity_qa_sha256"] == sha256(accepted_bytes).hexdigest()


def test_closed_policy_and_unconnected_quote_resolver_does_not_gate_preflight(tmp_path):
    from src.hybrid.live_execution import LiveExecutionPolicy

    extra = policy() | {"bypass_live_block": True}
    with pytest.raises(ValueError, match="closed"):
        LiveExecutionPolicy.from_dict(extra)

    resolver = QuoteResolver(valid_until=4_102_444_800)
    service_, _executor, _ledger, trust_root = service(tmp_path, resolver=resolver)
    job = live_job(tmp_path)
    identity = persisted_qa(trust_root / "identity.json", job, "IDENTITY_QA")
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")
    service_.preflight(job, adapter="fal_flux", identity_qa_path=identity,
                       assets_qa_path=assets, receipt_path=tmp_path / "p.json")
    assert resolver.calls == 0


def test_preflight_records_verified_external_qa_evidence_without_live_authority(tmp_path):
    service_, _executor, _ledger, trust_root = service(tmp_path)
    job = live_job(tmp_path)
    identity = persisted_qa(trust_root / "identity.json", job, "IDENTITY_QA")
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    trust_root.joinpath("external-trust-root.json").write_text(json.dumps({
        "schema": 1, "kind": "EXTERNAL_SIGNER_TRUST_ROOT", "environment": "TEST",
        "algorithm": "Ed25519", "version": "test-root-v1", "revoked_signers": [],
        "keys": [{"signer_id": "test-qa", "role": "QA", "scopes": ["EVIDENCE:QA"],
                  "public_key_b64": base64.b64encode(public).decode("ascii"),
                  "not_before": 4_102_443_000, "not_after": 4_102_445_000}],
    }), encoding="utf-8")
    source_sha256 = sha256(json.dumps(job.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    evidence = {"schema": 1, "kind": "SIGNED_QA_EVIDENCE", "version": "evidence-v1",
                "signer_id": "test-qa", "role": "QA", "scope": "EVIDENCE:QA",
                "request_id": job.request_id, "episode": job.scene, "source_sha256": source_sha256,
                "assets_sha256": job.manifest.checksum, "policy_sha256": service_.policy.digest,
                "issued_at": 4_102_444_000, "expires_at": 4_102_444_120, "nonce": "service-test-1"}
    canonical = json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    evidence["signature_b64"] = base64.b64encode(private.sign(canonical)).decode("ascii")
    evidence_path = trust_root / "signed-qa-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = service_.preflight(job, adapter="fal_flux", identity_qa_path=identity,
                                assets_qa_path=assets, receipt_path=tmp_path / "p.json",
                                signed_qa_evidence_path=evidence_path)

    assert result.execution_state == "BLOCKED_NOT_QUALIFIED"
    assert result.document["signed_qa_evidence"] == {"role": "QA", "signer_id": "test-qa", "state": "CRYPTOGRAPHICALLY_VALID_UNAUTHENTICATED_ROOT"}


def test_app_config_composition_uses_pinned_external_root_but_keeps_live_blocked(tmp_path):
    from types import SimpleNamespace

    from src.hybrid.live_execution import LiveExecutionPolicy, LiveExecutionService

    _service, _executor, _ledger, trust_root = service(tmp_path)
    job = live_job(tmp_path)
    identity = persisted_qa(trust_root / "identity.json", job, "IDENTITY_QA")
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    root = {
        "schema": 1, "kind": "EXTERNAL_SIGNER_TRUST_ROOT", "environment": "PRODUCTION",
        "algorithm": "Ed25519", "version": "production-test-root", "revoked_signers": [],
        "keys": [{"signer_id": "configured-qa", "role": "QA", "scopes": ["EVIDENCE:QA"],
                  "public_key_b64": base64.b64encode(public).decode("ascii"),
                  "not_before": 4_102_443_000, "not_after": 4_102_445_000}],
    }
    root_path = trust_root / "external-trust-root.json"
    root_path.write_text(json.dumps(root), encoding="utf-8")
    app_config = SimpleNamespace(raw={"external_trust_root": {
        "root_path": str(root_path), "root_sha256": sha256(root_path.read_bytes()).hexdigest(),
        "evidence_root": str(trust_root), "environment": "PRODUCTION",
    }})
    executor, ledger, resolver = ForbiddenExecutor(), ForbiddenLedger(), QuoteResolver()
    instance = LiveExecutionService.from_app_config(
        app_config, executor=executor, ledger=ledger,
        policy=LiveExecutionPolicy.from_dict(policy()), quote_resolver=resolver,
        trust_root=trust_root, clock=lambda: 4_102_444_000,
    )
    evidence = {"schema": 1, "kind": "SIGNED_QA_EVIDENCE", "version": "evidence-v1",
                "signer_id": "configured-qa", "role": "QA", "scope": "EVIDENCE:QA",
                "request_id": job.request_id, "episode": job.scene,
                "source_sha256": sha256(json.dumps(job.payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest(),
                "assets_sha256": job.manifest.checksum, "policy_sha256": instance.policy.digest,
                "issued_at": 4_102_444_000, "expires_at": 4_102_444_120, "nonce": "configured-service-test"}
    evidence["signature_b64"] = base64.b64encode(private.sign(json.dumps(
        evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode())).decode("ascii")
    evidence_path = trust_root / "configured-evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")

    result = instance.preflight(job, adapter="fal_flux", identity_qa_path=identity,
                                assets_qa_path=assets, receipt_path=tmp_path / "p.json",
                                signed_qa_evidence_path=evidence_path)

    assert result.execution_state == "BLOCKED_NOT_QUALIFIED"
    assert result.document["signed_qa_evidence"]["state"] == "EXTERNAL_CONFIGURATION_VALIDATED"
    assert result.document["billing_status"] == "NOT_AUTHENTICATED"
    assert executor.calls == 0 and ledger.calls == [] and resolver.calls == 0


def test_missing_app_trust_configuration_reports_not_configured_without_provider_work(tmp_path):
    from types import SimpleNamespace

    from src.hybrid.live_execution import LiveExecutionPolicy, LiveExecutionService

    _service, _executor, _ledger, trust_root = service(tmp_path)
    job = live_job(tmp_path)
    identity = persisted_qa(trust_root / "identity.json", job, "IDENTITY_QA")
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")
    executor, ledger, resolver = ForbiddenExecutor(), ForbiddenLedger(), QuoteResolver()
    instance = LiveExecutionService.from_app_config(
        SimpleNamespace(raw={}), executor=executor, ledger=ledger,
        policy=LiveExecutionPolicy.from_dict(policy()), quote_resolver=resolver,
        trust_root=trust_root, clock=lambda: 4_102_444_000,
    )

    result = instance.preflight(job, adapter="fal_flux", identity_qa_path=identity,
                                assets_qa_path=assets, receipt_path=tmp_path / "p.json")

    assert result.document["signed_qa_evidence"] == {"state": "NOT_CONFIGURED"}
    assert result.document["billing_status"] == "NOT_AUTHENTICATED"
    assert executor.calls == 0 and ledger.calls == [] and resolver.calls == 0


def test_configured_billing_snapshot_reads_real_test_executor_and_ledger_without_sqlite_mutation(tmp_path):
    """A local TEST receipt is observable, never LIVE billing authority."""
    from types import SimpleNamespace

    from src.hybrid.execution import Executor
    from src.hybrid.live_execution import (
        BillingStatusSnapshot,
        BillingStatusSnapshotPublisher,
    )
    from src.hybrid.planner import Config
    from src.hybrid.standing_budget import StandingBudgetLedger

    reference = tmp_path / "test-reference.png"
    Image.new("RGB", (8, 8), "blue").save(reference)
    asset = FrozenAsset.approve(reference, "test", "TEST")
    sheet = tmp_path / "test-sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "test", "TEST"), "test", "TEST")
    job = Job("TEST-S001", "first", "TEST", "fal-ai/flux/dev", {"prompt": "fixture"}, manifest, Decimal("0.05"))
    ledger_path, executor_path = tmp_path / "ledger.sqlite3", tmp_path / "executor.sqlite3"
    ledger = StandingBudgetLedger(ledger_path, policy_sha256="a" * 64, episode_limit_usd="1", month_limit_usd="1", experiment_limit_usd="1", mode="TEST")
    executor = Executor(executor_path, Config(limit=Decimal(1)), standing_ledger=ledger)
    prepared = executor.prepare_intent(job)
    ledger.reserve(job.request_id, "TEST-EP", job.endpoint, job.cost,
                   settlement_mode="FAL_READBACK_V2", auth_scope=prepared["settlement_binding"]["auth_scope"],
                   provider_request_id="UNASSIGNED", authority_mode="TEST")

    snapshot = tmp_path / "billing-status.json"
    produced_digest = BillingStatusSnapshotPublisher(
        snapshot, clock=lambda: 4_102_444_000,
    ).publish(
        job, executor_receipt=executor.inspect(job.request_id),
        ledger_claim=ledger.claim(job.request_id), settlement_receipt=None,
    )
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.name.startswith(("ledger.sqlite3", "executor.sqlite3"))}
    status = BillingStatusSnapshot.from_app_config(SimpleNamespace(raw={"billing_status_snapshot": {
        "path": str(snapshot), "sha256": produced_digest,
    }}), clock=lambda: 4_102_444_030).inspect(job)
    after = {path.name: path.read_bytes() for path in tmp_path.iterdir() if path.name.startswith(("ledger.sqlite3", "executor.sqlite3"))}

    assert status == {
        "billing_bridge": "LOCAL_SNAPSHOT", "billing_status": "TEST",
        "billing_provenance": "LOCAL_SNAPSHOT_UNAUTHENTICATED",
        "billing_readback": "READBACK_REQUIRED", "actual_cost_usd": "UNKNOWN",
    }
    assert before == after


def test_billing_snapshot_stale_mismatch_and_pending_are_never_zero_or_qualified(tmp_path):
    from types import SimpleNamespace

    from src.hybrid.live_execution import BillingStatusSnapshot

    snapshot = tmp_path / "billing-status.json"
    body = {
        "schema": 1, "kind": "FAL_BILLING_STATUS_SNAPSHOT", "generated_at": 4_102_443_000,
        "request_id": "other-request", "endpoint_id": "other-endpoint", "mode": "LIVE",
        "executor_receipt": {}, "ledger_claim": {}, "settlement_receipt": None,
        "provenance": "LOCAL_SNAPSHOT_UNAUTHENTICATED",
    }
    body["snapshot_sha256"] = sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    snapshot.write_text(json.dumps(body, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    reader = BillingStatusSnapshot.from_app_config(SimpleNamespace(raw={"billing_status_snapshot": {
        "path": str(snapshot), "sha256": sha256(snapshot.read_bytes()).hexdigest(),
    }}), clock=lambda: 4_102_444_000)

    status = reader.inspect(type("Job", (), {"request_id": "wanted", "endpoint": "wanted-endpoint", "mode": "LIVE"})())

    assert status["billing_status"] == "STALE"
    assert status["actual_cost_usd"] == "UNKNOWN"
    assert status["billing_readback"] == "READBACK_REQUIRED"


def test_real_executor_ledger_snapshot_producer_flows_through_app_config_preflight(tmp_path):
    """The positive snapshot comes from real durable records, not hand-authored JSON."""
    from types import SimpleNamespace

    from src.hybrid.execution import Executor
    from src.hybrid.live_execution import (
        BillingStatusSnapshotPublisher,
        LiveExecutionPolicy,
        LiveExecutionService,
    )
    from src.hybrid.planner import Config
    from src.hybrid.standing_budget import StandingBudgetLedger

    _base, _executor, _ledger, trust_root = service(tmp_path)
    job = live_job(tmp_path)
    executor = Executor(tmp_path / "executor.sqlite3", Config(limit=Decimal(1)))
    ledger = StandingBudgetLedger(
        tmp_path / "ledger.sqlite3", policy_sha256="a" * 64,
        episode_limit_usd="1", month_limit_usd="1", experiment_limit_usd="1", mode="LIVE",
    )
    prepared = executor.prepare_intent(job, auth_scope=f"LIVE_AUTHORIZATION_REQUEST:{job.request_id}")
    ledger.reserve(
        job.request_id, "EP-LIVE", job.endpoint, job.cost,
        settlement_mode="FAL_READBACK_V2", auth_scope=prepared["settlement_binding"]["auth_scope"],
        provider_request_id="UNASSIGNED", authority_mode="LIVE",
    )
    snapshot = tmp_path / "billing-status.json"
    digest = BillingStatusSnapshotPublisher(snapshot, clock=lambda: 4_102_444_000).publish(
        job, executor_receipt=prepared, ledger_claim=ledger.claim(job.request_id), settlement_receipt=None,
    )
    instance = LiveExecutionService.from_app_config(
        SimpleNamespace(raw={"billing_status_snapshot": {"path": str(snapshot), "sha256": digest}}),
        executor=executor, ledger=ledger, policy=LiveExecutionPolicy.from_dict(policy()),
        quote_resolver=QuoteResolver(), trust_root=trust_root, clock=lambda: 4_102_444_001,
    )
    identity = persisted_qa(trust_root / "identity.json", job, "IDENTITY_QA")
    assets = persisted_qa(trust_root / "assets.json", job, "ASSETS_QA")

    result = instance.preflight(job, adapter="fal_flux", identity_qa_path=identity,
                                assets_qa_path=assets, receipt_path=tmp_path / "preflight.json")

    assert result.execution_state == "BLOCKED_NOT_QUALIFIED"
    assert result.document["billing_status"] == "PENDING"
    assert result.document["billing_readback"] == "READBACK_REQUIRED"
    with pytest.raises(PermissionError, match="disabled until external trust"):
        asyncio.run(instance.dispatch(job, preflight_path=tmp_path / "preflight.json"))


@pytest.mark.parametrize("bad_value", ["1e999", "NaN", "Infinity", "-Infinity"])
def test_shared_strict_loader_rejects_nonfinite_numbers_including_overflow(tmp_path, bad_value):
    from src.hybrid.external_trust_root import (
        TrustRootVerificationError,
        load_strict_json_snapshot,
    )

    document = tmp_path / "nonfinite.json"
    document.write_text('{"nested":{"value":' + bad_value + '}}', encoding="utf-8")

    with pytest.raises(TrustRootVerificationError, match="non-finite"):
        load_strict_json_snapshot(document, "nonfinite test")
