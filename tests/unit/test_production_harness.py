"""Real local CLI acceptance test: source packet -> encode -> independent gates."""
import json
import sqlite3
import wave
from pathlib import Path

import pytest

from tests.unit.test_ep8_offline_adapter import source, snapshot
from src.hybrid.artifacts import atomic_json, sha256
from src.hybrid.offline import OfflineCoordinator
from src.hybrid.render import probe


@pytest.fixture
def approved_source(source):
    with wave.open(str(source / "audio.mp3"), "wb") as wav:
        wav.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 156000)
    board = json.loads((source / "story.json").read_text())
    for i, frame in enumerate(board["frames"]):
        frame.update(start_s=i * .5, end_s=(i + 1) * .5,
                     narration_text="Deus fez uma promessa.", source_ref="Gênesis 15:1-6")
    board["audio_contract"]["duration_s"] = 19.5
    atomic_json(source / "story.json", board)
    state = json.loads((source / "state.json").read_text())
    state["checkpoint"]["revision_v2"]["storyboard_sha256"] = sha256(source / "story.json")
    atomic_json(source / "state.json", state)
    (source / "script.md").write_text("\n".join(f["narration_text"] for f in board["frames"]), encoding="utf-8")
    audio = json.loads((source / "audio/narration_v1_manifest.json").read_text())
    audio.update(duration_s=19.5, audio_sha256=sha256(source / "audio.mp3"), script_sha256=sha256(source / "script.md"))
    atomic_json(source / "audio/narration_v1_manifest.json", audio)
    return source


def cli(monkeypatch, capsys, workspace, action, *args):
    from src.cli.main import main
    monkeypatch.setattr("sys.argv", ["studio", "production", action, "--workspace", str(workspace), *map(str, args)])
    code = main()
    return code, json.loads(capsys.readouterr().out)


def test_approved_episode_cli_reaches_independent_gates(approved_source, tmp_path, monkeypatch, capsys):
    from src.hybrid import production
    invocations = []
    real_command = production.command
    def counted(args, **kwargs):
        invocations.append(args)
        return real_command(args, **kwargs)
    monkeypatch.setattr(production, "command", counted)
    root = tmp_path / "production"
    before = snapshot(approved_source)
    code, plan = cli(monkeypatch, capsys, root, "plan", "--source-root", approved_source)
    assert code == 0 and plan["status"] == "WAITING_PLAN_APPROVAL"
    assert cli(monkeypatch, capsys, root, "run")[0] == 1
    assert cli(monkeypatch, capsys, root, "approve-plan", "--plan-hash", plan["plan_hash"], "--reviewer", "test human")[0] == 0
    code, run = cli(monkeypatch, capsys, root, "run")
    assert code == 0 and run["status"] == "TECHNICAL_QA_PASSED"
    video = Path(run["video"])
    info = probe(video)
    assert [(s["codec_name"], s.get("width"), s.get("height")) for s in info["streams"]] == [("h264", 1920, 1080), ("aac", None, None)]
    assert abs(float(info["format"]["duration"]) - 23.5) < .1
    for name in ("transcript.txt", "captions.srt", "captions.vtt", "metadata.json"):
        assert (video.parent / name).is_file()
    report = json.loads((video.parent / "performance.json").read_text())
    assert report["render_invocations"] == 1 and report["api_cost"] == 0
    assert "subtitles=" not in report["filtergraph"]
    files = snapshot(video.parent)
    assert cli(monkeypatch, capsys, root, "run")[0] == 0
    assert snapshot(video.parent) == files
    code, delivery = cli(monkeypatch, capsys, root, "deliver")
    assert code == 0 and delivery["status"] == "WAITING_THUMBNAIL_APPROVAL"
    pair = delivery["active"]
    assert Path(pair["video"]["path"]).suffix == ".mp4"
    assert len(invocations) == 1
    c = OfflineCoordinator(root / "delivery")
    with pytest.raises(ValueError, match="thumbnail approval"):
        c.decide("video", pair["revision"], pair["video"]["sha256"], approved=True, reviewer="human")
    c.decide("thumbnail", pair["revision"], pair["thumbnail"]["sha256"], approved=True, reviewer="human")
    assert cli(monkeypatch, capsys, root, "status")[1]["status"] == "WAITING_VIDEO_APPROVAL"
    c.decide("video", pair["revision"], pair["video"]["sha256"], approved=True, reviewer="human")
    assert cli(monkeypatch, capsys, root, "status")[1]["status"] == "READY_FOR_PUBLICATION"
    c.decide("video", pair["revision"], pair["video"]["sha256"], approved=False, reviewer="human", feedback="revise")
    assert cli(monkeypatch, capsys, root, "status")[1]["status"] == "REJECTED"
    assert cli(monkeypatch, capsys, root, "deliver")[0] == 1
    assert len(invocations) == 1
    assert snapshot(approved_source) == before
    with sqlite3.connect(root / "production.sqlite3") as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("DELETE FROM events")
    (approved_source / "script.md").write_text("changed", encoding="utf-8")
    assert cli(monkeypatch, capsys, root, "status")[0] == 1
    assert cli(monkeypatch, capsys, root, "deliver")[0] == 1


def test_missing_assets_fail_closed(approved_source, tmp_path, monkeypatch, capsys):
    (approved_source / "approved.png").unlink()
    root = tmp_path / "production"
    code, result = cli(monkeypatch, capsys, root, "plan", "--source-root", approved_source)
    assert code == 1 and result["status"] == "ASSETS_REQUIRED"
    assert not list(root.rglob("*.mp4"))
    assert cli(monkeypatch, capsys, root, "status")[1]["status"] == "ASSETS_REQUIRED"


@pytest.mark.parametrize("name", ["script.md", "audio.mp3", "approved.png", "state.json", "story.json"])
def test_source_mutation_blocks_run(approved_source, tmp_path, monkeypatch, capsys, name):
    root = tmp_path / "production"
    _, plan = cli(monkeypatch, capsys, root, "plan", "--source-root", approved_source)
    assert cli(monkeypatch, capsys, root, "approve-plan", "--plan-hash", plan["plan_hash"], "--reviewer", "human")[0] == 0
    path = approved_source / name
    path.write_bytes(path.read_bytes() + b" ")
    assert cli(monkeypatch, capsys, root, "run")[0] == 1
    assert cli(monkeypatch, capsys, root, "deliver")[0] == 1
    assert not list(root.rglob("*.mp4"))


@pytest.mark.parametrize("change", ["baby", "names", "scope", "copy", "unsafe", "script"])
def test_editorial_violations_block_before_render(approved_source, tmp_path, monkeypatch, capsys, change):
    board = json.loads((approved_source / "story.json").read_text())
    frame = board["frames"][0]
    if change == "baby":
        frame["action_visual_pt"] = "Sara segura o bebê Isaque no colo"
    elif change == "names":
        frame["action_visual_pt"] = "Abraão e Sara"
    elif change == "scope":
        frame["source_ref"] = "Gênesis 21:1-6"
    elif change == "copy":
        board["thumbnail_plan"]["required_title"] = "A different title"
    elif change == "unsafe":
        frame["narration_text"] = "Comente seu nome."
    else:
        frame["narration_text"] = "Texto sem aprovação."
    atomic_json(approved_source / "story.json", board)
    state = json.loads((approved_source / "state.json").read_text())
    state["checkpoint"]["revision_v2"]["storyboard_sha256"] = sha256(approved_source / "story.json")
    atomic_json(approved_source / "state.json", state)
    root = tmp_path / "production"
    assert cli(monkeypatch, capsys, root, "plan", "--source-root", approved_source)[0] == 1
    assert not list(root.rglob("*.mp4"))


def test_packet_hash_approval_and_overlap_fail_closed(approved_source, tmp_path, monkeypatch, capsys):
    before = snapshot(approved_source)
    assert cli(monkeypatch, capsys, approved_source / "output", "plan", "--source-root", approved_source)[0] == 1
    assert snapshot(approved_source) == before
    root = tmp_path / "production"
    _, plan = cli(monkeypatch, capsys, root, "plan", "--source-root", approved_source)
    assert cli(monkeypatch, capsys, root, "plan", "--source-root", approved_source)[1] == plan
    assert cli(monkeypatch, capsys, root, "approve-plan", "--plan-hash", "old", "--reviewer", "human")[0] == 1
    packet = Path(plan["packet"]) / "editorial.json"
    packet.write_bytes(packet.read_bytes() + b" ")
    assert cli(monkeypatch, capsys, root, "status")[0] == 1
    assert cli(monkeypatch, capsys, root, "run")[0] == 1


def test_missing_audio_has_no_tts_side_effect(approved_source, tmp_path, monkeypatch, capsys):
    (approved_source / "audio.mp3").unlink()
    code, result = cli(monkeypatch, capsys, tmp_path / "production", "plan", "--source-root", approved_source)
    assert code == 1 and result["status"] == "ASSETS_REQUIRED"


def test_current_storyboard_field_names_are_normalized(approved_source, tmp_path, monkeypatch, capsys):
    """The preserved EP8 storyboard uses exact_active_narration/biblical_reference."""
    board = json.loads((approved_source / "story.json").read_text())
    for frame in board["frames"]:
        frame["exact_active_narration"] = frame.pop("narration_text")
        frame["biblical_reference"] = frame.pop("source_ref")
    board["frames"][0]["biblical_reference"] = "Gênesis 17:15-16"
    atomic_json(approved_source / "story.json", board)
    state = json.loads((approved_source / "state.json").read_text())
    state["checkpoint"]["revision_v2"]["storyboard_sha256"] = sha256(approved_source / "story.json")
    atomic_json(approved_source / "state.json", state)
    code, result = cli(monkeypatch, capsys, tmp_path / "production", "plan", "--source-root", approved_source)
    assert code == 0
    assert result["status"] == "WAITING_PLAN_APPROVAL"


def test_markdown_editorial_headings_are_excluded_from_narration_binding(approved_source, tmp_path, monkeypatch, capsys):
    script = approved_source / "script.md"
    script.write_text("# EP8\n\n**Público:** crianças\n\n### História\n\n" + script.read_text(encoding="utf-8"), encoding="utf-8")
    audio = json.loads((approved_source / "audio/narration_v1_manifest.json").read_text())
    audio["script_sha256"] = sha256(script)
    atomic_json(approved_source / "audio/narration_v1_manifest.json", audio)
    code, result = cli(monkeypatch, capsys, tmp_path / "production", "plan", "--source-root", approved_source)
    assert code == 0
    assert result["status"] == "WAITING_PLAN_APPROVAL"
