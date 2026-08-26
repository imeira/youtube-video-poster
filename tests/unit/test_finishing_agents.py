"""Tests for finishing agents (Captions, Thumbnail, Metadata) — Phase 8."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from src.agents.captions import CaptionsAgent
from src.agents.metadata import MetadataAgent
from src.agents.thumbnail import ThumbnailAgent

# ── Captions Agent (§31-32) ──────────────────────────────────────────────────

class TestCaptionsAgent:
    def test_wrap_preserves_word_order_after_second_line_starts(self):
        agent = CaptionsAgent()
        text = "cultivar o solo Uma névoa suave subia da terra regando o chão preparado para uma"

        wrapped = agent._wrap(text)

        assert wrapped.replace("\n", " ") == text

    @pytest.mark.asyncio
    async def test_generates_srt_vtt_transcript(self, tmp_path: Path):
        agent = CaptionsAgent()
        timestamps = [
            {"start": 0.0, "end": 4.5, "duration": 4.5, "text": "Deus criou a luz no primeiro dia."},
            {"start": 4.5, "end": 8.0, "duration": 3.5, "text": "E Deus viu que a luz era boa."},
        ]
        result = await agent.run(
            episode_id="T",
            sentence_timestamps=timestamps,
            narration="Deus criou a luz no primeiro dia. E Deus viu que a luz era boa.",
            subtitles_dir=str(tmp_path),
        )
        assert result.success
        assert (tmp_path / "transcript.txt").exists()
        assert (tmp_path / "captions.srt").exists()
        assert (tmp_path / "captions.vtt").exists()

    @pytest.mark.asyncio
    async def test_srt_format_valid(self, tmp_path: Path):
        agent = CaptionsAgent()
        timestamps = [{"start": 1.5, "end": 3.25, "duration": 1.75, "text": "Olá mundo."}]
        await agent.run(episode_id="T", sentence_timestamps=timestamps, subtitles_dir=str(tmp_path))
        srt = (tmp_path / "captions.srt").read_text(encoding="utf-8")
        assert "1\n" in srt
        assert "00:00:01,500 --> 00:00:03,250" in srt
        assert "Olá mundo." in srt

    @pytest.mark.asyncio
    async def test_vtt_has_header(self, tmp_path: Path):
        agent = CaptionsAgent()
        timestamps = [{"start": 0.0, "end": 2.0, "duration": 2.0, "text": "Teste."}]
        await agent.run(episode_id="T", sentence_timestamps=timestamps, subtitles_dir=str(tmp_path))
        vtt = (tmp_path / "captions.vtt").read_text(encoding="utf-8")
        assert vtt.startswith("WEBVTT")
        assert "00:00:00.000 --> 00:00:02.000" in vtt

    @pytest.mark.asyncio
    async def test_no_timestamps_fails(self):
        agent = CaptionsAgent()
        result = await agent.run(episode_id="T", sentence_timestamps=None, word_timestamps=None)
        assert not result.success

    @pytest.mark.asyncio
    async def test_long_sentence_split(self, tmp_path: Path):
        agent = CaptionsAgent()
        long_text = "Deus " * 40  # ~200 chars
        timestamps = [{"start": 0.0, "end": 20.0, "duration": 20.0, "text": long_text.strip()}]
        result = await agent.run(episode_id="T", sentence_timestamps=timestamps, subtitles_dir=str(tmp_path))
        assert result.success
        assert result.data["cue_count"] > 1  # split into multiple cues


# ── Thumbnail Agent (§91) ────────────────────────────────────────────────────

@pytest.fixture
def test_images(tmp_path: Path):
    """Create test scene images."""
    images = []
    for i in range(1, 4):
        img = tmp_path / f"SC{i:03d}.png"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
             "-i", "color=c=blue:s=512x512:d=1", "-frames:v", "1", str(img)],
            check=True, timeout=15,
        )
        images.append({"scene_id": f"SC{i:03d}", "image_path": str(img)})
    return images


class TestThumbnailAgent:
    @pytest.mark.asyncio
    async def test_generates_thumbnail(self, test_images, tmp_path: Path):
        agent = ThumbnailAgent()
        thumb_dir = tmp_path / "thumbnails"
        result = await agent.run(
            episode_id="T",
            images=test_images,
            scenes=[
                {"scene_id": "SC001", "importance": "NORMAL", "characters": []},
                {"scene_id": "SC002", "importance": "CRITICAL", "characters": ["deus"]},
                {"scene_id": "SC003", "importance": "NORMAL", "characters": []},
            ],
            headline="A Criação do Mundo",
            thumbnails_dir=str(thumb_dir),
        )
        assert result.success
        assert os.path.exists(result.data["thumbnail_path"])
        # Verify it's 1280x720
        from PIL import Image
        img = Image.open(result.data["thumbnail_path"])
        assert img.size == (1280, 720)

    @pytest.mark.asyncio
    async def test_generates_required_title_subtitle_and_book(self, test_images, tmp_path: Path):
        agent = ThumbnailAgent()
        result = await agent.run(
            episode_id="EP2",
            images=test_images,
            scenes=[{"scene_id": "SC001", "importance": "CRITICAL", "characters": ["adão", "eva"]}],
            headline="ADÃO E EVA",
            subtitle="O JARDIM DO ÉDEN",
            book_subtitle="GÊNESIS 2–3",
            thumbnails_dir=str(tmp_path),
        )

        assert result.success
        assert result.data["headline"] == "ADÃO E EVA"
        assert result.data["subtitle"] == "O JARDIM DO ÉDEN"
        assert result.data["book_subtitle"] == "GÊNESIS 2–3"
        assert Path(result.data["thumbnail_path"]).stat().st_size > 1_000

    @pytest.mark.asyncio
    async def test_selects_critical_scene_as_hero(self, test_images, tmp_path: Path):
        agent = ThumbnailAgent()
        result = await agent.run(
            episode_id="T",
            images=test_images,
            scenes=[
                {"scene_id": "SC001", "importance": "NORMAL", "characters": []},
                {"scene_id": "SC002", "importance": "CRITICAL", "characters": ["deus"]},
                {"scene_id": "SC003", "importance": "LOW", "characters": []},
            ],
            headline="Teste",
            thumbnails_dir=str(tmp_path),
        )
        assert result.success
        assert "SC002" in result.data["hero_scene_image"]

    @pytest.mark.asyncio
    async def test_no_images_fails(self):
        agent = ThumbnailAgent()
        result = await agent.run(episode_id="T", images=None)
        assert not result.success


# ── Metadata Agent (§92-93) ──────────────────────────────────────────────────

@pytest.fixture
def research_criacao():
    return {
        "story": "Criação do Mundo",
        "summary": "Deus criou o mundo em seis dias e descansou no sétimo.",
        "references": [{"book": "Gênesis", "chapter": 1, "verses": "1-31"}],
        "narrative_classification": {
            "BIBLICAL_FACT": ["Deus criou a luz", "Deus criou os animais"],
        },
    }


class TestMetadataAgent:
    @pytest.mark.asyncio
    async def test_generates_metadata_template(self, research_criacao, tmp_path: Path):
        # No LLM → template fallback
        agent = MetadataAgent(llm_provider=None)
        scenes = [
            {"scene_id": "SC001", "start": 0.0, "end": 10.0, "narration": "Deus criou a luz"},
            {"scene_id": "SC002", "start": 10.0, "end": 20.0, "narration": "Deus criou o céu"},
            {"scene_id": "SC003", "start": 20.0, "end": 35.0, "narration": "Deus criou a terra"},
        ]
        result = await agent.run(
            episode_id="T",
            theme="História da criação do mundo",
            research_data=research_criacao,
            scenes=scenes,
            metadata_dir=str(tmp_path),
        )
        assert result.success
        meta = result.data["metadata"]
        assert meta["title"]
        assert meta["description"]
        assert len(meta["tags"]) > 0
        assert meta["made_for_kids"] is True
        assert (tmp_path / "metadata.json").exists()

    @pytest.mark.asyncio
    async def test_playlist_selection_old_testament(self, research_criacao):
        agent = MetadataAgent(llm_provider=None)
        result = await agent.run(
            episode_id="T",
            theme="Criação do Mundo",
            research_data=research_criacao,
            scenes=[],
        )
        assert result.data["metadata"]["playlist"] == "Aventuras do Antigo Testamento"

    @pytest.mark.asyncio
    async def test_description_includes_references(self, research_criacao):
        agent = MetadataAgent(llm_provider=None)
        result = await agent.run(
            episode_id="T", theme="Criação", research_data=research_criacao, scenes=[],
        )
        desc = result.data["metadata"]["description"]
        assert "Gênesis" in desc

    @pytest.mark.asyncio
    async def test_chapters_start_at_zero(self, research_criacao):
        agent = MetadataAgent(llm_provider=None)
        scenes = [
            {"scene_id": f"SC{i:03d}", "start": i * 10.0, "end": (i + 1) * 10.0,
             "narration": f"Cena {i}"}
            for i in range(9)
        ]
        result = await agent.run(
            episode_id="T", theme="Criação", research_data=research_criacao, scenes=scenes,
        )
        chapters = result.data["metadata"]["chapters"]
        assert len(chapters) >= 1
        assert chapters[0]["start"] == 0.0
