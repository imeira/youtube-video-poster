"""Tests for scene schema and storyboard (§34, §46-48)."""

from __future__ import annotations

import pytest

from src.pipeline.scene import (
    Scene,
    Storyboard,
    SceneImportance,
    VisualStrategy,
    QAStatus,
    decide_visual_strategy,
)


class TestSceneSchema:
    """§34: Scene schema."""

    def test_scene_creation(self):
        """Create a basic scene."""
        scene = Scene(
            scene_id="SC001",
            narration="Era uma vez um pastor.",
            start=0.0,
            end=4.5,
            duration=4.5,
        )
        assert scene.scene_id == "SC001"
        assert scene.importance == SceneImportance.NORMAL
        assert scene.visual_strategy == VisualStrategy.LOCAL_ANIMATED_STILL
        assert scene.qa_status == QAStatus.PENDING

    def test_scene_to_dict(self):
        """Scene should serialize to dict correctly."""
        scene = Scene(
            scene_id="SC002",
            narration="Davi derrotou Golias",
            start=10.0, end=15.0, duration=5.0,
            importance=SceneImportance.CRITICAL,
            visual_strategy=VisualStrategy.RUNPOD_GENERATIVE_VIDEO,
        )
        d = scene.to_dict()
        assert d["importance"] == "CRITICAL"
        assert d["visual_strategy"] == "RUNPOD_GENERATIVE_VIDEO"
        assert d["duration"] == 5.0

    def test_scene_from_dict(self):
        """Scene should deserialize from dict."""
        data = {
            "scene_id": "SC003",
            "narration": "Test",
            "start": 5.0, "end": 10.0, "duration": 5.0,
            "importance": "HIGH",
            "visual_strategy": "LOCAL_ANIMATED_STILL",
            "qa_status": "APPROVED",
        }
        scene = Scene.from_dict(data)
        assert scene.importance == SceneImportance.HIGH
        assert scene.qa_status == QAStatus.APPROVED


class TestVisualStrategyEngine:
    """§46-48: Visual Strategy Engine."""

    def test_critical_with_movement_and_budget_uses_runpod(self):
        """CRITICAL + movement + budget → RunPod (§68)."""
        strategy = decide_visual_strategy(
            importance=SceneImportance.CRITICAL,
            has_movement=True,
            budget_allows_cloud=True,
        )
        assert strategy == VisualStrategy.RUNPOD_GENERATIVE_VIDEO

    def test_high_with_movement_and_budget_uses_runpod(self):
        """HIGH + movement + budget → RunPod."""
        strategy = decide_visual_strategy(
            SceneImportance.HIGH, True, True
        )
        assert strategy == VisualStrategy.RUNPOD_GENERATIVE_VIDEO

    def test_normal_uses_local(self):
        """NORMAL → local animation."""
        strategy = decide_visual_strategy(
            SceneImportance.NORMAL, True, True
        )
        assert strategy == VisualStrategy.LOCAL_ANIMATED_STILL

    def test_critical_without_budget_uses_local(self):
        """CRITICAL but no budget → local fallback (§77)."""
        strategy = decide_visual_strategy(
            SceneImportance.CRITICAL, True, False
        )
        assert strategy == VisualStrategy.LOCAL_ANIMATED_STILL

    def test_no_movement_uses_local(self):
        """No movement → local animated still."""
        strategy = decide_visual_strategy(
            SceneImportance.NORMAL, False, True
        )
        assert strategy == VisualStrategy.LOCAL_ANIMATED_STILL


class TestStoryboard:
    """Storyboard aggregation."""

    def test_storyboard_properties(self):
        """Storyboard should count scenes correctly."""
        scenes = [
            Scene("SC001", "a", 0, 4, 4, visual_strategy=VisualStrategy.LOCAL_ANIMATED_STILL),
            Scene("SC002", "b", 4, 8, 4, visual_strategy=VisualStrategy.RUNPOD_GENERATIVE_VIDEO),
            Scene("SC003", "c", 8, 12, 4, visual_strategy=VisualStrategy.LOCAL_ANIMATED_STILL),
        ]
        sb = Storyboard(episode_id="TEST", scenes=scenes, total_duration=12)
        assert sb.scene_count == 3
        assert sb.runpod_scene_count == 1
        assert sb.local_scene_count == 2
        assert sb.runpod_seconds == 4.0

    def test_storyboard_serialization(self):
        """Storyboard should survive to_dict/from_dict."""
        scenes = [
            Scene("SC001", "test", 0, 5, 5, importance=SceneImportance.HIGH),
        ]
        sb = Storyboard(episode_id="TEST", scenes=scenes, total_duration=5)
        d = sb.to_dict()
        sb2 = Storyboard.from_dict(d)
        assert sb2.scene_count == 1
        assert sb2.scenes[0].importance == SceneImportance.HIGH
