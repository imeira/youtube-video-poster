"""Validation for versioned canonical character identity cards."""

from __future__ import annotations

from typing import Any


class CharacterContractError(ValueError):
    """Raised when a character cannot be used as a canonical visual reference."""


_REQUIRED = frozenset({
    "id", "name", "life_stage", "face_shape", "skin_tone", "eyes", "hair", "relative_height",
    "proportions", "clothing", "colors", "shoes", "accessories", "expressions", "visual_personality",
    "reference_images",
})


def validate_character_card(card: dict[str, Any]) -> dict[str, Any]:
    """Reject text-only identities and incomplete reusable character definitions."""
    missing = sorted(_REQUIRED - card.keys())
    if missing:
        raise CharacterContractError(f"missing identity fields: {', '.join(missing)}")
    if not isinstance(card["id"], str) or ".v" not in card["id"]:
        raise CharacterContractError("id must be a versioned persistent identifier")
    if not isinstance(card["reference_images"], list) or not card["reference_images"] or not all(
        isinstance(item, str) and item.strip() for item in card["reference_images"]
    ):
        raise CharacterContractError("reference_images must contain canonical image references")
    if not isinstance(card["expressions"], list) or not card["expressions"]:
        raise CharacterContractError("expressions must be a nonempty list")
    return card
