"""Offline CLI contract for the GET-only FAL billing consumer."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from src.hybrid import fal_billing_cli
from src.hybrid.fal_billing import LoopbackFalBillingFixture
from src.hybrid.fal_billing_cli import main

ROOT = Path(__file__).resolve().parents[2]
PYTHON = Path(sys.executable)


def test_fal_billing_preflight_is_fresh_process_offline_and_unqualified():
    completed = subprocess.run(
        [str(PYTHON), "-m", "src.cli.main", "fal-billing", "preflight", "--endpoint-id", "fal-ai/flux/dev"],
        cwd=ROOT, env={**os.environ, "PYTHONPATH": "", "FAL_KEY": ""},
        capture_output=True, text=True, check=False,
    )
    report = json.loads(completed.stdout)
    assert completed.returncode == 0, completed.stderr
    assert report == {
        "mode": "READ_ONLY", "network": "NOT_REQUESTED", "qualification": "NOT_QUALIFIED",
        "status": "PREFLIGHT_ONLY", "endpoint_id": "fal-ai/flux/dev",
    }


@pytest.mark.parametrize("argv", [
    ["preflight", "--endpoint-id", ""],
    ["quote", "--readback", "--endpoint-id", "", "--quantity-cap", "1"],
    ["readback", "--readback", "--endpoint-id", "fal-ai/flux/dev", "--request-id", "\x01", "--start", "2026-10-25T00:00:00Z", "--end", "2026-10-25T02:00:00Z"],
])
def test_invalid_cli_contract_never_reads_secret_or_constructs_client(monkeypatch, capsys, argv):
    calls = 0

    def factory(_key):
        nonlocal calls
        calls += 1
        raise AssertionError("invalid input must not construct a client")

    original_environ = os.environ

    class NoFalKeyRead:
        def get(self, key, *args, **kwargs):
            if key == "FAL_KEY":
                raise AssertionError("invalid input must not read FAL_KEY")
            return original_environ.get(key, *args, **kwargs)

    monkeypatch.setattr(fal_billing_cli, "os", types.SimpleNamespace(environ=NoFalKeyRead()))
    assert asyncio.run(main(argv, client_factory=factory)) == 1
    assert calls == 0
    assert json.loads(capsys.readouterr().out)["status"] == "UNKNOWN"


def test_explicit_readback_uses_injected_test_loopback_and_never_claims_live(monkeypatch, capsys):
    fixture = LoopbackFalBillingFixture(
        pricing_pages=[],
        billing_pages=[{
            "billing_events": [{
                "request_id": "opaque-id", "endpoint_id": "fal-ai/flux/dev", "timestamp": "2026-10-25T01:00:00Z",
                "quantity": 1, "output_units": 1, "unit": "image", "unit_price": 0.025,
                "percent_discount": None, "cost_subtotal": 0.025, "cost_discount": 0,
                "cost_total": 0.025, "cost_estimate_nano_usd": 25000000,
            }], "next_cursor": None, "has_more": False,
        }],
    )
    monkeypatch.setenv("FAL_KEY", "test-key-never-output")
    code = asyncio.run(main([
        "readback", "--readback", "--request-id", "opaque-id", "--endpoint-id", "fal-ai/flux/dev",
        "--start", "2026-10-25T00:00:00Z", "--end", "2026-10-25T02:00:00Z",
    ], client_factory=lambda _: fixture))
    report = json.loads(capsys.readouterr().out)

    assert code == 0
    assert report["status"] == "BILLED"
    assert report["actual_cost_usd"] == "0.025"
    assert report["authority"] == "TEST_NOT_AUTHORITY"
    assert report["qualification"] == "NOT_QUALIFIED"
    assert "test-key-never-output" not in json.dumps(report)
