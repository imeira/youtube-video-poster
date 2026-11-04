import asyncio
from decimal import Decimal

import pytest

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.execution import Executor, ProviderResult
from src.hybrid.planner import Config


def source_manifest(tmp_path):
    from PIL import Image

    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "green").save(source)
    frozen = FrozenAsset.approve(source, "visual-qa", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((frozen,), sheet)
    return Manifest.freeze(
        (frozen,), FrozenAsset.approve(sheet, "visual-qa", "TEST"), "visual-qa", "TEST"
    )


def live_source_manifest(tmp_path):
    from PIL import Image

    source = tmp_path / "live-source.png"
    Image.new("RGB", (64, 64), "green").save(source)
    frozen = FrozenAsset.approve(source, "visual-qa", "LIVE")
    sheet = tmp_path / "live-sheet.png"
    contact_sheet((frozen,), sheet)
    return Manifest.freeze(
        (frozen,),
        FrozenAsset.approve(sheet, "visual-qa", "LIVE"),
        "visual-qa",
        "LIVE",
    )


class Provider:
    mode = "TEST"

    def __init__(self, root):
        self.root = root
        self.calls = []
        self.active = 0
        self.maximum = 0

    async def submit(self, job, request_id, checkpoint):
        self.calls.append(job)
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            checkpoint(provider_id=f"provider-{request_id}")
            await asyncio.sleep(0.01)
            result = self.root / f"{request_id}.png"
            result.write_bytes(request_id.encode())
            return ProviderResult(result, job.cost)
        finally:
            self.active -= 1

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        raise AssertionError("the test never recovers")


def test_compile_rejects_nonsemantic_or_overlapping_timestamps(tmp_path):
    from src.hybrid.compiled import CompiledEpisode, FrameSpec

    audio = tmp_path / "narration.wav"
    audio.write_bytes(b"approved narration")
    approved_audio = FrozenAsset.approve(audio, "audio-qa", "TEST")

    with pytest.raises(ValueError, match="semantic"):
        FrameSpec("R001", 0.0, 2.0, "", "Abraão olha o céu")
    with pytest.raises(ValueError, match="overlap"):
        CompiledEpisode.compile(
            "EP8",
            approved_audio,
            (
                FrameSpec("R001", 0.0, 2.0, "prompt 1", "ação 1"),
                FrameSpec("R002", 1.0, 3.0, "prompt 2", "ação 2"),
            ),
        )


def test_live_visual_qa_rejects_self_asserted_identity_before_executor(
    tmp_path, monkeypatch
):
    from src.hybrid.compiled import CompiledEpisode, FrameSpec, ProductionRun

    audio = tmp_path / "live-narration.wav"
    audio.write_bytes(b"approved narration")
    episode = CompiledEpisode.compile(
        "LIVE-EP",
        FrozenAsset.approve(audio, "audio-qa", "LIVE"),
        (FrameSpec("R001", 0.0, 2.0, "prompt", "action"),),
    )
    executor = Executor(tmp_path / "live-control.db", Config())
    run = ProductionRun(
        episode,
        executor,
        live_source_manifest(tmp_path),
        endpoint="flux",
        image_cost=Decimal(".02"),
    )
    job = run.baseline_jobs()[0]
    called = False

    def inspect(request_id):
        assert request_id == job.request_id
        return {
            "result_sha256": "a" * 64,
            "qa": True,
            "qa_reviewer_identity": {
                "subject": "claimed-reviewer",
                "issuer": "OPENROUTER",
            },
        }

    def qa(*_args, **_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(executor, "inspect", inspect)
    monkeypatch.setattr(executor, "qa", qa)

    with pytest.raises(ValueError, match="trusted reviewer evidence"):
        run.record_visual_qa(
            "R001",
            "a" * 64,
            True,
            "OpenRouter:untrusted-model:self-asserted-authority",
            reviewer_identity={"subject": "claimed-reviewer", "issuer": "OPENROUTER"},
        )

    assert called is False
    assert run.render_ready() is False


@pytest.mark.asyncio
async def test_compiled_episode_batches_baselines_and_releases_only_approved_heroes(
    tmp_path,
):
    from src.hybrid.compiled import CompiledEpisode, FrameSpec, ProductionRun

    audio = tmp_path / "narration.wav"
    audio.write_bytes(b"approved narration")
    episode = CompiledEpisode.compile(
        "EP8",
        FrozenAsset.approve(audio, "audio-qa", "TEST"),
        (
            FrameSpec("R001", 0.0, 2.0, "prompt 1", "Abraão olha o céu", hero=True),
            FrameSpec("R002", 2.0, 4.0, "prompt 2", "Sara escuta em silêncio"),
        ),
    )
    manifest = source_manifest(tmp_path)
    executor = Executor(tmp_path / "control.db", Config(concurrency=2))
    provider = Provider(tmp_path)
    run = ProductionRun(
        episode, executor, manifest, endpoint="flux", image_cost=Decimal(".02")
    )

    receipts = await run.dispatch_baselines(provider)

    assert set(receipts) == {"R001", "R002"}
    assert provider.maximum == 2
    assert all(job.payload["prompt"].startswith("prompt") for job in provider.calls)
    assert run.eligible_hero_jobs(Decimal(".26"), "seedance") == ()

    run.record_visual_qa(
        "R001",
        receipts["R001"]["result_sha256"],
        True,
        "independent-qa",
        reviewer_identity={"subject": "independent-qa", "issuer": "TEST"},
    )
    run.record_visual_qa(
        "R002",
        receipts["R002"]["result_sha256"],
        True,
        "independent-qa",
        reviewer_identity={"subject": "independent-qa", "issuer": "TEST"},
    )
    with pytest.raises(ValueError, match="immutable"):
        run.record_visual_qa(
            "R001",
            receipts["R001"]["result_sha256"],
            False,
            "other-qa",
            reviewer_identity={"subject": "other-qa", "issuer": "TEST"},
        )
    heroes = run.eligible_hero_jobs(Decimal(".26"), "seedance")

    assert len(heroes) == 1
    assert heroes[0].scene == "R001"
    assert run.render_ready() is False

    hero_receipt = await executor.run(heroes[0], provider)
    resumed = ProductionRun(
        episode, executor, manifest, endpoint="flux", image_cost=Decimal(".02")
    )
    assert resumed.eligible_hero_jobs(Decimal(".26"), "seedance") == ()
    run.record_visual_qa(
        "R001",
        hero_receipt["result_sha256"],
        True,
        "independent-qa",
        reviewer_identity={"subject": "independent-qa", "issuer": "TEST"},
    )
    assert run.render_ready() is True


@pytest.mark.asyncio
async def test_compiled_run_skips_imported_frames_and_rejects_consumed_frames(tmp_path):
    from src.hybrid.compiled import CompiledEpisode, FrameSpec, ProductionRun

    audio = tmp_path / "narration.wav"
    audio.write_bytes(b"approved narration")
    episode = CompiledEpisode.compile(
        "EP8",
        FrozenAsset.approve(audio, "audio-qa", "TEST"),
        (
            FrameSpec("R029", 0, 2, "imported", "ação"),
            FrameSpec("R030", 2, 4, "imported", "ação"),
            FrameSpec("R031", 4, 6, "blocked", "ação"),
            FrameSpec("R032", 6, 8, "new", "ação"),
        ),
    )
    provider = Provider(tmp_path)
    run = ProductionRun(
        episode,
        Executor(tmp_path / "control.db", Config()),
        source_manifest(tmp_path),
        endpoint="flux",
        image_cost=Decimal(".02"),
        imported_scenes={"R029", "R030"},
        blocked_scenes={"R029", "R030", "R031"},
    )

    receipts = await run.dispatch_baselines(provider)

    assert set(receipts) == {"R032"}
    assert [job.scene for job in provider.calls] == ["R032"]


def test_compiled_run_allows_one_remediation_only_after_hash_bound_rejection(tmp_path):
    from src.hybrid.compiled import CompiledEpisode, FrameSpec, ProductionRun

    audio = tmp_path / "narration.wav"
    audio.write_bytes(b"approved narration")
    episode = CompiledEpisode.compile(
        "EP8",
        FrozenAsset.approve(audio, "audio-qa", "TEST"),
        (FrameSpec("R001", 0, 2, "prompt", "ação"),),
    )
    run = ProductionRun(
        episode,
        Executor(tmp_path / "control.db", Config()),
        source_manifest(tmp_path),
        endpoint="flux",
        image_cost=Decimal(".02"),
    )

    with pytest.raises(ValueError, match="rejected"):
        run.remediation_job("R001", {"code": "ACTION"})


def test_renderer_refuses_compiled_run_without_all_qa_receipts(tmp_path):
    from src.hybrid.compiled import CompiledEpisode, FrameSpec, ProductionRun
    from src.hybrid.render import LocalRenderer

    audio = tmp_path / "narration.wav"
    audio.write_bytes(b"approved narration")
    episode = CompiledEpisode.compile(
        "EP8",
        FrozenAsset.approve(audio, "audio-qa", "TEST"),
        (FrameSpec("R001", 0, 2, "prompt", "ação"),),
    )
    run = ProductionRun(
        episode,
        Executor(tmp_path / "control.db", Config()),
        source_manifest(tmp_path),
        endpoint="flux",
        image_cost=Decimal(".02"),
    )

    with pytest.raises(ValueError, match="compiled QA"):
        LocalRenderer().render_compiled(
            run, (), None, None, None, tmp_path / "final.mkv"
        )
