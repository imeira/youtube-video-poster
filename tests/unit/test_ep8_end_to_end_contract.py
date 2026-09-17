"""Regression contracts for EP8's safe route to human delivery gates."""

from __future__ import annotations

import json

import pytest

from src.agents.research import ResearchAgent
from src.agents.script import ScriptAgent
from src.agents.script_qa import ScriptQAAgent
from src.agents.thumbnail import ThumbnailContract, ThumbnailContractError
from src.state.machine import EpisodeStateStore

EP8_THEME = "A promessa de um filho para Abraão e Sara — Gênesis 15–18"
EP8_TITLE = "A promessa de um filho para Abraão e Sara"
EP8_SUBTITLE = "— Gênesis 15–18"


@pytest.mark.asyncio
async def test_ep8_theme_resolves_to_precise_child_safe_scope(tmp_path):
    result = await ResearchAgent().run("EP8", EP8_THEME, str(tmp_path))

    assert result.success
    assert result.data["references"] == [
        {"book": "Gênesis", "chapter": 15, "verses": "1-6"},
        {"book": "Gênesis", "chapter": 17, "verses": "1-9, 15-21"},
        {"book": "Gênesis", "chapter": 18, "verses": "1-15"},
    ]
    assert result.data["source_authority"]["language"] == "pt-BR"
    assert result.data["accuracy_report"]["omissions"]
    assert "Isaque já nasceu" not in " ".join(result.data["narrative_classification"]["BIBLICAL_FACT"])
    assert (tmp_path / "sources.json").is_file()


@pytest.mark.asyncio
async def test_script_packet_is_the_exact_narration_sent_to_tts(tmp_path):
    research = await ResearchAgent().run("EP8", EP8_THEME, str(tmp_path / "research"))
    result = await ScriptAgent().run(
        "EP8", research.data, target_duration_s=390, script_dir=str(tmp_path / "script")
    )

    packet = json.loads((tmp_path / "script" / "script.json").read_text(encoding="utf-8"))
    assert result.data["narration"] == "\n\n".join(segment["narration"] for segment in packet["segments"])
    assert ScriptQAAgent().review(packet).approved


@pytest.mark.parametrize(
    "unsafe",
    [
        "Você irá para o inferno se não obedecer.",
        "Comente seu nome para participar.",
        "Peça aos seus pais para comprarem agora.",
        "Este segredo proibido vai chocar você.",
    ],
)
def test_script_qa_reviews_real_narration_and_rejects_child_unsafe_text(unsafe):
    packet = {
        "audience": {"min_age": 6, "max_age": 10},
        "closing_duration_s": 4,
        "narration": unsafe,
        "segments": [
            {
                "id": "S001",
                "kind": "biblical_paraphrase",
                "narration": unsafe,
                "source_refs": ["Gênesis 15:1-6"],
            }
        ],
    }
    assert not ScriptQAAgent().review(packet).approved


def test_state_round_trip_preserves_unknown_checkpoint_revisions():
    original = {
        "episode_id": "EP8",
        "current_state": "GENERATING_IMAGES",
        "checkpoint": {
            "revision_v2": {"required_frame_count": 39, "approved_frame_count": 39},
            "approved_assets": ["R001"],
        },
    }
    restored = EpisodeStateStore.from_dict(original)
    assert restored.to_dict()["checkpoint"] == original["checkpoint"]


def test_ep8_thumbnail_contract_requires_exact_title_and_subtitle():
    contract = ThumbnailContract(
        headline="Uma promessa que trouxe esperança",
        title=EP8_TITLE,
        book_subtitle=EP8_SUBTITLE,
        required_book_subtitle=EP8_SUBTITLE,
    )
    assert contract.title == EP8_TITLE
    with pytest.raises(ThumbnailContractError):
        ThumbnailContract(
            headline="URGENTE!", title=EP8_TITLE, book_subtitle=EP8_SUBTITLE,
            required_book_subtitle=EP8_SUBTITLE,
        )


@pytest.mark.asyncio
async def test_start_episode_refuses_to_overwrite_existing_request(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    root = tmp_path / "EP8"
    root.mkdir()
    request = root / "request.json"
    request.write_text('{"theme":"original"}', encoding="utf-8")

    from src.agents.director import DirectorAgent
    with pytest.raises(FileExistsError):
        await DirectorAgent().start_episode(EP8_THEME, episode_id="EP8")
    assert request.read_text(encoding="utf-8") == '{"theme":"original"}'
