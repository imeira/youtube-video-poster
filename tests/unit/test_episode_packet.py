"""A structured episode packet is mandatory before media work."""

from __future__ import annotations

import pytest

from src.pipeline.episode_packet import EpisodePacketError, validate_episode_packet


def complete_packet():
    return {
        "metadata": {"language": "pt-BR", "audience": {"min_age": 6, "max_age": 10}},
        "sources": [{"book": "Gênesis", "chapter": 15, "verses": "1-18"}],
        "accuracy_report": {},
        "safety_report": {},
        "characters": [],
        "visual_bible": {
            "id": "channel.visual.v1",
            "style": "stylized 3D children animation",
            "palette": ["warm gold"],
            "lighting": "warm cinematic",
            "proportions": "soft rounded",
            "scenery": "historically respectful",
            "camera": "gentle cinematic",
            "forbidden_changes": ["watermarks"],
        },
        "chapters": [{"id": "C01"}],
        "narration_segments": [{
            "id": "N01", "chapter": "C01", "text": "Uma promessa.", "emotion": "esperança",
            "estimated_duration_s": 3.0, "reference": {"book": "Gênesis"}, "characters": [],
            "location": "tenda", "editorial_risk": "LOW", "visual_prompt": "warm scene",
            "transition": "cut", "suggested_sfx": [],
        }],
        "scene_plan": [{
            "id": "S01", "narration_segment_id": "N01", "visual_bible_id": "channel.visual.v1",
            "prompt_language": "en", "prompt": "Warm tent at sunset", "negative_prompt": "watermark, text",
            "reference_images": ["references/tent.png"], "camera": "gentle push in", "lighting": "warm sunset",
        }],
        "thumbnail_concepts": [], "title_candidates": [], "description": "Resumo", "tags": [],
        "production_notes": [], "estimated_word_count": 2, "estimated_duration_seconds": 184,
    }


def test_packet_requires_all_contract_sections_and_source_bound_segments():
    assert validate_episode_packet(complete_packet())["metadata"]["language"] == "pt-BR"
    packet = complete_packet(); del packet["safety_report"]
    with pytest.raises(EpisodePacketError, match="safety_report"):
        validate_episode_packet(packet)
    packet = complete_packet(); packet["narration_segments"][0]["reference"] = {}
    with pytest.raises(EpisodePacketError, match="reference"):
        validate_episode_packet(packet)


def test_packet_requires_versioned_visual_bible_and_image_to_image_scene_contract():
    packet = complete_packet()
    packet["scene_plan"][0]["reference_images"] = []
    with pytest.raises(EpisodePacketError, match="reference_images"):
        validate_episode_packet(packet)
    packet = complete_packet()
    packet["scene_plan"][0]["visual_bible_id"] = "other.visual.v1"
    with pytest.raises(EpisodePacketError, match="visual_bible"):
        validate_episode_packet(packet)
