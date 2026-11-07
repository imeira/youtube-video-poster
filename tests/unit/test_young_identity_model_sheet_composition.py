from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def _fixture(root):
    operation = {"id": "JACO_GEN28_YOUNG_IDENTITY_MODEL_SHEET_001", "kind": "MODEL_SHEET", "endpoint": "blackforestlabs/flux-3/text-to-image", "call_limit": 1, "maximum_usd": "0.048", "payload": {"prompt": "closed fixture", "aspect_ratio": "16:9"}, "payload_sha256": ""}
    operation["payload_sha256"] = sha256(json.dumps(operation["payload"], sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    preflight = _write(root / "preflight.json", {"operation": operation, "replay_prohibition": True})
    policy = _write(root / "policy.json", {"canonical_consent": {"consent_message_id": 147620}})
    financial = _write(root / "financial.json", {"currency": "USD", "total_episode_cap": "10.00"})
    psha, fsha = sha256(policy.read_bytes()).hexdigest(), sha256(financial.read_bytes()).hexdigest()
    authority = _write(root / "authority.json", {"schema": "jaco-derived-operation-authority/v1", "status": "TEST_ONLY", "operation_id": operation["id"], "predecessor_operation_id": "JACO_GEN28_STYLE_REMEDIATION_001", "executor_predecessor_request_id": "", "executor_category": "first", "policy_path": str(policy), "policy_sha256": psha, "preflight_path": str(preflight), "preflight_sha256": sha256(preflight.read_bytes()).hexdigest(), "financial_source": {"path": str(financial), "sha256": fsha}, "financial_policy_sha256": fsha, "maximum_next_cost_usd": "0.048", "mode": "TEST"})
    return authority


@pytest.mark.asyncio
async def test_closed_manifest_executes_once_and_reopens_durable_evidence(tmp_path):
    from src.hybrid.execution import ProviderResult
    from src.hybrid.young_identity_model_sheet_composition import (
        YoungIdentityModelSheetComposition,
    )

    authority = _fixture(tmp_path)
    class Provider:
        mode = "TEST"
        def __init__(self, output): self.output = output
        async def submit(self, _job, _id, checkpoint):
            checkpoint(provider_id="remote-1", partial=self.output)
            self.output.write_bytes(b"one")
            _write(tmp_path / "provider-audit.json", {"submits": 1, "gets": 0})
            return ProviderResult(self.output, Decimal("0.048"))
        async def recover(self, *_args): raise AssertionError("no recovery after complete")
    composition = YoungIdentityModelSheetComposition.for_test(tmp_path, authority)
    first = await composition.execute(lambda output: Provider(output))
    second = await YoungIdentityModelSheetComposition.for_test(tmp_path, authority).execute(lambda output: Provider(output))
    assert first["status"] == second["status"] == "COMPLETE"
    assert json.loads((tmp_path / "provider-audit.json").read_text()) == {"submits": 1, "gets": 0}
    assert composition.audit()["claims"] == 1


@pytest.mark.parametrize("field,value", [("call_limit", True), ("maximum_usd", True), ("payload_sha256", "A" * 64)])
def test_manifest_rejects_bool_and_noncanonical_hash(tmp_path, field, value):
    from src.hybrid.young_identity_model_sheet_composition import (
        CompositionError,
        YoungIdentityModelSheetComposition,
    )
    authority = _fixture(tmp_path)
    preflight = tmp_path / "preflight.json"; body = json.loads(preflight.read_text()); body["operation"][field] = value; _write(preflight, body)
    auth = json.loads(authority.read_text()); auth["preflight_sha256"] = sha256(preflight.read_bytes()).hexdigest(); _write(authority, auth)
    with pytest.raises(CompositionError): YoungIdentityModelSheetComposition.for_test(tmp_path, authority).manifest()


def test_unqualified_live_fails_closed(tmp_path):
    from src.hybrid.young_identity_model_sheet_composition import (
        CompositionError,
        YoungIdentityModelSheetComposition,
    )
    authority = _fixture(tmp_path)
    auth = json.loads(authority.read_text()); auth["mode"] = "LIVE"; _write(authority, auth)
    with pytest.raises(CompositionError): YoungIdentityModelSheetComposition.for_test(tmp_path, authority).manifest()


def test_direct_construction_is_not_an_unqualified_live_route(tmp_path):
    from src.hybrid.young_identity_model_sheet_composition import (
        CompositionError,
        YoungIdentityModelSheetComposition,
    )

    authority = _fixture(tmp_path)
    with pytest.raises(CompositionError, match="direct composition"):
        YoungIdentityModelSheetComposition(tmp_path, authority)


def test_fresh_process_resume_uses_durable_audits(tmp_path):
    authority = _fixture(tmp_path)
    child = tmp_path / "child.py"
    child.write_text('''import asyncio,json,sys
from decimal import Decimal
from pathlib import Path
from src.hybrid.execution import ProviderResult
from src.hybrid.young_identity_model_sheet_composition import YoungIdentityModelSheetComposition
root,authority=Path(sys.argv[1]),Path(sys.argv[2])
class P:
 mode="TEST"
 def __init__(self,o): self.o=o
 async def submit(self,j,r,c): c(provider_id="remote",partial=self.o);(root/"provider-audit.json").write_text('{"submit":1,"get":0}');raise TimeoutError()
 async def recover(self,j,r,remote,partial,c): self.o.write_bytes(b"ok");(root/"provider-audit.json").write_text('{"submit":1,"get":1}');return ProviderResult(self.o,Decimal("0.048"))
async def go():
 try: print(json.dumps(await YoungIdentityModelSheetComposition.for_test(root,authority).execute(P)))
 except TimeoutError: print('{"status":"CRASHED"}')
asyncio.run(go())''', encoding="utf-8")
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()))
    def run():
        result = subprocess.run([sys.executable, str(child), str(tmp_path), str(authority)], check=False, env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    assert run()["status"] == "CRASHED"
    assert run()["status"] == "COMPLETE"
    assert json.loads((tmp_path / "provider-audit.json").read_text()) == {"submit": 1, "get": 1}
    from src.hybrid.young_identity_model_sheet_composition import (
        YoungIdentityModelSheetComposition,
    )
    assert YoungIdentityModelSheetComposition.for_test(tmp_path, authority).audit() == {"claims": 1}
    with sqlite3.connect(tmp_path / "ledger.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM claims").fetchone()[0] == 1
    with sqlite3.connect(tmp_path / "executor.sqlite3") as db:
        rows = [json.loads(row[0]) for row in db.execute("SELECT data FROM jobs")]
    assert len(rows) == 1
    assert rows[0]["status"] == "COMPLETE"
    assert rows[0]["provider_id"] == "remote"
