"""Parent-only FAL credential resolution and one bounded GET preflight.

This module deliberately reads the Hermes default-profile dotenv as data without
loading it into ``os.environ``.  It never constructs a reviewer process and has
no POST, reservation, or dispatch capability.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from dotenv import dotenv_values

from src.hybrid.fal_billing import AiohttpFalBillingClient, FalConsumerError
from src.hybrid.jaco_flux3_final_admission import ENDPOINT, MAX_USD

# This is the Windows platform default Hermes home, not a selected profile and
# not an environment-controlled path.  The parent alone may read it.
DEFAULT_PROFILE_HERMES_HOME = Path.home() / "AppData" / "Local" / "hermes"
DEFAULT_DOTENV_PATH = DEFAULT_PROFILE_HERMES_HOME / ".env"
_PRICE_API_URL = "https://api.fal.ai/v1/models/pricing"
_OPENAPI_URL = "https://fal.ai/api/openapi/queue/openapi.json?endpoint_id=blackforestlabs/flux-3/text-to-image"
_OPENAPI_HOST = "fal.ai"
_MAX_OPENAPI_BYTES = 1_048_576


class FalCredentialResolutionError(RuntimeError):
    """A scrubbed status-only credential resolution failure."""

    def __init__(self, message: str, presence: dict[str, bool]) -> None:
        super().__init__(message)
        self.presence = presence


def _present(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def resolve_default_fal_credential(*, dotenv_path: Path | None = None) -> tuple[str, dict[str, bool]]:
    """Read only known FAL aliases from the explicit default dotenv path.

    This does not consult or modify process environment, so a reviewer or a
    caller-provided inherited environment cannot silently supply a secret.
    """
    path = DEFAULT_DOTENV_PATH if dotenv_path is None else Path(dotenv_path)
    try:
        values = dotenv_values(path)
    except (OSError, UnicodeDecodeError):
        values = {}
    primary, compatibility = values.get("FAL_KEY"), values.get("FAL_API_KEY")
    presence = {"fal_key_present": _present(primary), "fal_api_key_present": _present(compatibility)}
    if presence["fal_key_present"]:
        assert isinstance(primary, str)
        return primary.strip(), presence
    if presence["fal_api_key_present"]:
        assert isinstance(compatibility, str)
        return compatibility.strip(), presence
    raise FalCredentialResolutionError("default dotenv has no supported FAL credential", presence)


def _canonical(value: object) -> bytes:
    def decimal_only(candidate: object) -> str:
        if isinstance(candidate, Decimal):
            return str(candidate)
        raise TypeError("unsupported canonical value")

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False, default=decimal_only).encode("utf-8")


def _price_snapshot(raw_price_page: object, *, observed_at_epoch: int) -> dict[str, object]:
    """Parse exactly one documented endpoint price from the GET response."""
    if type(observed_at_epoch) is not int or not isinstance(raw_price_page, dict):
        raise RuntimeError("FAL pricing response is invalid")
    if set(raw_price_page) != {"prices", "next_cursor", "has_more"}:
        raise RuntimeError("FAL pricing response shape is unsupported")
    if raw_price_page.get("next_cursor") is not None or raw_price_page.get("has_more") is not False:
        raise RuntimeError("FAL pricing response is paginated")
    records = raw_price_page.get("prices")
    if not isinstance(records, list):
        raise TypeError("FAL pricing response lacks prices")
    matches = [record for record in records if isinstance(record, dict) and record.get("endpoint_id") == ENDPOINT]
    if len(matches) != 1:
        raise RuntimeError("FAL pricing response lacks one endpoint quote")
    record = matches[0]
    if set(record) != {"endpoint_id", "unit_price", "unit", "currency"}:
        raise RuntimeError("FAL endpoint quote shape is unsupported")
    unit = record.get("unit")
    if record.get("currency") != "USD" or not isinstance(unit, str) or not unit or str(record.get("unit_price")) != MAX_USD:
        raise RuntimeError("FAL endpoint quote is not the required flat price")
    return {
        "maximum_usd": MAX_USD,
        "raw_sanitized_sha256": sha256(_canonical(raw_price_page)).hexdigest(),
        "observed_at_epoch": observed_at_epoch,
    }


def _fresh_openapi_sha256() -> str:
    request = urllib.request.Request(_OPENAPI_URL, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # nosec: fixed HTTPS origin
            body = response.read(_MAX_OPENAPI_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"FAL OpenAPI GET returned HTTP {error.code}") from None
    except (OSError, TimeoutError, ValueError):
        raise RuntimeError("FAL OpenAPI GET failed") from None
    if len(body) > _MAX_OPENAPI_BYTES:
        raise RuntimeError("FAL OpenAPI response exceeded body limit")
    try:
        document = json.loads(body.decode("utf-8"))
        endpoint = document["info"]["x-fal-metadata"]["endpointId"]
    except (KeyError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
        raise RuntimeError("FAL OpenAPI response is invalid") from None
    if endpoint != ENDPOINT:
        raise RuntimeError("FAL OpenAPI endpoint mismatch")
    return sha256(body).hexdigest()


def build_get_only_evidence(*, output_path: Path, credential_presence: dict[str, bool],
                            price_snapshot: dict[str, object], openapi_sha256: str,
                            observed_at_epoch: int) -> dict[str, object]:
    """Persist the minimal durable projection; it contains no URLs or secrets."""
    if (set(credential_presence) != {"fal_key_present", "fal_api_key_present"}
            or not all(type(value) is bool for value in credential_presence.values())):
        raise ValueError("invalid credential presence projection")
    proof = price_snapshot.get("raw_sanitized_sha256")
    if (price_snapshot.get("maximum_usd") != MAX_USD or not isinstance(proof, str) or len(proof) != 64
            or not isinstance(openapi_sha256, str) or len(openapi_sha256) != 64 or type(observed_at_epoch) is not int):
        raise ValueError("invalid preflight evidence")
    body: dict[str, object] = {
        "schema": "FAL_GET_ONLY_PREFLIGHT/1",
        "mode": "GET_ONLY_PREFLIGHT",
        "endpoint_id": ENDPOINT,
        "credential_presence": credential_presence,
        "price_usd": MAX_USD,
        "proof_sha256": proof,
        "openapi_sha256": openapi_sha256,
        "observed_at_epoch": observed_at_epoch,
        "post_count": 0,
        "reservation_count": 0,
    }
    evidence = {**body, "artifact_sha256": sha256(_canonical(body)).hexdigest()}
    encoded = _canonical(evidence)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(output_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())
    return evidence


def run_get_only_preflight(*, output_path: Path, dotenv_path: Path | None = None) -> dict[str, object]:
    """Perform exactly one authenticated pricing GET and one public OpenAPI GET."""
    credential, presence = resolve_default_fal_credential(dotenv_path=dotenv_path)
    observed_at = int(time.time())
    try:
        raw_price_page = asyncio.run(AiohttpFalBillingClient(credential).pricing({"endpoint_id": ENDPOINT}))
    except FalConsumerError as error:
        raise RuntimeError(str(error)) from None
    snapshot = _price_snapshot(raw_price_page, observed_at_epoch=observed_at)
    openapi_sha256 = _fresh_openapi_sha256()
    return build_get_only_evidence(output_path=output_path, credential_presence=presence,
                                   price_snapshot=snapshot, openapi_sha256=openapi_sha256,
                                   observed_at_epoch=observed_at)
