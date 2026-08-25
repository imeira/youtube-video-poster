"""Character-reference routing for EP2 image generation."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from src.agents.image_gen import ImageGenAgent


class FakeProvider:
    def __init__(self, mode: str, calls: list[dict]):
        self.mode = mode
        self.calls = calls

    async def generate(self, **kwargs):
        self.calls.append({"mode": self.mode, **kwargs})
        # ImageGenAgent moves the returned image, so give each call a real file.
        temp = Path.cwd() / f"fake_{len(self.calls)}.png"
        Image.new("RGB", (64, 64), "green").save(temp)
        return SimpleNamespace(
            success=True,
            image_path=str(temp),
            seed=kwargs.get("seed", 0),
            generation_time=0.01,
            error="",
        )


class FailingProvider(FakeProvider):
    async def generate(self, **kwargs):
        if "second" in kwargs["prompt"]:
            return SimpleNamespace(success=False, error="synthetic failure")
        return await super().generate(**kwargs)


@pytest.fixture
def canonical_assets(tmp_path, monkeypatch):
    root = tmp_path / "characters" / "creation"
    for name, color in (("adam", "blue"), ("eve", "pink")):
        path = root / name / "face_v1.png"
        path.parent.mkdir(parents=True)
        Image.new("RGB", (128, 128), color).save(path)
    monkeypatch.setenv("STUDIO_CHARACTER_ASSETS_DIR", str(root))
    return root


@pytest.mark.asyncio
async def test_adam_and_eve_scene_requires_external_reference_grounded_image(tmp_path, canonical_assets):
    calls: list[dict] = []
    agent = ImageGenAgent(provider_factory=lambda mode: FakeProvider(mode, calls))
    scenes = [{
        "scene_id": "SC001",
        "characters": ["adão", "eva"],
        "image_prompt": "Adam and Eve walking in Eden",
        "negative_prompt": "",
    }]

    result = await agent.run("EP2", scenes=scenes, images_dir=str(tmp_path / "images"))

    assert result.success is False
    assert result.data["total_failed"] == 1
    assert "external reference-grounded image" in result.data["failed"][0]["error"]
    assert calls == []


@pytest.mark.asyncio
async def test_non_character_scene_stays_on_fast_local_lcm(tmp_path, canonical_assets):
    calls: list[dict] = []
    agent = ImageGenAgent(provider_factory=lambda mode: FakeProvider(mode, calls))
    scenes = [{
        "scene_id": "SC001",
        "characters": [],
        "image_prompt": "The garden river and colorful trees",
        "negative_prompt": "",
    }]

    result = await agent.run("EP2", scenes=scenes, images_dir=str(tmp_path / "images"))

    assert result.success is True
    assert calls[0]["mode"] == "lcm"
    assert (calls[0]["width"], calls[0]["height"]) == (1024, 576)
    assert calls[0]["reference_images"] is None


@pytest.mark.asyncio
async def test_missing_canonical_identity_fails_character_scene(tmp_path, monkeypatch):
    monkeypatch.setenv("STUDIO_CHARACTER_ASSETS_DIR", str(tmp_path / "missing"))
    calls: list[dict] = []
    agent = ImageGenAgent(provider_factory=lambda mode: FakeProvider(mode, calls))
    scenes = [{
        "scene_id": "SC001",
        "characters": ["adão"],
        "image_prompt": "Adam in Eden",
        "negative_prompt": "",
    }]

    result = await agent.run("EP2", scenes=scenes, images_dir=str(tmp_path / "images"))

    assert result.success is False
    assert result.data["total_failed"] == 1
    assert "Canonical reference missing" in result.data["failed"][0]["error"]
    assert calls == []


@pytest.mark.asyncio
async def test_partial_generation_is_not_reported_as_success(tmp_path, canonical_assets):
    calls: list[dict] = []
    agent = ImageGenAgent(provider_factory=lambda mode: FailingProvider(mode, calls))
    scenes = [
        {"scene_id": "SC001", "characters": [], "image_prompt": "first", "negative_prompt": ""},
        {"scene_id": "SC002", "characters": [], "image_prompt": "second", "negative_prompt": ""},
    ]

    result = await agent.run("EP2", scenes=scenes, images_dir=str(tmp_path / "images"))

    assert result.success is False
    assert result.data["total_generated"] == 1
    assert result.data["total_failed"] == 1
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_valid_existing_scene_is_reused_on_resume(tmp_path, canonical_assets):
    calls: list[dict] = []
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    Image.new("RGB", (1024, 576), "green").save(image_dir / "SC001.png")
    agent = ImageGenAgent(provider_factory=lambda mode: FakeProvider(mode, calls))
    scenes = [
        {"scene_id": "SC001", "characters": [], "image_prompt": "first", "negative_prompt": ""},
        {"scene_id": "SC002", "characters": [], "image_prompt": "second", "negative_prompt": ""},
    ]

    result = await agent.run("EP2", scenes=scenes, images_dir=str(image_dir))

    assert result.success is True
    assert len(calls) == 1
    reused = next(item for item in result.data["generated"] if item["scene_id"] == "SC001")
    assert reused["reused"] is True
