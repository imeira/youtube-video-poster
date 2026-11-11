"""Bounded authenticated client for Hermes' local receipt export.

Production discovery is deliberately pathless from the caller's perspective:
the endpoint and HMAC capability are read from the fixed protected Hermes
deployment.  The capability authenticates proofs and is never sent on the
wire.  Tests may opt into an explicitly named TEST constructor.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import socket
from pathlib import Path
from typing import Any

from src.hybrid.canonical_operation_binding import (
    CanonicalOperationBindingError,
    _stable_bytes,
)

_FRAME_LIMIT = 65_536
_PRODUCTION_IPC_ROOT = Path(
    "C:/HermesStudio/production/hermes/studio-consent-ipc"
)
_TOKEN_FILE = "receipt-export.token"
_ENDPOINT_FILE = "receipt-export.endpoint"
_TEST_TOKEN = object()
_HEX_256 = re.compile(r"[0-9a-f]{64}")
_BASE64URL_256 = re.compile(r"[A-Za-z0-9_-]{43}")
_SERVER_DOMAIN = b"hermes-studio-server-v1\0"
_CLIENT_DOMAIN = b"hermes-studio-client-v1\0"


class HermesIssuerClientError(PermissionError):
    """Hermes IPC configuration or response failed closed."""


def _pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in items:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _json_object(raw: bytes, label: str) -> dict[str, Any]:
    if not raw or len(raw) > _FRAME_LIMIT:
        raise HermesIssuerClientError(f"{label} exceeds the Hermes frame limit")
    try:
        value = json.loads(
            raw.decode("utf-8", errors="strict"),
            object_pairs_hook=_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise HermesIssuerClientError(f"{label} is not strict JSON") from error
    if type(value) is not dict:
        raise HermesIssuerClientError(f"{label} must be a JSON object")
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    if raw != canonical:
        raise HermesIssuerClientError(f"{label} must be canonical JSON")
    return value


class HermesIssuerClient:
    """Challenge-authenticated client for the exact ``receipt.current`` wire."""

    __slots__ = ("_endpoint_file", "_production", "_token_file")

    def __init__(
        self,
        *,
        endpoint_file: Path,
        token_file: Path,
        production: bool,
        _test_token: object,
    ) -> None:
        if _test_token is not _TEST_TOKEN:
            raise TypeError("use production() or for_test()")
        self._endpoint_file = Path(endpoint_file)
        self._token_file = Path(token_file)
        self._production = production

    @classmethod
    def production(cls) -> HermesIssuerClient:
        return cls(
            endpoint_file=_PRODUCTION_IPC_ROOT / _ENDPOINT_FILE,
            token_file=_PRODUCTION_IPC_ROOT / _TOKEN_FILE,
            production=True,
            _test_token=_TEST_TOKEN,
        )

    @classmethod
    def for_test(
        cls, *, endpoint_file: Path, token_file: Path
    ) -> HermesIssuerClient:
        return cls(
            endpoint_file=Path(endpoint_file),
            token_file=Path(token_file),
            production=False,
            _test_token=_TEST_TOKEN,
        )

    @staticmethod
    def _read_ascii(path: Path, label: str, *, protected: bool) -> str:
        try:
            raw = _stable_bytes(path, label, protected=protected)
            value = raw.decode("ascii", errors="strict")
        except (CanonicalOperationBindingError, OSError, UnicodeError) as error:
            raise HermesIssuerClientError(f"{label} is unreadable") from error
        if not value or value != value.strip() or len(raw) > 4096:
            raise HermesIssuerClientError(f"{label} is malformed")
        return value

    def _connect(self, endpoint: str) -> socket.socket:
        tcp_schemes = ("test+tcp+hmac://", "tcp+hmac://")
        scheme = next((item for item in tcp_schemes if endpoint.startswith(item)), None)
        if scheme is not None:
            if self._production and scheme == "test+tcp+hmac://":
                raise HermesIssuerClientError("Hermes TEST endpoint is forbidden in production")
            address = endpoint.removeprefix(scheme)
            if address.count(":") != 1:
                raise HermesIssuerClientError("Hermes TCP endpoint is malformed")
            host, raw_port = address.split(":", 1)
            if host != "127.0.0.1" or not raw_port.isdecimal():
                raise HermesIssuerClientError("Hermes TCP endpoint must be published localhost")
            port = int(raw_port)
            if not 1 <= port <= 65535:
                raise HermesIssuerClientError("Hermes TCP port is invalid")
            return socket.create_connection((host, port), timeout=2.0)
        if "://" in endpoint:
            raise HermesIssuerClientError("Hermes endpoint scheme is unsupported")
        if not hasattr(socket, "AF_UNIX"):
            raise HermesIssuerClientError("AF_UNIX is unavailable for Hermes endpoint")
        path = Path(endpoint)
        if not path.is_absolute() or "\x00" in endpoint:
            raise HermesIssuerClientError("Hermes Unix endpoint must be absolute")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(2.0)
        client.connect(endpoint)
        return client

    def _exchange(self, endpoint: str, request: dict[str, Any], label: str) -> dict[str, Any]:
        packet = {"request": request}
        frame = json.dumps(
            packet, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8") + b"\n"
        if len(frame) > _FRAME_LIMIT:
            raise HermesIssuerClientError("Hermes request exceeds the frame limit")
        try:
            with self._connect(endpoint) as client:
                client.settimeout(2.0)
                client.sendall(frame)
                response = b""
                while len(response) <= _FRAME_LIMIT and not response.endswith(b"\n"):
                    chunk = client.recv(min(4096, _FRAME_LIMIT + 1 - len(response)))
                    if not chunk:
                        break
                    response += chunk
                if len(response) > _FRAME_LIMIT or not response.endswith(b"\n"):
                    raise HermesIssuerClientError("Hermes response is not one bounded frame")
                client.settimeout(0.05)
                try:
                    trailing = client.recv(1)
                except TimeoutError:
                    trailing = b""
                if trailing:
                    raise HermesIssuerClientError("Hermes returned trailing response data")
        except HermesIssuerClientError:
            raise
        except (OSError, TimeoutError) as error:
            raise HermesIssuerClientError("Hermes local receipt export is unavailable") from error
        value = _json_object(response[:-1], label)
        if set(value) == {"error"}:
            raise HermesIssuerClientError(f"Hermes receipt export rejected request: {value['error']}")
        return value

    @staticmethod
    def _challenge(value: dict[str, Any], client_nonce: str, token: str) -> dict[str, str]:
        fields = {"server_epoch", "session_id", "server_challenge", "server_proof"}
        if set(value) != fields:
            raise HermesIssuerClientError("Hermes challenge response schema is not closed")
        for name in ("server_epoch", "session_id", "server_challenge"):
            field = value[name]
            if type(field) is not str or _BASE64URL_256.fullmatch(field) is None:
                raise HermesIssuerClientError(f"Hermes challenge {name} is malformed")
        proof = value["server_proof"]
        if type(proof) is not str or _HEX_256.fullmatch(proof) is None:
            raise HermesIssuerClientError("Hermes server proof is malformed")
        signed = {
            "client_nonce": client_nonce,
            "server_challenge": value["server_challenge"],
            "server_epoch": value["server_epoch"],
            "session_id": value["session_id"],
        }
        expected = hmac.new(token.encode("ascii"), _SERVER_DOMAIN + json.dumps(
            signed, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(proof, expected):
            raise HermesIssuerClientError("Hermes server proof is invalid")
        return {name: value[name] for name in ("server_epoch", "session_id", "server_challenge")}

    def current(self, receipt_id: str) -> dict[str, Any]:
        if not isinstance(receipt_id, str) or not receipt_id or len(receipt_id) > 256:
            raise HermesIssuerClientError("bounded Hermes receipt ID is required")
        endpoint = self._read_ascii(
            self._endpoint_file, "Hermes endpoint capability", protected=self._production
        )
        token = self._read_ascii(
            self._token_file, "Hermes HMAC capability", protected=self._production
        )
        client_nonce = secrets.token_hex(32)
        challenge = self._challenge(
            self._exchange(
                endpoint,
                {"method": "session.challenge", "client_nonce": client_nonce},
                "Hermes challenge response",
            ),
            client_nonce,
            token,
        )
        request = {
            "method": "receipt.current",
            "receipt_id": receipt_id,
            "client_nonce": client_nonce,
            **challenge,
        }
        request["client_proof"] = hmac.new(
            token.encode("ascii"),
            _CLIENT_DOMAIN + json.dumps(
                request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        value = self._exchange(endpoint, request, "Hermes current response")
        if set(value) != {"receipt", "revocation"}:
            raise HermesIssuerClientError("Hermes response schema is not closed")
        return value
