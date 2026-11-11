from __future__ import annotations

import base64
import hashlib
import hmac
import json
import socket
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from src.hybrid.hermes_issuer_client import HermesIssuerClient, HermesIssuerClientError

TOKEN = "9f" * 32
SERVER_DOMAIN = b"hermes-studio-server-v1\0"
CLIENT_DOMAIN = b"hermes-studio-client-v1\0"


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _b64(value: int) -> str:
    return base64.urlsafe_b64encode(bytes([value]) * 32).rstrip(b"=").decode()


def _proof(domain: bytes, value: object) -> str:
    return hmac.new(TOKEN.encode(), domain + _canonical(value), hashlib.sha256).hexdigest()


def _challenge(request: dict[str, object]) -> dict[str, object]:
    signed = {
        "client_nonce": request["client_nonce"],
        "server_challenge": _b64(1),
        "server_epoch": _b64(2),
        "session_id": _b64(3),
    }
    return {**{key: signed[key] for key in signed if key != "client_nonce"},
            "server_proof": _proof(SERVER_DOMAIN, signed)}


class _SocketService:
    def __init__(
        self,
        tmp_path: Path,
        exchanges: int,
        response: Callable[[int, dict[str, object]], bytes],
    ) -> None:
        self.packets: list[dict[str, object]] = []
        self._response = response
        self._exchanges = exchanges
        self._listener = socket.socket()
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen()
        port = self._listener.getsockname()[1]
        self.endpoint_file = tmp_path / "receipt-export.endpoint"
        self.token_file = tmp_path / "receipt-export.token"
        self.endpoint_file.write_text(f"test+tcp+hmac://127.0.0.1:{port}", encoding="ascii")
        self.token_file.write_text(TOKEN, encoding="ascii")
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            for index in range(self._exchanges):
                connection, _ = self._listener.accept()
                with connection:
                    raw = b""
                    while not raw.endswith(b"\n"):
                        raw += connection.recv(4096)
                    packet = json.loads(raw)
                    self.packets.append(packet)
                    connection.sendall(self._response(index, packet) + b"\n")
        finally:
            self._listener.close()

    def client(self) -> HermesIssuerClient:
        return HermesIssuerClient.for_test(
            endpoint_file=self.endpoint_file, token_file=self.token_file
        )

    def finish(self) -> None:
        self._thread.join(timeout=3)
        assert not self._thread.is_alive()


def _authenticated_response(index: int, packet: dict[str, object]) -> bytes:
    assert set(packet) == {"request"}
    request = packet["request"]
    assert isinstance(request, dict)
    if index % 2 == 0:
        assert set(request) == {"method", "client_nonce"}
        assert request["method"] == "session.challenge"
        return _canonical(_challenge(request))
    assert set(request) == {
        "method", "receipt_id", "client_nonce", "server_epoch", "session_id",
        "server_challenge", "client_proof",
    }
    assert request["method"] == "receipt.current"
    signed = {key: value for key, value in request.items() if key != "client_proof"}
    assert hmac.compare_digest(request["client_proof"], _proof(CLIENT_DOMAIN, signed))
    return _canonical({"receipt": {"receipt_id": request["receipt_id"]}, "revocation": None})


def test_authenticated_wire_hides_token_and_uses_fresh_challenge(tmp_path):
    server = _SocketService(tmp_path, 4, _authenticated_response)
    try:
        assert server.client().current("receipt-1")["receipt"]["receipt_id"] == "receipt-1"
        assert server.client().current("receipt-2")["receipt"]["receipt_id"] == "receipt-2"
    finally:
        server.finish()

    assert TOKEN.encode() not in b"\n".join(_canonical(packet) for packet in server.packets)
    first_nonce = server.packets[0]["request"]["client_nonce"]
    second_nonce = server.packets[2]["request"]["client_nonce"]
    assert first_nonce != second_nonce
    assert server.packets[1]["request"]["client_nonce"] == first_nonce
    assert server.packets[3]["request"]["client_nonce"] == second_nonce


@pytest.mark.parametrize("attack", ["error", "schema", "proof", "noncanonical"])
def test_challenge_response_attacks_fail_closed(tmp_path, attack):
    def response(_index, packet):
        request = packet["request"]
        value = _challenge(request)
        if attack == "error":
            return _canonical({"error": "unauthorized"})
        if attack == "schema":
            value["extra"] = True
        elif attack == "proof":
            value["server_proof"] = "0" * 64
        if attack == "noncanonical":
            return json.dumps(value, indent=2).encode()
        return _canonical(value)

    server = _SocketService(tmp_path, 1, response)
    try:
        with pytest.raises(HermesIssuerClientError):
            server.client().current("receipt-1")
    finally:
        server.finish()


@pytest.mark.parametrize("attack", ["error", "schema", "noncanonical"])
def test_current_response_attacks_fail_closed(tmp_path, attack):
    def response(index, packet):
        if index == 0:
            return _canonical(_challenge(packet["request"]))
        value = {"receipt": {}, "revocation": None}
        if attack == "error":
            value = {"error": "unauthorized"}
        elif attack == "schema":
            value["extra"] = True
        if attack == "noncanonical":
            return json.dumps(value, indent=2).encode()
        return _canonical(value)

    server = _SocketService(tmp_path, 2, response)
    try:
        with pytest.raises(HermesIssuerClientError):
            server.client().current("receipt-1")
    finally:
        server.finish()
