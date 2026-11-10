"""Bounded authenticated client for Hermes' local receipt export.

Production discovery is deliberately pathless from the caller's perspective:
the endpoint and bearer capability are read from the fixed protected Hermes
deployment.  Tests may opt into an explicitly named TEST constructor.
"""
from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
from pathlib import Path
from typing import Any

from src.path_guard import reject_reparse_path

_FRAME_LIMIT = 65_536
_PRODUCTION_IPC_ROOT = Path(
    "C:/HermesStudio/production/hermes/studio-consent-ipc"
)
_TOKEN_FILE = "receipt-export.token"
_ENDPOINT_FILE = "receipt-export.endpoint"
_TEST_TOKEN = object()


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


def _checked_file(path: Path, label: str) -> Path:
    try:
        checked = reject_reparse_path(path)
        info = checked.stat()
    except (OSError, ValueError) as error:
        raise HermesIssuerClientError(f"{label} is unavailable") from error
    if not stat.S_ISREG(info.st_mode):
        raise HermesIssuerClientError(f"{label} must be a regular file")
    return checked


def _verify_windows_acl(path: Path) -> None:
    """Require an explicit owner/SYSTEM/Administrators-only Windows DACL."""
    script = r"""
$ErrorActionPreference = 'Stop'
$acl = Get-Acl -LiteralPath $args[0]
$owner = $acl.Owner
$allowed = @(
  $owner,
  'NT AUTHORITY\SYSTEM',
  'BUILTIN\Administrators'
)
$bad = @($acl.Access | Where-Object {
  $_.AccessControlType -eq 'Allow' -and $allowed -notcontains $_.IdentityReference.Value
})
[pscustomobject]@{ protected = -not $acl.AreAccessRulesProtected; bad = $bad.Count }
  | ConvertTo-Json -Compress
"""
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script, str(path)],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        result = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as error:
        raise HermesIssuerClientError("cannot verify protected Windows capability ACL") from error
    # Inheritance is acceptable only when every effective allow ACE remains in
    # the narrow owner/SYSTEM/Administrators set.  ``protected`` is reported for
    # diagnostics but the effective rule set is the security boundary.
    if type(result) is not dict or result.get("bad") != 0:
        raise HermesIssuerClientError("Hermes capability ACL grants another principal")


def _verify_protection(path: Path) -> None:
    if os.name == "nt":
        _verify_windows_acl(path)
        return
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
        raise HermesIssuerClientError("Hermes capability file is not owner-only")


class HermesIssuerClient:
    """One-request client for the exact Hermes ``receipt.current`` wire."""

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
        checked = _checked_file(path, label)
        if protected:
            _verify_protection(checked)
        try:
            raw = checked.read_bytes()
            value = raw.decode("ascii", errors="strict")
        except (OSError, UnicodeError) as error:
            raise HermesIssuerClientError(f"{label} is unreadable") from error
        if not value or value != value.strip() or len(raw) > 4096:
            raise HermesIssuerClientError(f"{label} is malformed")
        return value

    def _connect(self, endpoint: str) -> socket.socket:
        if endpoint.startswith("tcp://"):
            address = endpoint.removeprefix("tcp://")
            if address.count(":") != 1:
                raise HermesIssuerClientError("Hermes TCP endpoint is malformed")
            host, raw_port = address.split(":", 1)
            if host != "127.0.0.1" or not raw_port.isdecimal():
                raise HermesIssuerClientError("Hermes TCP endpoint must be published localhost")
            port = int(raw_port)
            if not 1 <= port <= 65535:
                raise HermesIssuerClientError("Hermes TCP port is invalid")
            return socket.create_connection((host, port), timeout=2.0)
        if not hasattr(socket, "AF_UNIX"):
            raise HermesIssuerClientError("AF_UNIX is unavailable for Hermes endpoint")
        path = Path(endpoint)
        if not path.is_absolute() or "\x00" in endpoint:
            raise HermesIssuerClientError("Hermes Unix endpoint must be absolute")
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(2.0)
        client.connect(endpoint)
        return client

    def current(self, receipt_id: str) -> dict[str, Any]:
        if not isinstance(receipt_id, str) or not receipt_id or len(receipt_id) > 256:
            raise HermesIssuerClientError("bounded Hermes receipt ID is required")
        endpoint = self._read_ascii(
            self._endpoint_file, "Hermes endpoint capability", protected=self._production
        )
        token = self._read_ascii(
            self._token_file, "Hermes bearer capability", protected=self._production
        )
        packet = {
            "token": token,
            "request": {"method": "receipt.current", "receipt_id": receipt_id},
        }
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
                # A second frame or trailing bytes is never part of this protocol.
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
        value = _json_object(response[:-1], "Hermes response")
        if set(value) == {"error"}:
            raise HermesIssuerClientError(f"Hermes receipt export rejected request: {value['error']}")
        if set(value) != {"receipt", "revocation"}:
            raise HermesIssuerClientError("Hermes response schema is not closed")
        return value
