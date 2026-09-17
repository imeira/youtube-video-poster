"""Offline delivery, immutable receipts and fail-closed human gates."""
import json
import sqlite3
import wave
from dataclasses import replace
from unittest.mock import Mock

import pytest
from PIL import Image

from src.agents.thumbnail import ThumbnailContract
from src.approval.receipts import record_plan_approval, require_plan_approval
from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.compiled import CompiledEpisode, FrameSpec
from src.hybrid.offline import OfflineCoordinator, main
from src.hybrid.render import LocalRenderer


@pytest.fixture
def inputs(tmp_path):
    audio = tmp_path / "audio.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * 8000)
    image = tmp_path / "image.png"
    Image.new("RGB", (64, 64), "green").save(image)
    asset = FrozenAsset.approve(image, "human", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet([asset], sheet)
    manifest = Manifest.freeze([asset], FrozenAsset.approve(sheet, "human", "TEST"), "human", "TEST")
    episode = CompiledEpisode.compile("EP8", FrozenAsset.approve(audio, "human", "TEST"),
        [FrameSpec("R001", 0, 1, "sky", "look")])
    contract = ThumbnailContract("Uma promessa de esperança", "A promessa de um filho para Abraão e Sara", "— Gênesis 15–18")
    return episode, manifest, contract


class Renderer:
    def render(self, scenes, manifest, audio, srt, output, *, hold):
        output.write_bytes(f"local video {hold}".encode())
        return {"api_cost": 0}


def prepare(coordinator, inputs, hold=4, renderer=None):
    receipt = coordinator.approve_plan(*inputs, reviewer="human", hold=hold)
    return coordinator.prepare(*inputs, plan_receipt=receipt, hold=hold, renderer=renderer or Renderer())


def decide(c, state, kind, approved=True):
    pair = state["active"]
    return c.decide(kind, pair["revision"], pair[kind]["sha256"], approved=approved,
                    reviewer="human", feedback="Revise composition and closing" if not approved else "")


def receipts(c):
    with sqlite3.connect(c.database) as db:
        return dict(db.execute("SELECT id, body FROM receipts"))


@pytest.mark.parametrize("rejected_kind", ["thumbnail", "video"])
def test_restart_rejection_supersedes_pair_without_rewriting_receipts(tmp_path, inputs, rejected_kind):
    c = OfflineCoordinator(tmp_path / "delivery")
    first = prepare(c, inputs)
    with pytest.raises(ValueError, match="thumbnail approval"):
        decide(c, first, "video")
    first = decide(c, first, "thumbnail")
    first = decide(c, first, "video")
    assert first["status"] == "READY_FOR_PUBLICATION"
    history = receipts(c)
    c = OfflineCoordinator(c.root)
    rejected = decide(c, first, rejected_kind, False)
    assert rejected["active"] is None
    assert all(receipts(c)[key] == value for key, value in history.items())
    with pytest.raises(ValueError, match="active revision"):
        decide(c, first, "video")
    with pytest.raises(ValueError, match="distinct hashes"):
        prepare(c, inputs)
    updated = (*inputs[:2], replace(inputs[2], headline="Esperar com confiança"))
    second = prepare(c, updated, hold=5)
    for kind in ("video", "thumbnail"):
        assert second["active"][kind] != first["active"][kind]
        assert second["active"][kind]["sha256"] != first["active"][kind]["sha256"]
    assert second["active"]["approvals"] == {}
    with pytest.raises(ValueError, match="active revision"):
        decide(c, first, "thumbnail")
    assert decide(c, decide(c, second, "thumbnail"), "video")["status"] == "READY_FOR_PUBLICATION"


def test_invalid_plan_and_mutated_inputs_never_render(tmp_path, inputs):
    c = OfflineCoordinator(tmp_path / "delivery")
    renderer = Mock()
    with pytest.raises(ValueError, match="persisted"):
        c.prepare(*inputs, plan_receipt="APROVAR", renderer=renderer)
    receipt = c.approve_plan(*inputs, reviewer="human")
    with pytest.raises(ValueError, match="persisted"):
        c.prepare(*inputs, plan_receipt=receipt, hold=5, renderer=renderer)
    inputs[0].audio.path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash mismatch"):
        c.prepare(*inputs, plan_receipt=receipt, renderer=renderer)
    renderer.render.assert_not_called()


def test_approval_checks_current_bytes_and_resume_is_idempotent(tmp_path, inputs):
    c = OfflineCoordinator(tmp_path / "delivery")
    state = prepare(c, inputs)
    renderer = Mock()
    assert prepare(c, inputs, renderer=renderer) == state
    renderer.render.assert_not_called()
    from pathlib import Path
    Path(state["active"]["thumbnail"]["path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        decide(c, state, "thumbnail")


def test_rejection_transaction_rolls_back_both_receipt_and_pair(tmp_path, inputs, monkeypatch):
    c = OfflineCoordinator(tmp_path / "delivery")
    state = decide(c, prepare(c, inputs), "thumbnail")
    history = receipts(c)
    monkeypatch.setattr(c, "_save", Mock(side_effect=RuntimeError("disk failure")))
    with pytest.raises(RuntimeError, match="disk failure"):
        decide(c, state, "video", False)
    assert c.status() == state
    assert receipts(c) == history


def test_failed_render_cannot_advance_gate(tmp_path, inputs):
    c = OfflineCoordinator(tmp_path / "delivery")
    renderer = Mock()
    renderer.render.side_effect = RuntimeError("render failed")
    with pytest.raises(RuntimeError, match="render failed"):
        prepare(c, inputs, renderer=renderer)
    assert c.status()["active"] is None
    assert all(json.loads(body)["kind"] == "plan" for body in receipts(c).values())


def test_main_cli_offline_does_not_construct_director(tmp_path, monkeypatch):
    import src.cli.main as cli
    monkeypatch.setattr(cli, "DirectorAgent", Mock(side_effect=AssertionError("provider path")))
    monkeypatch.setattr("sys.argv", ["studio", "offline", "status", "--workspace", str(tmp_path)])
    assert cli.main() == 0


def test_cli_arbitrary_approve_plan_cannot_grant_approval(tmp_path, inputs):
    episode, manifest, contract = inputs
    episode.save(tmp_path / "compiled.json")
    manifest.save(tmp_path / "manifest.json")
    from dataclasses import asdict
    (tmp_path / "copy.json").write_text(json.dumps(asdict(contract)), encoding="utf-8")
    with pytest.raises(SystemExit) as error:
        main(["prepare", "--workspace", str(tmp_path / "delivery"),
              "--compiled", str(tmp_path / "compiled.json"),
              "--manifest", str(tmp_path / "manifest.json"), "--copy", str(tmp_path / "copy.json"),
              "--approve-plan", "any human-looking string"])
    assert error.value.code == 2
    assert not list((tmp_path / "delivery").glob("revisions/*"))


def test_legacy_plan_receipt_is_required_and_hash_bound(tmp_path):
    plan = tmp_path / "plan.json"
    plan.write_text("{}")
    with pytest.raises(ValueError, match="Persisted"):
        require_plan_approval(plan)
    receipt = record_plan_approval(plan, "human")
    original = receipt.read_bytes()
    require_plan_approval(plan)
    plan.write_text('{"changed":true}')
    with pytest.raises(ValueError, match="Persisted"):
        require_plan_approval(plan)
    assert receipt.read_bytes() == original


def test_real_local_render_reaches_both_human_gates(tmp_path, inputs):
    import shutil
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("local FFmpeg required")
    c = OfflineCoordinator(tmp_path / "delivery")
    state = prepare(c, inputs, renderer=LocalRenderer(width=64, height=64, fps=10))
    assert state["status"] == "WAITING_THUMBNAIL_APPROVAL"
    state = decide(c, state, "thumbnail")
    assert state["status"] == "WAITING_VIDEO_APPROVAL"
    assert decide(c, state, "video")["status"] == "READY_FOR_PUBLICATION"
