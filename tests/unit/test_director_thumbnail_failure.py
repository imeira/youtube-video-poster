"""Director fail-closed behavior for required thumbnail generation."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agents.base import AgentResult
from src.agents.director import DirectorAgent
from src.state.machine import EpisodeState, EpisodeStateStore


class _VisualStrategy:
    def decide(self, **_kwargs):
        return SimpleNamespace(strategy="LOCAL_ANIMATION", reason="unit test")

    def get_usage_summary(self):
        return {
            "generative_clips_used": 0,
            "generative_seconds_used": 0,
            "max_seconds": 0,
        }


def _agent(name: str, result: AgentResult) -> SimpleNamespace:
    return SimpleNamespace(name=name, run=AsyncMock(return_value=result))


@pytest.mark.asyncio
async def test_thumbnail_failure_marks_episode_failed_and_stops_finishing(tmp_path: Path):
    episode_id = "EP-THUMBNAIL-FAIL"
    paths = SimpleNamespace(
        plan_json=tmp_path / "plan.json",
        research_dir=tmp_path / "research",
        state_json=tmp_path / "state.json",
        script_dir=tmp_path / "script",
        audio_dir=tmp_path / "audio",
        storyboard_dir=tmp_path / "storyboard",
        images_dir=tmp_path / "images",
        animation_dir=tmp_path / "animation",
        final_video=tmp_path / "renders" / "final.mp4",
        request_json=tmp_path / "request.json",
        subtitles_dir=tmp_path / "subtitles",
        thumbnails_dir=tmp_path / "thumbnails",
        metadata_dir=tmp_path / "metadata",
    )
    for directory in (
        paths.research_dir,
        paths.script_dir,
        paths.audio_dir,
        paths.storyboard_dir,
        paths.images_dir,
        paths.animation_dir,
        paths.final_video.parent,
        paths.subtitles_dir,
        paths.thumbnails_dir,
        paths.metadata_dir,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    paths.plan_json.write_text(
        json.dumps({"duration_plan": {"recommended_duration_s": 180}}),
        encoding="utf-8",
    )
    (paths.research_dir / "sources.json").write_text("{}", encoding="utf-8")
    script_packet = paths.script_dir / "script.json"
    script_packet.write_text(
        json.dumps(
            {
                "audience": {"min_age": 6, "max_age": 10},
                "closing_duration_s": 4,
                "recurring_narrator": {
                    "name": "Lorena",
                    "age": 8,
                    "canonical_model_sheet": "assets/characters/narrator/lorena/model_sheet_v1.png",
                    "canonical_model_sheet_sha256": "a7da7c7aee3e780efc7265834d515316a199596889f9119b795a30f0b92696e7",
                    "canonical_voice": "assets/characters/narrator/lorena/voice_v9.mp3",
                    "canonical_voice_sha256": "cbd1bfe7e4de31f838a1068c21c2441bd1cb7541ec2581abc7ef0b5594c10e0f",
                    "canonical_voice_duration_s": 7.68,
                    "canonical_voice_language": "pt-BR",
                    "canonical_voice_scope": "closing_messages_only",
                },
                "narration": "Narração de teste.",
                "segments": [
                    {
                        "id": "S001",
                        "kind": "family_reflection",
                        "narration": "Narração de teste.",
                        "source_refs": ["Gênesis 6–9"],
                        "presenter": "Lorena",
                        "visual_mode": "generated_video",
                        "duration_s": 4,
                        "lesson_role": "episode_central_message",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    paths.request_json.write_text(
        json.dumps({"theme": "Noé e a grande arca — Gênesis 6–9", "language": "pt-BR"}),
        encoding="utf-8",
    )

    scene = {
        "scene_id": "SC001",
        "importance": "NORMAL",
        "duration": 5.0,
        "narration": "Narração de teste.",
    }
    director = DirectorAgent.__new__(DirectorAgent)
    director.script = _agent(
        "Script",
        AgentResult(
            True,
            {
                "narration": "Narração de teste.",
                "word_count": 3,
                "script_packet_path": str(script_packet),
            },
        ),
    )
    director.audio = _agent(
        "Audio",
        AgentResult(
            True,
            {
                "audio_path": str(tmp_path / "audio" / "narration.mp3"),
                "duration_s": 5.0,
                "sentence_timestamps": [],
                "word_timestamps": [],
            },
        ),
    )
    director.storyboard = _agent(
        "Storyboard",
        AgentResult(True, {"scenes": [scene], "scene_count": 1}),
    )
    director.image_gen = _agent(
        "ImageGen",
        AgentResult(
            True,
            {"generated": [{"scene_id": "SC001", "image_path": "scene.png"}], "total_generated": 1, "total_time_s": 0.1},
        ),
    )
    director.animation = _agent(
        "Animation",
        AgentResult(True, {"clips": ["scene.mp4"], "total_clips": 1, "total_time_s": 0.1}),
    )
    director.assembly = _agent("Assembly", AgentResult(True, {"duration_s": 5.0}))
    director.captions = _agent("Captions", AgentResult(True, {"files": {}}))
    director.thumbnail = _agent(
        "Thumbnail",
        AgentResult(False, error="Biblical book subtitle could not be rendered"),
    )
    director.metadata = _agent("Metadata", AgentResult(True, {"metadata": {}}))
    director._build_visual_strategy_engine = lambda *_args: _VisualStrategy()

    state = EpisodeStateStore(
        episode_id=episode_id,
        current_state=EpisodeState.WAITING_PLAN_APPROVAL,
    )

    result = await director._run_production(episode_id, SimpleNamespace(paths=paths), state)

    assert result["state"] == "GENERATING_IMAGES"
    assert result["compiled_activation"]["audio"] == str(tmp_path / "audio" / "narration.mp3")
    assert director.storyboard.run.await_args.kwargs["audio_duration_s"] == 5.0
    assert state.current_state is EpisodeState.GENERATING_IMAGES
    persisted = json.loads(paths.state_json.read_text(encoding="utf-8"))
    assert persisted["current_state"] == "GENERATING_IMAGES"
    director.thumbnail.run.assert_not_awaited()
    director.metadata.run.assert_not_awaited()
