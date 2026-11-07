from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from src.hybrid.jaco_flux3_production import (
    _FAL_MEDIA_HOSTS,
    _FAL_PRICE_API_URL,
    _FAL_QUEUE_HOST,
    FixedFalProductionFactory,
    ProductionCompositionError,
    _fal_png_locator_projection,
    _fixed_https_url,
    _FixedFalProvider,
    _official_openapi_endpoint,
    _price_snapshot,
    _queue_route,
)


def _write_hermetic_production_authority(tmp_path, monkeypatch, *, assistant_role="assistant"):
    """Install complete TEST-only authority bytes; never read operator state."""
    import src.hybrid.jaco_flux3_production as production

    root = tmp_path / "TEST_ONLY_NONSECRET_PRODUCTION_AUTHORITY"
    source = tmp_path / "TEST_ONLY_NONSECRET_SOURCE_EVIDENCE"
    root.mkdir()
    source.mkdir()
    session_id = "local-712d007e4f3efba4d8c6cfc52f947e69023fed6f445a54206a6c3ede9b317fbb"
    assistant_content = "TEST ONLY: proposed bounded local evaluation."
    user_content = "TEST ONLY: I authorize one bounded local evaluation."
    finance = {
        "schema": "jaco-factory-financial-reconciliation/1",
        "request_hash": production.REQUEST_ID,
        "currency": "USD",
        "total_episode_cap": "10.00",
        "authorizes_generation": False,
    }
    (root / "financial-record.json").write_text(json.dumps(finance), encoding="utf-8")
    (source / "financial-record.json").write_text(json.dumps(finance), encoding="utf-8")
    contract = source / "official-contract-review"
    contract.mkdir()
    (contract / "official-contract-review.md").write_text("TEST ONLY contract review", encoding="utf-8")
    (source / "packet.json").write_text("TEST ONLY packet", encoding="utf-8")
    preflight = source / "get-only-current"
    preflight.mkdir()
    (preflight / "preflight.json").write_text("TEST ONLY preflight", encoding="utf-8")
    mandate = {
        "schema": "JACO_USER_EXECUTION_MANDATE/2", "request_id": production.REQUEST_ID,
        "revision": "818556d97edb648f", "endpoint": production.ENDPOINT,
        "payload_sha256": sha256(production._canon(production._PAYLOAD)).hexdigest(),
        "maximum_usd": production.MAX_USD, "call_limit": 1, "budget_cap_usd": "10.00",
        "scope": "EVALUATION_GENERATION_ONLY", "release_authorization": "NOT_AUTHORIZED",
        "license_status": "EVALUATION_ONLY_CONDITIONALLY_SUPPORTABLE",
    }
    (root / "user-mandate.json").write_text(json.dumps(mandate), encoding="utf-8")
    provenance = {
        "schema": "JACO_FLUX3_CANONICAL_TRANSCRIPT_PROVENANCE/1", "session_id": session_id,
        "user_message_id": 144800, "adjacent_assistant_message_id": 144799,
        "user_message_sha256": sha256(user_content.encode()).hexdigest(),
        "assistant_message_sha256": sha256(assistant_content.encode()).hexdigest(),
        "financial_record_sha256": production._sha_path(source / "financial-record.json"),
        "review_source_sha256": production._sha_path(contract / "official-contract-review.md"),
    }
    (root / "provenance.json").write_text(json.dumps(provenance), encoding="utf-8")
    policy = {
        "schema": "JACO_FLUX3_LOCAL_OPERATOR_POLICY/1",
        "trust_derivation": "CANONICAL_USER_TRANSCRIPT_PLUS_TRUSTED_LOCAL_OPERATOR",
        "mandate_sha256": production._sha_path(root / "user-mandate.json"),
        "provenance_sha256": production._sha_path(root / "provenance.json"),
        "source_evidence": {
            "financial_record": production._sha_path(source / "financial-record.json"),
            "official_contract_review": production._sha_path(contract / "official-contract-review.md"),
            "packet": production._sha_path(source / "packet.json"),
            "get_only_preflight": production._sha_path(preflight / "preflight.json"),
        },
    }
    (root / "parent-provisioned-policy.json").write_text(json.dumps(policy), encoding="utf-8")
    (root / "production-composition.json").write_text(json.dumps({
        "schema": "JACO_FLUX3_FIXED_PRODUCTION_COMPOSITION/1", "financial_record": "financial-record.json",
    }), encoding="utf-8")
    (root / "final-admission-authorization.json").write_text(json.dumps({
        "schema": 1, "kind": "JACO_FLUX3_FINAL_ADMISSION_AUTHORIZATION", "version": "TEST_ONLY",
        "request_id": production.REQUEST_ID, "endpoint": production.ENDPOINT,
        "payload_sha256": sha256(production._canon(production._PAYLOAD)).hexdigest(),
        "maximum_usd": production.MAX_USD, "call_limit": 1,
        "user_authorization_evidence": "USER_AUTHORIZED_LOCAL_QA_AND_ONE_ALTERNATIVE_IMAGE_EVAL_WITHIN_USD10",
    }), encoding="utf-8")
    state = tmp_path / "TEST_ONLY_NONSECRET_canonical_state.sqlite3"
    with sqlite3.connect(state) as database:
        database.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)")
        database.execute("INSERT INTO messages VALUES (?, ?, ?, ?)", (144799, session_id, assistant_role, assistant_content))
        database.execute("INSERT INTO messages VALUES (?, ?, ?, ?)", (144800, session_id, "user", user_content))
    monkeypatch.setattr(production, "_PROVISIONED_ROOT", root)
    monkeypatch.setattr(production, "_SOURCE_EVIDENCE_ROOT", source)
    monkeypatch.setattr(production, "_CANONICAL_STATE_DB", state)
    return root


def test_mandate_loader_requires_pinned_root_and_never_claims_license_verification(tmp_path):
    """Local operator authority is source-bound, not a caller root or fake license."""
    from src.hybrid.jaco_flux3_production import _PROVISIONED_ROOT, _validate_mandate

    caller_root = tmp_path / "caller-root"
    caller_root.mkdir()
    with pytest.raises(ProductionCompositionError, match="pinned deployment root"):
        _validate_mandate(caller_root)

    pinned = _PROVISIONED_ROOT
    if not pinned.exists():
        pytest.skip("operator-provisioned mandate has not been materialized")
    result = _validate_mandate(pinned)
    assert result["trust_derivation"] == "CANONICAL_USER_TRANSCRIPT_PLUS_TRUSTED_LOCAL_OPERATOR"
    assert result["license_status"] == "EVALUATION_ONLY_CONDITIONALLY_SUPPORTABLE"
    assert result["release_authorization"] == "NOT_AUTHORIZED"


def test_cli_rejects_caller_selected_root_before_any_fixture_network(tmp_path):
    """A caller cannot turn a localhost fixture into the production authority."""
    completed = subprocess.run(
        [sys.executable, "-m", "src.hybrid.jaco_flux3_production", "--root", str(tmp_path)],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode != 0
    assert "--root" in completed.stderr


def test_factory_rejects_caller_root_and_legacy_config_urls(tmp_path):
    """Config cannot nominate a trust root, endpoint, result URL, or image input."""
    (tmp_path / "production-composition.json").write_text(
        json.dumps(
            {
                "schema": "JACO_FLUX3_FIXED_PRODUCTION_COMPOSITION/1",
                "financial_record": "finance.json",
                "submit_url": "http://127.0.0.1/steal-key",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ProductionCompositionError, match="closed production composition"):
        FixedFalProductionFactory(tmp_path)

    (tmp_path / "production-composition.json").write_text(
        json.dumps(
            {
                "schema": "JACO_FLUX3_FIXED_PRODUCTION_COMPOSITION/1",
                "financial_record": "finance.json",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ProductionCompositionError, match="caller-selected production root"):
        FixedFalProductionFactory(tmp_path)


def test_only_fixed_https_fal_hosts_can_receive_provider_or_result_traffic():
    _fixed_https_url(
        f"https://{_FAL_QUEUE_HOST}/blackforestlabs/flux-3/text-to-image",
        hosts=frozenset({_FAL_QUEUE_HOST}),
    )
    # Official fal CDN documentation shows v3b.fal.media files URLs.  The query
    # is opaque signed data and may never be recorded in evidence.
    projection = _fal_png_locator_projection(
        "https://v3b.fal.media/files/b/store/candidate.png?signature=SIGNED_SECRET",
    )
    assert projection == {
        "scheme": "https", "hostname": "v3b.fal.media", "port": None,
        "extension": ".png", "query_present": True,
    }
    assert "SIGNED_SECRET" not in json.dumps(projection)
    for hostile in (
        "http://queue.fal.run/submit",
        "https://queue.fal.run.evil.example/submit",
        "https://user@queue.fal.run/submit",
        "https://queue.fal.run:444/submit",
        "https://127.0.0.1/submit",
        "https://queue.fal.run/submit#fragment",
        "https://v3b.fal.media.evil.example/result.png?signature=x",
        "https://user@v3b.fal.media/result.png?signature=x",
        "https://v3b.fal.media:444/result.png?signature=x",
    ):
        with pytest.raises(ProductionCompositionError, match="unapproved FAL HTTPS origin"):
            _fixed_https_url(hostile, hosts=_FAL_MEDIA_HOSTS)
    with pytest.raises(ProductionCompositionError, match="PNG locator"):
        _fal_png_locator_projection("https://v3b.fal.media/files/b/store/no-extension?signature=x")


def test_persisted_official_openapi_uses_metadata_endpoint_and_official_queue_routes():
    """Regression fixture is the reviewed official OpenAPI, not an invented shape."""
    fixture = (
        Path(__file__).parents[1]
        / "fixtures"
        / "fal_flux3_queue_openapi_20261106.json"
    )
    openapi = json.loads(fixture.read_text(encoding="utf-8"))
    assert _official_openapi_endpoint(openapi) == "blackforestlabs/flux-3/text-to-image"
    assert _queue_route(openapi, "status", "opaque/provider id") == (
        "https://queue.fal.run/blackforestlabs/flux-3/text-to-image/requests/opaque%2Fprovider%20id/status"
    )
    assert _queue_route(openapi, "result", "opaque/provider id") == (
        "https://queue.fal.run/blackforestlabs/flux-3/text-to-image/requests/opaque%2Fprovider%20id"
    )


def test_flat_1k_price_snapshot_keeps_consumer_url_raw_hash_and_no_fake_expiry():
    snapshot = _price_snapshot(
        raw_price_page={
            "prices": [{"endpoint_id": "blackforestlabs/flux-3/text-to-image", "unit_price": 0.048, "unit": "image", "currency": "USD"}],
            "next_cursor": None,
            "has_more": False,
        },
        observed_at_epoch=1_700_000_000,
    )
    assert snapshot["api_url"] == _FAL_PRICE_API_URL
    assert snapshot["semantic_unit"] == "FLAT_1K_SINGLE_IMAGE"
    assert snapshot["maximum_usd"] == "0.048"
    assert snapshot["provider_expiry"] == "UNKNOWN_NOT_ASSERTED"
    assert len(snapshot["raw_sanitized_sha256"]) == 64


def test_schema_valid_multi_image_result_is_quarantined_without_promotion(tmp_path, monkeypatch):
    openapi = json.loads((Path(__file__).parents[1] / "fixtures" / "fal_flux3_queue_openapi_20261106.json").read_text())
    provider = _FixedFalProvider(tmp_path / "receipt.json", openapi)

    def fake_open(request):
        if request.full_url.endswith("/status"):
            return b'{"status":"COMPLETED","request_id":"remote"}'
        return b'{"images":[{"url":"https://fal.media/a.png"},{"url":"https://fal.media/b.png"}]}'

    monkeypatch.setattr(provider, "_open", fake_open)
    with pytest.raises(ProductionCompositionError, match="unexpected output cardinality"):
        asyncio.run(provider.recover(None, "local", "remote", {}, lambda **_: None))
    receipt = json.loads((tmp_path / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["qa_status"] == "FAIL_NOT_PROMOTABLE"
    assert receipt["actual_billed_amount"] == "UNKNOWN_UNTIL_BILLING_RECONCILIATION"
    assert not (tmp_path / "receipt.png").exists()


def test_recovery_status_get_uses_key_auth_on_persisted_queue_url(tmp_path, monkeypatch):
    """Recovery retains the exact POST-returned URL and authenticates its sole GET."""
    openapi = {"info": {"x-fal-metadata": {"endpointId": "blackforestlabs/flux-3/text-to-image"}}, "paths": {}}
    provider = _FixedFalProvider(tmp_path / "receipt.json", openapi, credential="TEST_NONSECRET_KEY")
    persisted_url = "https://queue.fal.run/blackforestlabs/flux-3/requests/remote/status"
    recovery = tmp_path / "provider-receipt.recovery.json"
    recovery.write_text(json.dumps({
        "schema": 1, "provider_request_id": "remote", "status_url": persisted_url,
        "response_url": "https://queue.fal.run/blackforestlabs/flux-3/requests/remote",
    }), encoding="utf-8")
    seen = []

    def fake_open(request):
        seen.append(request)
        return b'{"status":"IN_PROGRESS","request_id":"remote"}'

    monkeypatch.setattr(provider, "_open", fake_open)
    with pytest.raises(ProductionCompositionError, match="request is pending"):
        asyncio.run(provider.recover(None, "local", "remote", str(recovery), lambda **_: None))
    assert len(seen) == 1
    assert seen[0].get_method() == "GET"
    assert seen[0].full_url == persisted_url
    assert seen[0].get_header("Authorization") == "Key TEST_NONSECRET_KEY"


def test_test_only_cli_composition_reviews_reserves_submits_and_fresh_process_recovers(tmp_path):
    """The explicit local TEST domain cannot activate or route the LIVE factory."""
    root = tmp_path / "protected-test-policy"
    root.mkdir()
    config = root / "test-composition.json"
    config.write_text(
        json.dumps(
            {
                "schema": "JACO_FLUX3_TEST_COMPOSITION/1",
                "mode": "TEST",
                "domain": "LOCAL_ONLY_NON_PROMOTABLE",
                "episode_id": "JACO_GENESIS28",
                "output_root": str(root / "runs"),
            }
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": str(Path.cwd()), "FAL_KEY": "must-not-be-read"}
    command = [
        sys.executable,
        "-m",
        "src.hybrid.jaco_flux3_production",
        "--test-config",
        str(config),
        "--execute",
    ]

    first = subprocess.run(command, cwd=Path.cwd(), env=env, capture_output=True, text=True, timeout=30, check=False)
    assert first.returncode != 0
    assert "TEST_SUBMISSION_INTERRUPTED_AFTER_REMOTE_ID" in first.stderr
    state = json.loads((root / "runs" / "transport-counts.json").read_text(encoding="utf-8"))
    assert state == {"gets": 0, "posts": 1}

    second = subprocess.run(command, cwd=Path.cwd(), env=env, capture_output=True, text=True, timeout=30, check=True)
    result = json.loads(second.stdout)
    assert result["mode"] == "TEST"
    assert result["status"] == "COMPLETE"
    assert result["review_provenance"] == "LOCAL_PROCESS_REVIEW_SAME_HOST_NON_PROMOTABLE"
    assert json.loads((root / "runs" / "transport-counts.json").read_text(encoding="utf-8")) == {"gets": 1, "posts": 1}

    # No command-line option can convert the isolated TEST composition into LIVE.
    live = subprocess.run(command + ["--mode", "LIVE"], cwd=Path.cwd(), env=env, capture_output=True, text=True, timeout=30, check=False)
    assert live.returncode != 0
    assert "--mode" in live.stderr


def test_fixed_production_execute_uses_real_executor_and_recovery_without_resubmit(tmp_path, monkeypatch):
    """Production composition is exercised with blocked HTTP and the real reviewer/executor."""
    import src.hybrid.jaco_flux3_production as production

    root = tmp_path / "fixed-production"
    root.mkdir()
    (root / "production-composition.json").write_text(json.dumps({
        "schema": "JACO_FLUX3_FIXED_PRODUCTION_COMPOSITION/1", "financial_record": "finance.json",
    }), encoding="utf-8")
    (root / "parent-provisioned-policy.json").write_text("{}", encoding="utf-8")
    payload_sha = production.sha256(production._canon(production._PAYLOAD)).hexdigest()
    (root / "final-admission-authorization.json").write_text(json.dumps({
        "schema": 1, "kind": "JACO_FLUX3_FINAL_ADMISSION_AUTHORIZATION", "version": "fixture-consent",
        "request_id": production.REQUEST_ID, "endpoint": production.ENDPOINT, "payload_sha256": payload_sha,
        "maximum_usd": production.MAX_USD, "call_limit": 1,
        "user_authorization_evidence": "USER_AUTHORIZED_LOCAL_QA_AND_ONE_ALTERNATIVE_IMAGE_EVAL_WITHIN_USD10",
    }), encoding="utf-8")
    (root / "finance.json").write_text(json.dumps({
        "schema": "jaco-factory-financial-reconciliation/1", "request_hash": production.REQUEST_ID,
        "currency": "USD", "total_episode_cap": "10.00", "authorizes_generation": False,
    }), encoding="utf-8")
    monkeypatch.setattr(production, "_PROVISIONED_ROOT", root)
    monkeypatch.setattr(production, "_validate_mandate", lambda _root: {
        "mandate_sha256": "a" * 64, "release_authorization": "NOT_AUTHORIZED",
    })
    monkeypatch.setattr(production, "resolve_default_fal_credential", lambda: ("fixture-key", {}))

    class PricingContent:
        async def read(self, _limit):
            return json.dumps({"prices": [{"endpoint_id": production.ENDPOINT, "unit_price": 0.048,
                "unit": "image", "currency": "USD"}], "next_cursor": None, "has_more": False}).encode()

    class PricingResponse:
        status = 200
        content = PricingContent()
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False

    class PricingSession:
        def __init__(self, **_): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        def get(self, url, **_):
            assert url == production._FAL_PRICE_API_URL
            return PricingResponse()

    class PricingBoundary:
        ClientTimeout = staticmethod(lambda **_: object())
        ClientSession = PricingSession
        DummyCookieJar = staticmethod(lambda: object())

    monkeypatch.setitem(sys.modules, "aiohttp", PricingBoundary)
    openapi = {"info": {"x-fal-metadata": {"endpointId": production.ENDPOINT}}, "paths": {}}

    class OpenApiResponse:
        def __enter__(self): return self
        def __exit__(self, *_): return False
        def read(self): return json.dumps(openapi).encode()

    monkeypatch.setattr(production.urllib.request, "urlopen", lambda request, timeout: OpenApiResponse())

    factory = production.FixedFalProductionFactory(root)
    requests = []

    def blocked_http(request):
        if isinstance(request, str):
            requests.append(("GET", request))
            return b"\x89PNG\r\n\x1a\nfixture"
        requests.append((request.get_method(), request.full_url))
        if request.get_method() == "POST":
            return b'{"request_id":"remote-1","status":"COMPLETED","status_url":"https://queue.fal.run/status","response_url":"https://queue.fal.run/result"}'
        if request.full_url.endswith("/status"):
            return b'{"request_id":"remote-1","status":"COMPLETED"}'
        if request.full_url.endswith("/result"):
            return b'{"images":[{"url":"https://fal.media/image.png"}]}'
        return b"\x89PNG\r\n\x1a\nfixture"

    monkeypatch.setattr(production._FixedFalProvider, "_open", staticmethod(blocked_http))
    with pytest.raises(production.ProductionCompositionError, match="canonical budget bootstrap blocked"):
        asyncio.run(factory.execute())
    with pytest.raises(production.ProductionCompositionError, match="canonical budget bootstrap blocked"):
        asyncio.run(factory.execute())
    assert requests == []
    assert not (root / "ledger" / "standing-budget.sqlite3").exists()
    assert not (root / "execution" / "provider-receipt.quarantine.png").exists()


@pytest.mark.asyncio
async def test_execute_accepts_reconciled_bootstrap_and_reaches_pretransport_boundary(
    tmp_path, monkeypatch
):
    """An initialized zero-history ledger is reusable; diagnostics stop before transport."""
    import src.hybrid.jaco_flux3_production as production

    root = tmp_path / "provisioned-root"
    root.mkdir()
    (root / "production-composition.json").write_text(
        json.dumps(
            {
                "schema": "JACO_FLUX3_FIXED_PRODUCTION_COMPOSITION/1",
                "financial_record": "finance.json",
            }
        ),
        encoding="utf-8",
    )
    (root / "parent-provisioned-policy.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(production, "_PROVISIONED_ROOT", root)
    factory = production.FixedFalProductionFactory(root)
    monkeypatch.setattr(
        factory,
        "_mandate",
        lambda: {"mandate_sha256": "a" * 64, "release_authorization": "NOT_AUTHORIZED"},
    )
    ledger_path = root / "ledger" / "standing-budget.sqlite3"
    production.StandingBudgetLedger(
        ledger_path,
        policy_sha256="a" * 64,
        episode_limit_usd="10",
        month_limit_usd="10",
        experiment_limit_usd="10",
        mode="LIVE",
    )
    monkeypatch.setattr(
        production,
        "bootstrap_canonical_budget",
        lambda *, root: {
            "status": "PASS_RECONCILED",
            "ledger_path": str(ledger_path),
            "policy_sha256": "a" * 64,
            "available_usd": "10.000000",
        },
    )

    async def network_blocked_before_transport():
        raise production.ProductionCompositionError("NETWORK_BLOCKED_BEFORE_TRANSPORT")

    monkeypatch.setattr(factory, "fresh_review", network_blocked_before_transport)
    with pytest.raises(production.ProductionCompositionError, match="NETWORK_BLOCKED_BEFORE_TRANSPORT"):
        await factory.execute()

    assert not (root / "execution" / "provider-receipt.json").exists()
    with sqlite3.connect(ledger_path) as db:
        assert db.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_actual_fresh_review_is_awaitable_inside_execute_loop_with_only_http_boundaries_faked(tmp_path, monkeypatch):
    """Real mandate validation and review run against explicit TEST-only authority."""
    import src.hybrid.fal_get_only_preflight as fal_preflight
    import src.hybrid.jaco_flux3_production as production

    root = _write_hermetic_production_authority(tmp_path, monkeypatch)
    credential_fixture = tmp_path / "test-fal-credentials.fixture"
    credential_fixture.write_text("FAL_KEY=test-only-fresh-review-key\n", encoding="utf-8")
    monkeypatch.setattr(fal_preflight, "DEFAULT_DOTENV_PATH", credential_fixture)
    factory = production.FixedFalProductionFactory(root)

    class Response:
        def __init__(self, body): self.body = body
        def read(self): return self.body
        def __enter__(self): return self
        def __exit__(self, *_): return False

    class Content:
        async def read(self, _limit):
            return json.dumps({"prices": [{"endpoint_id": production.ENDPOINT, "unit_price": 0.048,
                "unit": "image", "currency": "USD"}], "next_cursor": None, "has_more": False}).encode()

    class AioResponse:
        status = 200
        content = Content()
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False

    class Session:
        def __init__(self, **_): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return False
        def get(self, url, **_):
            assert url == production._FAL_PRICE_API_URL
            return AioResponse()

    class AiohttpBoundary:
        ClientTimeout = staticmethod(lambda **_: object())
        ClientSession = Session
        DummyCookieJar = staticmethod(lambda: object())

    monkeypatch.setitem(sys.modules, "aiohttp", AiohttpBoundary)
    # Fresh-review transport receives the complete saved official contract, not
    # a synthetic endpoint-only shape that omits the protected 16:9 enum.
    openapi = json.loads(
        next((Path(__file__).parents[1] / "fixtures").glob("*.json")).read_text(encoding="utf-8")
    )
    monkeypatch.setattr(production.urllib.request, "urlopen", lambda request, timeout: Response(json.dumps(openapi).encode()))

    review, run, quote = await factory.fresh_review()
    assert review["packet_sha256"] == production._sha_path(run / "packet.json")
    assert quote == run / "quote.json"


def test_actual_fresh_review_subprocess_accepts_only_complete_test_contract(tmp_path, monkeypatch):
    """A fresh interpreter validates the closed fixture contract before review."""
    root = _write_hermetic_production_authority(tmp_path, monkeypatch)
    credential_fixture = tmp_path / "test-fal-credentials.fixture"
    credential_fixture.write_text("FAL_KEY=test-only-subprocess-key\n", encoding="utf-8")
    script = tmp_path / "run_test_only_fresh_review.py"
    script.write_text(
        """import asyncio, json, sys, types
from pathlib import Path
import src.hybrid.fal_get_only_preflight as fal
import src.hybrid.jaco_flux3_production as production
root, source, state, credential = map(Path, sys.argv[1:])
production._PROVISIONED_ROOT = root
production._SOURCE_EVIDENCE_ROOT = source
production._CANONICAL_STATE_DB = state
fal.DEFAULT_DOTENV_PATH = credential
class Response:
    def read(self): return next(Path("tests/fixtures").glob("*.json")).read_bytes()
    def __enter__(self): return self
    def __exit__(self, *_): return False
class Content:
    async def read(self, _limit): return json.dumps({\"prices\": [{\"endpoint_id\": production.ENDPOINT, \"unit_price\": 0.048, \"unit\": \"image\", \"currency\": \"USD\"}], \"next_cursor\": None, \"has_more\": False}).encode()
class AioResponse:
    status = 200
    content = Content()
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
class Session:
    def __init__(self, **_): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *_): return False
    def get(self, url, **_):
        assert url == production._FAL_PRICE_API_URL
        return AioResponse()
sys.modules[\"aiohttp\"] = types.SimpleNamespace(ClientTimeout=lambda **_: object(), ClientSession=Session, DummyCookieJar=lambda: object())
production.urllib.request.urlopen = lambda request, timeout: Response()
review, run, quote = asyncio.run(production.FixedFalProductionFactory(root).fresh_review())
assert review[\"packet_sha256\"] == production._sha_path(run / \"packet.json\")
assert quote == run / \"quote.json\"
print(json.dumps({\"review\": review[\"output_sha256\"], \"run\": str(run)}))
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [sys.executable, str(script), str(root), str(tmp_path / "TEST_ONLY_NONSECRET_SOURCE_EVIDENCE"),
         str(tmp_path / "TEST_ONLY_NONSECRET_canonical_state.sqlite3"), str(credential_fixture)],
        cwd=Path.cwd(), env={**os.environ, "PYTHONPATH": str(Path.cwd())},
        capture_output=True, text=True, timeout=30, check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert len(result["review"]) == 64
    assert "TEST_ONLY_NONSECRET_PRODUCTION_AUTHORITY" in result["run"]


def test_validate_mandate_rejects_wrong_canonical_message_role_before_any_network(tmp_path, monkeypatch):
    """Matching hashes cannot authorize a swapped transcript role."""
    import src.hybrid.jaco_flux3_production as production

    root = _write_hermetic_production_authority(tmp_path, monkeypatch, assistant_role="user")

    with pytest.raises(production.ProductionCompositionError, match="canonical transcript"):
        production._validate_mandate(root)


@pytest.mark.parametrize("tampered", ["packet.json", "review-request.json", "quote.json", "openapi.json", "financial-record.json"])
def test_production_final_receipt_rejects_each_post_review_binding_before_ledger(tmp_path, tampered):
    """A PASS verdict is insufficient when any reviewed input changes afterwards."""
    import src.hybrid.jaco_flux3_production as production

    _review, packet, quote = production._test_review(tmp_path)
    run = packet.parent
    payload_sha = production.sha256(production._canon(production._PAYLOAD)).hexdigest()
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps({
        "schema": 1, "kind": "JACO_FLUX3_FINAL_ADMISSION_AUTHORIZATION", "version": "test",
        "request_id": production.REQUEST_ID, "endpoint": production.ENDPOINT, "payload_sha256": payload_sha,
        "maximum_usd": production.MAX_USD, "call_limit": 1,
        "user_authorization_evidence": "USER_AUTHORIZED_LOCAL_QA_AND_ONE_ALTERNATIVE_IMAGE_EVAL_WITHIN_USD10",
    }), encoding="utf-8")
    target = run / tampered
    target.write_bytes(target.read_bytes() + b" ")

    with pytest.raises(production.ProductionCompositionError, match="production final admission evidence"):
        production._production_final_receipt(packet=run / "packet.json", review=run / "review.json",
            authorization=authorization, quote=quote)
    assert not (tmp_path / "standing-budget.sqlite3").exists()
