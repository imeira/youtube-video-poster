import pytest

from src.hybrid.artifacts import FrozenAsset
from src.hybrid.render import Scene, timing
from tests.unit.test_hybrid_execution import manifest


def test_transition_compensation_does_not_shorten_narration(tmp_path):
    frozen = manifest(tmp_path)
    scenes = [
        Scene(frozen.assets[0], 5),
        Scene(frozen.assets[0], 8),
        Scene(frozen.assets[0], 4),
    ]
    result = timing(scenes, hold=4)
    assert result["render_seconds"] == [5.25, 8.25, 4]
    assert result["offsets"] == [5, 13]
    assert result["final_seconds"] == 21
    with pytest.raises(ValueError):
        timing(scenes, hold=float("nan"))


def test_timing_quantizes_cumulative_boundaries_without_per_scene_drift(tmp_path):
    frozen = manifest(tmp_path)
    scenes = [
        Scene(frozen.assets[0], 1.001),
        Scene(frozen.assets[0], 1.001),
        Scene(frozen.assets[0], 1.001),
    ]

    result = timing(scenes, hold=3, fps=24)

    assert result["offsets"] == [1.0, 2.0]
    assert result["frame_seconds"] == [1.0, 1.0, 1.0]
    assert result["final_seconds"] == 6.0


def test_hero_scene_freeze_rejects_wrong_mode_or_changed_clip_before_render(tmp_path):
    frozen = manifest(tmp_path)
    clip = tmp_path / "artifact.txt"
    clip.write_text("hash contract fixture; not video")
    source = FrozenAsset.approve(clip, "human", "LIVE")
    with pytest.raises(ValueError, match="mode"):
        Scene(frozen.assets[0], 5, clip=source).verify(frozen)
    source = FrozenAsset.approve(clip, "human", "TEST")
    scene = Scene(frozen.assets[0], 5, clip=source)
    scene.verify(frozen)
    clip.write_text("changed")
    with pytest.raises(ValueError, match="hash"):
        scene.verify(frozen)
