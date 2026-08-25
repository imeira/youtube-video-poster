"""SOL storyboard identity anchors must survive timestamp alignment."""

from __future__ import annotations

import json

import pytest

from src.agents.storyboard import StoryboardAgent


@pytest.mark.asyncio
async def test_sol_scene_characters_and_canonical_references_are_preserved(tmp_path, monkeypatch):
    plan = {
        "scenes": [{
            "scene_id": "SC001",
            "narration_pt": "O casal caminhava junto entre as árvores do jardim.",
            "visual_prompt_en": "High-quality stylized 3D children's animation, canonical Adam and Eve walking together in Eden",
            "characters": ["adão", "eva"],
            "references": [
                "assets/characters/creation/adam/face_v1.png",
                "assets/characters/creation/eve/face_v1.png",
            ],
            "forbidden_characters": [],
        }]
    }
    plan_path = tmp_path / "sol_plan.json"
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("STUDIO_SOL_PLAN_PATH", str(plan_path))

    result = await StoryboardAgent().run(
        episode_id="EP2",
        narration=plan["scenes"][0]["narration_pt"],
        sentence_timestamps=[{
            "text": plan["scenes"][0]["narration_pt"],
            "start": 0.0,
            "end": 5.0,
            "duration": 5.0,
        }],
        storyboard_dir=str(tmp_path / "storyboard"),
    )

    scene = result.data["scenes"][0]
    assert scene["characters"] == ["adão", "eva"]
    assert scene["references"] == plan["scenes"][0]["references"]
    assert scene["source_model"] == "gpt-5.6-sol"
    assert "3d render" not in scene["negative_prompt"].lower()
    assert "exposed intimate areas" in scene["negative_prompt"].lower()
    assert "unclothed" not in scene["animation_prompt"].lower()
