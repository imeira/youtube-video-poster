from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import sys
import time
from decimal import Decimal
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
    assistant_content, user_content = "canonical assistant consent context", "canonical user consent"
    state_db = root / "canonical-state.sqlite3"
    with sqlite3.connect(state_db) as database:
        database.execute("CREATE TABLE messages (id INTEGER, session_id TEXT, role TEXT, content TEXT)")
        database.executemany("INSERT INTO messages VALUES (?, ?, ?, ?)", [
            (147619, "test-canonical-session", "assistant", assistant_content),
            (147620, "test-canonical-session", "user", user_content),
        ])
    monkeypatch.setattr(__import__("src.hybrid.jaco_flux3_production", fromlist=["x"]), "_CANONICAL_STATE_DB", state_db)
    preflight = _write(root / "preflight" / production.PREFLIGHT_PATH.name, {"schema": "jaco-episode-operation-preflight/v2", "status": "VALIDATED_PRE_DISPATCH_BLOCKED_MISSING_OPERATION_AUTHORIZATION", "episode_id": production.EPISODE_ID, "request_id": production.REQUEST_ID, "replay_prohibition": True, "operation": {"id": production.OPERATION_ID, "kind": "MODEL_SHEET_THREE_VIEW", "endpoint": production.ENDPOINT, "call_limit": 1, "maximum_usd": "0.048", "payload": production.EXPECTED_PAYLOAD, "payload_sha256": sha256(production._canonical(production.EXPECTED_PAYLOAD)).hexdigest()}})
    policy = _write(root / "standing-policy" / production.POLICY_PATH.name, {
        "schema": "jaco-standing-episode-operation-policy/v2",
        "status": "ACTIVE_FOR_DERIVATION_REQUIRES_INDEPENDENT_OPERATION_REVIEW",
        "episode_id": production.EPISODE_ID,
        "canonical_consent": {"schema": "JACO_FLUX3_CANONICAL_TRANSCRIPT_PROVENANCE/1", "session_id": "test-canonical-session", "user_message_id": 147620, "adjacent_assistant_message_id": 147619, "user_message_sha256": sha256(user_content.encode()).hexdigest(), "assistant_message_sha256": sha256(assistant_content.encode()).hexdigest(), "financial_record_sha256": financial_sha, "review_source_sha256": sha256(preflight.read_bytes()).hexdigest()},
        "financial_baseline": {"currency": "USD", "total_episode_cap": "10.00", "remaining_usd": "10.00", "pending_usd": "0.00", "source_sha256": financial_sha},
    })
    authority = {"schema": "jaco-derived-operation-authority/v2", "status": "READY_FOR_INDEPENDENT_PARENT_REVIEW_NO_RESERVATION_NO_POST", "operation_id": production.OPERATION_ID, "episode_id": production.EPISODE_ID, "predecessor_operation_id": "", "executor_predecessor_request_id": "", "executor_category": "first", "migration_reason": "INITIAL_THREE_VIEW_PREFLIGHT", "non_reuse": True, "mode": "TEST", "policy_path": str(policy), "policy_sha256": sha256(policy.read_bytes()).hexdigest(), "preflight_path": str(preflight), "preflight_sha256": sha256(preflight.read_bytes()).hexdigest(), "maximum_next_cost_usd": "0.048", "canonical_ledger": {"path": str(root / "ledger" / "standing-budget.sqlite3"), "financial_policy_sha256": financial_sha, "operation_policy_sha256": sha256(policy.read_bytes()).hexdigest(), "financial_source": {"path": str(financial), "sha256": financial_sha}}}
    qualification = {"schema": "jaco-test-deployment-qualification/v2", "status": "QUALIFIED", "operation_id": production.OPERATION_ID, "episode_id": production.EPISODE_ID, "policy_sha256": authority["policy_sha256"], "operation_policy_sha256": authority["policy_sha256"], "preflight_sha256": authority["preflight_sha256"], "mode": "TEST", "authorization": "TEST_FIXTURE_CONSENT_ONLY", "review_packet_schema": "jaco-fal-flux3-successor-benchmark/3", "reviewer": "LOCAL_PROCESS_REVIEW_AUTOMATED_V1", "ci_digest_sha256": "0" * 64, "execution_code_sha256": sha256(Path(production.__file__).read_bytes()).hexdigest(), "financial_policy_sha256": financial_sha, "consent_fixture": {"kind": "TEST_ONLY_CONSENT_FIXTURE"}}
    qualification_path = _write(root / "standing-policy" / production.QUALIFICATION_PATH.name, qualification)
    authority["deployment_qualification"] = {"path": str(qualification_path), "sha256": sha256(qualification_path.read_bytes()).hexdigest()}
    return _write(root / "standing-policy" / production.AUTHORITY_PATH.name, authority)


def _fixture_inputs():
    return {
        "schema": "FAL_AUTHENTICATED_PRICE_CONSUMER_SNAPSHOT/2",
        "endpoint_id": "blackforestlabs/flux-3/text-to-image", "currency": "USD",
        "maximum_usd": "0.048", "unit_price_usd": "0.048", "provider_unit": "image",
        "semantic_unit": "FLAT_1K_SINGLE_IMAGE", "quantity": 1,
        "api_url": "https://api.fal.ai/v1/models/pricing", "raw_sanitized_sha256": "a" * 64,
        "observed_at_epoch": int(time.time()), "provider_expiry": "UNKNOWN_NOT_ASSERTED",
        "price_validity": "LOCAL_OBSERVED_AT_MAX_AGE_SECONDS_300_NOT_PROVIDER_EXPIRY",
    }, json.loads((Path(__file__).parents[1] / "fixtures" / "fal_flux3_queue_openapi_20261106.json").read_text(encoding="utf-8"))


class _ChildMonkeyPatch:
    """Small serializable setup adapter used by the controlled Python child."""
    def setattr(self, target, name, value):
        setattr(target, name, value)


def _subprocess_recovery_child(root_text: str, phase: str) -> None:
    """Run one durable attempt; disk fixtures are the child-process contract."""
    from src.hybrid.execution import ProviderResult
    from src.hybrid.young_identity_model_sheet_production import (
        YoungIdentityModelSheetProduction,
    )

    root = Path(root_text)
    authority = _qualified_test_root(root, _ChildMonkeyPatch()) if phase == "submit" else root / "standing-policy" / "JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001.authority.v2.json"
    # The fixture's independently checked transcript database is part of the
    # serialized child setup, so recovery does not inherit parent memory.
    import src.hybrid.jaco_flux3_production as canonical_production
    canonical_production._CANONICAL_STATE_DB = root / "canonical-state.sqlite3"
    counts_path = root / "transport-counts.json"
    counts = json.loads(counts_path.read_text()) if counts_path.exists() else {"posts": 0, "gets": 0}

    class Provider:
        mode = "TEST"
        def __init__(self, output, _openapi): self.output = output
        async def submit(self, _job, _request_id, checkpoint):
            counts["posts"] += 1; counts_path.write_text(json.dumps(counts))
            checkpoint(provider_id="child-remote-1", partial=self.output)
            raise TimeoutError("controlled child crash after checkpoint")
        async def recover(self, _job, _request_id, remote, _partial, _checkpoint):
            assert remote == "child-remote-1"
            counts["gets"] += 1; counts_path.write_text(json.dumps(counts))
            self.output.write_bytes(b"child recovered")
            return ProviderResult(self.output, Decimal("0.048"))

    async def run():
        quote, openapi = _fixture_inputs()
        production = YoungIdentityModelSheetProduction._for_test(root, authority)
        if phase == "submit":
            try:
                await production._execute_with_composition(quote_and_openapi=lambda _credential: _async(quote, openapi), test_provider_factory=Provider)
            except TimeoutError:
                pass
        else:
            await production._execute_with_composition(
                quote_and_openapi=lambda _credential: (_ for _ in ()).throw(
                    AssertionError("recovery must not refresh quote/OpenAPI")
                ),
                test_provider_factory=Provider,
            )
    asyncio.run(run())
    with sqlite3.connect(root / "ledger" / "standing-budget.sqlite3") as db:
        claims = db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    print(json.dumps({"phase": phase, **counts, "claims": claims}))


async def _async(*values):
    return values


@pytest.mark.parametrize("target, mutation", [
    ("authority", lambda d: d.__setitem__("unexpected", True)),
    ("policy", lambda d: d["canonical_consent"].__setitem__("transcript_sha256", "Z" * 64)),
    ("preflight", lambda d: d["operation"].__setitem__("call_limit", True)),
    ("financial", lambda d: d.__setitem__("authorizes_generation", "false")),
])
def test_closed_typed_protected_chain_rejects_forgery(tmp_path, monkeypatch, target, mutation):
    """RED security regressions: no partial or loosely typed trusted document."""
    from src.hybrid.young_identity_model_sheet_production import (
        ProductionAdmissionError,
        YoungIdentityModelSheetProduction,
    )
    authority_path = _qualified_test_root(tmp_path, monkeypatch)
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    paths = {"authority": authority_path, "policy": Path(authority["policy_path"]), "preflight": Path(authority["preflight_path"]), "financial": Path(authority["canonical_ledger"]["financial_source"]["path"])}
    path = paths[target]; document = json.loads(path.read_text(encoding="utf-8")); mutation(document); _write(path, document)
    # Rebinding hashes cannot turn a malformed document into authority.
    if target == "policy": authority["policy_sha256"] = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    if target == "preflight": authority["preflight_sha256"] = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    if target == "financial":
        digest = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        authority["canonical_ledger"]["financial_source"]["sha256"] = digest
        authority["canonical_ledger"]["financial_policy_sha256"] = digest
    if target != "authority":
        _write(authority_path, authority)
    with pytest.raises(ProductionAdmissionError):
        factory = YoungIdentityModelSheetProduction._for_test(tmp_path, authority_path)
        bound, _, _ = factory._authority()
        if target == "financial":
            factory._ledger_binding(bound)


def test_duplicate_json_key_is_rejected_before_any_protected_binding(tmp_path, monkeypatch):
    from src.hybrid.young_identity_model_sheet_production import (
        ProductionAdmissionError,
        YoungIdentityModelSheetProduction,
    )
    authority_path = _qualified_test_root(tmp_path, monkeypatch)
    authority = json.loads(authority_path.read_text(encoding="utf-8")); policy = Path(authority["policy_path"])
    policy.write_text('{"schema":"a","schema":"b"}', encoding="utf-8")
    authority["policy_sha256"] = __import__("hashlib").sha256(policy.read_bytes()).hexdigest(); _write(authority_path, authority)
    with pytest.raises(ProductionAdmissionError, match="strict readable JSON"):
        YoungIdentityModelSheetProduction._for_test(tmp_path, authority_path)._authority()


@pytest.mark.parametrize("target", ["authority", "policy", "preflight", "financial", "qualification"])
def test_every_serialized_authority_boundary_rejects_duplicate_keys_and_lowercase_hashes(tmp_path, monkeypatch, target):
    from src.hybrid.young_identity_model_sheet_production import (
        ProductionAdmissionError,
        YoungIdentityModelSheetProduction,
    )

    authority_path = _qualified_test_root(tmp_path, monkeypatch)
    authority = json.loads(authority_path.read_text())
    paths = {
        "authority": authority_path, "policy": Path(authority["policy_path"]),
        "preflight": Path(authority["preflight_path"]),
        "financial": Path(authority["canonical_ledger"]["financial_source"]["path"]),
        "qualification": Path(authority["deployment_qualification"]["path"]),
    }
    path = paths[target]
    # This exercises the loader at every boundary, rather than relying on a
    # parser unit test detached from the protected composition.
    path.write_text('{"schema":"first","schema":"second"}', encoding="utf-8")
    if target != "authority":
        if target == "policy": authority["policy_sha256"] = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        elif target == "preflight": authority["preflight_sha256"] = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        elif target == "qualification": authority["deployment_qualification"]["sha256"] = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        else: authority["canonical_ledger"]["financial_source"]["sha256"] = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        _write(authority_path, authority)
    factory = YoungIdentityModelSheetProduction._for_test(tmp_path, authority_path)
    with pytest.raises(ProductionAdmissionError):
        bound, _, _ = factory._authority()
        if target == "financial": factory._ledger_binding(bound)
        if target == "qualification": factory._qualification(bound)


@pytest.mark.parametrize("target", ["authority", "policy", "preflight", "financial", "qualification"])
def test_every_serialized_authority_boundary_rejects_malformed_lowercase_hashes(tmp_path, monkeypatch, target):
    from src.hybrid.young_identity_model_sheet_production import (
        ProductionAdmissionError,
        YoungIdentityModelSheetProduction,
    )

    authority_path = _qualified_test_root(tmp_path, monkeypatch)
    authority = json.loads(authority_path.read_text())
    if target == "authority": authority["policy_sha256"] = "A" * 64; _write(authority_path, authority)
    elif target == "policy":
        path = Path(authority["policy_path"]); doc = json.loads(path.read_text()); doc["canonical_consent"]["user_message_sha256"] = "A" * 64; _write(path, doc); authority["policy_sha256"] = __import__("hashlib").sha256(path.read_bytes()).hexdigest(); _write(authority_path, authority)
    elif target == "preflight":
        path = Path(authority["preflight_path"]); doc = json.loads(path.read_text()); doc["operation"]["payload_sha256"] = "A" * 64; _write(path, doc); authority["preflight_sha256"] = __import__("hashlib").sha256(path.read_bytes()).hexdigest(); _write(authority_path, authority)
    elif target == "financial": authority["canonical_ledger"]["financial_source"]["sha256"] = "A" * 64; _write(authority_path, authority)
    else:
        path = Path(authority["deployment_qualification"]["path"]); doc = json.loads(path.read_text()); doc["ci_digest_sha256"] = "A" * 64; _write(path, doc); authority["deployment_qualification"]["sha256"] = __import__("hashlib").sha256(path.read_bytes()).hexdigest(); _write(authority_path, authority)
    factory = YoungIdentityModelSheetProduction._for_test(tmp_path, authority_path)
    with pytest.raises(ProductionAdmissionError):
        bound, _, _ = factory._authority()
        if target == "financial": factory._ledger_binding(bound)
        if target == "qualification": factory._qualification(bound)


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows junction required")
def test_safe_path_rejects_actual_windows_junction(tmp_path):
    from src.hybrid.young_identity_model_sheet_production import (
        ProductionAdmissionError,
        _safe_path,
    )
    real, junction = tmp_path / "real", tmp_path / "junction"
    real.mkdir()
    made = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(junction), str(real)], capture_output=True, check=False)
    if made.returncode:
        pytest.skip("native junction setup unavailable")
    with pytest.raises(ProductionAdmissionError, match="reparse point"):
        _safe_path(junction / "authority.json", "authority")


@pytest.mark.asyncio
async def test_production_qualification_blocks_before_credentials_or_http(tmp_path):
    """A malformed protected root never reaches any live boundary."""
    from src.hybrid.young_identity_model_sheet_production import (
        ProductionAdmissionError,
        YoungIdentityModelSheetProduction,
    )

    factory = YoungIdentityModelSheetProduction._for_test(tmp_path, tmp_path / "missing-authority.json")
    calls = []

    async def quote_and_openapi(_credential):
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

    async def quote_and_openapi(_credential):
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
async def test_review_rejection_precedes_credential_execution_ledger_and_provider(tmp_path, monkeypatch):
    """A bad GET-only input cannot create mutable execution state or read a secret."""
    from src.hybrid.young_identity_model_sheet_production import (
        ProductionAdmissionError,
        YoungIdentityModelSheetProduction,
    )
    authority = _qualified_test_root(tmp_path, monkeypatch); calls = []
    async def bad_inputs(_credential):
        calls.append("get")
        return {"unexpected": True}, {}
    with pytest.raises(ProductionAdmissionError):
        await YoungIdentityModelSheetProduction._for_test(tmp_path, authority)._execute_with_composition(
            quote_and_openapi=bad_inputs,
            credential_loader=lambda: calls.append("credential"),
            test_provider_factory=lambda *_: calls.append("provider"),
        )
    assert calls == ["get"]
    assert not (tmp_path / "execution").exists()
    assert not (tmp_path / "ledger").exists()


@pytest.mark.asyncio
async def test_direct_execute_prepared_rejects_caller_review_capability(tmp_path, monkeypatch):
    """The method does not retain a caller-controlled review authorization seam."""
    from src.hybrid.young_identity_model_sheet_production import (
        YoungIdentityModelSheetProduction,
    )

    authority = _qualified_test_root(tmp_path, monkeypatch)
    quote, openapi = _fixture_inputs(); calls = []
    factory = YoungIdentityModelSheetProduction._for_test(tmp_path, authority)
    with pytest.raises(TypeError):
        await factory._execute_prepared(
            quote=quote, openapi=openapi,
            credential_loader=lambda: calls.append("credential") or "caller-secret",
            test_provider_factory=lambda *_: calls.append("factory"),
            _review_capability=object(),
        )
    assert calls == []
    assert not (tmp_path / "execution").exists() and not (tmp_path / "ledger").exists()


@pytest.mark.asyncio
async def test_direct_execute_prepared_runs_protected_review_before_post(tmp_path, monkeypatch):
    """No caller-supplied receipt/capability can replace the real reviewer."""
    import src.hybrid.young_identity_model_sheet_production as production
    from src.hybrid.execution import ProviderResult

    authority = _qualified_test_root(tmp_path, monkeypatch)
    quote, openapi = _fixture_inputs()
    calls = []
    original_review = production.YoungIdentityModelSheetProduction._review_for_execution

    def protected_review(self, reviewed_quote, reviewed_openapi, financial):
        calls.append("review")
        return original_review(self, reviewed_quote, reviewed_openapi, financial)

    class Provider:
        mode = "TEST"

        def __init__(self, output, _schema):
            self.output = output
            calls.append("factory")

        async def submit(self, _job, _request_id, checkpoint):
            assert calls == ["review", "factory"]
            calls.append("POST")
            checkpoint(provider_id="reviewed-remote", partial=self.output)
            self.output.write_bytes(b"reviewed")
            return ProviderResult(self.output, Decimal("0.048"))

    monkeypatch.setattr(production.YoungIdentityModelSheetProduction, "_review_for_execution", protected_review)
    result = await production.YoungIdentityModelSheetProduction._for_test(
        tmp_path, authority
    )._execute_prepared(quote=quote, openapi=openapi, test_provider_factory=Provider)
    assert result["status"] == "COMPLETE"
    assert calls == ["review", "factory", "POST"]


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", [
    "forged", "foreign-valid", "tampered-packet", "stale", "wrong-mode", "symlink", "missing-review",
])
async def test_caller_review_run_never_admits_or_creates_state(tmp_path, monkeypatch, attack):
    """Only composition-owned review evidence may cross the admission boundary."""
    from src.hybrid.young_identity_model_sheet_production import (
        YoungIdentityModelSheetProduction,
    )

    authority = _qualified_test_root(tmp_path, monkeypatch)
    quote, openapi = _fixture_inputs()
    review = tmp_path / "caller-review"
    review.mkdir()
    for name, value in {
        "packet.json": {}, "review.json": {"forged": True},
        "quote.json": quote, "openapi.json": openapi,
    }.items():
        _write(review / name, value)
    if attack == "foreign-valid":
        # A receipt from a different operation is still caller-controlled.
        _write(review / "review.json", {"verdict": "PASS", "request_id": "foreign"})
    elif attack == "tampered-packet":
        _write(review / "packet.json", {"payload": "tampered"})
    elif attack == "stale":
        _write(review / "review.json", {"reviewed_at_epoch": 0})
    elif attack == "wrong-mode":
        _write(review / "review.json", {"mode": "LIVE"})
    elif attack == "symlink":
        target = review / "packet-target.json"
        _write(target, {})
        (review / "packet.json").unlink()
        try:
            (review / "packet.json").symlink_to(target)
        except OSError:
            pytest.skip("symlink setup unavailable")
    elif attack == "missing-review":
        (review / "review.json").unlink()

    calls = []
    class Provider:
        mode = "TEST"
        def __init__(self, output, _openapi): self.output = output; calls.append("factory")
        async def submit(self, *_args): calls.append("POST")

    with pytest.raises(TypeError):
        await YoungIdentityModelSheetProduction._for_test(tmp_path, authority)._execute_prepared(
            quote=quote, openapi=openapi, test_provider_factory=Provider, review_run=review,
        )
    assert calls == []
    assert not (tmp_path / "execution").exists()
    assert not (tmp_path / "ledger" / "standing-budget.sqlite3").exists()


def test_openapi_payload_drift_and_boolean_quote_are_rejected(tmp_path, monkeypatch):
    from src.hybrid.young_identity_model_sheet_production import (
        ProductionAdmissionError,
        YoungIdentityModelSheetProduction,
    )
    authority = _qualified_test_root(tmp_path, monkeypatch); factory = YoungIdentityModelSheetProduction._for_test(tmp_path, authority)
    quote = {"schema": "FAL_AUTHENTICATED_PRICE_CONSUMER_SNAPSHOT/2", "endpoint_id": "blackforestlabs/flux-3/text-to-image", "currency": "USD", "maximum_usd": "0.048", "unit_price_usd": "0.048", "provider_unit": "image", "semantic_unit": "FLAT_1K_SINGLE_IMAGE", "quantity": True, "api_url": "https://api.fal.ai/v1/models/pricing", "raw_sanitized_sha256": "a" * 64, "observed_at_epoch": 0, "provider_expiry": "UNKNOWN_NOT_ASSERTED", "price_validity": "LOCAL_OBSERVED_AT_MAX_AGE_SECONDS_300_NOT_PROVIDER_EXPIRY"}
    with pytest.raises(ProductionAdmissionError): factory._validate_fresh_quote(quote, {})


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
    async def inputs(_credential):
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
    execution = tmp_path / "execution" / "JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001"
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


def test_controlled_python_child_recovers_existing_executor_and_ledger_with_get_only(tmp_path):
    """The second interpreter reopens durable state: no second POST or claim."""
    module = "tests.unit.test_young_identity_model_sheet_production"
    command = [sys.executable, "-c", f"from {module} import _subprocess_recovery_child as f; import sys; f(sys.argv[1], sys.argv[2])", str(tmp_path)]
    first = subprocess.run([*command, "submit"], text=True, capture_output=True, check=False, timeout=45)
    assert first.returncode == 0, first.stderr
    assert json.loads(first.stdout) == {"phase": "submit", "posts": 1, "gets": 0, "claims": 1}
    second = subprocess.run([*command, "recover"], text=True, capture_output=True, check=False, timeout=45)
    assert second.returncode == 0, second.stderr
    assert json.loads(second.stdout) == {"phase": "recover", "posts": 1, "gets": 1, "claims": 1}


@pytest.mark.asyncio
async def test_no_checkpoint_ambiguity_never_allows_a_second_post(tmp_path, monkeypatch):
    """A crash after POST but before a provider identity is a durable no-replay state."""
    from src.hybrid.young_identity_model_sheet_production import (
        YoungIdentityModelSheetProduction,
    )
    authority = _qualified_test_root(tmp_path, monkeypatch); quote, openapi = _fixture_inputs(); calls = []
    class Provider:
        mode = "TEST"
        def __init__(self, output, _openapi): self.output = output
        async def submit(self, *_args): calls.append("post"); raise TimeoutError("post accepted before checkpoint")
    async def inputs(_credential): return quote, openapi
    factory = YoungIdentityModelSheetProduction._for_test(tmp_path, authority)
    with pytest.raises(TimeoutError):
        await factory._execute_with_composition(quote_and_openapi=inputs, test_provider_factory=Provider)
    with pytest.raises(RuntimeError, match="unknown submission"):
        await YoungIdentityModelSheetProduction._for_test(tmp_path, authority)._execute_with_composition(quote_and_openapi=inputs, test_provider_factory=Provider)
    assert calls == ["post"]
    with sqlite3.connect(tmp_path / "ledger" / "standing-budget.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1
