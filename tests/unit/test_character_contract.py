"""Canonical character cards must be complete and versioned."""

from __future__ import annotations

import pytest

from src.pipeline.character_contract import (
    CharacterContractError,
    validate_character_card,
)


def card():
    return {
        "id": "abraao.adult.v1", "name": "Abraão", "life_stage": "adult",
        "face_shape": "oval", "skin_tone": "warm", "eyes": "brown", "hair": "gray", "relative_height": "tall",
        "proportions": "gentle", "clothing": "cream tunic", "colors": ["cream"], "shoes": "sandals",
        "accessories": [], "expressions": ["calm"], "visual_personality": "wise", "reference_images": ["faces/abraao.png"],
    }


def test_character_card_requires_identity_fields_and_reference_images():
    assert validate_character_card(card())["id"] == "abraao.adult.v1"
    invalid = card(); invalid["reference_images"] = []
    with pytest.raises(CharacterContractError, match="reference_images"):
        validate_character_card(invalid)
