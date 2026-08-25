from src.pipeline.reference_generation import align_plan_to_timestamps, select_reference
from src.agents.animation import AnimationAgent
from types import SimpleNamespace
import pytest


def test_aligns_split_tts_sentences_to_one_semantic_scene():
    plan = [
        {"narration_pt": "Deus criou a luz."},
        {"narration_pt": "Adão olhou para Eva. Ele sorriu."},
    ]
    timestamps = [
        {"text": "Deus criou a luz.", "start": 0.0, "end": 2.0},
        {"text": "Adão olhou para Eva.", "start": 2.0, "end": 4.0},
        {"text": "Ele sorriu.", "start": 4.0, "end": 5.0},
    ]
    result = align_plan_to_timestamps(plan, timestamps)
    assert len(result) == 2
    assert result[1]["start"] == 2.0
    assert result[1]["end"] == 5.0
    assert result[1]["duration"] == 3.0


def test_reference_selection_uses_approved_actor_images():
    refs = {"adam": "adam.png", "eve": "eve.png", "adam_eve": "both.png"}
    assert select_reference("sem pessoas", [], refs) is None
    assert select_reference("Adão cuida do jardim", ["adão"], refs) == "adam.png"
    assert select_reference("Adão encontra Eva", ["adão", "eva"], refs) == "both.png"


@pytest.mark.asyncio
async def test_animation_preserves_fractional_audio_duration():
    captured = []

    class FakeProvider:
        async def image_to_video(self, **kwargs):
            captured.append(kwargs["duration"])
            return SimpleNamespace(success=True, video_path="clip.mp4", generation_time=0.1)

    agent = AnimationAgent()
    agent._provider = FakeProvider()
    result = await agent.run(
        episode_id="EP",
        scenes=[{"scene_id": "SC001", "duration": 4.782, "camera": "slow_push_in"}],
        images=[{"scene_id": "SC001", "image_path": "image.png"}],
    )
    assert result.success
    assert captured == [4.782]
    assert result.data["clips"][0]["duration_s"] == 4.782
