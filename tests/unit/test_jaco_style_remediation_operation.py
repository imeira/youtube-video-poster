from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from hashlib import sha256
from pathlib import Path

import pytest


def _write(path: Path, value: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def _sha(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _authority(root: Path) -> tuple[Path, Path]:
    protected = root / "standing-policy"
    preflight = root / "preflight" / "JACO_GEN28_STYLE_REMEDIATION_001.preflight.json"
    _write(preflight, {"schema": "jaco-episode-operation-preflight/v1", "episode_id": "JACO_GENESIS28", "request_id": "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9", "operation": {"id": "JACO_GEN28_STYLE_REMEDIATION_001", "endpoint": "blackforestlabs/flux-3/text-to-image", "call_limit": 1, "maximum_usd": "0.048", "payload": {"prompt": "A child-safe fixture image for isolated successor acceptance.", "aspect_ratio": "16:9", "resolution": "1k", "output_format": "png", "enable_prompt_expansion": False, "safety_tolerance": 2}}})
    policy = _write(protected / "JACO_GENESIS28_STANDING_OPERATION_POLICY.v1.json", {"episode_id": "JACO_GENESIS28", "canonical_consent": {"session_id": "local-712d007e4f3efba4d8c6cfc52f947e69023fed6f445a54206a6c3ede9b317fbb", "consent_message_id": 147620, "prompt_message_id": 147618}, "financial_baseline": {"episode_cap_usd": "10.000000", "remaining_usd": "9.952000", "pending_usd": "0.000000"}})
    authority = _write(protected / "JACO_GEN28_STYLE_REMEDIATION_001.authority.v1.json", {"schema": "jaco-derived-operation-authority/v1", "status": "READY_FOR_INDEPENDENT_PARENT_REVIEW_NO_RESERVATION_NO_POST", "operation_id": "JACO_GEN28_STYLE_REMEDIATION_001", "episode_id": "JACO_GENESIS28", "maximum_next_cost_usd": "0.048000", "policy_path": str(policy), "policy_sha256": _sha(policy), "preflight_path": str(preflight), "preflight_sha256": _sha(preflight), "budget_check": {"actual_usd": "0.048000", "pending_usd": "0.000000", "episode_cap_usd": "10.000000", "next_cap_usd": "0.048000", "post_reservation_exposure_usd": "0.096000"}})
    return policy, authority


def _qualified_test_authority(root: Path) -> Path:
    """A hermetic deployment contract; it is deliberately not a LIVE artifact."""
    policy, authority_path = _authority(root)
    preflight = root / "preflight" / "JACO_GEN28_STYLE_REMEDIATION_001.preflight.json"
    qualification = root / "standing-policy" / "JACO_GEN28_STYLE_REMEDIATION_001.qualification.v1.json"
    qualification_body = {
        "schema": "jaco-test-deployment-qualification/v1",
        "status": "QUALIFIED",
        "operation_id": "JACO_GEN28_STYLE_REMEDIATION_001",
        "episode_id": "JACO_GENESIS28",
        "policy_sha256": _sha(policy),
        "preflight_sha256": _sha(preflight),
        "mode": "TEST",
        "review_packet_schema": "jaco-fal-flux3-successor-benchmark/3",
        "reviewer": "LOCAL_PROCESS_REVIEW_AUTOMATED_V1",
        "ci_digest_sha256": "c" * 64,
        "execution_code_sha256": _sha(Path(__file__).parents[2] / "src" / "hybrid" / "jaco_style_remediation_operation.py"),
        "authorization": "TEST_FIXTURE_CONSENT_ONLY",
        "consent_fixture": {"kind": "TEST_ONLY_CONSENT_FIXTURE", "subject": "isolated acceptance"},
    }
    _write(qualification, qualification_body)
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    authority.update({
        "preflight_sha256": _sha(preflight),
        "deployment_qualification": {"path": str(qualification), "sha256": _sha(qualification)},
        "canonical_ledger": {
            "path": str(root / "ledger" / "standing-budget.sqlite3"),
            "policy_sha256": _sha(policy),
            "financial_source": {
                "path": str(root / "financial-record-source.json"),
                "sha256": "",
            },
        },
    })
    financial_source = _write(root / "financial-record-source.json", {
        "schema": "jaco-factory-financial-reconciliation/1",
        "request_hash": "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9",
        "revision": "818556d97edb648f", "currency": "USD", "total_episode_cap": "10.00",
        "status": "APPROVED_USD10", "approval_scope": "FINANCIAL_ONLY", "authorizes_generation": False,
    })
    authority["canonical_ledger"]["financial_source"]["sha256"] = _sha(financial_source)
    _write(authority_path, authority)
    return authority_path


def _fixture_quote() -> dict[str, object]:
    return {
        "schema": "FAL_AUTHENTICATED_PRICE_CONSUMER_SNAPSHOT/2",
        "endpoint_id": "blackforestlabs/flux-3/text-to-image", "currency": "USD",
        "maximum_usd": "0.048", "unit_price_usd": "0.048", "provider_unit": "image",
        "semantic_unit": "FLAT_1K_SINGLE_IMAGE", "quantity": 1,
        "api_url": "https://api.fal.ai/v1/models/pricing", "raw_sanitized_sha256": "a" * 64,
        "observed_at_epoch": int(time.time()), "provider_expiry": "UNKNOWN_NOT_ASSERTED",
        "price_validity": "LOCAL_OBSERVED_AT_MAX_AGE_SECONDS_300_NOT_PROVIDER_EXPIRY",
    }


def _fixture_openapi() -> dict[str, object]:
    path = Path(__file__).parents[1] / "fixtures" / "fal_flux3_queue_openapi_20261106.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_protected_successor_rejects_legacy_4_3_geometry_before_review_or_reservation():
    """The successor, unlike the legacy canary reviewer, is fixed at 16:9."""
    from src.hybrid.jaco_style_remediation_operation import (
        OperationAdmissionError,
        _validate_geometry,
    )

    with pytest.raises(OperationAdmissionError, match="geometry drifted"):
        _validate_geometry({"aspect_ratio": "4:3"})


def test_operation_factory_makes_fresh_immutable_review_packet_from_fixed_authority(tmp_path):
    from src.hybrid.jaco_style_remediation_operation import OperationAdmissionFactory

    _policy, authority = _authority(tmp_path)
    factory = OperationAdmissionFactory._for_test(root=tmp_path, authority_path=authority)
    artifact = factory.build_review_packet(quote={"price": "0.048"}, openapi={"endpoint": "blackforestlabs/flux-3/text-to-image"})

    assert artifact["status"] == "READY_FOR_INDEPENDENT_PARENT_REVIEW_NO_RESERVATION_NO_POST"
    assert artifact["operation_id"] == "JACO_GEN28_STYLE_REMEDIATION_001"
    assert artifact["budget"] == {"actual_usd": "0.048000", "pending_usd": "0.000000", "next_cap_usd": "0.048000", "episode_cap_usd": "10.000000", "post_reservation_exposure_usd": "0.096000"}
    assert Path(artifact["packet_path"]).is_file()
    assert artifact["packet_sha256"] == _sha(Path(artifact["packet_path"]))


@pytest.mark.parametrize("change", ["old_packet", "consumed_authority", "wrong_operation", "scope", "per_job_reset", "replay"])
def test_operation_factory_rejects_old_or_mutated_authority_before_review_artifact(tmp_path, change):
    from src.hybrid.jaco_style_remediation_operation import (
        OperationAdmissionError,
        OperationAdmissionFactory,
    )

    _policy, authority_path = _authority(tmp_path)
    authority = json.loads(authority_path.read_text())
    if change == "old_packet":
        authority["preflight_sha256"] = "0" * 64
    elif change == "consumed_authority":
        authority["status"] = "CONSUMED"
    elif change == "wrong_operation":
        authority["operation_id"] = "JACO_GEN28_CANONICAL_001"
    elif change == "scope":
        authority["episode_id"] = "OTHER"
    elif change == "per_job_reset":
        authority["budget_check"]["actual_usd"] = "0.000000"
    else:
        run = tmp_path / "operation-runs" / "existing"
        run.mkdir(parents=True)
        (run / "review-packet.json").write_text("{}", encoding="utf-8")
    _write(authority_path, authority)

    with pytest.raises(OperationAdmissionError):
        OperationAdmissionFactory._for_test(root=tmp_path, authority_path=authority_path).build_review_packet(quote={"price": "0.048"}, openapi={"endpoint": "blackforestlabs/flux-3/text-to-image"})
    assert not list((tmp_path / "operation-runs").glob("*/review-packet.json")) if change != "replay" else True


@pytest.mark.asyncio
async def test_execute_rejects_missing_protected_qualification_before_quote_or_credential(tmp_path):
    """The paid/network composition must remain unreachable until root admission."""
    from src.hybrid.jaco_style_remediation_operation import (
        OperationAdmissionError,
        OperationAdmissionFactory,
    )

    _policy, authority = _authority(tmp_path)
    calls = []

    async def quote_and_openapi():
        calls.append("network")
        return {}, {}

    def credential():
        calls.append("credential")
        return "secret"

    factory = OperationAdmissionFactory._for_test(root=tmp_path, authority_path=authority)
    with pytest.raises(OperationAdmissionError, match="qualification"):
        await factory._execute_with_composition(
            quote_and_openapi=quote_and_openapi,
            credential_loader=credential,
        )
    assert calls == []


def test_production_factory_does_not_accept_caller_selected_root():
    from src.hybrid.jaco_style_remediation_operation import OperationAdmissionFactory

    with pytest.raises(TypeError):
        OperationAdmissionFactory(root=Path("C:/untrusted-root"))


def test_test_qualification_is_rejected_outside_private_fixture_composition(tmp_path):
    from src.hybrid.jaco_style_remediation_operation import (
        OperationAdmissionError,
        OperationAdmissionFactory,
    )

    authority = _qualified_test_authority(tmp_path)
    factory = OperationAdmissionFactory._for_test(root=tmp_path, authority_path=authority)
    factory._test_only = False  # model the public/LIVE composition with this fixture root
    with pytest.raises(OperationAdmissionError, match="qualification"):
        factory._qualification(factory._authority()[0])


@pytest.mark.asyncio
async def test_execution_rejects_forged_current_financial_source_before_review_or_reservation(tmp_path):
    from src.hybrid.jaco_style_remediation_operation import (
        OperationAdmissionError,
        OperationAdmissionFactory,
    )

    authority = _qualified_test_authority(tmp_path)
    source = tmp_path / "financial-record-source.json"
    source.write_text('{"forged":true}', encoding="utf-8")
    factory = OperationAdmissionFactory._for_test(root=tmp_path, authority_path=authority)

    with pytest.raises(OperationAdmissionError, match="financial source"):
        await factory._execute_prepared(
            quote=_fixture_quote(),
            openapi=_fixture_openapi(),
            credential="",
            test_provider_factory=lambda *_args: None,
        )
    assert not (tmp_path / "ledger" / "standing.sqlite3").exists()
    assert not list((tmp_path / "operation-runs").glob("*/review.json"))


@pytest.mark.asyncio
async def test_execution_rejects_payload_replay_before_review_or_reservation(tmp_path):
    from src.hybrid.jaco_style_remediation_operation import (
        OperationAdmissionError,
        OperationAdmissionFactory,
    )

    authority = _qualified_test_authority(tmp_path)
    preflight = tmp_path / "preflight" / "JACO_GEN28_STYLE_REMEDIATION_001.preflight.json"
    document = json.loads(preflight.read_text(encoding="utf-8"))
    document["operation"]["payload"]["prompt"] = "forged replacement payload"
    _write(preflight, document)
    factory = OperationAdmissionFactory._for_test(root=tmp_path, authority_path=authority)

    with pytest.raises(OperationAdmissionError, match="hash changed"):
        await factory._execute_prepared(
            quote=_fixture_quote(),
            openapi=_fixture_openapi(),
            credential="",
            test_provider_factory=lambda *_args: None,
        )
    assert not (tmp_path / "ledger" / "standing.sqlite3").exists()


@pytest.mark.asyncio
async def test_execution_rejects_stale_price_before_reservation(tmp_path):
    from src.hybrid.jaco_style_remediation_operation import OperationAdmissionFactory
    from src.hybrid.local_process_review import LocalProcessReviewError

    authority = _qualified_test_authority(tmp_path)
    quote = _fixture_quote()
    quote["observed_at_epoch"] = 0
    factory = OperationAdmissionFactory._for_test(root=tmp_path, authority_path=authority)

    with pytest.raises(LocalProcessReviewError, match="stale"):
        await factory._execute_prepared(
            quote=quote,
            openapi=_fixture_openapi(),
            credential="",
            test_provider_factory=lambda *_args: None,
        )
    assert not (tmp_path / "ledger" / "standing.sqlite3").exists()


@pytest.mark.asyncio
async def test_execution_rejects_unknown_budget_before_review_or_reservation(tmp_path):
    from src.hybrid.jaco_style_remediation_operation import (
        OperationAdmissionError,
        OperationAdmissionFactory,
    )

    authority_path = _qualified_test_authority(tmp_path)
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    authority["budget_check"]["actual_usd"] = "UNKNOWN"
    _write(authority_path, authority)
    factory = OperationAdmissionFactory._for_test(root=tmp_path, authority_path=authority_path)

    with pytest.raises(OperationAdmissionError, match="actual spend"):
        await factory._execute_prepared(
            quote=_fixture_quote(),
            openapi=_fixture_openapi(),
            credential="",
            test_provider_factory=lambda *_args: None,
        )
    assert not (tmp_path / "ledger" / "standing.sqlite3").exists()
    assert not list((tmp_path / "operation-runs").glob("*/review.json"))


def test_successor_test_composition_recovers_remote_id_once_in_fresh_processes(tmp_path):
    """Same Executor/ledger/reviewer orchestration, with only local TEST transport."""
    authority = _qualified_test_authority(tmp_path)
    (tmp_path / "official-openapi.json").write_bytes(
        (Path(__file__).parents[1] / "fixtures" / "fal_flux3_queue_openapi_20261106.json").read_bytes()
    )
    script = tmp_path / "successor_child.py"
    script.write_text(
        '''
import asyncio, json, socket, sys, time
from decimal import Decimal
from pathlib import Path
from src.hybrid.execution import ProviderResult
from src.hybrid.jaco_style_remediation_operation import OperationAdmissionFactory
class NoNetworkSocket(socket.socket):
    def connect(self, address, *_args, **_kwargs):
        if address[0] not in {"127.0.0.1", "::1"}: raise AssertionError("TEST fixture attempted network")
        return super().connect(address)
    def connect_ex(self, address, *_args, **_kwargs):
        if address[0] not in {"127.0.0.1", "::1"}: raise AssertionError("TEST fixture attempted network")
        return super().connect_ex(address)
socket.socket = NoNetworkSocket
root, authority, action = map(Path, sys.argv[1:3]) + (sys.argv[3],)
counts_path = root / "fixture-counts.json"
counts = json.loads(counts_path.read_text()) if counts_path.exists() else {"posts": 0, "gets": 0}
quote = {"schema":"FAL_AUTHENTICATED_PRICE_CONSUMER_SNAPSHOT/2","endpoint_id":"blackforestlabs/flux-3/text-to-image","currency":"USD","maximum_usd":"0.048","unit_price_usd":"0.048","provider_unit":"image","semantic_unit":"FLAT_1K_SINGLE_IMAGE","quantity":1,"api_url":"https://api.fal.ai/v1/models/pricing","raw_sanitized_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","observed_at_epoch":0,"provider_expiry":"UNKNOWN_NOT_ASSERTED","price_validity":"LOCAL_OBSERVED_AT_MAX_AGE_SECONDS_300_NOT_PROVIDER_EXPIRY"}
quote["observed_at_epoch"] = int(time.time())
openapi = json.loads((root / "official-openapi.json").read_text(encoding="utf-8"))
class FixtureTransport:
    mode = "TEST"
    def __init__(self, output, _schema): self.output = output
    async def submit(self, job, request_id, checkpoint):
        counts["posts"] += 1; counts_path.write_text(json.dumps(counts))
        if action == "unknown": raise TimeoutError("fixture acceptance before remote id")
        partial = self.output.with_suffix(".fixture.json"); partial.write_text(json.dumps({"remote":"TEST-REMOTE-1"}))
        checkpoint(provider_id="TEST-REMOTE-1", partial=partial)
        raise TimeoutError("fixture crash after remote id")
    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        assert provider_id == "TEST-REMOTE-1"; counts["gets"] += 1; counts_path.write_text(json.dumps(counts))
        self.output.write_bytes(b"fixture GET recovery")
        return ProviderResult(self.output, Decimal("0.048"))
async def inputs(): return quote, openapi
async def main():
    factory = OperationAdmissionFactory._for_test(root=root, authority_path=authority)
    try:
        result = await factory._execute_with_composition(quote_and_openapi=inputs, credential_loader=lambda: (_ for _ in ()).throw(AssertionError("TEST must not resolve credential")), test_provider_factory=FixtureTransport)
    except TimeoutError: result = {"status":"CRASHED"}
    except RuntimeError as error: result = {"status":"QUARANTINED", "error":str(error)}
    print(json.dumps(result, sort_keys=True))
asyncio.run(main())
'''.replace('root, authority, action = map(Path, sys.argv[1:3]) + (sys.argv[3],)', 'root = Path(sys.argv[1]); authority = Path(sys.argv[2]); action = sys.argv[3]'),
        encoding="utf-8",
    )
    executable = sys.executable
    env = dict(os.environ)
    env.update({"PYTHONPATH": str(Path.cwd()), "PYTHONIOENCODING": "utf-8"})
    for name in tuple(env):
        if "FAL" in name.upper() or "TOKEN" in name.upper() or "KEY" in name.upper() or "PROXY" in name.upper():
            env.pop(name)
    def run(root: Path, action: str) -> dict:
        completed = subprocess.run([executable, str(script), str(root), str(authority), action], env=env, capture_output=True, text=True, check=False, timeout=45)
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)
    first = run(tmp_path, "submit")
    assert first["status"] == "CRASHED", first
    assert run(tmp_path, "recover")["status"] == "COMPLETE"
    assert json.loads((tmp_path / "fixture-counts.json").read_text()) == {"posts": 1, "gets": 1}
    from src.hybrid.standing_budget import StandingBudgetLedger
    ledger = StandingBudgetLedger(tmp_path / "ledger" / "standing-budget.sqlite3", policy_sha256=_sha(tmp_path / "standing-policy" / "JACO_GENESIS28_STANDING_OPERATION_POLICY.v1.json"), episode_limit_usd="10", month_limit_usd="10", experiment_limit_usd="10", mode="TEST")
    with ledger._connect() as db:
        assert db.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1
    unknown_root = tmp_path / "unknown"
    unknown_authority = _qualified_test_authority(unknown_root)
    (unknown_root / "official-openapi.json").write_bytes((tmp_path / "official-openapi.json").read_bytes())
    authority = unknown_authority
    assert run(unknown_root, "unknown")["status"] == "CRASHED"
    assert run(unknown_root, "unknown")["status"] == "QUARANTINED"
    assert json.loads((unknown_root / "fixture-counts.json").read_text()) == {"posts": 1, "gets": 0}
