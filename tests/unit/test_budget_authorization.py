"""Authenticated outbound budget authority never becomes self-asserted."""
from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import BrokenBarrierError
from types import SimpleNamespace

import pytest

from src.approval.telegram_inbound import TelegramInboundAdapter
from src.hybrid.budget_authorization import (
    BudgetAuthorizationError,
    BudgetAuthorizationService,
    BudgetAuthorizationSettings,
)
from src.hybrid.standing_budget import BudgetDenied, StandingBudgetLedger


class LocalTestOutbound:
    """A local TEST boundary fake; it is not a network or Telegram adapter."""
    transport = "LOCAL_TEST"

    def __init__(self, *, tamper_body: bool = False) -> None:
        self.calls, self.deliveries, self.tamper_body = 0, {}, tamper_body

    def deliver(self, *, chat_id: str, canonical_body: bytes) -> dict[str, object]:
        self.calls += 1
        body_sha256 = "0" * 64 if self.tamper_body else hashlib.sha256(canonical_body).hexdigest()
        result = {"transport": self.transport, "chat_id": chat_id, "message_id": 42, "body_sha256": body_sha256}
        self.deliveries[42] = result
        return result

    def revalidate_delivery(self, document: object) -> dict[str, object] | None:
        current = self.deliveries.get(document.get("message_id")) if type(document) is dict else None
        return dict(current) if current == document else None


class _ProcessOutbound:
    """Cross-process TEST fake whose append-only call count is the assertion."""
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def deliver(self, *, chat_id: str, canonical_body: bytes) -> dict[str, object]:
        with (self.root / "outbound-calls.log").open("ab") as stream:
            stream.write(b"call\n")
            stream.flush()
            os.fsync(stream.fileno())
        return {"transport": "LOCAL_PROCESS_TEST", "chat_id": chat_id, "message_id": 42,
                "body_sha256": hashlib.sha256(canonical_body).hexdigest()}

    def revalidate_delivery(self, document: object) -> dict[str, object] | None:
        return dict(document) if type(document) is dict else None


class _UnusedInbound:
    pass


def _concurrent_delivery_worker(root: str, barrier, result_queue) -> None:
    """Pause exactly at the historical check-then-act seam in each process."""
    from src.hybrid import budget_authorization

    original_write_once = budget_authorization._write_once

    def synchronized_write_once(path, document, label):
        if label == "budget authorization intent":
            try:
                barrier.wait(timeout=1)
            except BrokenBarrierError:  # A correctly serialized caller is the only arrival.
                pass
        return original_write_once(path, document, label)

    budget_authorization._write_once = synchronized_write_once
    service = BudgetAuthorizationService(root=Path(root) / "authority", inbound_source=_UnusedInbound(),
        outbound_source=_ProcessOutbound(root),
        settings=BudgetAuthorizationSettings(chat_id="10001", financial_sender_ids=frozenset({"20002"}), mode="TEST"),
        clock=lambda: datetime(2026, 10, 25, tzinfo=UTC))
    request = service.create_request(_request(expires_at="2026-10-26T00:00:00+00:00"))
    result_queue.put(service.deliver_request(request).document["message_id"])


def _write_once_after_create_worker(path: str, barrier, result_queue) -> None:
    """Reproduce the old visible-empty-file window without network delivery."""
    from src.hybrid import budget_authorization

    target = Path(path)
    original_open = budget_authorization.os.open

    def pause_after_target_create(name, flags, *args):
        fd = original_open(name, flags, *args)
        if Path(name) == target and flags & os.O_CREAT and flags & os.O_EXCL:
            try:
                barrier.wait(timeout=1)
            except BrokenBarrierError:
                pass
        return fd

    budget_authorization.os.open = pause_after_target_create
    document = {"schema": 1, "kind": "TEST_WRITE_ONCE"}
    budget_authorization._write_once(target, document, "test write-once document")
    result_queue.put(budget_authorization._load(target, "test write-once document"))


def _request(*, expires_at: str | None = None):
    return {"schema": 1, "kind": "BUDGET_AUTHORIZATION", "mode": "TEST", "request_id": "budget-request-001",
        "episode_id": "EP-TEST-001", "policy_sha256": "a" * 64,
        "expires_at": expires_at or (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        "allowed_providers": ["test-image"], "currency": "USD",
        "caps_usd": {"episode": "1.00", "month": "2.00", "experiment": "0.25"}}


def _adapter(tmp_path: Path):
    return TelegramInboundAdapter(journal_path=tmp_path / "telegram-journal.sqlite3", expected_chat_id="10001",
        trusted_sender_ids=frozenset({"20002", "20003"}), webhook_secret="configured-external-webhook-secret", instance_id="budget-test-instance")


def _body(*, reply_to: int):
    return json.dumps({"update_id": 10, "message": {"message_id": 77, "from": {"id": 20002, "is_bot": False},
        "chat": {"id": 10001, "type": "private"}, "reply_to_message": {"message_id": reply_to},
        "text": "AUTORIZAR ORÇAMENTO budget-request-001"}}).encode()


def _service(tmp_path, adapter, outbound, *, clock=lambda: datetime.now(UTC), senders=frozenset({"20002"})):
    return BudgetAuthorizationService(root=tmp_path / "authority", inbound_source=adapter, outbound_source=outbound,
        settings=BudgetAuthorizationSettings(chat_id="10001", financial_sender_ids=senders, mode="TEST"), clock=clock)


def _authorized(tmp_path, *, clock=lambda: datetime.now(UTC), outbound=None):
    adapter, outbound = _adapter(tmp_path), outbound or LocalTestOutbound()
    service = _service(tmp_path, adapter, outbound, clock=clock)
    request = service.create_request(_request(expires_at=(clock() + timedelta(hours=1)).isoformat()))
    delivery = service.deliver_request(request)
    assert adapter.ingest_webhook(body=_body(reply_to=delivery.document["message_id"]), secret_header="configured-external-webhook-secret")
    return service, adapter, outbound, service.confirm(delivery)


def test_authenticated_outbound_readback_binds_exact_canonical_request_before_reply_authorizes(tmp_path):
    service, _, outbound, receipt = _authorized(tmp_path)
    claim = service.admit_simulated_test(receipt.path, operation_id="image-scene-001", provider="test-image", quoted_cap_usd="0.10")
    assert outbound.calls == 1
    assert receipt.document["delivery"]["body_sha256"] == receipt.document["request_sha256"]
    assert claim["state"] == "RESERVED"


def test_forged_caller_message_id_cannot_replace_trusted_outbound_delivery(tmp_path):
    service = _service(tmp_path, _adapter(tmp_path), LocalTestOutbound())
    request = service.create_request(_request())
    with pytest.raises(AttributeError):
        service.register_delivered_request(request, message_id=42)
    assert not (tmp_path / "authority" / "deliveries").exists()


def test_outbound_content_tamper_fails_before_reply_claim_or_receipt(tmp_path):
    adapter, outbound = _adapter(tmp_path), LocalTestOutbound(tamper_body=True)
    service = _service(tmp_path, adapter, outbound)
    with pytest.raises(BudgetAuthorizationError, match="outbound.*body"):
        service.deliver_request(service.create_request(_request()))
    assert adapter.ingest_webhook(body=_body(reply_to=42), secret_header="configured-external-webhook-secret")
    assert not (tmp_path / "authority" / "receipts").exists()


def test_unknown_outbound_intent_never_posts_again(tmp_path):
    class Ambiguous(LocalTestOutbound):
        def deliver(self, *, chat_id: str, canonical_body: bytes):
            self.calls += 1
            raise TimeoutError("unknown remote result")
    outbound = Ambiguous()
    service = _service(tmp_path, _adapter(tmp_path), outbound)
    request = service.create_request(_request())
    for _ in range(2):
        with pytest.raises(BudgetAuthorizationError, match="unknown|intent"):
            service.deliver_request(request)
    assert outbound.calls == 1


def test_ducktyped_snapshot_cannot_create_authenticated_ledger(tmp_path):
    forged = SimpleNamespace(policy_sha256="a" * 64, episode_cap_usd="1", month_cap_usd="1", experiment_cap_usd="1", mode="TEST")
    with pytest.raises((BudgetDenied, TypeError), match="loader|authenticated"):
        StandingBudgetLedger.from_authorized_policy(tmp_path / "budget.sqlite3", policy=forged)


def test_direct_authenticated_bridge_enforces_provider_and_episode_scope_before_write(tmp_path):
    service, _, _, receipt = _authorized(tmp_path)
    bridge = service.authorized_bridge(receipt.path)
    with pytest.raises(BudgetAuthorizationError, match="provider"):
        bridge.reserve(operation_id="image-scene-001", provider="other-provider", quoted_cap_usd="0.10")
    with pytest.raises(BudgetAuthorizationError, match="scope"):
        bridge.reserve(operation_id="image-scene-001", provider="test-image", quoted_cap_usd="0.10", episode_id="other")
    assert bridge.reserve(operation_id="image-scene-001", provider="test-image", quoted_cap_usd="0.10")["request_id"] == "image-scene-001"


def test_expired_policy_blocks_new_admission_after_loader_and_restart(tmp_path):
    now = [datetime(2026, 10, 25, tzinfo=UTC)]
    clock = lambda: now[0]
    service, adapter, outbound, receipt = _authorized(tmp_path, clock=clock)
    service.authorized_bridge(receipt.path).reserve(operation_id="image-scene-001", provider="test-image", quoted_cap_usd="0.10")
    now[0] += timedelta(hours=2)
    with pytest.raises(BudgetAuthorizationError, match="expired"):
        _service(tmp_path, adapter, outbound, clock=clock).authorized_bridge(receipt.path).reserve(operation_id="image-scene-001", provider="test-image", quoted_cap_usd="0.10")


def test_replay_under_changed_financial_role_configuration_fails_revalidation(tmp_path):
    _service_original, adapter, outbound, receipt = _authorized(tmp_path)
    with pytest.raises(BudgetAuthorizationError, match="authority|sender"):
        _service(tmp_path, adapter, outbound, senders=frozenset({"20003"})).authorized_bridge(receipt.path).reserve(operation_id="image-scene-001", provider="test-image", quoted_cap_usd="0.10")


def test_local_self_authored_receipt_never_loads_as_authority(tmp_path):
    service = _service(tmp_path, _adapter(tmp_path), LocalTestOutbound())
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps({"kind": "BUDGET_AUTHORIZATION_RECEIPT"}), encoding="utf-8")
    with pytest.raises(BudgetAuthorizationError, match="receipt|schema"):
        service.policy_loader().load(forged)


def test_two_process_services_emit_one_outbound_delivery_for_one_canonical_request(tmp_path):
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(2), context.Queue()
    workers = [context.Process(target=_concurrent_delivery_worker, args=(str(tmp_path), barrier, results)) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)
        assert worker.exitcode == 0
    assert sorted(results.get(timeout=1) for _ in workers) == [42, 42]
    assert (tmp_path / "outbound-calls.log").read_text(encoding="utf-8").splitlines() == ["call"]


def test_two_process_write_once_never_exposes_an_empty_canonical_document(tmp_path):
    context = multiprocessing.get_context("spawn")
    target = tmp_path / "canonical.json"
    barrier, results = context.Barrier(2), context.Queue()
    workers = [context.Process(target=_write_once_after_create_worker, args=(str(target), barrier, results)) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=15)
        assert worker.exitcode == 0
    assert [results.get(timeout=1) for _ in workers] == [{"schema": 1, "kind": "TEST_WRITE_ONCE"}] * 2
    assert json.loads(target.read_text(encoding="utf-8")) == {"schema": 1, "kind": "TEST_WRITE_ONCE"}


def test_write_once_collision_is_idempotent_only_for_the_exact_canonical_document(tmp_path):
    from src.hybrid.budget_authorization import _write_once

    target = tmp_path / "canonical.json"
    document = {"schema": 1, "kind": "TEST_WRITE_ONCE"}
    _write_once(target, document, "test write-once document")
    _write_once(target, document, "test write-once document")
    with pytest.raises(BudgetAuthorizationError, match="write-once"):
        _write_once(target, {**document, "kind": "DIFFERENT"}, "test write-once document")
    assert json.loads(target.read_text(encoding="utf-8")) == document


def test_partial_canonical_document_blocks_before_provider_and_preserves_evidence(tmp_path):
    adapter, outbound = _adapter(tmp_path), LocalTestOutbound()
    service = _service(tmp_path, adapter, outbound)
    target = tmp_path / "authority" / "requests" / "budget-request-001.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"schema":')

    with pytest.raises(BudgetAuthorizationError, match="unreadable"):
        service.create_request(_request())

    assert target.read_bytes() == b'{"schema":'
    assert outbound.calls == 0
    assert list(target.parent.glob("*.staging")) == []


def test_staging_is_same_directory_and_cleanup_preserves_foreign_evidence(tmp_path, monkeypatch):
    from src.hybrid import budget_authorization

    target = tmp_path / "canonical.json"
    document = {"schema": 1, "kind": "TEST_WRITE_ONCE"}
    foreign = b"external-crash-evidence"
    original_link = budget_authorization.os.link

    def competing_publish(source, destination):
        source, destination = Path(source), Path(destination)
        assert source.parent == destination.parent  # Never request a cross-volume hard link.
        evidence = source.with_name("preserved-external-evidence.bin")
        os.replace(source, evidence)
        source.write_bytes(foreign)
        destination.write_bytes(budget_authorization._canonical(document))
        raise FileExistsError

    monkeypatch.setattr(budget_authorization.os, "link", competing_publish)
    budget_authorization._write_once(target, document, "test write-once document")

    assert json.loads(target.read_text(encoding="utf-8")) == document
    staging = list(tmp_path.glob("*.staging"))
    assert len(staging) == 1
    assert staging[0].read_bytes() == foreign
    assert (tmp_path / "preserved-external-evidence.bin").exists()
    monkeypatch.setattr(budget_authorization.os, "link", original_link)


def test_crash_unknown_intent_blocks_a_new_service_instance_from_resending(tmp_path):
    class Ambiguous(LocalTestOutbound):
        def deliver(self, *, chat_id: str, canonical_body: bytes):
            self.calls += 1
            raise TimeoutError("crashed after remote submission")

    outbound = Ambiguous()
    request = _service(tmp_path, _adapter(tmp_path), outbound).create_request(_request())
    with pytest.raises(BudgetAuthorizationError, match="unknown|intent"):
        _service(tmp_path, _adapter(tmp_path), outbound).deliver_request(request)
    with pytest.raises(BudgetAuthorizationError, match="unknown|intent"):
        _service(tmp_path, _adapter(tmp_path), outbound).deliver_request(request)
    assert outbound.calls == 1


def test_bridge_requires_a_child_operation_id_and_hides_raw_ledger(tmp_path):
    service, _, _, receipt = _authorized(tmp_path)
    bridge = service.authorized_bridge(receipt.path)
    assert not hasattr(bridge, "ledger")
    with pytest.raises(BudgetAuthorizationError, match="operation"):
        bridge.reserve(provider="test-image", quoted_cap_usd="0.10")


def test_standing_authorization_allows_distinct_child_operations_and_rejects_changed_identity(tmp_path):
    service, _, _, receipt = _authorized(tmp_path)
    bridge = service.authorized_bridge(receipt.path)
    first = bridge.reserve(operation_id="image-scene-001", provider="test-image", quoted_cap_usd="0.10")
    assert first["request_id"] == "image-scene-001"
    assert bridge.reserve(operation_id="image-scene-001", provider="test-image", quoted_cap_usd="0.10") == first
    assert bridge.reserve(operation_id="image-scene-002", provider="test-image", quoted_cap_usd="0.10")["request_id"] == "image-scene-002"
    with pytest.raises(BudgetDenied, match="collision"):
        bridge.reserve(operation_id="image-scene-001", provider="test-image", quoted_cap_usd="0.11")


def test_authenticated_database_cannot_reopen_through_legacy_ledger_constructor(tmp_path):
    service, _, _, receipt = _authorized(tmp_path)
    bridge = service.authorized_bridge(receipt.path)
    bridge.reserve(operation_id="image-scene-001", provider="test-image", quoted_cap_usd="0.10")
    with pytest.raises(BudgetDenied, match="policy"):
        StandingBudgetLedger(tmp_path / "authority" / "standing-budget.sqlite3", policy_sha256="a" * 64,
            episode_limit_usd="1", month_limit_usd="2", experiment_limit_usd="0.25", mode="TEST")