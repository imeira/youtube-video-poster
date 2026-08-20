"""Style Guide — consistent art direction for episode visuals (§56-58).

Defines the canonical visual style for the series:
  - Base prompt fragments (style, mood, quality)
  - Negative prompts (what to avoid)
  - Color palettes
  - Technical settings

§56: Style consistency across episodes
§57: Children-safe, family-friendly visuals (ages 6-10)
§58: Biblical accuracy balanced with appeal
"""

# Base style for all images (prepended to every prompt)
BASE_STYLE = (
    "children's Bible storybook illustration, 2D animated cartoon style, "
    "soft rounded shapes, vibrant warm colors, gentle friendly characters, "
    "hand-drawn digital art, family-friendly, wholesome, "
    "inspired by VeggieTales and Superbook animation style, "
    "clean composition, age 6-10 appropriate"
)

# Quality/technical modifiers (appended after scene description)
QUALITY_BOOST = (
    "high quality, detailed, beautiful lighting, cinematic composition, "
    "professional children's animation, vibrant color palette"
)

# What to avoid (negative prompt for SD1.5)
NEGATIVE_PROMPT = (
    "photorealistic, 3D render, realistic human, scary, violent, dark, "
    "horror, adult themes, weapon violence, blood, gore, "
    "low quality, blurry, distorted, deformed faces, extra limbs, "
    "watermark, text, signature, ugly, creepy, unwanted humans, random people, human faces before human creation, "
    "clothing on Adam, clothing on Eve, tunic on Adam, dress on Eve, underwear, sexualized nudity, anatomical detail"
)

# Color palettes for different moods
COLOR_PALETTES = {
    "joyful": "bright warm yellows, sky blues, soft greens, cheerful pastels",
    "peaceful": "soft blues, gentle greens, warm beige, calming earth tones",
    "dramatic": "deep blues, golden sunlight, rich purples, contrasting shadows",
    "miraculous": "radiant golds, heavenly whites, ethereal soft glows, divine light",
    "sad": "muted blues, soft greys, gentle earth tones, subdued lighting",
    "adventurous": "vibrant greens, ocean blues, warm earth browns, dynamic skies",
}

# Lighting presets
LIGHTING = {
    "divine": "heavenly golden light rays, soft ethereal glow, warm divine radiance",
    "day": "bright natural sunlight, clear blue sky, cheerful daylight",
    "sunset": "warm golden hour, soft orange and pink sky, gentle shadows",
    "night": "gentle moonlight, soft starry sky, peaceful nighttime glow",
    "indoor": "warm soft interior lighting, cozy ambient light",
}

# Camera/composition hints
CAMERA_STYLES = {
    "wide": "wide establishing shot, full scene view, environmental context",
    "medium": "medium shot, character focus with environment visible",
    "close": "gentle close-up, character emotion focus, warm intimate framing",
    "dramatic": "dynamic angle, cinematic framing, engaging composition",
}


def build_full_prompt(
    scene_description: str,
    characters: list[str] | None = None,
    location: str = "",
    mood: str = "peaceful",
    lighting: str = "day",
    camera: str = "medium",
    character_descriptions: str = "",
) -> dict:
    """Build a complete SD1.5 prompt with style guide applied.
    
    CRITICAL: Order matters — CLIP truncates at 77 tokens. Put the MOST
    important content FIRST (scene + characters), style/quality LAST
    so if truncated, we lose fluff not the actual scene description.
    
    Returns:
        {
            "prompt": full positive prompt,
            "negative_prompt": negative prompt,
        }
    """
    # Order: scene description → characters → location → style (truncated last)
    parts = [scene_description]
    
    # Characters (high priority — after scene)
    if character_descriptions:
        parts.append(character_descriptions)
    
    # Location (translate common Portuguese locations to English for SD1.5)
    if location:
        loc_map = {
            "paisagem natural": "beautiful natural landscape",
            "mar": "ocean",
            "navio": "ship",
            "cova dos leões": "lions den",
            "arca": "ark",
            "céu": "sky",
            "terra": "land",
            "praia": "beach",
            "Nínive": "ancient city",
            "campo verde": "green field",
        }
        loc_en = loc_map.get(location, location)
        parts.append(f"in {loc_en}")
    
    # Compact style (keep short to avoid token waste)
    parts.append("children's Bible cartoon, 2D animation, soft rounded shapes, vibrant colors, family-friendly")
    
    # Mood (single compact phrase)
    if mood in COLOR_PALETTES:
        parts.append(COLOR_PALETTES[mood])
    
    # Lighting (compact)
    if lighting in LIGHTING:
        parts.append(LIGHTING[lighting])
    
    prompt = ", ".join(parts)
    
    return {
        "prompt": prompt,
        "negative_prompt": NEGATIVE_PROMPT,
    }
