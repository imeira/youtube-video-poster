"""Gateway-backed financial reconciliation never invents reply or outbound metadata."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.hybrid.gateway_financial_reconciliation import (
    GatewayFinancialApprovalReconciler,
    GatewayFinancialReconciliationSettings,
)

HASH = "72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9"
COMMAND = f"CONFIRMAR FINANCEIRO JACO USD10 {HASH}"
SESSION = "20260925_110450_e688f0"
CHAT = "141718934"


def _gateway(
    path: Path, *, outbound_message_id: str | None = "5037", command_count: int = 1
) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, user_id TEXT, chat_id TEXT, display_name TEXT);
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT,
            platform_message_id TEXT, timestamp REAL NOT NULL, display_metadata TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO sessions VALUES (?, 'telegram', ?, ?, 'untrusted display name')",
        (SESSION, CHAT, CHAT),
    )
    outbound = "\n".join(
        (
            "⚠️ CONFIRMAÇÃO FINANCEIRA NECESSÁRIA",
            f"REQUEST_HASH: {HASH}",
            "AUTORIZAÇÃO PEDIDA: teto TOTAL de USD 10.00 para este vídeo. A autorização anterior de USD 5.00 está SUPERADA — não é adicional.",
            f"Para confirmar exatamente estes termos, responda a ESTA mensagem: {COMMAND}",
        )
    )
    connection.execute(
        "INSERT INTO messages VALUES (?, ?, 'assistant', ?, ?, ?, NULL)",
        (1, SESSION, outbound, outbound_message_id, datetime.now(UTC).timestamp()),
    )
    for number in range(command_count):
        connection.execute(
            "INSERT INTO messages VALUES (?, ?, 'user', ?, ?, ?, ?)",
            (
                10 + number,
                SESSION,
                COMMAND,
                str(5046 + number),
                datetime.now(UTC).timestamp(),
                '{"gateway_input_owner":"trusted-gateway"}',
            ),
        )
    connection.commit()
    connection.close()


def _delivery_receipt(
    root: Path,
    *,
    message_id: str = "5037",
    body: str | None = None,
    gate_newline: bytes = b"\n",
    request_hash: str = HASH,
    revision: str = "818556d97edb648f",
    amount: str = "10.00",
    tamper_gate: bool = False,
) -> Path:
    gate = root / "financial-request.txt"
    gate.write_bytes(
        body.encode("utf-8")
        if body is not None
        else gate_newline.join(
            line.encode("utf-8")
            for line in (
                "⚠️ CONFIRMAÇÃO FINANCEIRA NECESSÁRIA",
                f"REQUEST_HASH: {request_hash}",
                f"REVISION: {revision}",
                f"AUTORIZAÇÃO PEDIDA: teto TOTAL de USD {amount} para este vídeo.",
                f"Para confirmar exatamente estes termos, responda a ESTA mensagem: {COMMAND}",
            )
        )
    )
    preflight = root / "preflight.json"
    preflight.write_text(
        json.dumps(
            {
                "schema": "jaco-new-production-financial-preflight/1",
                "request_hash": request_hash,
                "revision": revision,
                "financial_terms": {"currency": "USD", "total_episode_cap": amount},
                "authorizes_generation": False,
            }
        ),
        encoding="utf-8",
    )
    receipt = root / "delivery-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": "jaco-financial-telegram-delivery-receipt/1",
                "platform": "telegram",
                "chat_id": CHAT,
                "message_id": message_id,
                "request_hash": request_hash,
                "revision": revision,
                "currency": "USD",
                "financial_scope_usd": amount,
                "gate_path": str(gate),
                "gate_sha256": "0" * 64
                if tamper_gate
                else hashlib.sha256(gate.read_bytes()).hexdigest(),
                "preflight_path": str(preflight),
                "preflight_sha256": hashlib.sha256(preflight.read_bytes()).hexdigest(),
                "readback_method": "copyMessage_then_delete_ephemeral_copy",
                "readback_verified": True,
                "ephemeral_copy_deleted": True,
                "ephemeral_copy_message_id": "5038",
                "no_generation_authorized": True,
                "no_publication_authorized": True,
                "no_purchase_authorized": True,
            }
        ),
        encoding="utf-8",
    )
    return receipt


def _settings(
    path: Path,
    *,
    now: datetime | None = None,
    receipt: Path | None = None,
    factory_root: Path | None = None,
):
    return GatewayFinancialReconciliationSettings(
        gateway_state_db=path,
        session_id=SESSION,
        configured_chat_id=CHAT,
        configured_sender_id=CHAT,
        inbound_message_id="5046",
        outbound_message_id="5037",
        request_hash=HASH,
        revision="818556d97edb648f",
        amount_usd="10.00",
        command=COMMAND,
        valid_until=now or datetime.now(UTC) + timedelta(minutes=5),
        delivery_receipt_path=receipt,
        factory_request_root=factory_root,
    )


def test_joins_unique_gateway_command_to_provider_readback_bound_scope_and_persists_only_factory_record(
    tmp_path,
):
    db = tmp_path / "state.db"
    _gateway(db)
    receipt = _delivery_receipt(tmp_path)
    factory_root = tmp_path / "JACO_GENESIS28"

    result = GatewayFinancialApprovalReconciler(
        _settings(db, receipt=receipt, factory_root=factory_root)
    ).readback()

    assert result.status == "RECONCILED_FINANCIAL_ONLY"
    assert result.command_platform_message_id == "5046"
    assert result.reply_to_message_id is None
    assert result.outbound_message_id == "5037"
    assert result.amount_usd == "10.00"
    assert result.request_hash == HASH
    persisted = json.loads(
        (
            factory_root
            / "financial_reconciliation_72eb129dc96eeed89e27a67c0bb32e19aba484d629f58c6b6cae977a0a1bfba9.json"
        ).read_text(encoding="utf-8")
    )
    assert persisted["authorizes_generation"] is False
    assert persisted["reply_to_message_id"] is None
    assert (
        persisted["outbound_body_sha256"]
        == hashlib.sha256((tmp_path / "financial-request.txt").read_bytes()).hexdigest()
    )


def test_reports_human_approved_execution_receipt_unbound_when_gateway_lacks_raw_outbound_id(
    tmp_path,
):
    db = tmp_path / "state.db"
    _gateway(db, outbound_message_id=None)

    result = GatewayFinancialApprovalReconciler(_settings(db)).readback()

    assert result.status == "HUMAN_APPROVED_TECH_UNBOUND"
    assert result.command_platform_message_id == "5046"
    assert result.reply_to_message_id is None
    assert result.outbound_message_id is None


def test_repeated_exact_commands_are_one_nonadditive_confirmation_scope(tmp_path):
    db = tmp_path / "state.db"
    _gateway(db, command_count=2)

    receipt = _delivery_receipt(tmp_path)
    result = GatewayFinancialApprovalReconciler(
        _settings(db, receipt=receipt, factory_root=tmp_path / "JACO_GENESIS28")
    ).readback()

    assert result.status == "RECONCILED_FINANCIAL_ONLY"
    assert result.command_platform_message_id == "5046"
    assert result.amount_usd == "10.00"


def test_expired_scope_is_not_reconciled_even_when_gateway_evidence_matches(tmp_path):
    db = tmp_path / "state.db"
    _gateway(db)

    result = GatewayFinancialApprovalReconciler(
        _settings(db, now=datetime.now(UTC) - timedelta(seconds=1))
    ).readback()

    assert result.status == "EXPIRED_SCOPE"


def test_tampered_delivery_body_fails_closed_without_factory_record(tmp_path):
    db = tmp_path / "state.db"
    _gateway(db)
    receipt = _delivery_receipt(tmp_path, tamper_gate=True)
    factory_root = tmp_path / "JACO_GENESIS28"

    result = GatewayFinancialApprovalReconciler(
        _settings(db, receipt=receipt, factory_root=factory_root)
    ).readback()

    assert result.status == "HUMAN_APPROVED_TECH_UNBOUND"
    assert not factory_root.exists()


@pytest.mark.parametrize("gate_newline", (b"\n", b"\r\n"))
@pytest.mark.parametrize("tamper_tool_digest", (False, True))
def test_verifies_distinct_trusted_delivery_and_telegram_inbound_sessions_without_raw_api_payload(
    tmp_path, gate_newline, tamper_tool_digest,
):
    db = tmp_path / "state.db"
    connection = sqlite3.connect(db)
    connection.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT NOT NULL, user_id TEXT, chat_id TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT,
            tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, platform_message_id TEXT, timestamp REAL NOT NULL);
    """)
    source = "20261030_102536_354b60"
    inbound = "20260925_110450_e688f0"
    receipt = _delivery_receipt(tmp_path, gate_newline=gate_newline)
    gate_digest = hashlib.sha256(
        (tmp_path / "financial-request.txt").read_bytes()
    ).hexdigest()
    logged_gate_digest = (
        ("0" if gate_digest[0] != "0" else "1") + gate_digest[1:]
        if tamper_tool_digest
        else gate_digest
    )
    connection.execute(
        "INSERT INTO sessions VALUES (?, 'subagent', NULL, NULL)", (source,)
    )
    connection.execute(
        "INSERT INTO sessions VALUES (?, 'telegram', ?, ?)", (inbound, CHAT, CHAT)
    )
    call = "a2c0e477a2804337b1088b1c5c94d737"
    readback = "a10b507c666542d0b664b0962bb88298"
    connection.execute(
        "INSERT INTO messages VALUES (1, ?, 'assistant', '', NULL, ?, NULL, NULL, 0)",
        (
            source,
            json.dumps([{"id": f"call_{call}", "function": {"name": "terminal"}}]),
        ),
    )
    connection.execute(
        "INSERT INTO messages VALUES (2, ?, 'tool', ?, ?, NULL, 'terminal', NULL, 0)",
        (
            source,
            json.dumps(
                {
                    "output": f'GATE_SHA256 {logged_gate_digest}\n{{"success":true,"platform":"telegram","chat_id":"141718934","message_id":"5037"}}',
                    "exit_code": 0,
                }
            ),
            f"call_{call}",
        ),
    )
    connection.execute(
        "INSERT INTO messages VALUES (3, ?, 'assistant', '', NULL, ?, NULL, NULL, 0)",
        (
            source,
            json.dumps([{"id": f"call_{readback}", "function": {"name": "terminal"}}]),
        ),
    )
    connection.execute(
        "INSERT INTO messages VALUES (4, ?, 'tool', ?, ?, NULL, 'terminal', NULL, 0)",
        (
            source,
            json.dumps(
                {
                    "output": "READBACK_VERIFIED True\nORIGINAL_MESSAGE_ID 5037\nEPHEMERAL_COPY_DELETED True\nRECEIPT_SHA256 d2b073b36c5fb0323e4ef792eb91c7d99080b7d9489b0e9257a20da87fec2c14",
                    "exit_code": 0,
                }
            ),
            f"call_{readback}",
        ),
    )
    connection.execute(
        "INSERT INTO messages VALUES (5, ?, 'user', ?, NULL, NULL, NULL, '5046', 0)",
        (inbound, COMMAND),
    )
    connection.commit()
    connection.close()
    settings = _settings(db, receipt=receipt, factory_root=tmp_path / "factory")
    object.__setattr__(settings, "session_id", inbound)
    object.__setattr__(settings, "delivery_source_session_id", source)
    object.__setattr__(settings, "delivery_tool_call_id", call)
    object.__setattr__(settings, "readback_tool_call_id", readback)

    result = GatewayFinancialApprovalReconciler(settings).readback()

    expected_status = (
        "DELIVERY_TOOL_PROVENANCE_UNVERIFIED"
        if tamper_tool_digest
        else "RECONCILED_FINANCIAL_ONLY"
    )
    assert result.status == expected_status
    assert result.command_platform_message_id == "5046"
    assert (tmp_path / "factory").exists() is not tamper_tool_digest


def test_rejects_tampered_canonical_delivery_call_id_before_unique_join(tmp_path):
    db = tmp_path / "state.db"
    _gateway(db)
    receipt = _delivery_receipt(tmp_path)
    settings = _settings(db, receipt=receipt, factory_root=tmp_path / "factory")
    object.__setattr__(settings, "delivery_source_session_id", SESSION)
    object.__setattr__(settings, "delivery_tool_call_id", "f" * 32)
    object.__setattr__(settings, "readback_tool_call_id", "e" * 32)

    result = GatewayFinancialApprovalReconciler(settings).readback()

    assert result.status == "DELIVERY_TOOL_PROVENANCE_UNVERIFIED"
    assert not (tmp_path / "factory").exists()


def test_rejects_empty_canonical_delivery_source_session_id(tmp_path):
    try:
        GatewayFinancialReconciliationSettings(
            gateway_state_db=tmp_path / "state.db",
            session_id=SESSION,
            configured_chat_id=CHAT,
            configured_sender_id=CHAT,
            inbound_message_id="5046",
            outbound_message_id="5037",
            request_hash=HASH,
            revision="818556d97edb648f",
            amount_usd="10.00",
            command=COMMAND,
            valid_until=datetime.now(UTC) + timedelta(minutes=5),
            delivery_source_session_id="",
            delivery_tool_call_id="a" * 32,
            readback_tool_call_id="b" * 32,
        )
    except ValueError as error:
        assert str(error) == "canonical Hermes source session is required"
    else:
        raise AssertionError("empty canonical source session must be rejected")
