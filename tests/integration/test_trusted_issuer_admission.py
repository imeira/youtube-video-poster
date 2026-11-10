from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.hybrid.execution import ProviderResult

OPERATION_ID = "JOSE_GEN37_IDENTITY_FRAME_001"
KEY = b"test-only-trusted-issuer-key"


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _signed(payload: dict, domain: bytes) -> bytes:
    return _canonical({"payload": payload, "signature": hmac.new(KEY, domain + _canonical(payload), hashlib.sha256).hexdigest()})


class FixtureIssuerTransport:
    mode = "TEST"

    def __init__(self, receipt: dict, revocation: dict, telegram_result):
        self.receipt, self.revocation, self.telegram_result = receipt, revocation, telegram_result
        self.reads: list[str] = []
        self.revoke_on_read: int | None = None
        self.replace_after_revocation_read: tuple[int, object] | None = None

    def read_receipt(self, operation_id: str) -> bytes:
        assert operation_id == OPERATION_ID
        assert self.telegram_result.status == 200
        assert self.telegram_result.body["status"] == "approved"
        self.reads.append("receipt")
        return _signed(self.receipt, b"studio-trusted-issuer-receipt/v1\0")

    def read_revocation(self, operation_id: str) -> bytes:
        assert operation_id == OPERATION_ID
        self.reads.append("revocation")
        if self.revoke_on_read == self.reads.count("revocation"):
            self.revocation["status"] = "REVOKED"
        raw = _signed(self.revocation, b"studio-trusted-issuer-revocation/v1\0")
        if (
            self.replace_after_revocation_read is not None
            and self.reads.count("revocation")
            == self.replace_after_revocation_read[0]
        ):
            self.replace_after_revocation_read[1]()
        return raw


def _real_telegram_wire(tmp_path: Path, now: int):
    """Drive the existing authenticated Hermes ingress and Studio approval service."""
    from src.agents.director import DirectorAgent
    from src.approval.hermes_inbound_bridge import (
        HermesBridgeConfig,
        HermesStudioIngress,
        canonical_envelope,
    )
    from src.approval.receipts import ApprovalReceipt
    from src.approval.telegram_service import (
        TelegramDirectorService,
        TelegramInboundSettings,
    )
    from src.state.machine import EpisodeState, EpisodeStateStore

    episodes, episode = tmp_path / "episodes", tmp_path / "episodes" / "JOSE_GENESIS37"
    (episode / "qa").mkdir(parents=True)
    state = EpisodeStateStore.load_or_create(episode / "state.json", "JOSE_GENESIS37")
    state.current_state = EpisodeState.WAITING_THUMBNAIL_APPROVAL
    state.save(episode / "state.json")
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = SimpleNamespace(
        episodes_dir=episodes, approval_delivery_chat_id="10001",
        approval_delivery_trusted_sender_ids=frozenset({"20002"}),
    )
    settings = TelegramInboundSettings(
        webhook_secret="fixture-webhook-secret", chat_id="10001",
        trusted_sender_ids=frozenset({"20002"}), instance_id="jose-studio-fixture",
        journal_path=tmp_path / "telegram" / "journal.sqlite3",
        registry_path=tmp_path / "telegram" / "deliveries.sqlite3",
    )
    service = TelegramDirectorService(director=director, settings=settings)
    artifact = tmp_path / "jose-thumbnail.png"
    artifact.write_bytes(b"jose-thumbnail-fixture")
    delivery = tmp_path / "jose-delivery.json"
    delivery.write_text(json.dumps({
        "episode_id": "JOSE_GENESIS37", "artifact_kind": "thumbnail",
        "approval": asdict(ApprovalReceipt.approve("thumbnail", artifact, "fixture-delivery")),
        "chat_id": "10001", "message_id": 42,
    }), encoding="utf-8")
    service.register_delivery(delivery)
    raw_update = json.dumps({"update_id": 1, "message": {
        "message_id": 99, "from": {"id": 20002, "is_bot": False},
        "chat": {"id": 10001, "type": "private"},
        "reply_to_message": {"message_id": 42},
        "text": "APROVAR THUMBNAIL JOSE_GENESIS37",
    }}, separators=(",", ":")).encode()
    config = HermesBridgeConfig("jose-studio-fixture", "fixture-bridge-secret", "10001", frozenset({"20002"}))
    body = canonical_envelope(config, raw_update, nonce="jose-wire-0001", timestamp=now)
    signature = hmac.new(config.hmac_key.encode(), b"hermes-studio-approval-bridge/v1\0" + body, hashlib.sha256).hexdigest()
    result = HermesStudioIngress(config=config, service=service, now=lambda: now).handle(body, {
        "X-Studio-Bridge-Version": "1", "X-Studio-Bridge-Instance": config.instance_id,
        "X-Studio-Bridge-Signature": f"v1={signature}",
    })
    return result, hashlib.sha256(raw_update).hexdigest()


class FakeProvider:
    mode = "TEST"

    def __init__(self, root: Path, calls: list[str], *, fail_after_checkpoint: bool = False):
        self.root, self.calls, self.fail_after_checkpoint = root, calls, fail_after_checkpoint

    async def submit(self, job, request_id, checkpoint):
        self.calls.append("POST")
        checkpoint(provider_id="fake-provider-1")
        if self.fail_after_checkpoint:
            self.fail_after_checkpoint = False
            raise TimeoutError("simulated lost response")
        output = self.root / "provider.bin"
        output.write_bytes(b"jose fixture result")
        return ProviderResult(output, job.cost)

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        self.calls.append("GET")
        assert provider_id == "fake-provider-1"
        output = self.root / "provider.bin"
        output.write_bytes(b"jose fixture recovered result")
        return ProviderResult(output, job.cost)


def _fixture(tmp_path: Path, *, now: int = 1_800_000_000):
    from src.hybrid.trusted_issuer_admission import JoseTrustedIssuerAdmission

    root = tmp_path / "protected"
    (root / "operations").mkdir(parents=True)
    policy = {
        "schema": "jose-financial-policy/v1", "operation_id": OPERATION_ID,
        "episode_id": "JOSE_GENESIS37", "currency": "USD",
        "maximum_usd": "0.125", "status": "ACTIVE",
    }
    policy_raw = _canonical(policy)
    (root / "jose-financial-policy.json").write_bytes(policy_raw)
    descriptor = {
        "schema": "jose-trusted-operation/v1", "operation_id": OPERATION_ID,
        "episode_id": "JOSE_GENESIS37", "mode": "TEST",
        "endpoint": "fake/jose-image", "scene": "JOSE_GEN37_IDENTITY_FRAME",
        "category": "first", "input_kind": "TEXT_TO_IMAGE",
        "payload": {"prompt": "José age 17 canonical identity fixture"},
        "financial_policy": {"name": "jose-financial-policy.json", "sha256": hashlib.sha256(policy_raw).hexdigest()},
    }
    descriptor_raw = _canonical(descriptor)
    (root / "operations" / f"{OPERATION_ID}.json").write_bytes(descriptor_raw)
    telegram_result, update_sha256 = _real_telegram_wire(tmp_path, now)
    receipt = {
        "schema": "studio-trusted-issuer-receipt/v1", "receipt_id": "issuer-receipt-001",
        "operation_id": OPERATION_ID, "descriptor_sha256": hashlib.sha256(descriptor_raw).hexdigest(),
        "policy_sha256": hashlib.sha256(policy_raw).hexdigest(), "status": "APPROVED",
        "issued_at": now - 5, "expires_at": now + 300,
        "telegram_approval": {"chat_id": "10001", "sender_id": "20002", "message_id": 99,
                              "reply_to_message_id": 42, "update_sha256": update_sha256},
        "quote": {"endpoint": "fake/jose-image", "currency": "USD", "maximum_usd": "0.125",
                  "observed_at": now - 5, "expires_at": now + 60, "evidence_sha256": "b" * 64},
        "license": {"scope": "JOSE_GENESIS37:PAID_GENERATION", "status": "VALID",
                    "valid_until": now + 300, "evidence_sha256": "c" * 64},
    }
    revocation = {"schema": "studio-trusted-issuer-revocation/v1", "operation_id": OPERATION_ID,
                  "receipt_id": "issuer-receipt-001", "status": "NOT_REVOKED",
                  "observed_at": now - 1, "expires_at": now + 30}
    transport = FixtureIssuerTransport(receipt, revocation, telegram_result)
    calls: list[str] = []
    provider = FakeProvider(tmp_path, calls, fail_after_checkpoint=True)
    admission = JoseTrustedIssuerAdmission.for_test(
        root=root, transport=transport, issuer_key=KEY, provider=provider, now=lambda: now
    )
    return admission, transport, provider, calls, receipt, revocation


@pytest.mark.asyncio
async def test_fake_telegram_issuer_to_real_executor_recovers_without_duplicate_post(tmp_path):
    admission, transport, _provider, calls, _receipt, _revocation = _fixture(tmp_path)

    with pytest.raises(TimeoutError):
        await admission.admit(OPERATION_ID)
    result = await admission.admit(OPERATION_ID)

    assert result["status"] == "COMPLETE"
    assert calls == ["POST", "GET"]
    assert transport.reads.count("receipt") >= 6
    assert transport.reads.count("revocation") >= 6
    assert admission.last_financial_capability.maximum_usd == Decimal("0.125")
    assert admission.last_financial_capability.policy_sha256 != "916e8230db47406db96cbe774b9332c00ed24cf156c66d4d91d11a213b64f50d"
    with sqlite3.connect(tmp_path / "protected" / "state" / "ledger.sqlite3") as database:
        assert database.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["cancelled", "revoked", "tamper", "forgery", "stale", "quote", "license"])
async def test_negative_issuer_cases_never_create_executor_or_post(tmp_path, attack):
    admission, transport, _provider, calls, receipt, revocation = _fixture(tmp_path)
    if attack == "cancelled": receipt["status"] = "CANCELLED"
    elif attack == "revoked": revocation["status"] = "REVOKED"
    elif attack == "tamper": receipt["operation_id"] = "JACO_GENESIS28"
    elif attack == "forgery":
        original = transport.read_receipt
        transport.read_receipt = lambda operation_id: original(operation_id)[:-2] + b"0}"
    elif attack == "stale": revocation["expires_at"] = 1
    elif attack == "quote": receipt["quote"]["maximum_usd"] = "10.00"
    elif attack == "license": receipt["license"]["status"] = "UNKNOWN"

    with pytest.raises(PermissionError):
        await admission.admit(OPERATION_ID)

    assert calls == []
    assert not (tmp_path / "protected" / "state" / "executor.sqlite3").exists()


@pytest.mark.asyncio
async def test_revocation_race_after_reservation_is_caught_before_post(tmp_path):
    admission, transport, _provider, calls, _receipt, _revocation = _fixture(tmp_path)
    transport.revoke_on_read = 4

    with pytest.raises(PermissionError, match="non-revoked"):
        await admission.admit(OPERATION_ID)

    assert calls == []
    with sqlite3.connect(tmp_path / "protected" / "state" / "ledger.sqlite3") as database:
        assert database.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", ["descriptor", "policy", "receipt"])
async def test_later_snapshot_cannot_replace_admitted_identity_before_provider(
    tmp_path, identity
):
    admission, transport, _provider, calls, receipt, revocation = _fixture(tmp_path)
    root = admission._root

    def replace_identity():
        if identity == "receipt":
            receipt["receipt_id"] = "issuer-receipt-002"
            revocation["receipt_id"] = "issuer-receipt-002"
            return
        policy_path = root / "jose-financial-policy.json"
        descriptor_path = root / "operations" / f"{OPERATION_ID}.json"
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        if identity == "descriptor":
            descriptor["payload"]["prompt"] = "coherently replaced descriptor"
        else:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
            policy["maximum_usd"] = "0.124"
            policy_raw = _canonical(policy)
            policy_path.write_bytes(policy_raw)
            descriptor["financial_policy"]["sha256"] = hashlib.sha256(
                policy_raw
            ).hexdigest()
            receipt["policy_sha256"] = hashlib.sha256(policy_raw).hexdigest()
            receipt["quote"]["maximum_usd"] = "0.124"
        descriptor_raw = _canonical(descriptor)
        descriptor_path.write_bytes(descriptor_raw)
        receipt["descriptor_sha256"] = hashlib.sha256(descriptor_raw).hexdigest()

    transport.replace_after_revocation_read = (1, replace_identity)

    with pytest.raises(PermissionError, match="changed the admitted issuer identity"):
        await admission.admit(OPERATION_ID)

    assert calls == []


@pytest.mark.asyncio
async def test_descriptor_rejects_category_that_requires_predecessor(tmp_path):
    admission, transport, _provider, calls, receipt, _revocation = _fixture(tmp_path)
    descriptor_path = admission._root / "operations" / f"{OPERATION_ID}.json"
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    descriptor["category"] = "alternative"
    descriptor_raw = _canonical(descriptor)
    descriptor_path.write_bytes(descriptor_raw)
    receipt["descriptor_sha256"] = hashlib.sha256(descriptor_raw).hexdigest()

    with pytest.raises(PermissionError, match="distinct closed"):
        await admission.admit(OPERATION_ID)

    assert calls == []
    assert transport.reads == []


def _directory_symlink(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlinks unavailable: {error}")


@pytest.mark.asyncio
@pytest.mark.parametrize("attack", ["root_ancestor", "operations_ancestor", "state_ancestor"])
async def test_reparse_or_symlink_ancestors_are_rejected_before_io(tmp_path, attack):
    from src.hybrid.trusted_issuer_admission import JoseTrustedIssuerAdmission

    admission, transport, provider, calls, _receipt, _revocation = _fixture(tmp_path)
    if attack == "root_ancestor":
        linked_parent = tmp_path / "linked-parent"
        _directory_symlink(linked_parent, tmp_path)
        with pytest.raises(PermissionError, match="link or reparse point"):
            JoseTrustedIssuerAdmission.for_test(
                root=linked_parent / "protected", transport=transport,
                issuer_key=KEY, provider=provider, now=admission._now,
            )
    elif attack == "operations_ancestor":
        operations = admission._root / "operations"
        real_operations = admission._root / "real-operations"
        operations.rename(real_operations)
        _directory_symlink(operations, real_operations)
        with pytest.raises(PermissionError, match="link or reparse point"):
            await admission.admit(OPERATION_ID)
    else:
        external_state = tmp_path / "external-state"
        external_state.mkdir()
        _directory_symlink(admission._root / "state", external_state)
        with pytest.raises(PermissionError, match="link or reparse point"):
            await admission.admit(OPERATION_ID)
        assert list(external_state.iterdir()) == []

    assert calls == []


def test_live_activation_and_caller_injection_are_not_available(tmp_path):
    from src.hybrid.trusted_issuer_admission import JoseTrustedIssuerAdmission

    with pytest.raises(PermissionError, match="LIVE activation"):
        JoseTrustedIssuerAdmission.production()
    admission, *_ = _fixture(tmp_path)
    with pytest.raises(TypeError):
        admission.admit(OPERATION_ID, receipt={})
