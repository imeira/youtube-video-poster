"""Episode identifiers are literal storage keys, never filesystem paths."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.hybrid.artifacts import FrozenAsset, digest
from src.hybrid.compiled import CompiledEpisode, FrameSpec
from src.storage.episode_fs import EpisodeFS


def _asset(path: Path) -> FrozenAsset:
    path.write_bytes(b"TEST fixture")
    return FrozenAsset.approve(path, "TEST fixture", "TEST")


def _compiled(episode_id: str, audio: FrozenAsset) -> CompiledEpisode:
    return CompiledEpisode.compile(
        episode_id,
        audio,
        (FrameSpec("S001", 0, 1, "test prompt", "test action"),),
    )


@pytest.mark.parametrize(
    "episode_id",
    (
        "",
        ".",
        "..",
        "../../outside",
        "/absolute",
        r"C:foo",
        r"\\server\share",
        r"folder\episode",
        "episode:stream",
        "NUL",
        "con",
        "LPT9",
        "EP\x00ID",
    ),
)
def test_compiled_episode_rejects_nonliteral_storage_identifiers(tmp_path, episode_id):
    with pytest.raises(ValueError, match="episode_id"):
        _compiled(episode_id, _asset(tmp_path / "audio.wav"))


@pytest.mark.parametrize("episode_id", ("T", "EP8", "EP2_EDEN", "EP5_TOWER_OF_BABEL", "TEST-COMPILED-WORKER"))
def test_existing_episode_identifier_formats_remain_literal_and_valid(tmp_path, episode_id):
    episode = _compiled(episode_id, _asset(tmp_path / "audio.wav"))

    fs = EpisodeFS(episode.episode_id, SimpleNamespace(episodes_dir=tmp_path / "episodes"))

    assert fs.paths.root == tmp_path / "episodes" / episode_id


def test_episode_fs_rejects_escape_before_creating_any_directory(tmp_path):
    episodes_dir = tmp_path / "episodes"

    with pytest.raises(ValueError, match="episode_id"):
        EpisodeFS("../../outside", SimpleNamespace(episodes_dir=episodes_dir)).create_dirs()

    assert not episodes_dir.exists()
    assert not (tmp_path.parent / "outside").exists()


def test_start_compiled_rejects_traversal_episode_before_workspace_or_database(tmp_path):
    from src.hybrid.autonomous_cli import compiled_policy_document, start_compiled

    packet = tmp_path / "packet"
    packet.mkdir()
    audio = _asset(packet / "audio.wav")
    # Forge a checksum-valid packet as an untrusted caller could; construction
    # APIs already reject this literal, so exercise the JSON loader boundary.
    _compiled("TEST-PACKET", audio).save(packet / "compiled.json")
    compiled_document = json.loads((packet / "compiled.json").read_text(encoding="utf-8"))
    compiled_document["episode"]["episode_id"] = "../../outside"
    compiled_document["checksum"] = digest(compiled_document["episode"])
    (packet / "compiled.json").write_text(json.dumps(compiled_document), encoding="utf-8")
    # The unsafe literal must be rejected before the CLI parses any other packet input.
    (packet / "manifest.json").write_text("{}", encoding="utf-8")
    (packet / "policy.json").write_text(json.dumps(compiled_policy_document()), encoding="utf-8")
    workspace = tmp_path / "workspace"

    with pytest.raises(ValueError, match="episode_id"):
        start_compiled(workspace, packet, None)

    assert not workspace.exists()
    assert not (tmp_path.parent / "outside").exists()
    assert not list(tmp_path.rglob("*.sqlite3"))
