import asyncio
import json
import subprocess
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pytest
from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.execution import Executor, ProviderResult
from src.hybrid.planner import Config
from src.hybrid.render import commit_renderer_completion


def source_manifest(tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "green").save(source)
    frozen = FrozenAsset.approve(source, "source-qa", "TEST")
    sheet = tmp_path / "sources.png"
    contact_sheet((frozen,), sheet)
    return Manifest.freeze(
        (frozen,), FrozenAsset.approve(sheet, "source-qa", "TEST"), "source-qa", "TEST"
    )


def real_test_audio(path):
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=48000:duration=2", "-ac", "2", str(path),
        ],
        check=True,
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
        Image.new("RGB", (1280, 720), "red" if job.category == "hero" else "blue").save(output)
        return ProviderResult(output, job.cost)

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        raise AssertionError("unexpected recovery")


@pytest.mark.asyncio
async def test_test_compiled_adapter_runs_the_real_pipeline_jobs_through_durable_runner(
    tmp_path,
):
    from src.hybrid.autonomous import (
        AutonomousCompiledTestAdapter,
        AutonomousPolicy,
        AutonomousTwoGateRunner,
    )
    from src.hybrid.compiled import OperationalPipeline, compile_storyboard
    from src.hybrid.standing_budget import StandingBudgetLedger

    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    episode = compile_storyboard(
        "TEST-EPX",
        FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [{"scene_id": "R001", "start": 0, "end": 2, "image_prompt": "céu", "action": "céu"}],
    )
    executor = Executor(tmp_path / "control.db", Config(limit=Decimal("1.00")))
    pipeline = OperationalPipeline(
        episode, executor, source_manifest(tmp_path), workspace=tmp_path / "compiled",
        endpoint="test-image", image_cost=Decimal(".02"),
    )
    policy_document = {
        "schema": 1, "version": "test-v1", "mode": "TEST", "episode_cap_usd": "1.00",
        "month_cap_usd": "1.00", "experiment_cap_usd": "1.00",
        "allowed_providers": ["test-image"], "publication": {"enabled": False},
    }
    policy = AutonomousPolicy.from_dict(policy_document)
    budget = StandingBudgetLedger(
        tmp_path / "budget.sqlite3", policy_sha256=policy.digest,
        episode_limit_usd=policy.episode_cap_usd, month_limit_usd=policy.month_cap_usd,
        experiment_limit_usd=policy.experiment_cap_usd, mode="TEST",
    )
    runner = AutonomousTwoGateRunner(tmp_path / "runner", "TEST-EPX", policy, executor, budget)
    adapter = AutonomousCompiledTestAdapter(runner, pipeline)

    assert adapter.start()["state"] == "READY"
    result = await adapter.resume(
        ImageProvider(tmp_path),
        lambda receipt: {
            "approved": True, "reviewer": "independent-test-qa",
            "reviewer_identity": {"subject": "test-qa", "issuer": "TEST"},
            "request_id": receipt["request_id"], "result_sha256": receipt["result_sha256"],
        },
    )

    assert result["state"] == "MEDIA_PENDING"
    assert pipeline.render_ready() is True
    manifest = pipeline.approved_manifest()
    assert manifest.assets[0].sha256 == executor.inspect(pipeline.baseline_jobs()[0].request_id)["result_sha256"]


def test_director_builds_test_compiled_adapter_with_the_pipeline_executor(tmp_path):
    from src.agents.director import DirectorAgent
    from src.hybrid.compiled import OperationalPipeline, compile_storyboard

    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    episode = compile_storyboard(
        "TEST-EPX",
        FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [{"scene_id": "R001", "start": 0, "end": 2, "image_prompt": "céu", "action": "céu"}],
    )
    executor = Executor(tmp_path / "control.db", Config(limit=Decimal("1.00")))
    pipeline = OperationalPipeline(
        episode, executor, source_manifest(tmp_path), workspace=tmp_path / "compiled",
        endpoint="test-image", image_cost=Decimal(".02"),
    )
    policy_document = {
        "schema": 1, "version": "test-v1", "mode": "TEST", "episode_cap_usd": "1.00",
        "month_cap_usd": "1.00", "experiment_cap_usd": "1.00",
        "allowed_providers": ["test-image"], "publication": {"enabled": False},
    }
    director = DirectorAgent.__new__(DirectorAgent)

    adapter = director.create_autonomous_compiled_test_adapter(
        pipeline, tmp_path / "runner", policy_document
    )

    assert adapter.runner.executor is executor
    assert adapter.runner.episode_id == "TEST-EPX"


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
    assert len(list((tmp_path / "compiled" / "qa").glob("*.json"))) == 1
    assert (tmp_path / "compiled" / "manifest.json").is_file()


@pytest.mark.asyncio
async def test_operational_pipeline_prepares_hash_bound_technical_qa_packets(tmp_path):
    from src.hybrid.compiled import OperationalPipeline, compile_storyboard

    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    episode = compile_storyboard(
        "EPX", FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [{"scene_id": "R001", "start": 0, "end": 2, "image_prompt": "céu", "action": "céu"}],
    )
    pipeline = OperationalPipeline(
        episode, Executor(tmp_path / "control.db", Config()), source_manifest(tmp_path),
        workspace=tmp_path / "compiled", endpoint="flux", image_cost=Decimal(".02"),
    )
    receipts = await pipeline.dispatch_baselines(ImageProvider(tmp_path))
    assert len(list((tmp_path / "compiled" / "qa_packets").glob("*.json"))) == 1

    packets = pipeline.prepare_qa_packets()

    assert packets["R001"]["result_sha256"] == receipts["R001"]["result_sha256"]
    assert packets["R001"]["dimensions"] == [1280, 720]
    assert packets["R001"]["promotion_authorized"] is False


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


@pytest.mark.asyncio
async def test_remediation_becomes_the_only_active_promotable_image(tmp_path):
    from src.hybrid.compiled import OperationalPipeline, compile_storyboard

    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    episode = compile_storyboard(
        "EPX", FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [{"scene_id": "R001", "start": 0, "end": 2, "image_prompt": "céu", "action": "céu"}],
    )
    pipeline = OperationalPipeline(
        episode, Executor(tmp_path / "control.db", Config()), source_manifest(tmp_path),
        workspace=tmp_path / "compiled", endpoint="flux", image_cost=Decimal(".02"),
    )
    provider = ImageProvider(tmp_path)
    initial = (await pipeline.dispatch_baselines(provider))["R001"]
    pipeline.record_visual_qa("R001", initial["result_sha256"], False, "independent-qa")

    corrected = (await pipeline.dispatch_remediation_wave(
        {"R001": "sem defeito"}, provider, prefetch=1
    ))["R001"]
    pipeline.record_visual_qa("R001", corrected["result_sha256"], True, "independent-qa")
    manifest = pipeline.approved_manifest()

    assert manifest.assets[0].sha256 == corrected["result_sha256"]
    assert pipeline.run._active_images["R001"].request_id == corrected["request_id"]


@pytest.mark.asyncio
async def test_operational_pipeline_promotes_approved_hero_into_render_scene(tmp_path):
    from src.hybrid.compiled import OperationalPipeline, compile_storyboard

    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    episode = compile_storyboard(
        "EPX",
        FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [{"scene_id": "R001", "start": 0, "end": 5, "image_prompt": "céu", "action": "céu", "hero": True}],
    )
    pipeline = OperationalPipeline(
        episode, Executor(tmp_path / "control.db", Config()), source_manifest(tmp_path),
        workspace=tmp_path / "compiled", endpoint="flux", image_cost=Decimal(".02"),
    )
    provider = ImageProvider(tmp_path)
    baseline = (await pipeline.dispatch_baselines(provider))["R001"]
    pipeline.record_visual_qa("R001", baseline["result_sha256"], True, "independent-qa")
    hero = pipeline.run.eligible_hero_jobs(Decimal(".26"), "seedance")[0]
    hero_receipt = await pipeline.run.executor.run(hero, provider)
    pipeline.record_visual_qa("R001", hero_receipt["result_sha256"], True, "independent-qa")
    manifest = pipeline.approved_manifest()

    scenes = pipeline.render_scenes(manifest)

    assert scenes[0].image.sha256 == baseline["result_sha256"]
    assert scenes[0].clip is not None
    assert scenes[0].clip.sha256 == hero_receipt["result_sha256"]
    assert scenes[0].seconds == 5


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


def test_director_accepts_ep8_timestamp_field_names_and_import_bridge(tmp_path, monkeypatch):
    from src.agents.director import DirectorAgent
    from src.config.loader import get_config
    from src.storage.episode_fs import EpisodeFS

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    fs = EpisodeFS("EP8", get_config())
    fs.create_dirs()
    storyboard = tmp_path / "ep8-storyboard.json"
    storyboard.write_text(
        '{"frames":[{"frame_id":"R001","start_s":0,"end_s":2,'
        '"prompt_en":"calm sky","action_visual_pt":"céu calmo"},'
        '{"frame_id":"R032","start_s":2,"end_s":4,'
        '"prompt_en":"calm room","action_visual_pt":"sala calma"}]}',
        encoding="utf-8",
    )
    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    source = tmp_path / "imported.png"
    Image.new("RGB", (64, 64), "green").save(source)
    imported = FrozenAsset.approve(source, "qa", "TEST")
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = get_config()

    pipeline = director.create_operational_pipeline(
        "EP8",
        approved_audio=FrozenAsset.approve(audio, "audio-qa", "TEST"),
        source_manifest=source_manifest(tmp_path),
        database=tmp_path / "control.db",
        endpoint="flux",
        image_cost=Decimal(".02"),
        storyboard_path=storyboard,
        imported_assets={"R001": imported},
        blocked_scenes={"R001"},
        prior_spend=Decimal(".50"),
    )

    assert [frame.scene_id for frame in pipeline.episode.frames] == ["R001", "R032"]
    assert set(pipeline.run._baselines) == {"R032"}
    assert pipeline.run.executor.prior_spend == Decimal(".50")


def test_director_activates_compiled_pipeline_only_from_image_generation_state(tmp_path, monkeypatch):
    from src.agents.director import DirectorAgent
    from src.config.loader import get_config
    from src.state.machine import EpisodeState, EpisodeStateStore
    from src.storage.episode_fs import EpisodeFS

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    fs = EpisodeFS("EPX", get_config())
    fs.create_dirs()
    (fs.paths.storyboard_dir / "scenes.json").write_text(
        '{"scenes":[{"scene_id":"R001","start":0,"end":2,"image_prompt":"céu","action":"céu"}]}',
        encoding="utf-8",
    )
    state = EpisodeStateStore.load_or_create(fs.paths.state_json, "EPX")
    state.transition_to(EpisodeState.RESEARCHING)
    state.transition_to(EpisodeState.PLANNING)
    state.transition_to(EpisodeState.WAITING_PLAN_APPROVAL)
    state.transition_to(EpisodeState.SCRIPTING)
    state.transition_to(EpisodeState.GENERATING_AUDIO)
    state.transition_to(EpisodeState.STORYBOARDING)
    state.transition_to(EpisodeState.GENERATING_IMAGES)
    state.save(fs.paths.state_json)
    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = get_config()

    pipeline = director.activate_compiled_production(
        "EPX",
        approved_audio=FrozenAsset.approve(audio, "audio-qa", "TEST"),
        source_manifest=source_manifest(tmp_path),
        database=tmp_path / "control.db",
        endpoint="flux",
        image_cost=Decimal(".02"),
    )

    assert pipeline.episode.episode_id == "EPX"
    assert EpisodeStateStore.load(fs.paths.state_json).current_state == EpisodeState.GENERATING_IMAGES


@pytest.mark.asyncio
async def test_director_dispatches_compiled_baselines_to_independent_visual_qa(tmp_path, monkeypatch):
    from src.agents.director import DirectorAgent
    from src.config.loader import get_config
    from src.state.machine import EpisodeState, EpisodeStateStore
    from src.storage.episode_fs import EpisodeFS

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    fs = EpisodeFS("EPX", get_config())
    fs.create_dirs()
    (fs.paths.storyboard_dir / "scenes.json").write_text(
        '{"scenes":[{"scene_id":"R001","start":0,"end":2,"image_prompt":"céu","action":"céu"}]}',
        encoding="utf-8",
    )
    state = EpisodeStateStore.load_or_create(fs.paths.state_json, "EPX")
    for target in (
        EpisodeState.RESEARCHING, EpisodeState.PLANNING, EpisodeState.WAITING_PLAN_APPROVAL,
        EpisodeState.SCRIPTING, EpisodeState.GENERATING_AUDIO, EpisodeState.STORYBOARDING,
        EpisodeState.GENERATING_IMAGES,
    ):
        state.transition_to(target)
    state.save(fs.paths.state_json)
    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = get_config()
    pipeline = director.activate_compiled_production(
        "EPX", approved_audio=FrozenAsset.approve(audio, "audio-qa", "TEST"),
        source_manifest=source_manifest(tmp_path), database=tmp_path / "control.db",
        endpoint="flux", image_cost=Decimal(".02"),
    )

    result = await director.dispatch_compiled_baselines("EPX", pipeline, ImageProvider(tmp_path))

    assert result["qa_packets"]["R001"]["promotion_authorized"] is False
    assert EpisodeStateStore.load(fs.paths.state_json).current_state == EpisodeState.VISUAL_QA

    promoted = director.record_compiled_visual_qa(
        "EPX",
        pipeline,
        [{
            "scene_id": "R001",
            "result_sha256": result["qa_packets"]["R001"]["result_sha256"],
            "approved": True,
            "reviewer": "independent-qa",
        }],
    )

    assert promoted["approved"]["R001"]
    assert EpisodeStateStore.load(fs.paths.state_json).current_state == EpisodeState.PLANNING_ANIMATION

    class Renderer:
        def render_compiled(
            self,
            production,
            scenes,
            manifest,
            audio,
            srt,
            output,
            *,
            hold,
            completion_receipt_path,
            render_intent_sha256,
            render_contract_sha256,
            canonical_episode_root,
        ):
            assert production is pipeline
            assert len(scenes) == 1
            assert manifest.checksum == pipeline.approved_manifest().checksum
            assert audio == pipeline.episode.audio
            assert srt is None
            assert hold == 4
            output.write_bytes(b"render")
            receipt = {
                "output_sha256": sha256(output.read_bytes()).hexdigest(),
                "subtitles_sha256": None,
                "hold_seconds": hold,
                "audio_operation": "derived_master",
            }
            commit_renderer_completion(
                completion_receipt_path,
                intent_sha256=render_intent_sha256,
                render_contract_sha256=render_contract_sha256,
                receipt=receipt,
                canonical_episode_root=canonical_episode_root,
            )
            return receipt

    receipt = director.render_compiled_video("EPX", pipeline, Renderer())

    assert receipt["renderer_receipt"]["subtitles_sha256"] is None
    assert fs.paths.final_video.is_file()
    assert EpisodeStateStore.load(fs.paths.state_json).current_state == EpisodeState.FINAL_QA


@pytest.mark.asyncio
async def test_director_forwards_hash_bound_live_authority_to_compiled_dispatch(tmp_path, monkeypatch):
    from src.agents.director import DirectorAgent
    from src.config.loader import get_config
    from src.state.machine import EpisodeState, EpisodeStateStore
    from src.storage.episode_fs import EpisodeFS

    class Pipeline:
        episode = type("Episode", (), {"audio": type("Audio", (), {"mode": "LIVE"})()})()
        run = type(
            "Run", (), {"executor": type("Executor", (), {"sync_cost_ledger": lambda *_args, **_kwargs: None})()}
        )()

        def baseline_jobs(self):
            return (type("Job", (), {"request_id": "job"})(),)

        async def dispatch_baselines(self, provider, *, authorizations, prices):
            assert provider == "live-provider"
            assert authorizations == {"job": "authorization"}
            assert prices == {"job": "fresh-price"}
            return {}

        def prepare_qa_packets(self):
            return {}

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    fs = EpisodeFS("EPX", get_config())
    fs.create_dirs()
    EpisodeStateStore(episode_id="EPX", current_state=EpisodeState.GENERATING_IMAGES).save(fs.paths.state_json)
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = get_config()

    result = await director.dispatch_compiled_baselines(
        "EPX", Pipeline(), "live-provider", authorizations={"job": "authorization"}, prices={"job": "fresh-price"}
    )

    assert result["state"] == EpisodeState.VISUAL_QA.value


@pytest.mark.asyncio
async def test_director_rejects_live_dispatch_without_authority_for_every_compiled_job(tmp_path, monkeypatch):
    from src.agents.director import DirectorAgent
    from src.config.loader import get_config
    from src.state.machine import EpisodeState, EpisodeStateStore
    from src.storage.episode_fs import EpisodeFS

    class Pipeline:
        episode = type("Episode", (), {"audio": type("Audio", (), {"mode": "LIVE"})()})()

        def baseline_jobs(self):
            return (type("Job", (), {"request_id": "job-1"})(),)

        async def dispatch_baselines(self, *_args, **_kwargs):
            raise AssertionError("LIVE dispatch reached provider without complete authority")

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    fs = EpisodeFS("EPX", get_config())
    fs.create_dirs()
    EpisodeStateStore(episode_id="EPX", current_state=EpisodeState.GENERATING_IMAGES).save(fs.paths.state_json)
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = get_config()

    with pytest.raises(ValueError, match="every compiled LIVE job"):
        await director.dispatch_compiled_baselines("EPX", Pipeline(), "live-provider")


def test_director_issues_live_preflight_for_compiled_baselines(tmp_path, monkeypatch):
    from src.agents.director import DirectorAgent
    from src.config.loader import get_config
    from src.state.machine import EpisodeState, EpisodeStateStore
    from src.storage.episode_fs import EpisodeFS

    class Resolver:
        def quote(self, job):
            return {"endpoint": job.endpoint, "amount": job.cost, "evidence": "official"}

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    fs = EpisodeFS("EPX", get_config())
    fs.create_dirs()
    EpisodeStateStore(episode_id="EPX", current_state=EpisodeState.GENERATING_IMAGES).save(fs.paths.state_json)
    pipeline = type("Pipeline", (), {"episode": type("Episode", (), {"audio": type("Audio", (), {"mode": "LIVE"})()})(), "baseline_jobs": lambda self: ()})()
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = get_config()

    with pytest.raises(ValueError, match="nonempty"):
        director.issue_compiled_live_preflight("EPX", pipeline, Resolver(), reviewer="budget-qa")


@pytest.mark.asyncio
async def test_compiled_adapter_renders_local_sidecars_and_stages_two_separate_test_gates_after_fresh_resume(tmp_path):
    """The compiled bridge reaches actual local media without manufacturing a human decision."""
    from src.hybrid.autonomous import (
        AutonomousCompiledTestAdapter,
        AutonomousPolicy,
        AutonomousTwoGateRunner,
    )
    from src.hybrid.compiled import OperationalPipeline, compile_storyboard
    from src.hybrid.render import LocalRenderer
    from src.hybrid.standing_budget import StandingBudgetLedger

    audio = tmp_path / "approved.wav"
    real_test_audio(audio)
    episode = compile_storyboard(
        "TEST-EPX", FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [{"scene_id": "R001", "start": 0, "end": 2, "image_prompt": "céu", "action": "céu"}],
    )
    executor = Executor(tmp_path / "control.db", Config(limit=Decimal("1.00")))
    pipeline = OperationalPipeline(
        episode, executor, source_manifest(tmp_path), workspace=tmp_path / "compiled",
        endpoint="test-image", image_cost=Decimal(".02"),
    )
    parsed = AutonomousPolicy.from_dict({
        "schema": 1, "version": "test-v1", "mode": "TEST", "episode_cap_usd": "1.00",
        "month_cap_usd": "1.00", "experiment_cap_usd": "1.00",
        "allowed_providers": ["test-image"], "publication": {"enabled": False},
    })
    budget = StandingBudgetLedger(
        tmp_path / "budget.sqlite3", policy_sha256=parsed.digest,
        episode_limit_usd=parsed.episode_cap_usd, month_limit_usd=parsed.month_cap_usd,
        experiment_limit_usd=parsed.experiment_cap_usd, mode="TEST",
    )
    root = tmp_path / "runner"
    adapter = AutonomousCompiledTestAdapter(
        AutonomousTwoGateRunner(root, "TEST-EPX", parsed, executor, budget), pipeline
    )
    adapter.start()
    await adapter.resume(
        ImageProvider(tmp_path),
        lambda receipt: {
            "approved": True, "reviewer": "independent-image-test-qa",
            "reviewer_identity": {"subject": "image-qa", "issuer": "TEST"},
            "request_id": receipt["request_id"], "result_sha256": receipt["result_sha256"],
        },
    )

    # A new process reloads the durable executor/runner facts and does not rerun image work.
    resumed = AutonomousCompiledTestAdapter(
        AutonomousTwoGateRunner(root, "TEST-EPX", parsed, executor, budget), pipeline
    )
    assert (await resumed.resume(ImageProvider(tmp_path), lambda _: pytest.fail("QA reran")))["state"] == "MEDIA_PENDING"
    renderer = LocalRenderer(width=320, height=180)
    with pytest.raises(ValueError, match="independent TEST media QA rejected"):
        resumed.stage_local_delivery(
            renderer,
            lambda evidence: {
                "approved": False, "mode": "TEST", "reviewer": "independent-media-test-qa",
                "reviewer_identity": {"subject": "media-qa", "issuer": "TEST"},
                "manifest_sha256": evidence["manifest_sha256"],
                "thumbnail_sha256": evidence["thumbnail_sha256"],
                "video_sha256": evidence["video_sha256"],
                "sidecars_sha256": {kind: item["sha256"] for kind, item in evidence["sidecars"].items()},
            },
            hold=3,
        )
    assert resumed.runner._load()["state"] == "MEDIA_PENDING"
    master = tmp_path / "compiled" / "delivery" / "master.mp4"
    master_sha256 = sha256(master.read_bytes()).hexdigest()
    staged = resumed.stage_local_delivery(
        object(),  # Resume consumes verified evidence; it must not render again.
        lambda evidence: {
            "approved": True, "mode": "TEST", "reviewer": "independent-media-test-qa",
            "reviewer_identity": {"subject": "media-qa", "issuer": "TEST"},
            "manifest_sha256": evidence["manifest_sha256"],
            "thumbnail_sha256": evidence["thumbnail_sha256"],
            "video_sha256": evidence["video_sha256"],
            "sidecars_sha256": {kind: item["sha256"] for kind, item in evidence["sidecars"].items()},
        },
        hold=3,
    )

    assert staged["state"] == "WAITING_THUMBNAIL_APPROVAL"
    assert sha256(master.read_bytes()).hexdigest() == master_sha256
    evidence = json.loads((tmp_path / "compiled" / "delivery" / "evidence.json").read_text())
    assert evidence["mode"] == "TEST"
    assert set(evidence["sidecars"]) == {"transcript", "srt", "vtt"}
    assert (tmp_path / "compiled" / "delivery" / "master.mp4").is_file()
    assert resumed.runner.record_human_gate(
        "thumbnail", Path(staged["media"]["thumbnail"]["path"]), approved=True, reviewer="TEST decision"
    )["state"] == "WAITING_VIDEO_APPROVAL"
    assert resumed.runner.record_human_gate(
        "video", Path(staged["media"]["video"]["path"]), approved=True, reviewer="TEST decision"
    )["state"] == "READY_TO_PUBLISH"


@pytest.mark.asyncio
async def test_compiled_adapter_preserves_rejected_baseline_then_resumes_remediation_hero_and_two_gates(tmp_path):
    """A rejected image is retired; only its hash-bound successor can reach render."""
    from src.hybrid.autonomous import (
        AutonomousCompiledTestAdapter,
        AutonomousPolicy,
        AutonomousTwoGateRunner,
    )
    from src.hybrid.compiled import OperationalPipeline, compile_storyboard
    from src.hybrid.render import LocalRenderer
    from src.hybrid.standing_budget import StandingBudgetLedger

    class MixedProvider(ImageProvider):
        async def submit(self, job, request_id, checkpoint):
            self.calls += 1
            checkpoint(provider_id=f"provider-{request_id}")
            if job.category == "hero":
                output = self.root / f"{request_id}.mp4"
                await asyncio.to_thread(
                    subprocess.run,
                    [
                        "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                        "color=c=red:s=1280x720:d=5", "-f", "lavfi", "-i",
                        "anullsrc=r=48000:cl=stereo", "-shortest", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", "-c:a", "aac", str(output),
                    ],
                    check=True,
                )
            else:
                output = self.root / f"{request_id}.png"
                Image.new("RGB", (1280, 720), "blue").save(output)
            return ProviderResult(output, job.cost)

    audio = tmp_path / "approved.wav"
    await asyncio.to_thread(
        subprocess.run,
        [
            "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
            "sine=frequency=440:sample_rate=48000:duration=7", "-ac", "2", str(audio),
        ],
        check=True,
    )
    episode = compile_storyboard(
        "TEST-RECOVERY",
        FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [
            {"scene_id": "R001", "start": 0, "end": 2, "image_prompt": "céu", "action": "céu"},
            {"scene_id": "R002", "start": 2, "end": 7, "image_prompt": "mar", "action": "mar", "hero": True},
        ],
    )
    executor = Executor(tmp_path / "control.db", Config(limit=Decimal("1.00")))
    pipeline = OperationalPipeline(
        episode, executor, source_manifest(tmp_path), workspace=tmp_path / "compiled",
        endpoint="test-image", image_cost=Decimal(".02"),
    )
    parsed = AutonomousPolicy.from_dict({
        "schema": 1, "version": "test-v1", "mode": "TEST", "episode_cap_usd": "1.00",
        "month_cap_usd": "1.00", "experiment_cap_usd": "1.00",
        "allowed_providers": ["test-image"], "publication": {"enabled": False},
    })
    budget = StandingBudgetLedger(
        tmp_path / "budget.sqlite3", policy_sha256=parsed.digest,
        episode_limit_usd=parsed.episode_cap_usd, month_limit_usd=parsed.month_cap_usd,
        experiment_limit_usd=parsed.experiment_cap_usd, mode="TEST",
    )
    root = tmp_path / "runner"
    adapter = AutonomousCompiledTestAdapter(
        AutonomousTwoGateRunner(root, "TEST-RECOVERY", parsed, executor, budget), pipeline
    )
    provider = MixedProvider(tmp_path)
    adapter.start()
    rejected = await adapter.resume(
        provider,
        lambda receipt: {
            "approved": receipt["request"]["scene"] != "R001", "reviewer": "independent-image-qa",
            "reviewer_identity": {"subject": "image-qa", "issuer": "TEST"},
            "request_id": receipt["request_id"], "result_sha256": receipt["result_sha256"],
            "codes": ["SEMANTIC_REJECT"],
        },
    )
    rejected_id = next(job.request_id for job in pipeline.baseline_jobs() if job.scene == "R001")
    assert rejected["state"] == "REJECTED"
    assert executor.inspect(rejected_id)["qa"] is False

    remediated = await adapter.remediate(
        {"R001": "céu sem defeito"}, provider,
        lambda receipt: {
            "approved": True, "reviewer": "independent-image-qa",
            "reviewer_identity": {"subject": "image-qa", "issuer": "TEST"},
            "request_id": receipt["request_id"], "result_sha256": receipt["result_sha256"],
        },
    )
    correction = pipeline.run._active_images["R001"]
    assert remediated["state"] == "MEDIA_PENDING"
    assert correction.predecessor == rejected_id
    assert correction.request_id != rejected_id
    assert executor.inspect(rejected_id)["qa"] is False

    heroed = await adapter.run_heroes(
        Decimal(".02"), "test-image", provider,
        lambda receipt: {
            "approved": True, "reviewer": "independent-hero-qa",
            "reviewer_identity": {"subject": "hero-qa", "issuer": "TEST"},
            "request_id": receipt["request_id"], "result_sha256": receipt["result_sha256"],
        },
    )
    assert heroed["state"] == "MEDIA_PENDING"
    assert pipeline.render_ready() is True

    fresh_executor = Executor(tmp_path / "control.db", Config(limit=Decimal("1.00")))
    fresh_pipeline = OperationalPipeline(
        episode, fresh_executor, source_manifest(tmp_path), workspace=tmp_path / "compiled",
        endpoint="test-image", image_cost=Decimal(".02"),
    )
    fresh_budget = StandingBudgetLedger(
        tmp_path / "budget.sqlite3", policy_sha256=parsed.digest,
        episode_limit_usd=parsed.episode_cap_usd, month_limit_usd=parsed.month_cap_usd,
        experiment_limit_usd=parsed.experiment_cap_usd, mode="TEST",
    )
    fresh = AutonomousCompiledTestAdapter(
        AutonomousTwoGateRunner(root, "TEST-RECOVERY", parsed, fresh_executor, fresh_budget), fresh_pipeline
    )
    calls_before_resume = provider.calls
    assert (await fresh.resume(provider, lambda _: pytest.fail("completed QA reran")))["state"] == "MEDIA_PENDING"
    assert provider.calls == calls_before_resume
    staged = fresh.stage_local_delivery(
        LocalRenderer(width=320, height=180),
        lambda evidence: {
            "approved": True, "mode": "TEST", "reviewer": "independent-media-qa",
            "reviewer_identity": {"subject": "media-qa", "issuer": "TEST"},
            "manifest_sha256": evidence["manifest_sha256"],
            "thumbnail_sha256": evidence["thumbnail_sha256"],
            "video_sha256": evidence["video_sha256"],
            "sidecars_sha256": {kind: item["sha256"] for kind, item in evidence["sidecars"].items()},
        }, hold=3,
    )
    assert staged["state"] == "WAITING_THUMBNAIL_APPROVAL"
    assert fresh.runner.record_human_gate("thumbnail", Path(staged["media"]["thumbnail"]["path"]), approved=True, reviewer="TEST decision")["state"] == "WAITING_VIDEO_APPROVAL"
    assert fresh.runner.record_human_gate("video", Path(staged["media"]["video"]["path"]), approved=True, reviewer="TEST decision")["state"] == "READY_TO_PUBLISH"
