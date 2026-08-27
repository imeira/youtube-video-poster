"""Episode-specific thumbnail copy used by the Director."""

import pytest

from src.agents.director import DirectorAgent
from src.content.roadmap import ROADMAP


def test_ep2_thumbnail_uses_required_title_subtitle_and_book():
    title, subtitle, book_subtitle = DirectorAgent.thumbnail_copy(
        "Adão e Eva no Jardim do Éden — Gênesis 2–3"
    )

    assert title == "ADÃO E EVA"
    assert subtitle == "O JARDIM DO ÉDEN"
    assert book_subtitle == "GÊNESIS 2–3"


def test_noah_thumbnail_preserves_biblical_book_reference():
    title, subtitle, book_subtitle = DirectorAgent.thumbnail_copy(
        "Noé e a grande arca — Gênesis 6–9"
    )

    assert title == "NOÉ E A GRANDE ARCA"
    assert subtitle == ""
    assert book_subtitle == "GÊNESIS 6–9"


def test_thumbnail_copy_rejects_theme_without_biblical_reference():
    try:
        DirectorAgent.thumbnail_copy("Noé e a grande arca")
    except ValueError as error:
        assert str(error) == "Every thumbnail requires a biblical book reference"
    else:
        raise AssertionError("theme without biblical reference was accepted")


def test_thumbnail_copy_rejects_theme_without_headline():
    with pytest.raises(ValueError, match="Every thumbnail requires a headline"):
        DirectorAgent.thumbnail_copy(" — Gênesis 1")


def test_thumbnail_copy_rejects_non_biblical_suffix():
    with pytest.raises(ValueError, match="Every thumbnail requires a valid biblical reference"):
        DirectorAgent.thumbnail_copy("Noé e a grande arca — texto qualquer 1")


@pytest.mark.parametrize(
    "passage",
    [
        "Gênesis 1eeee",
        "Gênesis 1 e a",
        "Gênesis 1...",
        "Gênesis 1---",
        "Gênesis 1:",
    ],
)
def test_thumbnail_copy_rejects_malformed_biblical_locator(passage):
    with pytest.raises(ValueError, match="Every thumbnail requires a valid biblical reference"):
        DirectorAgent.thumbnail_copy(f"Tema válido — {passage}")


@pytest.mark.parametrize(
    "theme",
    [
        "Noé e a grande arca — Gênesis 6:5–9:17",
        "Davi e Golias — 1 Samuel 17",
        "Pedro aprende sobre perdão — Lucas 22; João 21",
        "Jesus conversa com Nicodemos — João 3:16, 18",
        "Uma jornada pelos salmos — Salmos 1 a 3",
        "Barnabé encoraja a igreja — Atos 4; 9; 11",
    ],
)
def test_thumbnail_copy_accepts_supported_biblical_reference_formats(theme):
    _headline, _subtitle, book_subtitle = DirectorAgent.thumbnail_copy(theme)

    assert book_subtitle == theme.partition(" — ")[2].upper()


def test_thumbnail_copy_accepts_every_non_empty_roadmap_passage():
    invalid = []
    for playlist in ROADMAP.values():
        for episode in playlist.episodes:
            if not episode.passage:
                continue
            try:
                DirectorAgent.thumbnail_copy(f"{episode.theme} — {episode.passage}")
            except ValueError as error:
                invalid.append((playlist.name, episode.number, episode.passage, str(error)))

    assert invalid == []


@pytest.mark.asyncio
async def test_start_episode_rejects_missing_reference_before_pipeline_setup():
    director = DirectorAgent.__new__(DirectorAgent)

    with pytest.raises(ValueError, match="Every thumbnail requires a biblical book reference"):
        await director.start_episode("Noé e a grande arca")
