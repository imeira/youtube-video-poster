from __future__ import annotations

from pathlib import Path

import pytest


def _write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(__import__("json").dumps(body, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def _qualified_test_root(root, monkeypatch):
    from hashlib import sha256

    import src.hybrid.young_identity_model_sheet_production as production
    financial = _write(root / "financial-record-source.json", {
        "schema": "jaco-factory-financial-reconciliation/1",
        "request_hash": production.REQUEST_ID, "revision": "818556d97edb648f",
        "currency": "USD", "total_episode_cap": "10.00",
        "status": "APPROVED_USD10", "approval_scope": "FINANCIAL_ONLY",
        "authorizes_generation": False,
    })
    financial_sha = sha256(financial.read_bytes()).hexdigest(); monkeypatch.setattr(production, "FINANCIAL_SHA256", financial_sha)
    policy = _write(root / "standing-policy" / production.POLICY_PATH.name, {"episode_id": production.EPISODE_ID, "canonical_consent": {"consent_message_id": 147620}})
    payload = production.EXPECTED_PAYLOAD
    preflight = _write(root / "preflight" / production.PREFLIGHT_PATH.name, {"request_id": production.REQUEST_ID, "operation": {"id": production.OPERATION_ID, "endpoint": production.ENDPOINT, "call_limit": 1, "maximum_usd": "0.048", "payload": payload, "payload_sha256": sha256(production._canonical(payload)).hexdigest()}})
    authority = {"schema": "jaco-derived-operation-authority/v1", "status": "READY_FOR_INDEPENDENT_PARENT_REVIEW_NO_RESERVATION_NO_POST", "operation_id": production.OPERATION_ID, "episode_id": production.EPISODE_ID, "policy_path": str(policy), "policy_sha256": sha256(policy.read_bytes()).hexdigest(), "preflight_path": str(preflight), "preflight_sha256": sha256(preflight.read_bytes()).hexdigest(), "maximum_next_cost_usd": "0.048", "canonical_ledger": {"path": str(root / "ledger" / "standing-budget.sqlite3"), "financial_policy_sha256": financial_sha, "operation_policy_sha256": sha256(policy.read_bytes()).hexdigest(), "financial_source": {"path": str(financial), "sha256": financial_sha}}}
    qualification = {"schema": "jaco-test-deployment-qualification/v2", "status": "QUALIFIED", "operation_id": production.OPERATION_ID, "episode_id": production.EPISODE_ID, "policy_sha256": authority["policy_sha256"], "operation_policy_sha256": authority["policy_sha256"], "preflight_sha256": authority["preflight_sha256"], "mode": "TEST", "authorization": "TEST_FIXTURE_CONSENT_ONLY", "review_packet_schema": "jaco-fal-flux3-successor-benchmark/3", "reviewer": "LOCAL_PROCESS_REVIEW_AUTOMATED_V1", "ci_digest_sha256": "0" * 64, "execution_code_sha256": sha256(Path(production.__file__).read_bytes()).hexdigest(), "financial_policy_sha256": financial_sha, "consent_fixture": {"kind": "TEST_ONLY_CONSENT_FIXTURE"}}
    qualification_path = _write(root / "standing-policy" / production.QUALIFICATION_PATH.name, qualification)
    authority["deployment_qualification"] = {"path": str(qualification_path), "sha256": sha256(qualification_path.read_bytes()).hexdigest()}
    return _write(root / "standing-policy" / production.AUTHORITY_PATH.name, authority)


@pytest.mark.asyncio
async def test_production_qualification_blocks_before_credentials_or_http(tmp_path):
    """A malformed protected root never reaches any live boundary."""
    from src.hybrid.young_identity_model_sheet_production import (
        ProductionAdmissionError,
        YoungIdentityModelSheetProduction,
    )

    factory = YoungIdentityModelSheetProduction._for_test(tmp_path, tmp_path / "missing-authority.json")
    calls = []

    async def quote_and_openapi():
        calls.append("http")
        return {}, {}

    with pytest.raises(ProductionAdmissionError):
        await factory._execute_with_composition(
            quote_and_openapi=quote_and_openapi,
            credential_loader=lambda: calls.append("credential"),
        )
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["schema", "hash", "mode", "scope", "financial"])
async def test_invalid_qualification_never_reaches_credentials_http_or_writes(
    tmp_path, monkeypatch, mutation
):
    """Every protected qualification mismatch fails before an external boundary."""
    from src.hybrid.young_identity_model_sheet_production import (
        ProductionAdmissionError,
        YoungIdentityModelSheetProduction,
    )

    authority_path = _qualified_test_root(tmp_path, monkeypatch)
    authority = __import__("json").loads(authority_path.read_text(encoding="utf-8"))
    qualification_path = Path(authority["deployment_qualification"]["path"])
    qualification = __import__("json").loads(qualification_path.read_text(encoding="utf-8"))
    if mutation == "schema":
        qualification["schema"] = "wrong-schema"
    elif mutation == "hash":
        authority["deployment_qualification"]["sha256"] = "0" * 64
    elif mutation == "mode":
        qualification["mode"] = "LIVE"
    elif mutation == "scope":
        qualification["operation_policy_sha256"] = "1" * 64
    else:
        qualification["financial_policy_sha256"] = "2" * 64
    _write(qualification_path, qualification)
    _write(authority_path, authority)
    calls = []

    async def quote_and_openapi():
        calls.append("http")
        return {}, {}

    with pytest.raises(ProductionAdmissionError):
        await YoungIdentityModelSheetProduction._for_test(
            tmp_path, authority_path
        )._execute_with_composition(
            quote_and_openapi=quote_and_openapi,
            credential_loader=lambda: calls.append("credential"),
        )
    assert calls == []
    assert not (tmp_path / "execution").exists()


@pytest.mark.asyncio
async def test_private_fixture_uses_executor_once_and_recovers_without_second_post(tmp_path, monkeypatch):
    """The TEST factory follows the production composition and durable recovery path."""
    from decimal import Decimal

    from src.hybrid.execution import Executor, ProviderResult
    from src.hybrid.young_identity_model_sheet_production import (
        YoungIdentityModelSheetProduction,
    )

    authority = _qualified_test_root(tmp_path, monkeypatch); calls = []
    original_run = Executor.run
    async def spy_run(self, *args, **kwargs):
        calls.append("executor"); return await original_run(self, *args, **kwargs)
    monkeypatch.setattr(Executor, "run", spy_run)
    class Provider:
        mode = "TEST"
        def __init__(self, output, _schema): self.output = output
        async def submit(self, _job, _id, checkpoint):
            calls.append("post"); checkpoint(provider_id="remote-1", partial=self.output); raise TimeoutError("crash")
        async def recover(self, _job, _id, remote, _partial, _checkpoint):
            assert remote == "remote-1"; calls.append("get"); self.output.write_bytes(b"recovered"); return ProviderResult(self.output, Decimal("0.048"))
    async def inputs():
        return {
            "schema": "FAL_AUTHENTICATED_PRICE_CONSUMER_SNAPSHOT/2",
            "endpoint_id": "blackforestlabs/flux-3/text-to-image",
            "currency": "USD", "maximum_usd": "0.048",
            "unit_price_usd": "0.048", "provider_unit": "image",
            "semantic_unit": "FLAT_1K_SINGLE_IMAGE", "quantity": 1,
            "api_url": "https://api.fal.ai/v1/models/pricing",
            "raw_sanitized_sha256": "a" * 64,
            "observed_at_epoch": int(__import__("time").time()),
            "provider_expiry": "UNKNOWN_NOT_ASSERTED",
            "price_validity": "LOCAL_OBSERVED_AT_MAX_AGE_SECONDS_300_NOT_PROVIDER_EXPIRY",
        }, __import__("json").loads((
            Path(__file__).parents[1] / "fixtures" /
            "fal_flux3_queue_openapi_20261106.json"
        ).read_text(encoding="utf-8"))
    factory = YoungIdentityModelSheetProduction._for_test(tmp_path, authority)
    with pytest.raises(TimeoutError):
        await factory._execute_with_composition(quote_and_openapi=inputs, credential_loader=lambda: (_ for _ in ()).throw(AssertionError("no credential")), test_provider_factory=Provider)
    result = await YoungIdentityModelSheetProduction._for_test(tmp_path, authority)._execute_with_composition(quote_and_openapi=inputs, credential_loader=lambda: (_ for _ in ()).throw(AssertionError("no credential")), test_provider_factory=Provider)
    assert result["status"] == "COMPLETE" and calls.count("executor") == 2
    assert calls.count("post") == 1 and calls.count("get") == 1
    with __import__("sqlite3").connect(tmp_path / "ledger" / "standing-budget.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1
    execution = tmp_path / "execution" / "JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_002"
    assert (execution / "protected-qualification.json").is_file()
    with __import__("sqlite3").connect(execution / "executor.sqlite3") as db:
        marker = __import__("json").loads(db.execute(
            "SELECT data FROM jobs"
        ).fetchone()[0])["final_admission_marker"]
    assert marker["schema"] == 2
    assert marker["kind"] == "JACO_FLUX3_FINAL_ADMISSION_MARKER"
    assert marker["request_id"] == result["request_id"]
    assert marker["auth_scope"] == "TEST_LOCAL_ONLY"
    assert marker["trust_identity"] == "YOUNG_IDENTITY_MODEL_SHEET_PROTECTED_QUALIFICATION"
