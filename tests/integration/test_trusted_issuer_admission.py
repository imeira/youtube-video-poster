from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

OPERATION_ID = "JOSE_GEN37_IDENTITY_FRAME_001"
_HERMES_PAIRED_ROOT = os.environ.get("HERMES_PAIRED_ROOT")
HERMES_ROOT = Path(_HERMES_PAIRED_ROOT) if _HERMES_PAIRED_ROOT else Path("paired-hermes-unset")
# Private paired CI supplies its immutable checked-out issuer SHA. The legacy pin
# remains the explicit manual default for the original reviewed pairing.
HERMES_HEAD = os.environ.get("HERMES_EXPECTED_SHA", "5969fd7bb2d4c317048d218531307ec9a125d17a")
NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
pytestmark = pytest.mark.skipif(
    _HERMES_PAIRED_ROOT is None,
    reason="paired Hermes coverage is run manually/private-side with HERMES_PAIRED_ROOT",
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


async def _cross_repo_fixture(tmp_path: Path, *, actual_chat: str = "42"):
    assert (
        await asyncio.to_thread(
            subprocess.run,
            ["git", "-C", str(HERMES_ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    ).stdout.strip() == HERMES_HEAD
    sys.path.insert(0, str(HERMES_ROOT))
    from gateway.operation_consent import (
        ConsentTargetV1,
        EndpointId,
        FinancialPolicyV1,
        OperationConsentRequestV1,
        OperationKind,
        ProviderId,
        ReadbackMessageV1,
        RevocationReason,
        StandingOperationPolicyV1,
        compose_test_operation_consent_service,
    )
    from gateway.platforms.base import SendResult

    protected = tmp_path / "protected"
    (protected / "operations").mkdir(parents=True)
    service = compose_test_operation_consent_service(
        tmp_path / "hermes" / "operation-consent.sqlite3", now=lambda: NOW
    )
    financial = {
        "schema": "jose-financial-policy/v1", "policy_id": "jose-financial-v1",
        "operation_id": OPERATION_ID, "episode_id": "JOSE_GENESIS37",
        "currency": "USD", "max_amount_minor": 13, "maximum_usd": "0.125",
        "status": "ACTIVE",
    }
    standing = {
        "schema": "jose-standing-policy/v1", "policy_id": "jose-standing-v1",
        "operation_id": OPERATION_ID, "episode_id": "JOSE_GENESIS37",
        "operation_kind": "image_generation", "provider_id": "openai",
        "endpoint_id": "openai.images.generations", "authority_mode": "TEST",
        "gateway_instance_id": service.gateway_instance_id, "chat_id": "42", "sender_id": "42",
        "receipt_max_age_seconds": 300, "quote_max_age_seconds": 300,
        "license_scope": "JOSE_GENESIS37:PAID_GENERATION",
        "provider_endpoint": "test/jose-image", "provider_kind": "TEST_FAKE",
    }
    financial_sha = hashlib.sha256(_canonical(financial)).hexdigest()
    standing_sha = hashlib.sha256(_canonical(standing)).hexdigest()
    payload = {"prompt": "Jose age 17 canonical identity fixture"}
    producer = service.bind_fixed_producer(
        producer_key="studio-jose-v1",
        standing_policy=StandingOperationPolicyV1(
            policy_id="jose-standing-v1", policy_sha256=standing_sha,
            operation_kinds=frozenset({OperationKind.IMAGE_GENERATION}),
            providers=frozenset({ProviderId.OPENAI}),
            endpoints=frozenset({EndpointId.OPENAI_IMAGES_GENERATIONS}),
        ),
        financial_policy=FinancialPolicyV1(
            policy_id="jose-financial-v1", policy_sha256=financial_sha,
            currency="USD", max_amount_minor=13,
        ),
    )
    descriptor = producer.create_descriptor(OperationConsentRequestV1(
        operation_id=OPERATION_ID, episode_id="JOSE_GENESIS37",
        operation_kind=OperationKind.IMAGE_GENERATION, provider_id=ProviderId.OPENAI,
        endpoint_id=EndpointId.OPENAI_IMAGES_GENERATIONS,
        input_binding_sha256=hashlib.sha256(_canonical(payload)).hexdigest(),
        request_text="Reply exactly JOSE-NONCE-1 to approve this operation.",
        confirmation_token="JOSE-NONCE-1", expires_at_utc=NOW + timedelta(minutes=5),
        currency="USD", max_amount_minor=13,
    ))
    intent = service.create_pending_intent(descriptor, ConsentTargetV1(
        platform="telegram", adapter_identity="telegram:fixture-bot", chat_id=actual_chat,
        sender_id=actual_chat, thread_id=None, reply_to_message_id=None,
    ))

    class TelegramFixture:
        platform = "telegram"
        adapter_identity = "telegram:fixture-bot"

        async def send(self, chat_id, content, *, reply_to=None, metadata=None):
            return SendResult(success=True, message_id="501")

        async def readback_exact_message(self, *, chat_id, message_id):
            return ReadbackMessageV1(
                platform="telegram", chat_id=actual_chat, message_id="501",
                sender_id=self.adapter_identity, reply_to_message_id=None,
                exact_text_utf8=descriptor.request_text, sent_at_utc=NOW,
                read_at_utc=NOW + timedelta(seconds=1),
            )

    normalizer = service.bind_test_authenticated_adapter(
        platform="telegram", adapter_identity="telegram:fixture-bot",
        authenticated_transport_binding="fixture:authenticated-raw-update",
    )
    await service.deliver_pending_intent(intent.intent_id, TelegramFixture())
    event = normalizer.normalize(
        update_id=700, message_id=900, chat_id=int(actual_chat), sender_id=int(actual_chat),
        reply_to_message_id=501, reply_to_sender_id=999, reply_to_is_own_message=True,
        reply_to_body_utf8=descriptor.request_text, reply_to_entities_json=(),
        exact_text_utf8=descriptor.confirmation_token, entities_json=(),
        callback_data_if_any=None, received_at_utc=NOW + timedelta(seconds=2),
    )
    await service.studio_inbound_handler(event)
    receipt = service.list_receipts_for_descriptor(descriptor.descriptor_id)[0]
    quote = {
        "schema": "jose-provider-quote/v1", "operation_id": OPERATION_ID,
        "endpoint": "test/jose-image", "currency": "USD", "maximum_usd": "0.125",
        "observed_at_utc": "2026-11-10T11:59:55Z",
        "expires_at_utc": "2026-11-10T12:01:00Z", "evidence_sha256": "ab" * 32,
    }
    license_evidence = {
        "schema": "jose-provider-license/v1", "operation_id": OPERATION_ID,
        "scope": "JOSE_GENESIS37:PAID_GENERATION", "status": "VALID",
        "valid_until_utc": "2026-11-10T12:05:00Z", "evidence_sha256": "cd" * 32,
    }
    registration = {
        "schema": "jose-provider-registration/v1", "endpoint": "test/jose-image",
        "provider_kind": "TEST_FAKE", "mode": "TEST",
    }
    documents = {
        "standing-policy.json": standing, "financial-policy.json": financial,
        "quote.json": quote, "license.json": license_evidence,
        "provider-registration.json": registration,
    }
    for name, document in documents.items():
        (protected / name).write_bytes(_canonical(document))
    hermes_descriptor = descriptor.canonical_payload()
    hermes_descriptor["descriptor_id"] = descriptor.descriptor_id
    operation = {
        "schema": "jose-trusted-operation/v2", "operation_id": OPERATION_ID,
        "episode_id": "JOSE_GENESIS37", "mode": "TEST",
        "scene": "JOSE_GEN37_IDENTITY_FRAME", "category": "first",
        "input_kind": "TEXT_TO_IMAGE", "endpoint": "test/jose-image", "payload": payload,
        "hermes_receipt_id": receipt.receipt_id, "hermes_descriptor": hermes_descriptor,
        "standing_policy": {"name": "standing-policy.json", "sha256": standing_sha},
        "financial_policy": {"name": "financial-policy.json", "sha256": financial_sha},
        "quote": {"name": "quote.json", "sha256": hashlib.sha256(_canonical(quote)).hexdigest()},
        "license": {"name": "license.json", "sha256": hashlib.sha256(_canonical(license_evidence)).hexdigest()},
        "provider_registration": {"name": "provider-registration.json", "sha256": hashlib.sha256(_canonical(registration)).hexdigest()},
    }
    operation_path = protected / "operations" / f"{OPERATION_ID}.json"
    operation_path.write_bytes(_canonical(operation))
    export = service.studio_receipt_export
    export.start()
    published_endpoint = Path(export.endpoint_file).read_text(encoding="ascii")
    if os.name == "nt":
        assert published_endpoint.startswith("test+tcp+hmac://127.0.0.1:")
    else:
        assert "://" not in published_endpoint and Path(published_endpoint).is_absolute()
    return {
        "root": protected, "service": service, "export": export, "receipt": receipt,
        "operation": operation, "operation_path": operation_path, "documents": documents,
        "revocation_reason": RevocationReason.OPERATOR_REVOKED,
    }


def _admission(fixture, calls: list[str]):
    from src.hybrid.execution import ProviderResult
    from src.hybrid.hermes_issuer_client import HermesIssuerClient
    from src.hybrid.trusted_issuer_admission import JoseTrustedIssuerAdmission

    class TrapProvider:
        mode = "TEST"

        async def submit(self, job, request_id, checkpoint):
            calls.append("POST")
            raise AssertionError("negative case reached provider")

        async def recover(self, job, request_id, provider_id, partial, checkpoint):
            calls.append("GET")
            return ProviderResult(Path("unused"), job.cost)

    return JoseTrustedIssuerAdmission.for_test_ipc(
        root=fixture["root"],
        client=HermesIssuerClient.for_test(
            endpoint_file=fixture["export"].endpoint_file,
            token_file=fixture["export"].capability_file,
        ),
        provider_factory=TrapProvider, now=lambda: int(NOW.timestamp()),
    )


def test_real_hermes_ipc_two_fresh_studio_processes_recover_without_resubmit(tmp_path):
    fixture = asyncio.run(_cross_repo_fixture(tmp_path))
    effects, output = tmp_path / "effects.log", tmp_path / "provider-output.bin"
    command = [
        sys.executable, "-m", "tests.integration.trusted_issuer_process",
        str(fixture["root"]), fixture["export"].endpoint_file,
        fixture["export"].capability_file, str(effects), str(output), str(int(NOW.timestamp())),
    ]
    try:
        first = subprocess.run(command, check=False, cwd=Path.cwd(), timeout=20)
        second = subprocess.run(command, check=False, cwd=Path.cwd(), timeout=20)
    finally:
        fixture["export"].close()
    assert (first.returncode, second.returncode) == (75, 0)
    assert effects.read_text(encoding="ascii").splitlines() == ["POST", "GET"]
    with sqlite3.connect(fixture["root"] / "state" / "ledger.sqlite3") as database:
        assert database.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["revoked", "wrong_chat", "tampered", "quote", "license"])
async def test_violations_have_zero_provider_effects(tmp_path, attack):
    fixture = await _cross_repo_fixture(
        tmp_path, actual_chat="43" if attack == "wrong_chat" else "42"
    )
    calls: list[str] = []
    if attack == "revoked":
        fixture["service"].append_revocation(fixture["receipt"].receipt_id, fixture["revocation_reason"])
    elif attack == "tampered":
        fixture["operation"]["payload"]["prompt"] = "tampered"
        fixture["operation_path"].write_bytes(_canonical(fixture["operation"]))
    elif attack in {"quote", "license"}:
        name = "quote.json" if attack == "quote" else "license.json"
        key = "maximum_usd" if attack == "quote" else "status"
        fixture["documents"][name][key] = "9.99" if attack == "quote" else "UNKNOWN"
        raw = _canonical(fixture["documents"][name])
        (fixture["root"] / name).write_bytes(raw)
        fixture["operation"][attack]["sha256"] = hashlib.sha256(raw).hexdigest()
        fixture["operation_path"].write_bytes(_canonical(fixture["operation"]))
    try:
        with pytest.raises(PermissionError):
            await _admission(fixture, calls).admit(OPERATION_ID)
    finally:
        fixture["export"].close()
    assert calls == []
    assert not (fixture["root"] / "state" / "executor.sqlite3").exists()


def test_production_is_fixed_and_reports_unprovisioned_without_raising(
    monkeypatch, tmp_path
):
    import src.hybrid.trusted_issuer_admission as admission

    fixture_root = tmp_path / "unprovisioned-production-fixture"
    fixture_root.mkdir()
    monkeypatch.setattr(admission, "_PRODUCTION_ROOT", fixture_root)
    blocked = admission.JoseTrustedIssuerAdmission.production()
    assert blocked["status"] == "BLOCKED"
    with pytest.raises(TypeError):
        admission.JoseTrustedIssuerAdmission.production(root=Path("attacker"))
