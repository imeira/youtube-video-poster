"""Fail-closed schema checks for the source-of-truth episode packet."""

from __future__ import annotations

from typing import Any


class EpisodePacketError(ValueError):
    """Raised when an episode packet cannot drive safe production."""


_REQUIRED_TOP_LEVEL = frozenset({
    "metadata", "sources", "accuracy_report", "safety_report", "characters", "visual_bible",
    "chapters", "narration_segments", "scene_plan", "thumbnail_concepts", "title_candidates",
    "description", "tags", "production_notes", "estimated_word_count", "estimated_duration_seconds",
})
_REQUIRED_SEGMENT = frozenset({
    "id", "chapter", "text", "emotion", "estimated_duration_s", "reference", "characters",
    "location", "editorial_risk", "visual_prompt", "transition", "suggested_sfx",
})
_REQUIRED_VISUAL_BIBLE = frozenset({
    "id", "style", "palette", "lighting", "proportions", "scenery", "camera", "forbidden_changes",
})
_REQUIRED_SCENE = frozenset({
    "id", "narration_segment_id", "visual_bible_id", "prompt_language", "prompt", "negative_prompt",
    "reference_images", "camera", "lighting",
})


def validate_episode_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Validate structural authority before any TTS, image, or provider dispatch."""
    missing = sorted(_REQUIRED_TOP_LEVEL - packet.keys())
    if missing:
        raise EpisodePacketError(f"Missing required packet sections: {', '.join(missing)}")
    audience = packet["metadata"].get("audience", {}) if isinstance(packet["metadata"], dict) else {}
    if packet["metadata"].get("language") != "pt-BR" or audience != {"min_age": 6, "max_age": 10}:
        raise EpisodePacketError("metadata must declare pt-BR audience ages 6–10")
    if not isinstance(packet["sources"], list) or not packet["sources"]:
        raise EpisodePacketError("at least one biblical source is required")
    visual_bible = packet["visual_bible"]
    if not isinstance(visual_bible, dict):
        raise EpisodePacketError("visual_bible must be an object")
    missing_bible = sorted(_REQUIRED_VISUAL_BIBLE - visual_bible.keys())
    if missing_bible:
        raise EpisodePacketError(f"visual_bible missing fields: {', '.join(missing_bible)}")
    if not isinstance(visual_bible["id"], str) or ".v" not in visual_bible["id"]:
        raise EpisodePacketError("visual_bible requires a versioned persistent id")
    for field in ("palette", "forbidden_changes"):
        if not isinstance(visual_bible[field], list) or not visual_bible[field]:
            raise EpisodePacketError(f"visual_bible {field} must be nonempty")
    segment_ids = set()
    for segment in packet["narration_segments"]:
        missing_segment = sorted(_REQUIRED_SEGMENT - segment.keys())
        if missing_segment:
            raise EpisodePacketError(f"segment missing fields: {', '.join(missing_segment)}")
        if not isinstance(segment["reference"], dict) or not segment["reference"].get("book"):
            raise EpisodePacketError("every narration segment requires a biblical reference")
        if not isinstance(segment["text"], str) or not segment["text"].strip():
            raise EpisodePacketError("every narration segment requires text")
        if not isinstance(segment["id"], str) or not segment["id"].strip() or segment["id"] in segment_ids:
            raise EpisodePacketError("narration segments require unique ids")
        segment_ids.add(segment["id"])
    scenes = packet["scene_plan"]
    if not isinstance(scenes, list) or not scenes:
        raise EpisodePacketError("scene_plan requires image-to-image scenes")
    mapped_segments = set()
    for scene in scenes:
        if not isinstance(scene, dict):
            raise EpisodePacketError("scene_plan entries must be objects")
        missing_scene = sorted(_REQUIRED_SCENE - scene.keys())
        if missing_scene:
            raise EpisodePacketError(f"scene missing fields: {', '.join(missing_scene)}")
        if scene["narration_segment_id"] not in segment_ids:
            raise EpisodePacketError("scene must map to an active narration segment")
        if scene["visual_bible_id"] != visual_bible["id"]:
            raise EpisodePacketError("scene must bind the active visual_bible")
        if scene["prompt_language"] not in {"en", "pt-BR"}:
            raise EpisodePacketError("scene prompt_language must be en or pt-BR")
        if not isinstance(scene["reference_images"], list) or not scene["reference_images"]:
            raise EpisodePacketError("scene reference_images must bind image-to-image inputs")
        if not all(isinstance(path, str) and path.strip() for path in scene["reference_images"]):
            raise EpisodePacketError("scene reference_images must be nonempty paths")
        mapped_segments.add(scene["narration_segment_id"])
    if mapped_segments != segment_ids:
        raise EpisodePacketError("every narration segment requires a semantic image scene")
    duration = packet["estimated_duration_seconds"]
    if type(duration) not in {int, float} or not 180 <= duration <= 900:
        raise EpisodePacketError("estimated duration must be between 180 and 900 seconds")
    return packet
