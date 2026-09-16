"""Fail-closed thumbnail copy contracts for child-safe episodes."""

from __future__ import annotations

import pytest
from PIL import Image

from src.agents.thumbnail import ThumbnailAgent, ThumbnailContract, ThumbnailContractError


EP8 = ThumbnailContract(
    headline="A PROMESSA DE UM FILHO",
    title="PARA ABRAÃO E SARA",
    book_subtitle="— Gênesis 15–18",
)


def test_ep8_contract_preserves_literal_book_subtitle_unicode():
    assert EP8.book_subtitle == "— Gênesis 15–18"
    assert EP8.layers == (
        "A PROMESSA DE UM FILHO",
        "PARA ABRAÃO E SARA",
        "— Gênesis 15–18",
    )


@pytest.mark.parametrize(
    "book_subtitle",
    ["- Gênesis 15–18", "Gênesis 15–18", "— GÊNESIS 15–18", "— Gênesis 15-18"],
)
def test_ep8_contract_rejects_book_subtitle_normalization(book_subtitle: str):
    with pytest.raises(ThumbnailContractError):
        ThumbnailContract(
            headline="A PROMESSA DE UM FILHO",
            title="PARA ABRAÃO E SARA",
            book_subtitle=book_subtitle,
            required_book_subtitle="— Gênesis 15–18",
        )


@pytest.mark.parametrize("copy", ["SEGREDO PROIBIDO", "CHOQUE!", "SÓ HOJE!"])
def test_contract_rejects_child_unsafe_clickbait(copy: str):
    with pytest.raises(ThumbnailContractError):
        ThumbnailContract(headline=copy, title="PARA ABRAÃO E SARA", book_subtitle="— Gênesis 15–18")


@pytest.mark.asyncio
async def test_agent_uses_contract_and_preserves_literal_book_subtitle(tmp_path):
    hero = tmp_path / "hero.png"
    Image.new("RGB", (1280, 720), "navy").save(hero)

    result = await ThumbnailAgent().run(
        episode_id="EP8",
        images=[{"scene_id": "SC001", "image_path": str(hero)}],
        scenes=[{"scene_id": "SC001", "importance": "CRITICAL", "characters": ["abraão", "sara"]}],
        headline="wrong",
        subtitle="wrong",
        book_subtitle="wrong",
        copy_contract=EP8,
        thumbnails_dir=str(tmp_path / "out"),
    )

    assert result.success
    assert result.data["headline"] == EP8.headline
    assert result.data["subtitle"] == EP8.title
    assert result.data["book_subtitle"] == "— Gênesis 15–18"
