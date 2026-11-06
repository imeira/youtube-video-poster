"""One-shot GET-only recovery for the completed Jacó FLUX 3 request.

This is intentionally not a general FAL client: all request identities, roots,
limits, and paths are fixed. It cannot submit, reserve, settle, or poll status.
Signed media URLs exist only in local variables while the CDN GET is in flight.
"""
from __future__ import annotations

import json
import sqlite3
import ssl
import urllib.request
from hashlib import sha256
from pathlib import Path

import certifi

from src.hybrid.fal_get_only_preflight import resolve_default_fal_credential
from src.hybrid.jaco_flux3_production import (
    ProductionCompositionError,
    _download_fal_png_once,
    _fal_png_locator_projection,
    _NoRedirect,
    _write_once,
)

ROOT = Path("C:/HermesStudio/production/jaco-flux3")
EXECUTION = ROOT / "execution"
RECOVERY = EXECUTION / "provider-receipt.recovery.json"
EVIDENCE = EXECUTION / "recovery-evidence"
LOCAL_ID = "7f19aab26d71464a9bc257f4f566d2c6cb642aab0bc55864bad8dcbaeab0ae47"
REMOTE_ID = "01a1b229-3ac4-78f2-a320-73ee43fc8bc2"
RESULT_URL = "https://queue.fal.run/blackforestlabs/flux-3/requests/01a1b229-3ac4-78f2-a320-73ee43fc8bc2"
RECOVERY_SHA256 = "1f4543701600a508a23f9adc5db7a2d89b33309a855606f8ce0d687d1049fbe0"
RESULT_INTENT = EVIDENCE / "fal-completed-result-recovery-v1.intent.json"
IMAGE_INTENT = EVIDENCE / "fal-completed-image-download-v1.intent.json"
IMAGE_OBSERVATION = EVIDENCE / "fal-completed-image-download-v1.observation.json"
RAW = EXECUTION / "provider-receipt.quarantine.png"
RECEIPT = EXECUTION / "provider-receipt.json"
_MAX_RESULT_BYTES = 1_048_576


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _read_json(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise ProductionCompositionError("required recovery input is not a regular file")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProductionCompositionError("required recovery input is not an object")
    return value


def _validate_original_state() -> None:
    expected = {
        "schema": 1,
        "provider_request_id": REMOTE_ID,
        "status_url": RESULT_URL + "/status",
        "response_url": RESULT_URL,
    }
    if sha256(RECOVERY.read_bytes()).hexdigest() != RECOVERY_SHA256 or _read_json(RECOVERY) != expected:
        raise ProductionCompositionError("immutable recovery receipt changed")
    with sqlite3.connect(f"file:{(ROOT / 'ledger' / 'standing-budget.sqlite3').as_posix()}?mode=ro", uri=True) as db:
        row = db.execute(
            "SELECT cap, state, actual, provider_request_id, settlement_mode, authority_mode "
            "FROM claims WHERE request_id = ?", (LOCAL_ID,)
        ).fetchone()
    if row != (48000, "RESERVED", None, REMOTE_ID, "FAL_READBACK_V2", "LIVE"):
        raise ProductionCompositionError("original reservation is not the unchanged UNKNOWN 48000 micro-USD claim")


def _open_result_once(key: str) -> dict[str, object]:
    request = urllib.request.Request(RESULT_URL, method="GET", headers={"Authorization": f"Key {key}", "Accept": "application/json"})
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), _NoRedirect(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())),
    )
    try:
        with opener.open(request, timeout=30) as response:
            body = response.read(_MAX_RESULT_BYTES + 1)
    except OSError as exc:
        raise ProductionCompositionError("bounded immutable FAL result GET failed") from exc
    if len(body) > _MAX_RESULT_BYTES:
        raise ProductionCompositionError("bounded immutable FAL result exceeded size limit")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProductionCompositionError("FAL result is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ProductionCompositionError("FAL result is not an object")
    return value


def main() -> int:
    _validate_original_state()
    if any(path.exists() for path in (RESULT_INTENT, IMAGE_INTENT, IMAGE_OBSERVATION, RAW, RECEIPT)):
        raise ProductionCompositionError("one-shot recovery successor already materialized; no additional GET is allowed")
    result_url_sha256 = sha256(RESULT_URL.encode("utf-8")).hexdigest()
    _write_once(RESULT_INTENT, {
        "schema": 1, "kind": "FAL_COMPLETED_RESULT_GET_RECOVERY", "local_request_id": LOCAL_ID,
        "provider_request_id": REMOTE_ID, "recovery_receipt_sha256": RECOVERY_SHA256,
        "result_url_sha256": result_url_sha256, "result_get_count": 1, "image_get_count": 1,
        "reservation_micro_usd": 48000, "actual_billed_amount": "UNKNOWN_UNTIL_AUTHENTICATED_BILLING_RECONCILIATION",
    })
    key, _presence = resolve_default_fal_credential()
    result = _open_result_once(key)
    images = result.get("images")
    if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict) or not isinstance(images[0].get("url"), str):
        raise ProductionCompositionError("FAL result lacks one image URL")
    signed_url = images[0]["url"]
    projection = _fal_png_locator_projection(signed_url)
    _write_once(IMAGE_INTENT, {
        "schema": 1, "kind": "FAL_COMPLETED_IMAGE_GET_RECOVERY", "local_request_id": LOCAL_ID,
        "provider_request_id": REMOTE_ID, "result_url_sha256": result_url_sha256,
        "image_url_sha256": sha256(signed_url.encode("utf-8")).hexdigest(), "locator": projection,
        "image_get_count": 1, "credential_mode": "NONE", "actual_billed_amount": "UNKNOWN_UNTIL_AUTHENTICATED_BILLING_RECONCILIATION",
    })
    metadata = _download_fal_png_once(signed_url, RAW)
    _write_once(IMAGE_OBSERVATION, {
        "schema": 1, "kind": "FAL_COMPLETED_IMAGE_DOWNLOAD", "local_request_id": LOCAL_ID,
        "provider_request_id": REMOTE_ID, "image_url_sha256": sha256(signed_url.encode("utf-8")).hexdigest(),
        "locator": projection, "image": metadata,
        "actual_billed_amount": "UNKNOWN_UNTIL_AUTHENTICATED_BILLING_RECONCILIATION",
    })
    _write_once(RECEIPT, {
        "kind": "PNG_RESULT", "sha256": metadata["sha256"], "bytes": metadata["bytes"],
        "width": metadata["width"], "height": metadata["height"], "source_host": metadata["hostname"],
        "actual_billed_amount": "UNKNOWN_UNTIL_AUTHENTICATED_BILLING_RECONCILIATION",
    })
    print(json.dumps({"path": str(RAW), "sha256": metadata["sha256"], "dimensions": [metadata["width"], metadata["height"]], "source_host": metadata["hostname"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
