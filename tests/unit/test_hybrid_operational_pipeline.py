import asyncio
from decimal import Decimal

import pytest
from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.execution import Executor, ProviderResult
from src.hybrid.planner import Config


def source_manifest(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "green").save(source)
    frozen = FrozenAsset.approve(source, "source-qa", "TEST")
    sheet = tmp_path / "sources.png"
    contact_sheet((frozen,), sheet)
    return Manifest.freeze(
        (frozen,), FrozenAsset.approve(sheet, "source-qa", "TEST"), "source-qa", "TEST"
    )


class ImageProvider:
    mode = "TEST"

    def __init__(self, root):
        self.root = root
        self.calls = 0

    async def submit(self, job, request_id, checkpoint):
        self.calls += 1
        checkpoint(provider_id=f"provider-{request_id}")
        await asyncio.sleep(0)
        output = self.root / f"{request_id}.png"
        Image.new("RGB", (64, 64), "blue").save(output)
        return ProviderResult(output, job.cost)

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        raise AssertionError("unexpected recovery")


def test_compiled_packet_round_trips_exact_audio_and_semantic_storyboard(tmp_path):
    from src.hybrid.compiled import compile_storyboard

    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    packet = compile_storyboard(
        "EPX",
        FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [
            {
                "scene_id": "R001",
                "start": 0.0,
                "end": 2.5,
                "image_prompt": "Abraão olha estrelas no céu",
                "visual_action": "Abraão olha para o céu",
                "hero": True,
            }
        ],
    )
    saved = tmp_path / "compiled.json"
    packet.save(saved)

    loaded = packet.load(saved)

    assert loaded == packet
    assert loaded.frames[0].hero is True
    assert loaded.frames[0].semantic_action == "Abraão olha para o céu"


@pytest.mark.asyncio
async def test_operational_pipeline_promotes_only_hash_bound_approved_candidates(tmp_path):
    from src.hybrid.compiled import OperationalPipeline, compile_storyboard

    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    episode = compile_storyboard(
        "EPX",
        FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [
            {
                "scene_id": "R001",
                "start": 0.0,
                "end": 2.5,
                "image_prompt": "Abraão olha estrelas no céu",
                "visual_action": "Abraão olha para o céu",
            }
        ],
    )
    pipeline = OperationalPipeline(
        episode,
        Executor(tmp_path / "control.db", Config()),
        source_manifest(tmp_path),
        workspace=tmp_path / "compiled",
        endpoint="flux",
        image_cost=Decimal(".02"),
    )
    receipts = await pipeline.dispatch_baselines(ImageProvider(tmp_path))

    with pytest.raises(ValueError, match="eligible compiled receipt"):
        pipeline.record_visual_qa("R001", "wrong", True, "independent-qa")
    pipeline.record_visual_qa(
        "R001", receipts["R001"]["result_sha256"], True, "independent-qa"
    )
    manifest = pipeline.approved_manifest()

    assert manifest.assets[0].path.parent.name == "approved_images"
    assert manifest.assets[0].sha256 == receipts["R001"]["result_sha256"]
    assert (tmp_path / "compiled" / "qa" / "R001.json").is_file()
    assert (tmp_path / "compiled" / "manifest.json").is_file()


def test_operational_pipeline_cannot_freeze_manifest_before_all_visual_qa(tmp_path):
    from src.hybrid.compiled import OperationalPipeline, compile_storyboard

    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    episode = compile_storyboard(
        "EPX",
        FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [{"scene_id": "R001", "start": 0, "end": 2, "image_prompt": "céu", "action": "céu"}],
    )
    pipeline = OperationalPipeline(
        episode,
        Executor(tmp_path / "control.db", Config()),
        source_manifest(tmp_path),
        workspace=tmp_path / "compiled",
        endpoint="flux",
        image_cost=Decimal(".02"),
    )

    with pytest.raises(ValueError, match="approved QA"):
        pipeline.approved_manifest()


def test_director_opens_compiled_pipeline_from_approved_episode_inputs(tmp_path, monkeypatch):
    from src.agents.director import DirectorAgent
    from src.config.loader import get_config
    from src.storage.episode_fs import EpisodeFS

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    fs = EpisodeFS("EPX", get_config())
    fs.create_dirs()
    (fs.paths.storyboard_dir / "scenes.json").write_text(
        '{"scenes":[{"scene_id":"R001","start":0,"end":2,"image_prompt":"céu","action":"céu"}]}',
        encoding="utf-8",
    )
    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = get_config()

    pipeline = director.create_operational_pipeline(
        "EPX",
        approved_audio=FrozenAsset.approve(audio, "audio-qa", "TEST"),
        source_manifest=source_manifest(tmp_path),
        database=tmp_path / "control.db",
        endpoint="flux",
        image_cost=Decimal(".02"),
    )

    assert pipeline.episode.episode_id == "EPX"
    assert (fs.paths.compiled_dir / "compiled_episode.json").is_file()
