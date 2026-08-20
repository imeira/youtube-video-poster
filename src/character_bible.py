"""Character Bible — visual descriptions for biblical characters (§59-60).

Canonical character descriptions for consistent image generation across episodes.
Each character has:
  - visual_description: SD1.5 prompt fragment (appearance, clothing, age)
  - personality_tags: emotion/mood hints
  - symbolic_elements: objects/settings associated with the character

§59: Character consistency — critical for multi-episode series.
§60: Character library — hardcoded initial set, expandable.
"""

from __future__ import annotations

# Main characters for children's Bible stories (ages 6-10)
CHARACTER_BIBLE = {
    "deus": {
        "visual_description": (
            "divine radiant light, golden warm glow, soft ethereal presence, "
            "gentle heavenly rays, majestic and loving, no human face shown, "
            "represented by beautiful glowing light and celestial atmosphere"
        ),
        "personality_tags": ["loving", "powerful", "wise", "gentle"],
        "symbolic_elements": ["clouds", "heavenly light", "stars", "creation"],
        "age_group": "eternal",
        "primary_colors": ["gold", "white", "sky blue"],
    },
    "jesus": {
        "visual_description": (
            "kind Middle Eastern man with warm brown eyes, gentle smile, "
            "long brown hair, simple white and beige robes, "
            "friendly and approachable, children's Bible character style"
        ),
        "personality_tags": ["loving", "gentle", "wise", "compassionate"],
        "symbolic_elements": ["sheep", "children", "light", "cross"],
        "age_group": "adult",
        "primary_colors": ["white", "beige", "soft brown"],
    },
    "adão": {
        "visual_description": (
            "friendly young adult man with short dark brown hair, kind face, "
            "bare, unclothed, no garments, no clothing, barefoot, gentle expression, "
            "non-sexual child-safe framing with foliage naturally covering intimate areas, "
            "children's cartoon style, warm skin tone"
        ),
        "personality_tags": ["curious", "gentle", "innocent"],
        "symbolic_elements": ["garden", "animals", "tree", "fruit"],
        "age_group": "young_adult",
        "primary_colors": ["green", "brown", "tan"],
    },
    "eva": {
        "visual_description": (
            "friendly young adult woman with long dark hair, kind gentle face, "
            "bare, unclothed, no garments, no clothing, barefoot, warm smile, "
            "non-sexual child-safe framing with foliage naturally covering intimate areas, "
            "children's cartoon style, warm skin tone"
        ),
        "personality_tags": ["curious", "gentle", "caring"],
        "symbolic_elements": ["garden", "flowers", "animals", "fruit"],
        "age_group": "young_adult",
        "primary_colors": ["green", "soft pink", "tan"],
    },
    "noé": {
        "visual_description": (
            "elderly kind man with long white beard, wise gentle eyes, "
            "simple brown robe, wooden staff, loving grandfather appearance, "
            "children's Bible cartoon style"
        ),
        "personality_tags": ["wise", "faithful", "gentle", "patient"],
        "symbolic_elements": ["ark", "animals", "rainbow", "dove"],
        "age_group": "elderly",
        "primary_colors": ["brown", "grey", "blue"],
    },
    "moisés": {
        "visual_description": (
            "middle-aged man with kind face, short dark beard, "
            "simple white and brown robes, wooden staff, "
            "gentle leader appearance, children's cartoon style"
        ),
        "personality_tags": ["brave", "wise", "humble", "faithful"],
        "symbolic_elements": ["staff", "tablets", "mountain", "burning bush"],
        "age_group": "middle_aged",
        "primary_colors": ["brown", "white", "desert tan"],
    },
    "davi": {
        "visual_description": (
            "young boy or teen with friendly face, short curly dark hair, "
            "simple shepherd tunic, slingshot, kind brave expression, "
            "children's cartoon style"
        ),
        "personality_tags": ["brave", "faithful", "young", "musical"],
        "symbolic_elements": ["slingshot", "harp", "sheep", "stone"],
        "age_group": "child_teen",
        "primary_colors": ["brown", "tan", "green"],
    },
    "maria": {
        "visual_description": (
            "young kind woman with gentle face, long dark hair, "
            "simple blue and white robe, loving motherly expression, "
            "children's Bible cartoon style"
        ),
        "personality_tags": ["gentle", "loving", "faithful", "humble"],
        "symbolic_elements": ["baby Jesus", "star", "lily"],
        "age_group": "young_adult",
        "primary_colors": ["blue", "white", "soft pink"],
    },
    "jonas": {
        "visual_description": (
            "middle-aged man with worried but kind face, short beard, "
            "simple sailor tunic, weathered clothing, "
            "children's cartoon style"
        ),
        "personality_tags": ["reluctant", "faithful", "human", "redeemed"],
        "symbolic_elements": ["whale", "boat", "ocean", "gourd plant"],
        "age_group": "middle_aged",
        "primary_colors": ["blue", "tan", "grey"],
    },
    "daniel": {
        "visual_description": (
            "young man with kind wise face, simple robes, "
            "calm brave expression, children's Bible cartoon style"
        ),
        "personality_tags": ["faithful", "brave", "wise", "gentle"],
        "symbolic_elements": ["lions", "den", "prayer"],
        "age_group": "young_adult",
        "primary_colors": ["purple", "gold", "brown"],
    },
}

# Generic character types (crowds, angels, animals)
GENERIC_TYPES = {
    "anjo": {
        "visual_description": (
            "gentle glowing figure with soft white robes, beautiful wings, "
            "warm kind face, heavenly glow, children's cartoon style"
        ),
        "personality_tags": ["gentle", "heavenly", "messenger"],
        "symbolic_elements": ["wings", "light", "clouds"],
        "primary_colors": ["white", "gold", "soft blue"],
    },
    "criança": {
        "visual_description": (
            "happy child with bright friendly face, simple clothes, "
            "joyful expression, children's cartoon style"
        ),
        "personality_tags": ["joyful", "innocent", "playful"],
        "symbolic_elements": [],
        "primary_colors": ["bright colors", "earth tones"],
    },
    "animais": {
        "visual_description": (
            "friendly cartoon animals with big eyes, soft rounded shapes, "
            "non-threatening, cute children's book style"
        ),
        "personality_tags": ["gentle", "friendly", "cute"],
        "symbolic_elements": [],
        "primary_colors": ["natural animal colors", "soft pastels"],
    },
}


def get_character_description(name: str) -> dict | None:
    """Get character description by name (case-insensitive)."""
    name_lower = name.lower().strip()
    return CHARACTER_BIBLE.get(name_lower) or GENERIC_TYPES.get(name_lower)


def build_character_prompt(characters: list[str]) -> str:
    """Build a prompt fragment from a list of character names."""
    if not characters:
        return ""
    
    parts = []
    for char_name in characters:
        char = get_character_description(char_name)
        if char:
            parts.append(char["visual_description"])
    
    return ", ".join(parts) if parts else ""


def get_character_colors(characters: list[str]) -> list[str]:
    """Get primary colors for a list of characters."""
    colors = []
    for char_name in characters:
        char = get_character_description(char_name)
        if char:
            colors.extend(char.get("primary_colors", []))
    return list(set(colors))  # dedupe
