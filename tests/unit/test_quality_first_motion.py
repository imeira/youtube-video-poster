import asyncio
import json
import subprocess

import pytest


def evidence(score=0.9):
    return {"status": "PASS", "score": score, "evidence_hash": "a" * 64}


def qualified_route(route_id="test-i2v", *, route_kind="I2V", paid=False, score=0.9):
    route = {
        "route_id": route_id,
        "route_kind": route_kind,
        "endpoint": f"https://test.invalid/{route_id}",
        "model": "fixture-model",
        "geometry": {"width": 1280, "height": 720},
        "identity": evidence(score),
        "scene_adherence": evidence(score),
        "temporal_stability": evidence(score),
        "paid": paid,
        "evidence_mode": "TEST",
        "clip_fps": 30,
        "duration_s": 5,
        "audio_policy": "REQUIRED",
    }
    return route


@pytest.mark.parametrize("invalid_duration", [True, False, float("nan"), float("inf"), -1, 0, None])
def test_i2v_route_requires_a_finite_positive_non_boolean_selected_duration(invalid_duration):
    from src.hybrid.quality_first_motion import select_motion_inventory

    route = qualified_route()
    route["duration_s"] = invalid_duration

    inventory = select_motion_inventory([articulated_scene()], [route])

    assert inventory["status"] == "REQUIRED"
    assert inventory["items"][0]["reason"] == "NO_QUALIFIED_ROUTE"


def test_i2v_route_with_missing_selected_duration_is_required():
    from src.hybrid.quality_first_motion import select_motion_inventory

    route = qualified_route()
    del route["duration_s"]

    inventory = select_motion_inventory([articulated_scene()], [route])

    assert inventory["status"] == "REQUIRED"
    assert inventory["items"][0]["reason"] == "NO_QUALIFIED_ROUTE"


def test_selected_i2v_duration_is_hash_bound_and_changes_job_request_identity(tmp_path):
    from decimal import Decimal

    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.compiled import OperationalPipeline, compile_storyboard
    from src.hybrid.execution import Executor
    from src.hybrid.planner import Config

    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "green").save(source)
    asset = FrozenAsset.approve(source, "qa", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "qa", "TEST"), "qa", "TEST")

    def pipeline_for(duration_s):
        route = qualified_route()
        route["duration_s"] = duration_s
        episode = compile_storyboard(
            "TEST-EPX", FrozenAsset.approve(audio, "audio-qa", "TEST"),
            [{"scene_id": "SC01", "start": 0, "end": duration_s, "image_prompt": "Abraão", "action": "gesto", **articulated_scene()}],
            motion_routes=[route],
        )
        pipeline = OperationalPipeline(episode, Executor(tmp_path / f"{duration_s}.db", Config()), manifest,
                                       workspace=tmp_path / f"compiled-{duration_s}", endpoint="test-image", image_cost=Decimal(".02"))
        return pipeline, episode

    five, five_episode = pipeline_for(5)
    three, three_episode = pipeline_for(3)
    five_selected = five.motion_inventory["items"][0]
    three_selected = three.motion_inventory["items"][0]

    assert five_selected["duration_s"] == 5
    assert three_selected["duration_s"] == 3
    assert five_selected["route_evidence_hash"] != three_selected["route_evidence_hash"]
    assert five_episode.checksum != three_episode.checksum
    for pipeline in (five, three):
        baseline = pipeline.run._active_images["SC01"]
        pipeline.run.executor.inspect = lambda request_id, baseline=baseline: {"qa": True} if request_id == baseline.request_id else None
    five_hero = five.run.eligible_hero_jobs(Decimal(".02"), "https://test.invalid/test-i2v")[0]
    three_hero = three.run.eligible_hero_jobs(Decimal(".02"), "https://test.invalid/test-i2v")[0]
    assert five_hero.payload["quality_first_motion"]["duration_s"] == 5
    assert three_hero.payload["quality_first_motion"]["duration_s"] == 3
    assert five_hero.request_id != three_hero.request_id


def articulated_scene(**overrides):
    scene = {
        "scene_id": "SC01",
        "motion_requirement": "ARTICULATED",
        "motion_reason": "Abraão levanta a mão enquanto responde",
        "hero": True,
        "hero_reason": "a resposta corporal é o ponto semântico da cena",
    }
    scene.update(overrides)
    return scene


def test_selects_highest_evidenced_quality_deterministically_without_provider_call():
    from src.hybrid.quality_first_motion import select_motion_inventory

    lower = qualified_route("zeta", score=0.8)
    higher = qualified_route("alpha", score=0.9)

    inventory = select_motion_inventory([articulated_scene()], [lower, higher])

    assert inventory["status"] == "PASS"
    item = inventory["items"][0]
    assert item["scene_id"] == "SC01"
    assert item["status"] == "SELECTED"
    assert item["motion_requirement"] == "ARTICULATED"
    assert item["motion_reason"] == "Abraão levanta a mão enquanto responde"
    assert item["hero"] is True
    assert item["hero_reason"] == "a resposta corporal é o ponto semântico da cena"
    assert item["route_id"] == "alpha"
    assert item["route_kind"] == "I2V"
    assert item["evidence_mode"] == "TEST"
    assert item["endpoint"] == "https://test.invalid/alpha"
    assert item["model"] == "fixture-model"
    assert item["geometry"] == {"width": 1280, "height": 720}
    assert item["quality"] == {"identity": 0.9, "scene_adherence": 0.9, "temporal_stability": 0.9}
    assert len(item["route_evidence_hash"]) == 64


def test_rejects_hero_without_a_semantic_reason():
    from src.hybrid.quality_first_motion import (
        MotionSelectionError,
        select_motion_inventory,
    )

    with pytest.raises(MotionSelectionError, match="hero requires"):
        select_motion_inventory([articulated_scene(hero_reason="")], [qualified_route()])


def test_rejects_scene_without_closed_motion_requirement():
    from src.hybrid.quality_first_motion import (
        MotionSelectionError,
        select_motion_inventory,
    )

    with pytest.raises(MotionSelectionError, match="motion requirement"):
        select_motion_inventory([{"scene_id": "SC01"}], [qualified_route()])


def test_unqualified_provider_route_produces_required_not_fake_pass():
    from src.hybrid.quality_first_motion import select_motion_inventory

    route = qualified_route()
    route["temporal_stability"]["status"] = "FAIL"

    inventory = select_motion_inventory([articulated_scene()], [route])

    assert inventory["status"] == "REQUIRED"
    assert inventory["items"][0]["status"] == "REQUIRED"
    assert inventory["items"][0]["reason"] == "NO_QUALIFIED_ROUTE"


def test_missing_identity_evidence_produces_required_not_fake_pass():
    from src.hybrid.quality_first_motion import select_motion_inventory

    route = qualified_route()
    del route["identity"]

    inventory = select_motion_inventory([articulated_scene()], [route])

    assert inventory["items"][0]["status"] == "REQUIRED"
    assert inventory["items"][0]["reason"] == "NO_QUALIFIED_ROUTE"


def test_articulated_motion_does_not_fall_back_to_local_panzoom():
    from src.hybrid.quality_first_motion import select_motion_inventory

    inventory = select_motion_inventory(
        [articulated_scene()], [qualified_route(route_id="local-pan", route_kind="LOCAL_STILL")]
    )

    assert inventory["items"][0]["status"] == "REQUIRED"
    assert inventory["items"][0]["reason"] == "NO_QUALIFIED_ROUTE"


def test_paid_route_without_explicit_price_evidence_is_required_without_inventing_a_price():
    from src.hybrid.quality_first_motion import select_motion_inventory

    inventory = select_motion_inventory([articulated_scene()], [qualified_route(paid=True)])

    item = inventory["items"][0]
    assert item["status"] == "REQUIRED"
    assert item["reason"] == "PAID_PRICE_UNKNOWN"
    assert "price" not in item


def test_paid_test_route_preserves_closed_bound_price_evidence_in_selected_route():
    from src.hybrid.quality_first_motion import select_motion_inventory

    route = qualified_route(paid=True)
    route["price_evidence"] = {
        "provenance": "TEST_FIXTURE", "amount": 0.02, "currency": "USD",
        "route_id": route["route_id"], "endpoint": route["endpoint"],
        "model": route["model"], "geometry": route["geometry"], "duration_s": route["duration_s"], "evidence_hash": "b" * 64,
    }

    selected = select_motion_inventory([articulated_scene()], [route])["items"][0]

    assert selected["status"] == "SELECTED"
    assert selected["price_evidence"] == route["price_evidence"]


def test_redigested_pass_inventory_with_required_item_is_rejected_at_pipeline_construction(tmp_path):
    from decimal import Decimal

    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet, digest
    from src.hybrid.compiled import OperationalPipeline, compile_storyboard
    from src.hybrid.execution import Executor
    from src.hybrid.planner import Config

    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "green").save(source)
    asset = FrozenAsset.approve(source, "qa", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "qa", "TEST"), "qa", "TEST")
    episode = compile_storyboard(
        "TEST-EPX", FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [{"scene_id": "SC01", "start": 0, "end": 5, "image_prompt": "Abraão", "action": "Abraão", **articulated_scene()}],
        motion_routes=[qualified_route()],
    )
    inventory = episode.source_binding["quality_first_motion"]
    inventory["items"][0] = {**inventory["items"][0], "status": "REQUIRED", "reason": "NO_QUALIFIED_ROUTE"}
    inventory["inventory_hash"] = digest({"schema_version": inventory["schema_version"], "status": "PASS", "items": inventory["items"]})

    with pytest.raises(ValueError, match="aggregate status"):
        OperationalPipeline(episode, Executor(tmp_path / "control.db", Config()), manifest,
                            workspace=tmp_path / "compiled", endpoint="test-image", image_cost=Decimal(".02"))


def test_i2v_clip_technical_qa_decodes_selected_duration_and_rejects_bad_inputs(tmp_path):
    from src.hybrid.artifacts import sha256
    from src.hybrid.technical_qa import inspect_clip

    valid = tmp_path / "valid.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "color=c=red:s=1280x720:r=30:d=5",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(valid),
    ], check=True)
    kwargs = {"expected_sha256": sha256(valid), "geometry": {"width": 1280, "height": 720},
              "duration_s": 5, "fps": 30, "audio_policy": "REQUIRED", "predecessor": "approved-image", "receipt_predecessor": "approved-image"}
    assert inspect_clip(valid, **kwargs)["technical_pass"] is True

    png = tmp_path / "not-a-clip.png"
    from PIL import Image
    Image.new("RGB", (1280, 720), "red").save(png)
    assert inspect_clip(png, **{**kwargs, "expected_sha256": sha256(png)})["technical_pass"] is False
    assert inspect_clip(valid, **{**kwargs, "duration_s": 4})["technical_pass"] is False
    assert inspect_clip(valid, **{**kwargs, "geometry": {"width": 640, "height": 360}})["technical_pass"] is False
    assert inspect_clip(valid, **{**kwargs, "receipt_predecessor": "wrong"})["technical_pass"] is False
    truncated = tmp_path / "truncated.mp4"
    truncated.write_bytes(valid.read_bytes()[:100])
    assert inspect_clip(truncated, **{**kwargs, "expected_sha256": sha256(truncated)})["technical_pass"] is False

    no_audio = tmp_path / "no-audio.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(valid), "-map", "0:v:0", "-c", "copy", str(no_audio)], check=True)
    assert inspect_clip(no_audio, **{**kwargs, "expected_sha256": sha256(no_audio), "audio_policy": "FORBIDDEN"})["technical_pass"] is True

    valid_transport = tmp_path / "valid.ts"
    subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "color=c=red:s=1280x720:r=30:d=5",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=5", "-shortest",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-f", "mpegts", str(valid_transport),
    ], check=True)
    corrupted_audio = tmp_path / "corrupted-audio.ts"
    payload = bytearray(valid_transport.read_bytes())
    for offset in range(0, len(payload) - 188, 188):
        if payload[offset] != 0x47 or ((payload[offset + 1] & 0x1F) << 8 | payload[offset + 2]) != 0x101:
            continue
        adaptation = (payload[offset + 3] >> 4) & 0x03
        start = offset + 4 + (1 + payload[offset + 4] if adaptation in {2, 3} else 0)
        if start < offset + 188:
            payload[start:min(start + 120, offset + 188)] = b"\0" * min(120, offset + 188 - start)
    corrupted_audio.write_bytes(payload)
    audio_failure = inspect_clip(corrupted_audio, **{**kwargs, "expected_sha256": sha256(corrupted_audio)})
    assert audio_failure["technical_pass"] is False
    assert "clip audio decode failed" in audio_failure["errors"]


@pytest.mark.asyncio
async def test_quality_first_facade_selected_i2v_reaches_real_render_with_clip_hash(tmp_path, monkeypatch):
    from decimal import Decimal

    from PIL import Image

    from src.agents.director import DirectorAgent
    from src.config.loader import get_config
    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet, sha256
    from src.hybrid.execution import ProviderResult
    from src.hybrid.render import LocalRenderer
    from src.storage.episode_fs import EpisodeFS

    class FakeExecutor:
        mode = "TEST"

        async def submit(self, job, request_id, checkpoint):
            checkpoint(provider_id=request_id)
            output = tmp_path / f"{request_id}.{'mp4' if job.category == 'hero' else 'png'}"
            if job.category == "hero":
                await asyncio.to_thread(subprocess.run, [
                    "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "color=c=red:s=1280x720:r=30:d=5",
                    "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(output),
                ], check=True)
            else:
                Image.new("RGB", (1280, 720), "blue").save(output)
            return ProviderResult(output, job.cost)

        async def recover(self, *_args):
            raise AssertionError("unexpected recovery")

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path / "episodes"))
    fs = EpisodeFS("TEST-I2V", get_config())
    fs.create_dirs()
    fs.paths.storyboard_dir.joinpath("scenes.json").write_text(
        '{"scenes":[{"scene_id":"SC01","start":0,"end":5,"image_prompt":"Abraão","action":"gesto",'
        '"motion_requirement":"ARTICULATED","motion_reason":"gesto","hero":true,"hero_reason":"gesto"}]}', encoding="utf-8")
    audio = tmp_path / "approved.wav"
    await asyncio.to_thread(subprocess.run, ["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=5", str(audio)], check=True)
    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "green").save(source)
    asset = FrozenAsset.approve(source, "qa", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "qa", "TEST"), "qa", "TEST")
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = get_config()
    pipeline = director.create_operational_pipeline("TEST-I2V", approved_audio=FrozenAsset.approve(audio, "audio", "TEST"), source_manifest=manifest,
        database=tmp_path / "control.db", endpoint="test-image", image_cost=Decimal(".02"), motion_routes=[qualified_route()], motion_policy={"mode": "TEST"})
    provider = FakeExecutor()
    baseline = (await pipeline.dispatch_baselines(provider))["SC01"]
    pipeline.record_visual_qa("SC01", baseline["result_sha256"], True, "semantic-image")
    hero = pipeline.run.eligible_hero_jobs(Decimal(".02"), "https://test.invalid/test-i2v")[0]
    hero_receipt = await pipeline.run.executor.run(hero, provider)
    pipeline.record_visual_qa("SC01", hero_receipt["result_sha256"], True, "semantic-clip")
    packet_path = pipeline.workspace / "qa_packets" / f"{hero.request_id}.json"
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    packet["duration"] = 4
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    assert pipeline.render_ready() is False
    packet["duration"] = 5
    packet_path.write_text(json.dumps(packet), encoding="utf-8")
    approved = pipeline.approved_manifest()
    receipt = LocalRenderer(width=320, height=180).render_compiled(pipeline, pipeline.render_scenes(approved), approved, pipeline.episode.audio, None, tmp_path / "final.mkv", hold=3)

    assert receipt["hero_sha256"] == [hero_receipt["result_sha256"]]
    assert receipt["output_sha256"] == sha256(tmp_path / "final.mkv")


def test_compiled_packet_binds_motion_inventory_and_pipeline_persists_it(tmp_path):
    from decimal import Decimal

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.compiled import (
        CompiledEpisode,
        OperationalPipeline,
        compile_storyboard,
    )
    from src.hybrid.execution import Executor
    from src.hybrid.planner import Config

    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    from PIL import Image

    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "green").save(source)
    source_asset = FrozenAsset.approve(source, "qa", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((source_asset,), sheet)
    manifest = Manifest.freeze((source_asset,), FrozenAsset.approve(sheet, "qa", "TEST"), "qa", "TEST")
    packet = compile_storyboard(
        "TEST-EPX",
        FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [{
            "scene_id": "SC01", "start": 0, "end": 2, "image_prompt": "Abraão ergue a mão",
            "action": "Abraão ergue a mão", **articulated_scene(),
        }],
        motion_routes=[qualified_route()],
    )
    saved = tmp_path / "compiled.json"
    packet.save(saved)
    loaded = CompiledEpisode.load(saved)
    pipeline = OperationalPipeline(
        loaded, Executor(tmp_path / "control.db", Config()), manifest, workspace=tmp_path / "compiled",
        endpoint="test-image", image_cost=Decimal("0.02"),
    )

    assert loaded.source_binding["quality_first_motion"]["inventory_hash"] == pipeline.motion_inventory["inventory_hash"]
    assert CompiledEpisode.load(tmp_path / "compiled" / "compiled_episode.json") == loaded


def test_legacy_compiled_packet_is_explicitly_unbound_from_quality_first_motion(tmp_path):
    from src.hybrid.artifacts import FrozenAsset
    from src.hybrid.compiled import compile_storyboard

    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")

    packet = compile_storyboard(
        "TEST-EPX", FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [{"scene_id": "SC01", "start": 0, "end": 2, "image_prompt": "céu", "action": "céu"}],
    )

    assert packet.source_binding is None


def test_live_route_is_required_until_a_trusted_evidence_adapter_exists():
    from src.hybrid.quality_first_motion import select_motion_inventory

    route = qualified_route()
    route["evidence_mode"] = "LIVE"

    inventory = select_motion_inventory([articulated_scene()], [route])

    assert inventory["status"] == "REQUIRED"
    assert inventory["items"][0]["reason"] == "NO_QUALIFIED_ROUTE"


def test_inventory_tamper_and_incomplete_coverage_are_rejected():
    from src.hybrid.artifacts import digest
    from src.hybrid.quality_first_motion import (
        MotionSelectionError,
        quality_first_motion_binding,
        select_motion_inventory,
        validate_quality_first_motion_inventory,
    )

    inventory = select_motion_inventory([articulated_scene()], [qualified_route()])
    inventory["items"][0]["model"] = "caller-tamper"
    with pytest.raises(MotionSelectionError, match="hash mismatch"):
        quality_first_motion_binding(inventory)

    incomplete = {"schema_version": 1, "status": "PASS", "items": []}
    incomplete["inventory_hash"] = digest(incomplete)
    with pytest.raises(MotionSelectionError, match="cover every compiled scene"):
        validate_quality_first_motion_inventory(incomplete, ("SC01",), "TEST")


@pytest.mark.asyncio
async def test_required_inventory_blocks_baseline_dispatch_and_render_without_provider_call(tmp_path):
    from decimal import Decimal

    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.compiled import OperationalPipeline, compile_storyboard
    from src.hybrid.execution import Executor
    from src.hybrid.planner import Config

    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "green").save(source)
    asset = FrozenAsset.approve(source, "qa", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "qa", "TEST"), "qa", "TEST")
    episode = compile_storyboard(
        "TEST-EPX", FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [{"scene_id": "SC01", "start": 0, "end": 5, "image_prompt": "Abraão", "action": "Abraão", **articulated_scene()}],
        motion_routes=[qualified_route(route_kind="LOCAL_STILL")],
    )
    pipeline = OperationalPipeline(
        episode, Executor(tmp_path / "control.db", Config()), manifest,
        workspace=tmp_path / "compiled", endpoint="test-image", image_cost=Decimal(".02"),
    )

    class ProviderSpy:
        calls = 0

        async def submit(self, *_args):
            self.calls += 1
            raise AssertionError("REQUIRED inventory must stop before provider submission")

    assert pipeline.baseline_jobs() == ()
    assert pipeline.render_ready() is False
    spy = ProviderSpy()
    with pytest.raises(ValueError, match="REQUIRED"):
        await pipeline.dispatch_baselines(spy)
    assert spy.calls == 0
    with pytest.raises(ValueError, match="REQUIRED"):
        pipeline.render_scenes(manifest)


def required_inventory_pipeline(tmp_path):
    from decimal import Decimal

    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.compiled import OperationalPipeline, compile_storyboard
    from src.hybrid.execution import Executor
    from src.hybrid.planner import Config

    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "green").save(source)
    asset = FrozenAsset.approve(source, "qa", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "qa", "TEST"), "qa", "TEST")
    episode = compile_storyboard(
        "TEST-EPX", FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [{"scene_id": "SC01", "start": 0, "end": 5, "image_prompt": "Abraão", "action": "Abraão", **articulated_scene()}],
        motion_routes=[qualified_route(route_kind="LOCAL_STILL")],
    )
    pipeline = OperationalPipeline(
        episode, Executor(tmp_path / "control.db", Config()), manifest,
        workspace=tmp_path / "compiled", endpoint="test-image", image_cost=Decimal(".02"),
    )
    return pipeline, manifest


def test_required_inventory_blocks_hero_successor_before_approved_baseline_can_release(tmp_path):
    from decimal import Decimal

    pipeline, _ = required_inventory_pipeline(tmp_path)
    baseline = pipeline.run._active_images["SC01"]
    pipeline.run.executor.inspect = lambda request_id: {"qa": True} if request_id == baseline.request_id else None

    with pytest.raises(ValueError, match="REQUIRED"):
        pipeline.run.eligible_hero_jobs(Decimal(".02"), "https://test.invalid/hero")

    assert pipeline.run._heroes == {}
    assert pipeline.run.executor.inspect(baseline.request_id)["qa"] is True


def test_required_inventory_blocks_remediation_successor_before_rejected_baseline_can_release(tmp_path):
    pipeline, _ = required_inventory_pipeline(tmp_path)
    baseline = pipeline.run._active_images["SC01"]
    pipeline.run.executor.inspect = lambda request_id: {"qa": False} if request_id == baseline.request_id else None

    with pytest.raises(ValueError, match="REQUIRED"):
        pipeline.run.remediation_job("SC01", "corrija a composição")

    assert pipeline.run._corrections == {}
    assert pipeline.run._active_images["SC01"] == baseline


@pytest.mark.asyncio
async def test_required_inventory_blocks_remediation_dispatch_before_successor_queue_or_provider(tmp_path):
    pipeline, _ = required_inventory_pipeline(tmp_path)
    baseline = pipeline.run._active_images["SC01"]
    pipeline.run.executor.inspect = lambda request_id: {"qa": False} if request_id == baseline.request_id else None

    class ProviderSpy:
        mode = "TEST"
        calls = 0

        async def submit(self, *_args):
            self.calls += 1
            raise AssertionError("REQUIRED inventory must stop before provider submission")

    spy = ProviderSpy()
    assert not pipeline.run.correction_queue_path.exists()
    with pytest.raises(ValueError, match="REQUIRED"):
        await pipeline.run.dispatch_remediation_wave({"SC01": "corrija a composição"}, spy)

    assert spy.calls == 0
    assert pipeline.run._corrections == {}
    assert pipeline.run._active_images["SC01"] == baseline
    assert not pipeline.run.correction_queue_path.exists()


def test_articulated_selected_route_binds_i2v_job_identity_and_rejects_endpoint_override(tmp_path):
    from decimal import Decimal

    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.compiled import OperationalPipeline, compile_storyboard
    from src.hybrid.execution import Executor
    from src.hybrid.planner import Config

    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "green").save(source)
    asset = FrozenAsset.approve(source, "qa", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "qa", "TEST"), "qa", "TEST")
    route = qualified_route("selected-i2v")
    episode = compile_storyboard(
        "TEST-EPX", FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [{"scene_id": "SC01", "start": 0, "end": 5, "image_prompt": "Abraão", "action": "Abraão", **articulated_scene()}],
        motion_routes=[route],
    )
    pipeline = OperationalPipeline(
        episode, Executor(tmp_path / "control.db", Config()), manifest,
        workspace=tmp_path / "compiled", endpoint="test-image", image_cost=Decimal(".02"),
    )

    with pytest.raises(ValueError, match="selected I2V endpoint"):
        pipeline.run.eligible_hero_jobs(Decimal(".02"), "caller-override")


def test_director_facade_propagates_explicit_test_motion_routes(tmp_path, monkeypatch):
    from decimal import Decimal

    from PIL import Image

    from src.agents.director import DirectorAgent
    from src.config.loader import get_config
    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.storage.episode_fs import EpisodeFS

    monkeypatch.setenv("STUDIO_EPISODES_DIR", str(tmp_path))
    fs = EpisodeFS("TEST-EPX", get_config())
    fs.create_dirs()
    fs.paths.storyboard_dir.joinpath("scenes.json").write_text(
        '{"scenes":[{"scene_id":"SC01","start":0,"end":5,"image_prompt":"Abraão","action":"Abraão",'
        '"motion_requirement":"ARTICULATED","motion_reason":"gesto","hero":true,"hero_reason":"gesto"}]}',
        encoding="utf-8",
    )
    audio = tmp_path / "approved.wav"
    audio.write_bytes(b"approved audio")
    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "green").save(source)
    asset = FrozenAsset.approve(source, "qa", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze((asset,), FrozenAsset.approve(sheet, "qa", "TEST"), "qa", "TEST")
    director = DirectorAgent.__new__(DirectorAgent)
    director.config = get_config()

    pipeline = director.create_operational_pipeline(
        "TEST-EPX", approved_audio=FrozenAsset.approve(audio, "audio-qa", "TEST"),
        source_manifest=manifest, database=tmp_path / "control.db", endpoint="test-image",
        image_cost=Decimal(".02"), motion_routes=[qualified_route()], motion_policy={"mode": "TEST"},
    )

    assert pipeline.motion_inventory["items"][0]["route_id"] == "test-i2v"
