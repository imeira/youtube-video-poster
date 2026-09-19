"""Canonical recurring narrator identity and closing contract."""

from __future__ import annotations

LORENA = {
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


def closing_fields(duration_s: int = 4) -> dict[str, object]:
    if not 3 <= duration_s <= 10:
        raise ValueError("Lorena closing duration must be 3 to 10 seconds")
    return {
        "presenter": LORENA["name"],
        "visual_mode": "generated_video",
        "duration_s": duration_s,
        "lesson_role": "episode_central_message",
    }


def bind_lorena_closing(packet: dict) -> dict:
    """Bind Lorena only to the final lesson without rewriting narration."""
    segments = [dict(segment) for segment in packet.get("segments", [])]
    if not segments or segments[-1].get("kind") != "family_reflection":
        raise ValueError("final family reflection is required for Lorena")
    duration = packet.get("closing_duration_s", 4)
    segments[-1].update(closing_fields(duration))
    return {
        **packet,
        "segments": segments,
        "recurring_narrator": dict(LORENA),
    }
