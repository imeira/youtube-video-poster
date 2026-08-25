"""Tests for the channel content roadmap (playlists + episode order)."""

from __future__ import annotations

import pytest

from src.content.roadmap import (
    EPISODES_PUBLISHED,
    ROADMAP,
    SENSITIVE_THEMES,
    ContentPlanError,
    find_episode,
    next_episode,
    series_label,
)

EXPECTED_PLAYLISTS = [
    "Aventuras do Antigo Testamento",
    "Histórias de Jesus",
    "Heróis e Heroínas da Bíblia",
    "Milagres da Bíblia",
    "Lições de Fé e Coragem",
]


class TestRoadmapStructure:
    def test_all_five_playlists_exist_in_order(self):
        assert list(ROADMAP.keys()) == EXPECTED_PLAYLISTS

    def test_episode_numbers_are_contiguous_per_playlist(self):
        for playlist in ROADMAP.values():
            numbers = [ep.number for ep in playlist.episodes]
            assert numbers == list(range(1, len(numbers) + 1)), playlist.name

    def test_total_episode_count_matches_spec(self):
        counts = {name: len(p.episodes) for name, p in ROADMAP.items()}
        assert counts == {
            "Aventuras do Antigo Testamento": 57,
            "Histórias de Jesus": 55,
            "Heróis e Heroínas da Bíblia": 63,
            "Milagres da Bíblia": 58,
            "Lições de Fé e Coragem": 80,
        }


class TestNextEpisode:
    def test_ep2_is_adao_e_eva_after_one_published(self):
        playlist, episode = next_episode(published=1)
        assert playlist.name == "Aventuras do Antigo Testamento"
        assert episode.number == 2
        assert episode.theme == "Adão e Eva no Jardim do Éden"
        assert episode.passage == "Gênesis 2–3"

    def test_first_ever_episode_is_criacao(self):
        _, episode = next_episode(published=0)
        assert episode.theme == "A criação do mundo"

    def test_default_published_count_reflects_channel_state(self):
        assert EPISODES_PUBLISHED == 1

    def test_exhausted_roadmap_raises(self):
        with pytest.raises(ContentPlanError):
            next_episode(published=sum(len(p.episodes) for p in ROADMAP.values()))


class TestFindEpisode:
    def test_finds_jose_series_start(self):
        _playlist, episode = find_episode("Aventuras do Antigo Testamento", 13)
        assert episode.theme == "José e sua túnica especial"

    def test_unknown_playlist_raises(self):
        with pytest.raises(ContentPlanError):
            find_episode("Playlist Inexistente", 1)


class TestSeries:
    def test_jose_series_detected(self):
        label = series_label("Aventuras do Antigo Testamento", 15)
        assert label is not None
        assert "José" in label

    def test_moses_series_spans_bebe_ate_mar_vermelho(self):
        assert series_label("Aventuras do Antigo Testamento", 18) is not None
        assert series_label("Aventuras do Antigo Testamento", 22) is not None
        assert series_label("Aventuras do Antigo Testamento", 26) is None

    def test_daniel_series_detected(self):
        assert series_label("Aventuras do Antigo Testamento", 53) is not None

    def test_non_series_episode_has_no_label(self):
        assert series_label("Aventuras do Antigo Testamento", 39) is None


class TestSensitiveThemes:
    def test_edem_story_marked_sensitive(self):
        _, ep2 = find_episode("Aventuras do Antigo Testamento", 2)
        assert ep2.theme in SENSITIVE_THEMES

    def test_cain_abel_marked_sensitive(self):
        _, ep3 = find_episode("Aventuras do Antigo Testamento", 3)
        assert ep3.theme in SENSITIVE_THEMES


class TestPrompt:
    def test_prompt_contains_playlist_and_theme_fields(self):
        playlist, episode = find_episode("Aventuras do Antigo Testamento", 2)
        prompt = episode.prompt(playlist.name)
        assert "Playlist: Aventuras do Antigo Testamento" in prompt
        assert "Tema: Adão e Eva no Jardim do Éden — Gênesis 2–3" in prompt
