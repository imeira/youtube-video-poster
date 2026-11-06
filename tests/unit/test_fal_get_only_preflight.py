from __future__ import annotations

from decimal import Decimal

import pytest

import src.hybrid.fal_get_only_preflight as fal_preflight
from src.hybrid.fal_get_only_preflight import (
    FalCredentialResolutionError,
    _price_snapshot,
    build_get_only_evidence,
    resolve_default_fal_credential,
)


def test_resolver_reads_test_default_fixture_privately_and_prefers_existing_fal_key(tmp_path, monkeypatch):
    dotenv = tmp_path / "test-fal-credentials.fixture"
    dotenv.write_text("FAL_API_KEY=test-secondary\nFAL_KEY=test-primary\n", encoding="utf-8")
    monkeypatch.setattr(fal_preflight, "DEFAULT_DOTENV_PATH", dotenv)

    credential, presence = resolve_default_fal_credential()

    assert credential == "test-primary"
    assert presence == {"fal_key_present": True, "fal_api_key_present": True}


def test_resolver_reports_actual_dotenv_absence_without_environment_fallback(tmp_path, monkeypatch):
    dotenv = tmp_path / "test-fal-credentials.fixture"
    dotenv.write_text("OTHER_KEY=configured\n", encoding="utf-8")
    monkeypatch.setattr(fal_preflight, "DEFAULT_DOTENV_PATH", dotenv)
    monkeypatch.setenv("FAL_KEY", "must-not-be-inherited")

    with pytest.raises(FalCredentialResolutionError, match="default dotenv has no supported FAL credential") as raised:
        resolve_default_fal_credential()

    assert raised.value.presence == {"fal_key_present": False, "fal_api_key_present": False}
    assert "must-not-be-inherited" not in str(raised.value)


def test_evidence_is_write_once_sanitized_and_hash_bound(tmp_path):
    output = tmp_path / "get-only-current" / "preflight.json"
    evidence = build_get_only_evidence(
        output_path=output,
        credential_presence={"fal_key_present": True, "fal_api_key_present": False},
        price_snapshot={
            "maximum_usd": "0.048",
            "raw_sanitized_sha256": "a" * 64,
            "observed_at_epoch": 1_700_000_000,
        },
        openapi_sha256="b" * 64,
        observed_at_epoch=1_700_000_001,
    )

    assert output.exists()
    assert evidence["mode"] == "GET_ONLY_PREFLIGHT"
    assert evidence["post_count"] == 0
    assert evidence["price_usd"] == "0.048"
    assert evidence["proof_sha256"] == "a" * 64
    assert evidence["openapi_sha256"] == "b" * 64
    rendered = output.read_text(encoding="utf-8")
    assert "http" not in rendered.lower()
    assert "FAL_KEY" not in rendered
    with pytest.raises(FileExistsError):
        build_get_only_evidence(
            output_path=output,
            credential_presence={"fal_key_present": True, "fal_api_key_present": False},
            price_snapshot={"maximum_usd": "0.048", "raw_sanitized_sha256": "a" * 64, "observed_at_epoch": 1},
            openapi_sha256="b" * 64,
            observed_at_epoch=2,
        )


def test_price_snapshot_accepts_the_decimal_numbers_emitted_by_the_live_get_client():
    snapshot = _price_snapshot(
        {
            "prices": [{
                "endpoint_id": "blackforestlabs/flux-3/text-to-image",
                "unit_price": Decimal("0.048"),
                "unit": "image",
                "currency": "USD",
            }],
            "next_cursor": None,
            "has_more": False,
        },
        observed_at_epoch=1_700_000_000,
    )
    assert len(snapshot["raw_sanitized_sha256"]) == 64
