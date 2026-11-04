"""Regression coverage for compiled worker evidence and filesystem trust boundaries."""

from __future__ import annotations

import asyncio
import os
import subprocess
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions are native reparse points")
def test_compiled_worker_rejects_junctioned_tc_before_metadata_read_or_sqlite_factory(
    tmp_path, monkeypatch
):
    """The configured workspace is guarded before candidate discovery or SQLite."""
    from src.hybrid import autonomous_cli

    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    (external / "runner").mkdir(parents=True)
    (external / "compiled-harness.json").write_text("{}", encoding="utf-8")
    workspace.mkdir()
    completed = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(workspace / "TC"), str(external)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if completed.returncode:
        pytest.skip("junction setup unavailable")

    reads: list[Path] = []
    original_read_text = Path.read_text

    def trap_read_text(self, *args, **kwargs):
        reads.append(self)
        return original_read_text(self, *args, **kwargs)

    def no_executor(*_args, **_kwargs):
        raise AssertionError("Executor factory must not run for a junctioned workspace")

    monkeypatch.setattr(Path, "read_text", trap_read_text)
    monkeypatch.setattr(autonomous_cli, "Executor", no_executor)

    with pytest.raises(ValueError, match="reparse-point"):
        asyncio.run(
            autonomous_cli._fixture_worker_handler(
                {
                    "kind": "compiled",
                    "root": str(workspace),
                    "episode_id": "TEST-JUNCTION",
                    "_worker_id": "worker-1",
                    "fencing_token": 1,
                    "_lease_assertion": lambda _ownership: None,
                }
            )
        )

    assert not [path for path in reads if path == external / "compiled-harness.json"]


def test_director_delivery_has_no_callback_capability_authority(monkeypatch):
    """Even a forged callable capability is rejected before any external copy/read."""
    import inspect
    import shutil

    from src.hybrid import autonomous
    from src.hybrid.autonomous import AutonomousCompiledTestAdapter

    parameters = inspect.signature(
        AutonomousCompiledTestAdapter.stage_director_delivery_owned
    ).parameters
    assert "recovery_capability" not in parameters
    assert not hasattr(autonomous, "_DirectorRecoveryCapability")
    assert not hasattr(autonomous, "_issue_director_recovery_capability")

    class ForgedCapability:
        def reopen(self):
            return {"schema": "compiled-director-render-receipt-v1"}

    copies: list[tuple] = []
    monkeypatch.setattr(shutil, "copyfile", lambda *args: copies.append(args))
    with pytest.raises(TypeError, match="recovery_capability"):
        AutonomousCompiledTestAdapter.stage_director_delivery_owned(
            object(), None, None, lambda _evidence: {}, ownership=object(),
            recovery_capability=ForgedCapability(),
        )
    assert copies == []


def test_director_stage_rejects_forged_on_disk_receipt_with_real_mp4_before_copy(tmp_path):
    """A real master does not rescue a receipt that disagrees with completion evidence."""
    from src.hybrid.autonomous import AutonomousCompiledTestAdapter, PolicyError
    from src.hybrid.compiled_render_recovery import (
        canonical_render_json,
        render_contract_sha256,
    )

    root = tmp_path / "episodes" / "TEST-FORGED"
    qa, renders = root / "qa", root / "renders"
    qa.mkdir(parents=True)
    renders.mkdir()
    video = renders / "final.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
         "color=c=black:s=16x16:d=0.1", "-pix_fmt", "yuv420p", str(video)], check=True,
    )
    video_sha256 = sha256(video.read_bytes()).hexdigest()
    scene_sha256 = "a" * 64
    manifest_sha256 = "b" * 64
    audio_sha256 = "c" * 64
    contract = {
        "schema": "compiled-render-contract-v1",
        "canonical_episode_root": str(root.absolute()),
        "manifest_sha256": manifest_sha256,
        "audio_sha256": audio_sha256,
        "scene_hashes": [{"image_sha256": scene_sha256, "clip_sha256": None}],
        "renderer_config": {
            "width": 1280, "height": 720, "fps": 24,
            "filter_complex_threads": None, "scene_workers": None, "encoder_threads": None,
        },
        "hold_seconds": 4,
    }
    contract_sha256 = render_contract_sha256(contract)
    intent = {
        "schema": "compiled-render-intent-v1",
        "render_contract": contract,
        "render_contract_sha256": contract_sha256,
    }
    intent_bytes = canonical_render_json(intent)
    (qa / "compiled_render_intent.json").write_bytes(intent_bytes)
    renderer_receipt = {"output_sha256": video_sha256}
    completion = {
        "schema": "compiled-renderer-completion-v1",
        "intent_sha256": sha256(intent_bytes).hexdigest(),
        "render_contract_sha256": contract_sha256,
        "output_sha256": video_sha256,
        "renderer_receipt": renderer_receipt,
        "renderer_receipt_sha256": sha256(canonical_render_json(renderer_receipt)).hexdigest(),
    }
    completion_bytes = canonical_render_json(completion)
    (qa / "renderer_completion_receipt.json").write_bytes(completion_bytes)
    forged_director_receipt = {
        "schema": "compiled-director-render-receipt-v1",
        "intent_sha256": sha256(intent_bytes).hexdigest(),
        "completion_sha256": "0" * 64,
        "render_contract": contract,
        "render_contract_sha256": contract_sha256,
        "renderer_receipt": renderer_receipt,
    }
    (qa / "compiled_render_receipt.json").write_bytes(canonical_render_json(forged_director_receipt))

    class Pipeline:
        episode = SimpleNamespace(audio=SimpleNamespace(sha256=audio_sha256))

        @staticmethod
        def approved_manifest():
            return SimpleNamespace(checksum=manifest_sha256)

        @staticmethod
        def render_scenes(_manifest):
            return [SimpleNamespace(image=SimpleNamespace(sha256=scene_sha256), clip=None)]

    adapter = object.__new__(AutonomousCompiledTestAdapter)
    adapter._canonical_episode_root = root
    adapter.pipeline = Pipeline()
    adapter.runner = SimpleNamespace(episode_id="TEST-FORGED")
    adapter._stage_local_delivery = lambda *_args, **_kwargs: pytest.fail("must reject before copy")

    with pytest.raises(PolicyError, match="canonical recovery is unavailable"):
        adapter.stage_director_delivery_owned(lambda _evidence: {}, ownership=object())

    assert video.read_bytes()[:8] != b""
