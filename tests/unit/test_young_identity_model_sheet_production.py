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
    qualification = {"schema": "jaco-test-deployment-qualification/v2", "status": "QUALIFIED", "operation_id": production.OPERATION_ID, "episode_id": production.EPISODE_ID, "policy_sha256": authority["policy_sha256"], "operation_policy_sha256": authority["policy_sha256"], "preflight_sha256": authority["preflight_sha256"], "mode": "TEST", "authorization": "TEST_FIXTURE_CONSENT_ONLY", "review_packet_schema": "jaco-fal-flux3-successor-benchmark/3", "reviewer": "LOCAL_PROCESS_REVIEW_AUTOMATED_V1", "ci_digest_sha256": "0" * 64, "execution_code_sha256": production._execution_source_sha256(), "financial_policy_sha256": financial_sha, "consent_fixture": {"kind": "TEST_ONLY_CONSENT_FIXTURE"}}
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


def _actual_v1_policy_three_view_root(root, monkeypatch):
    """Complete sanitized copy of the canonical v1 shape, not a reduced policy."""
    from hashlib import sha256

    import src.hybrid.young_identity_model_sheet_production as production

    financial = _write(root / "financial-record-source.json", {
        "schema": "jaco-factory-financial-reconciliation/1",
        "request_hash": production.REQUEST_ID, "revision": "818556d97edb648f",
        "currency": "USD", "total_episode_cap": "10.00", "status": "APPROVED_USD10",
        "approval_scope": "FINANCIAL_ONLY", "authorizes_generation": False,
    })
    financial_sha = sha256(financial.read_bytes()).hexdigest()
    monkeypatch.setattr(production, "FINANCIAL_SHA256", financial_sha)
    prompt_content = (
        '{"responses": [{"question": "Posso registrar uma autorização operacional permanente para gerar e corrigir os ativos de Jacó dentro do teto TOTAL já aprovado de US$ 10, incluindo o US$ 0,048 já gasto? Cada chamada continuará exigindo preço máximo verificável, reserva, revisão independente e QA, sem repetir chamadas ambíguas. Suas aprovações rotineiras continuarão sendo thumbnail e vídeo final; publicação exigirá ordem separada. Isso substitui a limitação atual de autorizar apenas uma imagem de teste.", "choices_offered": ["Autorizar execução dentro dos US$ 10", "Autorizar só mais uma imagem de US$ 0,048", "Manter apenas trabalho sem novas cobranças"], "status": "unanswered", "user_response": null}], "outcome": "timed_out", "notice": "[user did not respond within 60m]"}'
    )
    state_db = root / "canonical-state.sqlite3"
    with sqlite3.connect(state_db) as database:
        database.execute("CREATE TABLE messages (id INTEGER, session_id TEXT, role TEXT, content TEXT)")
        database.executemany("INSERT INTO messages VALUES (?, ?, ?, ?)", [
            (147618, "local-712d007e4f3efba4d8c6cfc52f947e69023fed6f445a54206a6c3ede9b317fbb", "tool", prompt_content),
            (147620, "local-712d007e4f3efba4d8c6cfc52f947e69023fed6f445a54206a6c3ede9b317fbb", "user", "autorizo"),
        ])
    canonical = __import__("src.hybrid.jaco_flux3_production", fromlist=["x"])
    monkeypatch.setattr(canonical, "_CANONICAL_STATE_DB", state_db)
    evidence = {}
    for name, body in {
        "canonical_script_packet": b"sanitized script packet",
        "ep8_f001_manifest": b"sanitized EP8 manifest",
        "ep8_f001_raster": b"sanitized EP8 raster",
        "ep8_f001_palette": b"derived palette bytes",
    }.items():
        path = root / "evidence" / f"{name}.bin"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        evidence[name] = {"path": str(path), "sha256": sha256(body).hexdigest(), "bytes": len(body)}
    # This is the byte-faithful reviewed payload; only external evidence paths
    # are sanitized into this temporary, hermetic fixture root.
    payload = json.loads(json.dumps(production.EXPECTED_PAYLOAD))
    preflight = _write(root / "preflight" / production.PREFLIGHT_PATH.name, {
        "schema": "jaco-episode-operation-preflight/v3",
        "status": "VALIDATED_PRE_DISPATCH_BLOCKED_MISSING_OPERATION_AUTHORIZATION",
        "episode_id": production.EPISODE_ID, "request_id": production.REQUEST_ID,
        "replay_prohibition": True, "source_evidence": evidence,
        "operation": {"id": production.OPERATION_ID, "kind": "MODEL_SHEET_THREE_VIEW",
                      "endpoint": production.ENDPOINT, "call_limit": 1, "maximum_usd": "0.048",
                      "payload": payload, "payload_sha256": sha256(production._canonical(payload)).hexdigest()},
    })
    policy = _write(root / "standing-policy" / production.POLICY_PATH.name, {
        "schema": "jaco-standing-episode-operation-policy/v1", "version": 1,
        "policy_id": "JACO_GENESIS28_STANDING_OPERATION_POLICY",
        "status": "ACTIVE_FOR_DERIVATION_REQUIRES_INDEPENDENT_OPERATION_REVIEW",
        "episode_id": production.EPISODE_ID, "root_request_id": production.REQUEST_ID,
        "trust_derivation": "CANONICAL_USER_TRANSCRIPT_PLUS_TRUSTED_LOCAL_OPERATOR",
        "canonical_consent": {"session_id": "local-712d007e4f3efba4d8c6cfc52f947e69023fed6f445a54206a6c3ede9b317fbb", "consent_message_id": 147620,
                               "consent_role": "user", "prompt_message_id": 147618, "prompt_role": "tool",
                               "consent_literal_utf8_bytes": 8,
                               "consent_literal_utf8_sha256": "f54d0bf3c6c609d5e7f93d348d4332364fdee0edc1592ea2a30137ba31ead0e1",
                               "prompt_utf8_bytes": 802, "prompt_utf8_sha256": "50b8aa006942c74cdc25d08ecfe9855c6349e6d2ca8d812b1472c1c2f2f5b44e"},
        "allowed_operation_class": {"kind": "QUALIFIED_JACO_EPISODE_ASSET_GENERATION_OR_QUALITY_REMEDIATION",
                                    "input_kind": "TEXT_TO_IMAGE", "providers": [production.ENDPOINT],
                                    "max_calls_per_operation": 1,
                                    "per_operation_cap_must_be_positive_and_at_most_remaining": True},
        "admission_requirements": ["unique_operation_id_not_previously_claimed"],
        "explicit_exclusions": ["publication_requires_later_explicit_separate_command"],
        "financial_baseline": {"canonical_settlement_receipt_path": "C:/sanitized/settlement.json",
                               "canonical_settlement_receipt_sha256": "c" * 64, "currency": "USD",
                               "episode_cap_usd": "10.000000", "no_duplicate_cap_authority": True,
                               "pending_usd": "0.000000", "prior_root_request_is_consumed": True,
                               "remaining_usd": "9.952000", "settled_actual_usd": "0.048000"},
        "history_preservation": {"does_not_overwrite_path": "C:/sanitized/user-mandate.json",
                                 "old_one_shot_sha256": "d" * 64,
                                 "supersedes_for_future_operations_only": "JACO_USER_EXECUTION_MANDATE/2 one-shot canary"},
    })
    authority = {"schema": "jaco-derived-operation-authority/v2", "status": "READY_FOR_INDEPENDENT_PARENT_REVIEW_NO_RESERVATION_NO_POST", "operation_id": production.OPERATION_ID, "episode_id": production.EPISODE_ID, "predecessor_operation_id": "", "executor_predecessor_request_id": "", "executor_category": "first", "migration_reason": "INITIAL_THREE_VIEW_PREFLIGHT", "non_reuse": True, "mode": "TEST", "policy_path": str(policy), "policy_sha256": sha256(policy.read_bytes()).hexdigest(), "preflight_path": str(preflight), "preflight_sha256": sha256(preflight.read_bytes()).hexdigest(), "maximum_next_cost_usd": "0.048", "canonical_ledger": {"path": str(root / "ledger" / "standing-budget.sqlite3"), "financial_policy_sha256": financial_sha, "operation_policy_sha256": sha256(policy.read_bytes()).hexdigest(), "financial_source": {"path": str(financial), "sha256": financial_sha}}}
    qualification = {"schema": "jaco-test-deployment-qualification/v2", "status": "QUALIFIED", "operation_id": production.OPERATION_ID, "episode_id": production.EPISODE_ID, "policy_sha256": authority["policy_sha256"], "operation_policy_sha256": authority["policy_sha256"], "preflight_sha256": authority["preflight_sha256"], "mode": "TEST", "authorization": "TEST_FIXTURE_CONSENT_ONLY", "review_packet_schema": "jaco-fal-flux3-successor-benchmark/3", "reviewer": "LOCAL_PROCESS_REVIEW_AUTOMATED_V1", "ci_digest_sha256": "0" * 64, "execution_code_sha256": production._execution_source_sha256(), "financial_policy_sha256": financial_sha, "consent_fixture": {"kind": "TEST_ONLY_CONSENT_FIXTURE"}}
    qualification_path = _write(root / "standing-policy" / production.QUALIFICATION_PATH.name, qualification)
    authority["deployment_qualification"] = {"path": str(qualification_path), "sha256": sha256(qualification_path.read_bytes()).hexdigest()}
    return _write(root / "standing-policy" / production.AUTHORITY_PATH.name, authority)


def _qualified_live_root(root, monkeypatch):
    """Install a hermetic v1 LIVE authority through the public fixed-root path."""
    from hashlib import sha256

    import src.hybrid.young_identity_model_sheet_production as production

    authority_path = _actual_v1_policy_three_view_root(root, monkeypatch)
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    qualification_path = Path(authority["deployment_qualification"]["path"])
    qualification = json.loads(qualification_path.read_text(encoding="utf-8"))
    authority["mode"] = "LIVE"
    qualification.update({
        "schema": "jaco-protected-deployment-qualification/v2",
        "mode": "LIVE",
        "authorization": "STANDING_USER_AUTHORIZATION_PROTECTED_POLICY",
        "execution_code_sha256": production._execution_source_sha256(),
    })
    del qualification["consent_fixture"]
    _write(qualification_path, qualification)
    authority["deployment_qualification"] = {
        "path": str(qualification_path),
        "sha256": sha256(qualification_path.read_bytes()).hexdigest(),
    }
    _write(authority_path, authority)
    monkeypatch.setattr(production, "ROOT", root)
    monkeypatch.setattr(production, "AUTHORITY_PATH", authority_path)
    return authority_path


@pytest.mark.asyncio
async def test_public_live_composition_reaches_final_admission_then_recovers_get_only(
    tmp_path, monkeypatch
):
    """A public LIVE Job uses the qualified composition, not a TEST relabel."""
    import src.hybrid.jaco_flux3_production as canonical_production
    import src.hybrid.young_identity_model_sheet_production as production
    from src.hybrid.execution import ProviderResult

    _qualified_live_root(tmp_path, monkeypatch)
    quote, openapi = _fixture_inputs()
    calls = []

    class OfflineLiveProvider:
        mode = "LIVE"

        def __init__(self, output, _openapi, credential):
            assert credential == "hermetic-non-secret-credential"
            self.output = output
            calls.append("provider")

        async def submit(self, _job, _request_id, checkpoint):
            calls.append("post")
            checkpoint(provider_id="live-remote-1", partial=self.output)
            raise TimeoutError("controlled checkpoint crash")

        async def recover(self, _job, _request_id, remote, _partial, _checkpoint):
            assert remote == "live-remote-1"
            calls.append("get")
            self.output.write_bytes(b"recovered-live-result")
            return ProviderResult(self.output, Decimal("0.048"))

    monkeypatch.setattr(canonical_production, "_FixedFalProvider", OfflineLiveProvider)

    async def inputs(credential):
        assert credential == "hermetic-non-secret-credential"
        calls.append("quote")
        return quote, openapi

    factory = production.YoungIdentityModelSheetProduction()
    with pytest.raises(TimeoutError, match="controlled checkpoint crash"):
        await factory._execute_with_composition(
            quote_and_openapi=inputs,
            credential_loader=lambda: "hermetic-non-secret-credential",
        )
    result = await production.YoungIdentityModelSheetProduction()._execute_with_composition(
        quote_and_openapi=lambda _credential: (_ for _ in ()).throw(
            AssertionError("recovery must not quote or review")
        ),
        credential_loader=lambda: "hermetic-non-secret-credential",
    )

    assert result["mode"] == "LIVE" and result["status"] == "COMPLETE"
    assert calls == ["quote", "provider", "post", "provider", "get"]
    execution = tmp_path / "execution" / production.OPERATION_ID
    with sqlite3.connect(execution / "executor.sqlite3") as database:
        row = json.loads(database.execute("SELECT data FROM jobs").fetchone()[0])
    assert row["status"] == "COMPLETE"
    assert row["final_admission_marker"]["schema"] == 2
    assert row["final_admission_marker"]["kind"] == "JACO_FLUX3_FINAL_ADMISSION_MARKER"
    authority = json.loads(
        (tmp_path / "standing-policy" / production.AUTHORITY_PATH.name).read_text()
    )
    assert row["final_admission_marker"]["auth_scope"] == (
        f"{production.OPERATION_ID}:{authority['policy_sha256']}"
    )
    assert (execution / "review.json").is_file()
    with sqlite3.connect(tmp_path / "ledger" / "standing-budget.sqlite3") as database:
        assert database.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_empty_pre_post_executor_retries_qualified_admission(tmp_path, monkeypatch):
    """An initialized zero-job DB is not an ambiguous remote submission."""
    import src.hybrid.jaco_flux3_production as canonical_production
    import src.hybrid.young_identity_model_sheet_production as production
    from src.hybrid.execution import Executor, ProviderResult
    from src.hybrid.planner import Config

    _qualified_live_root(tmp_path, monkeypatch)
    execution = tmp_path / "execution" / production.OPERATION_ID
    Executor(execution / "executor.sqlite3", Config(limit=Decimal(10)))
    qualification = json.loads(
        (tmp_path / "standing-policy" / production.QUALIFICATION_PATH.name).read_text()
    )
    _write(execution / "protected-qualification.json", qualification)
    quote, openapi = _fixture_inputs()
    calls = []

    class OfflineLiveProvider:
        mode = "LIVE"

        def __init__(self, output, _openapi, _credential):
            self.output = output

        async def submit(self, _job, _request_id, checkpoint):
            calls.append("post")
            checkpoint(provider_id="pre-post-retry", partial=self.output)
            self.output.write_bytes(b"single retry")
            return ProviderResult(self.output, Decimal("0.048"))

    monkeypatch.setattr(canonical_production, "_FixedFalProvider", OfflineLiveProvider)
    result = await production.YoungIdentityModelSheetProduction()._execute_with_composition(
        quote_and_openapi=lambda _credential: _async(quote, openapi),
        credential_loader=lambda: "hermetic-non-secret-credential",
    )
    assert result["status"] == "COMPLETE"
    assert calls == ["post"]


@pytest.mark.asyncio
async def test_pre_post_admits_two_bound_settled_live_history_and_preserves_cumulative_budget(
    tmp_path, monkeypatch
):
    """Two prior FAL SETTLED receipts are history, not a fresh-operation claim."""
    from hashlib import sha256

    import src.hybrid.jaco_flux3_production as canonical_production
    import src.hybrid.young_identity_model_sheet_production as production
    from src.hybrid.execution import Executor, ProviderResult
    from src.hybrid.planner import Config
    from src.hybrid.standing_budget import StandingBudgetLedger

    authority_path = _qualified_live_root(tmp_path, monkeypatch)
    authority = json.loads(authority_path.read_text())
    execution = tmp_path / "execution" / production.OPERATION_ID
    Executor(execution / "executor.sqlite3", Config(limit=Decimal(10)))
    qualification = json.loads(Path(authority["deployment_qualification"]["path"]).read_text())
    _write(execution / "protected-qualification.json", qualification)
    ledger_path = tmp_path / "ledger" / "standing-budget.sqlite3"
    StandingBudgetLedger(
        ledger_path, policy_sha256=authority["canonical_ledger"]["financial_policy_sha256"],
        episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="LIVE",
    )
    # Sanitized byte-faithful copies of the two protected production settled
    # claims.  The scope is a complete authenticated identifier, not a label.
    histories = (
        ("7f19aab26d71464a9bc257f4f566d2c6cb642aab0bc55864bad8dcbaeab0ae47",
         "JACO_FLUX3_FINAL_ADMISSION:a32871d181a22a45291a526c29934abf3fa2a9aea17f30cf881832dd8cebf170"),
        ("27ef6a66be109984c208daa6e053e4a9d4f51a0202b46d624ea964336f74d052",
         "JACO_GEN28_STYLE_REMEDIATION_001:46bb52a78057088ef1030ed77f54dd4e4dc671aa67576c0074939389bb947e11"),
    )
    with sqlite3.connect(ledger_path) as db:
        for request_id, scope in histories:
            receipt = {
                "schema": 2, "authority": "TRANSPORT_AUTHENTICATED_FAL_READBACK",
                "local_request_id": request_id, "provider_request_id": f"remote-{request_id}",
                "endpoint_id": production.ENDPOINT, "auth_scope": scope, "authority_mode": "LIVE",
                "currency": "USD", "cost_total_micro_usd": "48000", "evidence_hash": "a" * 64,
                "provenance": "FAL_BILLING_READBACK",
            }
            body = json.dumps(receipt, sort_keys=True, separators=("," , ":"))
            db.execute(
                "INSERT INTO claims VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (request_id, production.EPISODE_ID, production.ENDPOINT, "2026-11", 48000, 0,
                 "SETTLED", 48000, "FAL_READBACK_V2", scope, f"remote-{request_id}", "LIVE", None),
            )
            db.execute(
                "INSERT INTO settlement_receipts VALUES(?,?,?,?,?)",
                (request_id, sha256(body.encode()).hexdigest(), 48000, body, "FAL_BILLING_READBACK"),
            )
    before = {row[0]: row[1:] for row in sqlite3.connect(ledger_path).execute(
        "SELECT claims.request_id, claims.actual, receipt_body, receipt_sha256 FROM claims JOIN settlement_receipts USING(request_id)"
    )}
    calls = []

    class OfflineLiveProvider:
        mode = "LIVE"
        def __init__(self, output, _openapi, _credential): self.output = output
        async def submit(self, _job, _request_id, checkpoint):
            calls.append("post"); checkpoint(provider_id="new-remote", partial=self.output)
            self.output.write_bytes(b"new operation")
            return ProviderResult(self.output, Decimal("0.048"))

    monkeypatch.setattr(canonical_production, "_FixedFalProvider", OfflineLiveProvider)
    quote, openapi = _fixture_inputs()
    result = await production.YoungIdentityModelSheetProduction()._execute_with_composition(
        quote_and_openapi=lambda _credential: _async(quote, openapi),
        credential_loader=lambda: "hermetic-non-secret-credential",
    )
    assert result["status"] == "COMPLETE" and calls == ["post"]
    with sqlite3.connect(ledger_path) as db:
        assert db.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 3
        assert db.execute("SELECT SUM(COALESCE(actual, cap)) FROM claims").fetchone()[0] == 144000
        after = {row[0]: row[1:] for row in db.execute(
            "SELECT claims.request_id, claims.actual, receipt_body, receipt_sha256 FROM claims JOIN settlement_receipts USING(request_id)"
        )}
    assert {key: after[key] for key in before} == before


@pytest.mark.parametrize("scope", [
    "JACO_FLUX3_FINAL_ADMISSION",
    "JACO_GEN28_STYLE_REMEDIATION_001",
    "JACO_FLUX3_FINAL_ADMISSION:a32871d181a22a45291a526c29934abf3fa2a9aea17f30cf881832dd8cebf170 ",
    "JACO_FLUX3_FINAL_ADMISSION:A32871D181A22A45291A526C29934ABF3FA2A9AEA17F30CF881832DD8CEBF170",
    "JACO_FLUX3_FINAL_ADMISSION:a32871d181a22a45291a526c29934abf3fa2a9aea17f30cf881832dd8cebf171",
])
def test_historical_scope_classifier_rejects_nonexact_scope_forms(scope):
    """No trim, case-fold, prefix, or arbitrary digest compatibility path."""
    from src.hybrid.young_identity_model_sheet_production import (
        _HISTORICAL_SETTLED_CLAIMS,
    )

    assert scope not in _HISTORICAL_SETTLED_CLAIMS


def test_historical_scope_classifier_binds_each_exact_scope_to_its_request():
    from src.hybrid.young_identity_model_sheet_production import (
        _HISTORICAL_SETTLED_CLAIMS,
    )

    assert _HISTORICAL_SETTLED_CLAIMS == {
        "JACO_FLUX3_FINAL_ADMISSION:a32871d181a22a45291a526c29934abf3fa2a9aea17f30cf881832dd8cebf170":
            "7f19aab26d71464a9bc257f4f566d2c6cb642aab0bc55864bad8dcbaeab0ae47",
        "JACO_GEN28_STYLE_REMEDIATION_001:46bb52a78057088ef1030ed77f54dd4e4dc671aa67576c0074939389bb947e11":
            "27ef6a66be109984c208daa6e053e4a9d4f51a0202b46d624ea964336f74d052",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("claim_kind", ["matching-id-foreign-scope", "operation-different-id", "unexpected-row"])
async def test_pre_post_empty_ledger_claims_block_before_credentials_quote_or_review(
    tmp_path, monkeypatch, claim_kind
):
    """A zero-job executor never makes an existing canonical claim retryable."""
    import src.hybrid.young_identity_model_sheet_production as production
    from src.hybrid.execution import Executor, Job
    from src.hybrid.planner import Config
    from src.hybrid.standing_budget import StandingBudgetLedger

    authority_path = _qualified_live_root(tmp_path, monkeypatch)
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    qualification = json.loads(Path(authority["deployment_qualification"]["path"]).read_text())
    execution = tmp_path / "execution" / production.OPERATION_ID
    Executor(execution / "executor.sqlite3", Config(limit=Decimal(10)))
    _write(execution / "protected-qualification.json", qualification)
    ledger = StandingBudgetLedger(
        tmp_path / "ledger" / "standing-budget.sqlite3",
        policy_sha256=authority["canonical_ledger"]["financial_policy_sha256"],
        episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="LIVE",
    )
    expected_scope = f"{production.OPERATION_ID}:{authority['policy_sha256']}"
    expected_id = Job(
        production.OPERATION_ID, "first", "LIVE", production.ENDPOINT,
        production.EXPECTED_PAYLOAD, None, Decimal("0.048"), input_kind="TEXT_TO_IMAGE",
    ).request_id
    if claim_kind == "matching-id-foreign-scope":
        request_id, scope, provider = expected_id, "foreign-scope", production.ENDPOINT
    elif claim_kind == "operation-different-id":
        request_id, scope, provider = "different-request", expected_scope, production.ENDPOINT
    else:
        request_id, scope, provider = "unrelated-request", "unrelated-scope", "other/provider"
    ledger.reserve(request_id, production.EPISODE_ID, provider, "0.048", settlement_mode="FAL_READBACK_V2", auth_scope=scope, provider_request_id="UNASSIGNED", authority_mode="LIVE")
    before = (tmp_path / "ledger" / "standing-budget.sqlite3").read_bytes()
    calls = []
    with pytest.raises(production.ProductionAdmissionError, match="ambiguous execution checkpoint"):
        await production.YoungIdentityModelSheetProduction()._execute_with_composition(
            quote_and_openapi=lambda _credential: calls.append("quote"),
            credential_loader=lambda: calls.append("credential"),
        )
    assert calls == []
    assert (tmp_path / "ledger" / "standing-budget.sqlite3").read_bytes() == before


@pytest.mark.asyncio
async def test_unknown_executor_checkpoint_blocks_before_quote_or_credential(tmp_path, monkeypatch):
    """A row that cannot prove its exact operation never falls back to retry."""
    import src.hybrid.young_identity_model_sheet_production as production

    _qualified_live_root(tmp_path, monkeypatch)
    execution = tmp_path / "execution" / production.OPERATION_ID
    execution.mkdir(parents=True)
    with sqlite3.connect(execution / "executor.sqlite3") as database:
        database.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
        database.execute("INSERT INTO jobs VALUES (?, ?)", ("foreign", "{}"))
    calls = []
    with pytest.raises(production.ProductionAdmissionError, match="ambiguous execution checkpoint"):
        await production.YoungIdentityModelSheetProduction()._execute_with_composition(
            quote_and_openapi=lambda _credential: calls.append("quote"),
            credential_loader=lambda: calls.append("credential"),
        )
    assert calls == []


def test_actual_v1_contract_accepts_requested_three_views_and_bound_evidence(tmp_path, monkeypatch):
    """The merged validator must admit the actual v1 policy contract without a v2 rewrite."""
    from src.hybrid.young_identity_model_sheet_production import (
        YoungIdentityModelSheetProduction,
    )

    authority = _actual_v1_policy_three_view_root(tmp_path, monkeypatch)
    bound, policy, preflight = YoungIdentityModelSheetProduction._for_test(tmp_path, authority)._authority()
    assert policy["schema"] == "jaco-standing-episode-operation-policy/v1"
    assert preflight["operation"]["payload"]["prompt"].count("eyes open") == 2
    assert set(preflight["source_evidence"]) == {
        "canonical_script_packet", "ep8_f001_manifest", "ep8_f001_raster", "ep8_f001_palette"
    }
    assert bound["policy_sha256"] == __import__("hashlib").sha256(Path(bound["policy_path"]).read_bytes()).hexdigest()


def test_actual_rich_v3_candidate_payload_is_admitted_before_qualification(tmp_path, monkeypatch):
    """The reviewed candidate's exact rich prompt is not reduced to a fixture."""
    from hashlib import sha256

    from src.hybrid.young_identity_model_sheet_production import (
        APPROVED_RICH_PAYLOAD_SHA256,
        ProductionAdmissionError,
        YoungIdentityModelSheetProduction,
        _canonical,
    )

    authority_path = _actual_v1_policy_three_view_root(tmp_path, monkeypatch)
    authority = json.loads(authority_path.read_text())
    preflight_path = Path(authority["preflight_path"])
    preflight = json.loads(preflight_path.read_text())
    payload = preflight["operation"]["payload"]
    payload["prompt"] = (
        "Evaluation-only Genesis 28 young-adult Jacó identity model sheet in one 16:9 PNG. "
        "Exactly three views of the same single character: (1) front head-and-shoulders, eyes open, "
        "neutral expression; (2) three-quarter head-and-shoulders, eyes open; (3) standing full-body "
        "neutral pose with readable hair, mantle, tunic, sandals, and silhouette. Preserve the bound "
        "Genesis 28 packet characterization: dark wavy shoulder-length hair, short neat brown beard and "
        "moustache, kind observant brown eyes, cream linen tunic, muted dusty-blue wool mantle, leather "
        "sandals. Use only the bound EP8 F001 approved raster and manifest as style/palette-reference "
        "evidence; no unbound gallery substitute. Child-safe rounded 3D family-feature rendering; no text, "
        "labels, logo, watermark, extra people, duplicate identity, angels, stairway, sleeping pose, closed "
        "eyes, photorealism, modern objects, distorted limbs, or extra fingers."
    )
    preflight["operation"]["payload_sha256"] = sha256(_canonical(payload)).hexdigest()
    assert preflight["operation"]["payload_sha256"] == APPROVED_RICH_PAYLOAD_SHA256
    _write(preflight_path, preflight)
    authority["preflight_sha256"] = sha256(preflight_path.read_bytes()).hexdigest()
    _write(authority_path, authority)

    factory = YoungIdentityModelSheetProduction._for_test(tmp_path, authority_path)
    bound, _, admitted = factory._authority()
    assert admitted["operation"]["payload"] == payload
    Path(bound["deployment_qualification"]["path"]).unlink()
    with pytest.raises(ProductionAdmissionError, match="protected qualification is not strict readable JSON"):
        factory._qualification(bound)


def test_v1_consent_with_correct_shape_but_wrong_canonical_transcript_rejects_first(tmp_path, monkeypatch):
    """v1 descriptors are checked against canonical content, not accepted as hashes."""
    from hashlib import sha256

    from src.hybrid.young_identity_model_sheet_production import (
        ProductionAdmissionError,
        YoungIdentityModelSheetProduction,
    )

    authority_path = _actual_v1_policy_three_view_root(tmp_path, monkeypatch)
    authority = json.loads(authority_path.read_text())
    policy_path = Path(authority["policy_path"])
    policy = json.loads(policy_path.read_text())
    consent = policy["canonical_consent"]
    consent.update({
        "session_id": "actual-v1-session",
        "prompt_message_id": 147618,
        "consent_message_id": 147620,
        "prompt_utf8_bytes": 802,
        "prompt_utf8_sha256": "50b8aa006942c74cdc25d08ecfe9855c6349e6d2ca8d812b1472c1c2f2f5b44e",
        "consent_literal_utf8_bytes": 8,
        "consent_literal_utf8_sha256": "f54d0bf3c6c609d5e7f93d348d4332364fdee0edc1592ea2a30137ba31ead0e1",
    })
    state_db = tmp_path / "canonical-state.sqlite3"
    with sqlite3.connect(state_db) as database:
        database.execute("DELETE FROM messages")
        database.executemany("INSERT INTO messages VALUES (?, ?, ?, ?)", [
            (147618, "actual-v1-session", "tool", "wrong full prompt"),
            (147620, "actual-v1-session", "user", "autorizo"),
        ])
    canonical = __import__("src.hybrid.jaco_flux3_production", fromlist=["x"])
    monkeypatch.setattr(canonical, "_CANONICAL_STATE_DB", state_db)
    _write(policy_path, policy)
    authority["policy_sha256"] = sha256(policy_path.read_bytes()).hexdigest()
    _write(authority_path, authority)

    with pytest.raises(ProductionAdmissionError, match="canonical transcript provenance rejected"):
        YoungIdentityModelSheetProduction._for_test(tmp_path, authority_path)._authority()
    assert not (tmp_path / "ledger").exists()
    assert not (tmp_path / "execution").exists()


def test_legacy_v2_chain_is_private_test_only_and_never_live(tmp_path, monkeypatch):
    """A valid legacy fixture cannot become a public TEST or LIVE admission."""
    from hashlib import sha256

    import src.hybrid.young_identity_model_sheet_production as production

    authority_path = _qualified_test_root(tmp_path, monkeypatch)
    private = production.YoungIdentityModelSheetProduction._for_test(tmp_path, authority_path)
    authority, _, _ = private._authority()
    assert private._qualification(authority)["mode"] == "TEST"

    monkeypatch.setattr(production, "ROOT", tmp_path)
    monkeypatch.setattr(production, "AUTHORITY_PATH", authority_path)
    with pytest.raises(production.ProductionAdmissionError, match="legacy v2 chain is TEST-only"):
        production.YoungIdentityModelSheetProduction()._authority()

    authority = json.loads(authority_path.read_text())
    qualification_path = Path(authority["deployment_qualification"]["path"])
    qualification = json.loads(qualification_path.read_text())
    authority["mode"] = "LIVE"
    qualification.update({
        "schema": "jaco-protected-deployment-qualification/v2",
        "mode": "LIVE",
        "authorization": "STANDING_USER_AUTHORIZATION_PROTECTED_POLICY",
    })
    del qualification["consent_fixture"]
    _write(qualification_path, qualification)
    authority["deployment_qualification"]["sha256"] = sha256(qualification_path.read_bytes()).hexdigest()
    _write(authority_path, authority)
    with pytest.raises(production.ProductionAdmissionError, match="legacy v2 chain is TEST-only"):
        production.YoungIdentityModelSheetProduction._for_test(tmp_path, authority_path)._authority()


def test_v1_contract_reports_missing_qualification_after_schema_validation(tmp_path, monkeypatch):
    """Qualification absence is not reported as a malformed v1/v3 contract."""
    from src.hybrid.young_identity_model_sheet_production import (
        ProductionAdmissionError,
        YoungIdentityModelSheetProduction,
    )

    authority_path = _actual_v1_policy_three_view_root(tmp_path, monkeypatch)
    factory = YoungIdentityModelSheetProduction._for_test(tmp_path, authority_path)
    authority, _, _ = factory._authority()
    Path(authority["deployment_qualification"]["path"]).unlink()
    with pytest.raises(ProductionAdmissionError, match="protected qualification is not strict readable JSON"):
        factory._qualification(authority)


@pytest.mark.parametrize("mutation", [None, "old-code", "new-code", "foreign-operation", "unknown-field"])
def test_versioned_technical_amendment_preserves_hash_gate(tmp_path, monkeypatch, mutation):
    """Only an immutable old-to-current code amendment can select v3 qualification."""
    from hashlib import sha256

    import src.hybrid.young_identity_model_sheet_production as production

    authority_path = _qualified_live_root(tmp_path, monkeypatch)
    authority = json.loads(authority_path.read_text())
    prior_path = Path(authority["deployment_qualification"]["path"])
    prior = json.loads(prior_path.read_text())
    prior["execution_code_sha256"] = production.PRIOR_QUALIFIED_EXECUTION_SHA256
    _write(prior_path, prior)
    prior_sha = sha256(prior_path.read_bytes()).hexdigest()
    amended = dict(prior)
    amended.update({
        "schema": "jaco-protected-deployment-qualification/v3",
        "execution_code_sha256": production._execution_source_sha256(),
        "technical_deployment_amendment": {
            "schema": "jaco-technical-deployment-amendment/v1",
            "kind": "EXECUTION_CODE_HASH_SUCCESSOR_ONLY",
            "prior_qualification_path": str(prior_path),
            "prior_qualification_sha256": prior_sha,
            "prior_qualification_bytes_sha256": prior_sha,
            "prior_execution_code_sha256": production.PRIOR_QUALIFIED_EXECUTION_SHA256,
            "new_execution_code_sha256": production._execution_source_sha256(),
            "review_ci_provenance_sha256": prior["ci_digest_sha256"],
            "operation_policy_sha256": prior["operation_policy_sha256"],
            "financial_policy_sha256": prior["financial_policy_sha256"],
            "preflight_sha256": prior["preflight_sha256"],
        },
    })
    if mutation == "old-code":
        amended["technical_deployment_amendment"]["prior_execution_code_sha256"] = "0" * 64
    elif mutation == "new-code":
        amended["technical_deployment_amendment"]["new_execution_code_sha256"] = "1" * 64
    elif mutation == "foreign-operation":
        amended["technical_deployment_amendment"]["operation_policy_sha256"] = "2" * 64
    elif mutation == "unknown-field":
        amended["technical_deployment_amendment"]["extra"] = True
    amended_path = tmp_path / "standing-policy" / production.AMENDED_QUALIFICATION_PATH.name
    _write(amended_path, amended)
    authority["deployment_qualification"] = {"path": str(amended_path), "sha256": sha256(amended_path.read_bytes()).hexdigest()}
    _write(authority_path, authority)
    factory = production.YoungIdentityModelSheetProduction()
    if mutation is None:
        assert factory._qualification(authority)["schema"] == "jaco-protected-deployment-qualification/v3"
    else:
        with pytest.raises(production.ProductionAdmissionError):
            factory._qualification(authority)


@pytest.mark.parametrize("mutation", ["wrong-views", "missing-reference", "tampered-reference", "foreign-episode", "policy-version", "unknown-schema", "boolean"])
def test_actual_v1_contract_rejects_nonweakening_regressions(tmp_path, monkeypatch, mutation):
    from src.hybrid.young_identity_model_sheet_production import (
        ProductionAdmissionError,
        YoungIdentityModelSheetProduction,
    )

    authority_path = _actual_v1_policy_three_view_root(tmp_path, monkeypatch)
    authority = json.loads(authority_path.read_text())
    policy_path, preflight_path = Path(authority["policy_path"]), Path(authority["preflight_path"])
    policy, preflight = json.loads(policy_path.read_text()), json.loads(preflight_path.read_text())
    if mutation == "wrong-views": preflight["operation"]["payload"]["prompt"] = "front, profile, and back"
    elif mutation == "missing-reference": del preflight["source_evidence"]["ep8_f001_palette"]
    elif mutation == "tampered-reference": preflight["source_evidence"]["ep8_f001_raster"]["sha256"] = "0" * 64
    elif mutation == "foreign-episode": preflight["episode_id"] = "EP8_PROMISE_SON"
    elif mutation == "policy-version": policy["version"] = 2
    elif mutation == "unknown-schema": preflight["schema"] = "jaco-episode-operation-preflight/v99"
    else: preflight["operation"]["call_limit"] = True
    _write(policy_path, policy); _write(preflight_path, preflight)
    authority["policy_sha256"] = __import__("hashlib").sha256(policy_path.read_bytes()).hexdigest()
    authority["preflight_sha256"] = __import__("hashlib").sha256(preflight_path.read_bytes()).hexdigest()
    _write(authority_path, authority)
    with pytest.raises(ProductionAdmissionError):
        YoungIdentityModelSheetProduction._for_test(tmp_path, authority_path)._authority()


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


def _amended_live_recovery_child(
    root_text: str, phase: str, remote_id: str = "amended-live-remote-1"
) -> None:
    """Exercise the public LIVE route from disk-only v3 amendment state."""
    from hashlib import sha256

    import src.hybrid.jaco_flux3_production as canonical_production
    import src.hybrid.young_identity_model_sheet_production as production
    from src.hybrid.execution import Executor, ProviderResult
    from src.hybrid.planner import Config
    from src.hybrid.standing_budget import StandingBudgetLedger

    root = Path(root_text)
    counts_path = root / "live-transport-counts.json"
    counts = json.loads(counts_path.read_text()) if counts_path.exists() else {"POST": 0, "GET": 0}
    if phase == "submit":
        authority_path = _qualified_live_root(root, _ChildMonkeyPatch())
        authority = json.loads(authority_path.read_text(encoding="utf-8"))
        prior_path = Path(authority["deployment_qualification"]["path"])
        prior = json.loads(prior_path.read_text(encoding="utf-8"))
        prior["execution_code_sha256"] = production.PRIOR_QUALIFIED_EXECUTION_SHA256
        _write(prior_path, prior)
        prior_bytes, prior_sha = prior_path.read_bytes(), sha256(prior_path.read_bytes()).hexdigest()
        amended = dict(prior)
        amended.update({
            "schema": "jaco-protected-deployment-qualification/v3",
            "execution_code_sha256": production._execution_source_sha256(),
            "technical_deployment_amendment": {
                "schema": "jaco-technical-deployment-amendment/v1",
                "kind": "EXECUTION_CODE_HASH_SUCCESSOR_ONLY",
                "prior_qualification_path": str(prior_path),
                "prior_qualification_sha256": prior_sha,
                "prior_qualification_bytes_sha256": prior_sha,
                "prior_execution_code_sha256": production.PRIOR_QUALIFIED_EXECUTION_SHA256,
                "new_execution_code_sha256": production._execution_source_sha256(),
                "review_ci_provenance_sha256": prior["ci_digest_sha256"],
                "operation_policy_sha256": prior["operation_policy_sha256"],
                "financial_policy_sha256": prior["financial_policy_sha256"],
                "preflight_sha256": prior["preflight_sha256"],
            },
        })
        amended_path = root / "standing-policy" / production.AMENDED_QUALIFICATION_PATH.name
        _write(amended_path, amended)
        authority["deployment_qualification"] = {
            "path": str(amended_path), "sha256": sha256(amended_path.read_bytes()).hexdigest(),
        }
        _write(authority_path, authority)
        execution = root / "execution" / production.OPERATION_ID
        Executor(execution / "executor.sqlite3", Config(limit=Decimal(10)))
        _write(execution / "protected-qualification.json", prior)
        ledger_path = root / "ledger" / "standing-budget.sqlite3"
        StandingBudgetLedger(
            ledger_path, policy_sha256=authority["canonical_ledger"]["financial_policy_sha256"],
            episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="LIVE",
        )
        histories = (
            ("7f19aab26d71464a9bc257f4f566d2c6cb642aab0bc55864bad8dcbaeab0ae47",
             "JACO_FLUX3_FINAL_ADMISSION:a32871d181a22a45291a526c29934abf3fa2a9aea17f30cf881832dd8cebf170"),
            ("27ef6a66be109984c208daa6e053e4a9d4f51a0202b46d624ea964336f74d052",
             "JACO_GEN28_STYLE_REMEDIATION_001:46bb52a78057088ef1030ed77f54dd4e4dc671aa67576c0074939389bb947e11"),
        )
        with sqlite3.connect(ledger_path) as db:
            for request_id, scope in histories:
                receipt = {
                    "schema": 2, "authority": "TRANSPORT_AUTHENTICATED_FAL_READBACK",
                    "local_request_id": request_id, "provider_request_id": f"remote-{request_id}",
                    "endpoint_id": production.ENDPOINT, "auth_scope": scope, "authority_mode": "LIVE",
                    "currency": "USD", "cost_total_micro_usd": "48000", "evidence_hash": "a" * 64,
                    "provenance": "FAL_BILLING_READBACK",
                }
                body = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
                db.execute("INSERT INTO claims VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    request_id, production.EPISODE_ID, production.ENDPOINT, "2026-11", 48000, 0,
                    "SETTLED", 48000, "FAL_READBACK_V2", scope, f"remote-{request_id}", "LIVE", None,
                ))
                db.execute("INSERT INTO settlement_receipts VALUES(?,?,?,?,?)", (
                    request_id, sha256(body.encode()).hexdigest(), 48000, body, "FAL_BILLING_READBACK",
                ))
        old_receipts = {
            row[0]: {"body": row[1], "sha256": row[2]}
            for row in sqlite3.connect(ledger_path).execute(
                "SELECT request_id, receipt_body, receipt_sha256 FROM settlement_receipts"
            )
        }
        (root / "amended-live-before.json").write_text(json.dumps({
            "old_marker": prior_bytes.hex(), "old_marker_sha256": prior_sha,
            "old_receipts": old_receipts,
        }, sort_keys=True), encoding="utf-8")
    else:
        authority_path = root / "standing-policy" / production.AUTHORITY_PATH.name
        production.ROOT, production.AUTHORITY_PATH = root, authority_path
        production.FINANCIAL_SHA256 = sha256((root / "financial-record-source.json").read_bytes()).hexdigest()
        canonical_production._CANONICAL_STATE_DB = root / "canonical-state.sqlite3"

    class OfflineLiveProvider:
        mode = "LIVE"

        def __init__(self, output, _openapi, _credential):
            self.output = output

        async def submit(self, _job, _request_id, checkpoint):
            counts["POST"] += 1; counts_path.write_text(json.dumps(counts), encoding="utf-8")
            checkpoint(provider_id=remote_id, partial=self.output)
            raise TimeoutError("controlled LIVE crash after durable remote checkpoint")

        async def recover(self, _job, _request_id, remote, _partial, _checkpoint):
            assert remote == "amended-live-remote-1"
            counts["GET"] += 1; counts_path.write_text(json.dumps(counts), encoding="utf-8")
            self.output.write_bytes(b"recovered without resubmission")
            return ProviderResult(self.output, Decimal("0.048"))

    canonical_production._FixedFalProvider = OfflineLiveProvider
    quote, openapi = _fixture_inputs()
    if phase == "submit":
        async def quote_and_openapi(_credential):
            return quote, openapi
    else:
        def forbidden(*_args, **_kwargs):
            raise AssertionError("recovery must not call quote, OpenAPI, or review")
        quote_and_openapi = forbidden
        production.YoungIdentityModelSheetProduction._review_for_execution = forbidden

    try:
        result = asyncio.run(production.YoungIdentityModelSheetProduction()._execute_with_composition(
            quote_and_openapi=quote_and_openapi,
            credential_loader=lambda: "offline-non-secret-credential",
        ))
    except TimeoutError:
        if phase != "submit":
            raise
        result = {"status": "CHECKPOINTED"}
    ledger_path = root / "ledger" / "standing-budget.sqlite3"
    execution = root / "execution" / production.OPERATION_ID
    with sqlite3.connect(ledger_path) as db:
        claims = db.execute(
            "SELECT request_id, state, actual, cap, provider_request_id FROM claims ORDER BY request_id"
        ).fetchall()
        receipts = {
            row[0]: {"body": row[1], "sha256": row[2]}
            for row in db.execute("SELECT request_id, receipt_body, receipt_sha256 FROM settlement_receipts")
        }
    journal = json.loads(sqlite3.connect(execution / "executor.sqlite3").execute(
        "SELECT data FROM jobs"
    ).fetchone()[0])
    snapshot = json.loads((root / "amended-live-before.json").read_text(encoding="utf-8"))
    print(json.dumps({
        "phase": phase, "result": result["status"], "counts": counts, "claims": claims,
        "receipts": receipts, "old_marker_hex": (execution / "protected-qualification.json").read_bytes().hex(),
        "old_marker_sha256": sha256((execution / "protected-qualification.json").read_bytes()).hexdigest(),
        "new_marker_sha256": sha256((execution / "protected-qualification.v3.json").read_bytes()).hexdigest(),
        "selected_marker_sha256": json.loads((execution / "protected-qualification.selection.v1.json").read_text())["selected_marker_sha256"],
        "journal": journal, "before": snapshot,
    }, sort_keys=True))


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


def test_public_live_v3_amendment_recovers_in_fresh_process_without_second_reservation_or_post(tmp_path):
    """A real LIVE admission keeps v2 history immutable and recovers GET-only."""
    module = "tests.unit.test_young_identity_model_sheet_production"
    command = [
        sys.executable, "-c",
        f"from {module} import _amended_live_recovery_child as f; import sys; f(sys.argv[1], sys.argv[2])",
        str(tmp_path),
    ]
    first = subprocess.run([*command, "submit"], text=True, capture_output=True, check=False, timeout=60)
    assert first.returncode == 0, first.stderr
    submitted = json.loads(first.stdout)
    assert submitted["result"] == "CHECKPOINTED"
    assert submitted["counts"] == {"POST": 1, "GET": 0}
    assert len(submitted["claims"]) == 3
    assert sum((row[2] if row[2] is not None else row[3]) for row in submitted["claims"]) == 144000
    assert submitted["old_marker_hex"] == submitted["before"]["old_marker"]
    assert submitted["old_marker_sha256"] == submitted["before"]["old_marker_sha256"]
    assert submitted["receipts"] == submitted["before"]["old_receipts"]

    second = subprocess.run([*command, "recover"], text=True, capture_output=True, check=False, timeout=60)
    assert second.returncode == 0, second.stderr
    recovered = json.loads(second.stdout)
    assert recovered["result"] == "COMPLETE"
    assert recovered["counts"] == {"POST": 1, "GET": 1}
    assert len(recovered["claims"]) == 3
    assert sum((row[2] if row[2] is not None else row[3]) for row in recovered["claims"]) == 144000
    assert recovered["receipts"] == recovered["before"]["old_receipts"]
    assert recovered["old_marker_hex"] == recovered["before"]["old_marker"]
    assert recovered["old_marker_sha256"] == recovered["before"]["old_marker_sha256"]
    assert recovered["new_marker_sha256"] == recovered["selected_marker_sha256"]
    current = next(row for row in recovered["claims"] if row[0] not in recovered["receipts"])
    assert current[1:] == ["RESERVED", None, 48000, "amended-live-remote-1"]
    assert recovered["journal"]["status"] == "COMPLETE"
    assert recovered["journal"]["provider_id"] == "amended-live-remote-1"


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
