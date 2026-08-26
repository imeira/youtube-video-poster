"""Episode-specific thumbnail copy used by the Director."""

from src.agents.director import DirectorAgent


def test_ep2_thumbnail_uses_required_title_subtitle_and_book():
    title, subtitle, book_subtitle = DirectorAgent.thumbnail_copy(
        "Adão e Eva no Jardim do Éden — Gênesis 2–3"
    )

    assert title == "ADÃO E EVA"
    assert subtitle == "O JARDIM DO ÉDEN"
    assert book_subtitle == "GÊNESIS 2–3"
