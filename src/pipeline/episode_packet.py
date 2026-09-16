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
    for segment in packet["narration_segments"]:
        missing_segment = sorted(_REQUIRED_SEGMENT - segment.keys())
        if missing_segment:
            raise EpisodePacketError(f"segment missing fields: {', '.join(missing_segment)}")
        if not isinstance(segment["reference"], dict) or not segment["reference"].get("book"):
            raise EpisodePacketError("every narration segment requires a biblical reference")
        if not isinstance(segment["text"], str) or not segment["text"].strip():
            raise EpisodePacketError("every narration segment requires text")
    duration = packet["estimated_duration_seconds"]
    if type(duration) not in {int, float} or not 180 <= duration <= 900:
        raise EpisodePacketError("estimated duration must be between 180 and 900 seconds")
    return packet
