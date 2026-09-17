"""EP8 authority adapter tests use only temporary episode roots."""
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
from PIL import Image

from src.agents.thumbnail import ThumbnailContract
from src.hybrid.artifacts import atomic_json, sha256, Manifest
from src.hybrid.compiled import CompiledEpisode
from src.hybrid.ep8_offline import Ep8OfflineAdapter
from src.hybrid.offline import OfflineCoordinator, main


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    image = root / "approved.png"
    Image.new("RGB", (1920, 1080), "green").save(image)
    audio = root / "audio.mp3"
    audio.write_bytes(b"existing approved audio")
    script = root / "script.md"
    script.write_text("approved narration")
    bindings, frames = [], []
    for index in range(1, 40):
        fid = f"R{index:03d}"
        name = f"frames/{fid}.json"
        atomic_json(root / name, {"frame_id": fid, "status": "APPROVED",
            "publication_authorized": False, "asset_path": "approved.png", "sha256": sha256(image)})
        bindings.append({"frame_id": fid, "manifest_path": name, "manifest_sha256": sha256(root / name)})
        frames.append({"frame_id": fid, "sequence_index": index, "start_s": index-1,
            "end_s": index, "prompt_en": "existing prompt", "action_visual_pt": "existing action"})
    atomic_json(root / "story.json", {"episode_id": "EP8", "frames": frames,
        "audio_contract": {"source": "audio.mp3", "duration_s": 39},
        "video_plan": {"ending_tail_s": 4}, "thumbnail_plan": {
            "headline": "Uma promessa", "required_title": "A promessa de um filho para Abra\u00e3o e Sara",
            "required_subtitle": "\u2014 G\u00eanesis 15\u201318"}})
    atomic_json(root / "state.json", {"episode_id": "EP8", "publication_authorized": False,
        "checkpoint": {"revision_v2": {"storyboard_path": "story.json",
            "storyboard_sha256": sha256(root / "story.json"), "required_frame_count": 39,
            "approved_frame_count": 39, "approved_assets": [f["frame_id"] for f in frames],
            "pending_assets": [], "manifest_bindings": bindings}}})
    atomic_json(root / "compiled/EP8_visual_freeze_v2.json", {"episode_id": "EP8",
        "frame_count": 39, "frame_manifest_bindings": bindings, "visual_freeze_pass": True,
        "render_authorized": True, "publication_authorized": False,
        "contact_sheet": {"path": "approved.png", "sha256": sha256(image)}})
    atomic_json(root / "audio/narration_v1_manifest.json", {"episode_id": "EP8", "status": "PASS",
        "ffprobe_decode_verified": True, "audio_path": "audio.mp3", "audio_sha256": sha256(audio),
        "script_path": "script.md", "script_sha256": sha256(script), "duration_s": 39})
    return root


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def packet(source, workspace):
    adapter = Ep8OfflineAdapter(source)
    revision = adapter.build(workspace, dry_run=True)["source_revision"]
    result = adapter.build(workspace, expected_revision=revision)
    from pathlib import Path
    directory = Path(result["packet"])
    return (CompiledEpisode.load(directory / "compiled.json"), Manifest.load(directory / "manifest.json"),
        ThumbnailContract(**json.loads((directory / "copy.json").read_text())))


def test_cli_dry_run_is_read_only_and_packet_reaches_coordinator(source, tmp_path, capsys):
    before = snapshot(source)
    workspace = tmp_path / "delivery"
    assert main(["import-ep8", "--source-root", str(source), "--workspace", str(workspace), "--dry-run"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "VERIFIED" and result["frame_count"] == 39
    assert not workspace.exists()
    inputs = packet(source, workspace)
    c = OfflineCoordinator(workspace)
    receipt = c.approve_plan(*inputs, reviewer="human")
    renderer = Mock()
    def render(*args, **kwargs):
        args[4].write_bytes(b"local video")
        return {"api_cost": 0}
    renderer.render.side_effect = render
    state = c.prepare(*inputs, plan_receipt=receipt, renderer=renderer)
    assert state["status"] == "WAITING_THUMBNAIL_APPROVAL"
    assert len(renderer.render.call_args.args[0]) == 39
    assert snapshot(source) == before
    (source / "state.json").write_bytes((source / "state.json").read_bytes() + b" ")
    with pytest.raises(ValueError, match="stale EP8"):
        c.status()
    pair = state["active"]
    with pytest.raises(ValueError, match="stale EP8"):
        c.decide("thumbnail", pair["revision"], pair["thumbnail"]["sha256"],
                 approved=True, reviewer="human")


@pytest.mark.parametrize("name", ["state.json", "audio.mp3", "approved.png", "script.md", "frames/R031.json"])
def test_changed_source_blocks_coordinator_before_render(source, tmp_path, name):
    inputs = packet(source, tmp_path / "inputs")
    c = OfflineCoordinator(tmp_path / "delivery")
    receipt = c.approve_plan(*inputs, reviewer="human")
    path = source / name
    path.write_bytes(path.read_bytes() + b" ")
    renderer = Mock()
    with pytest.raises(ValueError):
        c.prepare(*inputs, plan_receipt=receipt, renderer=renderer)
    renderer.render.assert_not_called()
    assert c.status()["active"] is None


def test_stale_revision_and_unsafe_destination_write_nothing(source, tmp_path):
    adapter = Ep8OfflineAdapter(source)
    before = snapshot(source)
    for path in (source, source / "output", source.parent):
        with pytest.raises(ValueError, match="separate"):
            adapter.build(path, dry_run=True)
    workspace = tmp_path / "delivery"
    with pytest.raises(ValueError, match="stale"):
        adapter.build(workspace, expected_revision="old")
    with pytest.raises(ValueError, match="revision required"):
        adapter.build(workspace)
    assert not workspace.exists()
    assert snapshot(source) == before


@pytest.mark.parametrize("change", ["partial", "freeze", "duplicate", "gap", "escape"])
def test_invalid_authority_fails_closed(source, tmp_path, change):
    state = json.loads((source / "state.json").read_text())
    revision = state["checkpoint"]["revision_v2"]
    if change == "partial":
        revision["approved_frame_count"] = 38
    elif change == "freeze":
        freeze = source / "compiled/EP8_visual_freeze_v2.json"
        value = json.loads(freeze.read_text())
        value["frame_manifest_bindings"] = []
        atomic_json(freeze, value)
    elif change == "escape":
        outside = tmp_path / "outside.json"
        atomic_json(outside, {})
        revision["storyboard_path"] = str(outside)
    else:
        board = json.loads((source / "story.json").read_text())
        if change == "duplicate":
            board["frames"][1]["frame_id"] = "R001"
        else:
            board["frames"][1]["start_s"] = 1.5
        atomic_json(source / "story.json", board)
        revision["storyboard_sha256"] = sha256(source / "story.json")
    atomic_json(source / "state.json", state)
    with pytest.raises(ValueError):
        Ep8OfflineAdapter(source).build(tmp_path / "output", dry_run=True)
    assert not (tmp_path / "output").exists()


def test_copy_and_timeline_cannot_override_approved_source(source, tmp_path):
    episode, manifest, contract = packet(source, tmp_path / "inputs")
    c = OfflineCoordinator(tmp_path / "delivery")
    with pytest.raises(ValueError, match="approved artifacts"):
        c.approve_plan(episode, manifest, replace(contract, headline="changed"), reviewer="human")
    with pytest.raises(ValueError, match="compiled input"):
        c.approve_plan(replace(episode, frames=episode.frames[:-1]), manifest, contract, reviewer="human")


def test_source_change_during_render_never_activates_delivery(source, tmp_path):
    inputs = packet(source, tmp_path / "inputs")
    c = OfflineCoordinator(tmp_path / "delivery")
    receipt = c.approve_plan(*inputs, reviewer="human")
    renderer = Mock()
    def render(*args, **kwargs):
        args[4].write_bytes(b"local video")
        state = source / "state.json"
        state.write_bytes(state.read_bytes() + b" ")
        return {"api_cost": 0}
    renderer.render.side_effect = render
    with pytest.raises(ValueError, match="stale EP8"):
        c.prepare(*inputs, plan_receipt=receipt, renderer=renderer)
    assert c.status()["active"] is None
