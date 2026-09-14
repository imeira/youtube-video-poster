import pytest

from src.hybrid.control import ControlPlane, EpisodeSpec


def spec():
    return EpisodeSpec(
        episode_id="EP_TEST",
        mode="TEST",
        frames=("R001", "R002"),
        storyboard_sha256="a" * 64,
        audio_sha256="b" * 64,
        budget_cap_usd="1.00",
    )


def test_control_plane_emits_compact_baselines_then_render_after_independent_qa(
    tmp_path,
):
    control = ControlPlane(tmp_path / "episode.db", spec())

    assert [action.kind for action in control.tick()] == ["BASELINE", "BASELINE"]
    control.record_candidate("R001", "c" * 64, producer_id="generator")
    control.record_candidate("R002", "d" * 64, producer_id="generator")
    assert control.tick() == []

    control.record_qa("R001", "c" * 64, approved=True, reviewer_id="qa-a")
    control.record_qa("R002", "d" * 64, approved=True, reviewer_id="qa-b")
    assert [action.kind for action in control.tick()] == ["RENDER"]
    assert control.compact_status() == {
        "episode_id": "EP_TEST",
        "mode": "TEST",
        "frames": 2,
        "approved": 2,
        "rejected": 0,
        "pending": 0,
        "eligible": ["RENDER"],
    }


def test_control_plane_requires_hash_bound_independent_immutable_qa(tmp_path):
    control = ControlPlane(tmp_path / "episode.db", spec())
    control.record_candidate("R001", "c" * 64, producer_id="generator")

    with pytest.raises(ValueError, match="independent"):
        control.record_qa("R001", "c" * 64, approved=True, reviewer_id="generator")
    with pytest.raises(ValueError, match="candidate hash"):
        control.record_qa("R001", "d" * 64, approved=True, reviewer_id="qa")

    control.record_qa("R001", "c" * 64, approved=False, reviewer_id="qa")
    with pytest.raises(ValueError, match="immutable"):
        control.record_qa("R001", "c" * 64, approved=True, reviewer_id="qa-other")


def test_control_plane_releases_only_one_hash_bound_remediation_successor(tmp_path):
    control = ControlPlane(tmp_path / "episode.db", spec())
    control.record_candidate("R001", "c" * 64, producer_id="generator")
    control.record_qa("R001", "c" * 64, approved=False, reviewer_id="qa")

    assert [
        action.kind for action in control.tick() if action.frame_id == "R001"
    ] == ["REMEDIATE"]
    control.consume("REMEDIATE", "R001", predecessor_sha256="c" * 64)
    assert not [action for action in control.tick() if action.frame_id == "R001"]


def test_control_plane_cannot_consume_an_ineligible_action(tmp_path):
    control = ControlPlane(tmp_path / "episode.db", spec())
    control.record_candidate("R001", "c" * 64, producer_id="generator")

    with pytest.raises(ValueError, match="not eligible"):
        control.consume("BASELINE", "R001")
    with pytest.raises(ValueError, match="not eligible"):
        control.consume("RENDER", None)
