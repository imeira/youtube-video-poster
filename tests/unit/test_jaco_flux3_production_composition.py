from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from src.hybrid.jaco_flux3_production import (
    _FAL_MEDIA_HOSTS,
    _FAL_PRICE_API_URL,
    _FAL_QUEUE_HOST,
    FixedFalProductionFactory,
    ProductionCompositionError,
    _fixed_https_url,
    _FixedFalProvider,
    _official_openapi_endpoint,
    _price_snapshot,
    _queue_route,
)


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
    _fixed_https_url("https://v3.fal.media/result.png?signature=x", hosts=_FAL_MEDIA_HOSTS)
    for hostile in (
        "http://queue.fal.run/submit",
        "https://queue.fal.run.evil.example/submit",
        "https://user@queue.fal.run/submit",
        "https://queue.fal.run:444/submit",
        "https://127.0.0.1/submit",
        "https://queue.fal.run/submit#fragment",
    ):
        with pytest.raises(ProductionCompositionError, match="unapproved FAL HTTPS origin"):
            _fixed_https_url(hostile, hosts=frozenset({_FAL_QUEUE_HOST}))


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
    """The real fresh-review method cannot nest asyncio.run under execute()."""
    import src.hybrid.jaco_flux3_production as production

    source_root = production._PROVISIONED_ROOT
    root = tmp_path / "provisioned-root"
    root.mkdir()
    for name in ("production-composition.json", "parent-provisioned-policy.json", "user-mandate.json", "provenance.json", "financial-record-source.json"):
        (root / name).write_bytes((source_root / name).read_bytes())
    monkeypatch.setattr(production, "_PROVISIONED_ROOT", root)
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
    openapi = {"info": {"x-fal-metadata": {"endpointId": production.ENDPOINT}}, "paths": {}}
    monkeypatch.setattr(production.urllib.request, "urlopen", lambda request, timeout: Response(json.dumps(openapi).encode()))

    review, run, quote = await factory.fresh_review()
    assert review["packet_sha256"] == production._sha_path(run / "packet.json")
    assert quote == run / "quote.json"


def test_validate_mandate_rejects_wrong_canonical_message_role_before_any_network(tmp_path, monkeypatch):
    """Matching provenance hashes alone cannot authorize a swapped transcript role."""
    import sqlite3

    import src.hybrid.jaco_flux3_production as production

    state = tmp_path / "state.db"
    connection = sqlite3.connect(state)
    connection.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT)")
    connection.execute("INSERT INTO messages VALUES (?, ?, ?, ?)", (144799,
        "local-712d007e4f3efba4d8c6cfc52f947e69023fed6f445a54206a6c3ede9b317fbb", "user", "approved"))
    connection.execute("INSERT INTO messages VALUES (?, ?, ?, ?)", (144800,
        "local-712d007e4f3efba4d8c6cfc52f947e69023fed6f445a54206a6c3ede9b317fbb", "user", "autorizo"))
    connection.commit(); connection.close()
    monkeypatch.setattr(production, "_CANONICAL_STATE_DB", state)

    with pytest.raises(production.ProductionCompositionError, match="canonical transcript"):
        production._validate_mandate(production._PROVISIONED_ROOT)


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
