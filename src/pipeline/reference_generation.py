"""Reference-first scene generation helpers for the Creation episode."""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def align_plan_to_timestamps(plan_scenes: list[dict], timestamps: list[dict]) -> list[dict]:
    """Greedily align semantic plan scenes to real TTS sentence boundaries.

    A plan scene may contain two or more TTS sentences. Boundaries are consumed
    until the combined spoken text matches the plan narration; no uniform time
    slicing or proportional assignment is used.
    """
    result: list[dict] = []
    cursor = 0
    for index, plan in enumerate(plan_scenes):
        if cursor >= len(timestamps):
            raise ValueError(f"No timestamp left for plan scene {index + 1}")
        target = _normalize(str(plan.get("narration_pt", "")))
        consumed: list[dict] = []
        best_ratio = 0.0
        while cursor < len(timestamps):
            consumed.append(timestamps[cursor])
            cursor += 1
            combined = _normalize(" ".join(str(x.get("text", "")) for x in consumed))
            ratio = SequenceMatcher(None, target, combined).ratio()
            best_ratio = max(best_ratio, ratio)
            if ratio >= 0.86 or len(combined.split()) >= max(1, len(target.split())):
                break
        if best_ratio < 0.65:
            raise ValueError(f"Low narration/timestamp match for scene {index + 1}: {best_ratio:.2f}")
        start = float(consumed[0]["start"])
        end = float(consumed[-1]["end"])
        scene = dict(plan)
        scene.update({
            "scene_id": f"SC{index + 1:03d}",
            "narration": plan.get("narration_pt", ""),
            "start": start,
            "end": end,
            "duration": round(end - start, 3),
            "tts_segments": len(consumed),
        })
        result.append(scene)
    if cursor != len(timestamps):
        raise ValueError(f"Unconsumed TTS timestamps: {len(timestamps) - cursor}")
    return result


def select_reference(narration: str, characters: list[str], references: dict[str, str]) -> str | None:
    """Select immutable canonical actor reference for a scene."""
    haystack = " ".join([narration, *characters]).lower()
    has_adam = "adão" in haystack or "adao" in haystack or "adam" in haystack
    has_eve = "eva" in haystack or "eve" in haystack
    if has_adam and has_eve:
        return references.get("adam_eve")
    if has_adam:
        return references.get("adam")
    if has_eve:
        return references.get("eve")
    return None
