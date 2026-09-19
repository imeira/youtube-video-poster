"""Recurring Lorena narrator contract for every episode closing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.agents.script import ScriptAgent
from src.agents.script_qa import ScriptQAAgent
from src.content.narrator import LORENA, bind_lorena_closing, closing_fields
from src.hybrid.planner import Config, plan


@pytest.mark.asyncio
async def test_script_packet_assigns_lorena_to_the_final_three_to_five_second_lesson(tmp_path: Path):
    research = {
        "references": [{"book": "Gênesis", "chapter": "21", "verses": "1-7"}],
        "narrative_classification": {
            "BIBLICAL_FACT": ["Sara deu à luz Isaque, como Deus havia prometido."]
        },
    }

    result = await ScriptAgent().run(
        episode_id="EP9",
        research_data=research,
        target_duration_s=180,
        script_dir=str(tmp_path),
    )

    assert result.success
    packet = json.loads((tmp_path / "script.json").read_text(encoding="utf-8"))
    assert packet["recurring_narrator"] == {
        "name": "Lorena",
        "age": 8,
        "canonical_model_sheet": "assets/characters/narrator/lorena/model_sheet_v1.png",
        "canonical_model_sheet_sha256": "a7da7c7aee3e780efc7265834d515316a199596889f9119b795a30f0b92696e7",
        "canonical_voice": "assets/characters/narrator/lorena/voice_v9.mp3",
        "canonical_voice_sha256": "cbd1bfe7e4de31f838a1068c21c2441bd1cb7541ec2581abc7ef0b5594c10e0f",
        "canonical_voice_duration_s": 7.68,
        "canonical_voice_language": "pt-BR",
        "canonical_voice_scope": "closing_messages_only",
    }
    closing = packet["segments"][-1]
    assert closing["kind"] == "family_reflection"
    assert closing["presenter"] == "Lorena"
    assert closing["visual_mode"] == "generated_video"
    assert closing["duration_s"] in (3, 4, 5)
    assert closing["lesson_role"] == "episode_central_message"


def test_script_qa_rejects_a_closing_without_the_canonical_lorena_video_contract():
    packet = {
        "audience": {"min_age": 6, "max_age": 10},
        "closing_duration_s": 4,
        "recurring_narrator": {"name": "Outra personagem", "age": 8},
        "segments": [
            {
                "id": "S001",
                "kind": "family_reflection",
                "narration": "Podemos confiar em Deus durante a espera.",
                "source_refs": [],
            }
        ],
        "narration": "Podemos confiar em Deus durante a espera.",
    }

    result = ScriptQAAgent().review(packet)

    assert result.approved is False
    assert "CANONICAL_LORENA_NARRATOR_REQUIRED" in result.findings
    assert "FINAL_LORENA_VIDEO_LESSON_REQUIRED" in result.findings


def test_lorena_closing_allows_ten_seconds_without_changing_story_narrator_segments():
    closing = {
        "id": "S002",
        "kind": "family_reflection",
        "narration": "Hoje aprendemos que Deus cumpre suas promessas.",
        "source_refs": [],
        **closing_fields(10),
    }
    packet = {
        "audience": {"min_age": 6, "max_age": 10},
        "closing_duration_s": 10,
        "recurring_narrator": dict(LORENA),
        "segments": [
            {
                "id": "S001",
                "kind": "biblical_paraphrase",
                "narration": "Sara deu à luz Isaque, como Deus havia prometido.",
                "source_refs": ["Gênesis 21:1-7"],
            },
            closing,
        ],
        "narration": "Sara deu à luz Isaque, como Deus havia prometido.\n\nHoje aprendemos que Deus cumpre suas promessas.",
    }

    result = ScriptQAAgent().review(packet)

    assert result.approved is True
    assert "presenter" not in packet["segments"][0]
    assert packet["segments"][-1]["presenter"] == "Lorena"


def test_binding_lorena_at_the_pipeline_boundary_preserves_official_story_narration():
    original = {
        "closing_duration_s": 8,
        "segments": [
            {"id": "S001", "kind": "biblical_paraphrase", "narration": "Texto oficial da história.", "source_refs": ["Gênesis 21:1"]},
            {"id": "S002", "kind": "family_reflection", "narration": "Lição final.", "source_refs": []},
        ],
        "narration": "Texto oficial da história.\n\nLição final.",
    }

    bound = bind_lorena_closing(original)

    assert bound["narration"] == original["narration"]
    assert bound["segments"][0] == original["segments"][0]
    assert bound["segments"][-1]["presenter"] == "Lorena"
    assert bound["segments"][-1]["duration_s"] == 8
    assert "presenter" not in original["segments"][-1]


def test_episode_plan_accepts_a_lorena_closing_video_up_to_ten_seconds():
    ten_seconds = plan(Config(), recommended_duration_seconds=180, closing_seconds=10)
    assert ten_seconds["delivery"]["closing_seconds"] == 10

    with pytest.raises(ValueError, match="3 to 10"):
        plan(Config(), recommended_duration_seconds=180, closing_seconds=11)
