import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet, sha256
from src.hybrid.execution import Authorization, Executor, Job, Price, ProviderResult
from src.hybrid.planner import Config
from src.hybrid.revision import RevisionHarness, read


class FakeHeroProvider:
    mode = "TEST"

    def __init__(self, root, *, ambiguous=False, terminal_scene=""):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.ambiguous = ambiguous
        self.terminal_scene = terminal_scene
        self.posts = 0
        self.recovers = 0
        self.active = self.maximum = 0

    async def submit(self, job, request_id, checkpoint):
        self.posts += 1
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            if self.ambiguous:
                raise TimeoutError("unknown submit outcome")
            checkpoint(provider_id="remote-" + job.scene)
            if job.scene == self.terminal_scene:
                raise RuntimeError("terminal provider failure")
            await asyncio.sleep(0)
            output = self.root / f"{request_id}.mp4"
            output.write_bytes(b"hero-" + request_id.encode())
            return ProviderResult(output, Decimal("0.25"))
        finally:
            self.active -= 1

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        self.recovers += 1
        output = self.root / f"{request_id}.mp4"
        output.write_bytes(b"recovered-" + request_id.encode())
        return ProviderResult(output, Decimal("0.25"))


def _manifest(tmp_path):
    stills = []
    for scene in ("S001", "S002", "S003"):
        path = tmp_path / f"{scene}.png"
        Image.new("RGB", (16, 16), "navy").save(path)
        stills.append(FrozenAsset.approve(path, "test", "TEST"))
    sheet = tmp_path / "sheet.png"
    contact_sheet(stills, sheet)
    return Manifest.freeze(stills, FrozenAsset.approve(sheet, "test", "TEST"), "TEST", "TEST"), stills


def _plan():
    return {"mode": "TEST", "hero_plan": {"enabled": True, "endpoint": "runpod-test", "unit_cost_usd": "0.25"},
            "image_workers": 2}


def _live_plan():
    return {"mode": "LIVE", "hero_plan": {"enabled": True, "endpoint": "runpod-live", "unit_cost_usd": "0.25"},
            "image_workers": 2}


@pytest.mark.asyncio
async def test_hero_execution_persists_payload_checkpoint_receipt_and_clip_hashes(tmp_path):
    manifest, stills = _manifest(tmp_path)
    provider = FakeHeroProvider(tmp_path / "provider")
    h = RevisionHarness(tmp_path / "revision")
    scenes = [{"scene_id": "S001", "importance": "HIGH", "duration": 5, "motion_intent": "slow pan"},
              {"scene_id": "S002", "importance": "NORMAL", "duration": 5, "motion_intent": "skip"},
              {"scene_id": "S003", "importance": "CRITICAL", "duration": 5, "motion_intent": "slow reveal"}]

    result = await h.execute_heroes(_plan(), "approved-plan", manifest, dict(zip(("S001", "S002", "S003"), stills)), scenes, provider, tmp_path / "hero-manifest.json")

    saved = read(tmp_path / "hero-manifest.json")
    assert provider.posts == 2
    assert provider.maximum <= 2
    assert {entry["scene_id"] for entry in saved["receipts"]} == {"S001", "S003"}
    assert all(entry["provider_id"].startswith("remote-") and entry["clip_sha256"] for entry in saved["receipts"])
    assert all(set(entry["payload"]["input"]) == {"image", "prompt", "duration", "resolution", "aspect_ratio", "camera_fixed", "generate_audio"} for entry in saved["receipts"])
    assert result["clips"]["S001"].sha256 == saved["receipts"][0]["clip_sha256"]


@pytest.mark.asyncio
async def test_hero_recovery_uses_checkpoint_without_second_post(tmp_path):
    manifest, stills = _manifest(tmp_path)
    provider = FakeHeroProvider(tmp_path / "provider")
    target = tmp_path / "hero-manifest.json"
    payload = {"input": {"image": str(stills[0].path), "prompt": "slow pan", "duration": 5, "resolution": "720p", "aspect_ratio": "16:9", "camera_fixed": True, "generate_audio": False}}
    request_id = Job("S001", "hero", "TEST", "runpod-test", payload, manifest, Decimal("0.25")).request_id
    target.write_text(__import__("json").dumps({"schema_version": 2, "enabled": True, "plan_hash": "approved-plan", "endpoint": "runpod-test", "receipts": [{"scene_id": "S001", "request_id": request_id, "provider_id": "remote-S001", "status": "PENDING", "payload": payload}], "fallback_scenes": []}), encoding="utf-8")

    result = await h_execute(RevisionHarness(tmp_path / "revision"), _plan(), manifest, stills, provider, target)

    assert provider.posts == 0
    assert provider.recovers == 1
    assert result["clips"]["S001"].sha256 == sha256(provider.root / f"{request_id}.mp4")


async def h_execute(h, plan, manifest, stills, provider, target):
    return await h.execute_heroes(plan, "approved-plan", manifest, {"S001": stills[0]}, [{"scene_id": "S001", "importance": "HIGH", "duration": 5, "motion_intent": "slow pan"}], provider, target)


@pytest.mark.asyncio
async def test_hero_ambiguous_submit_blocks_resume_without_another_post(tmp_path):
    manifest, stills = _manifest(tmp_path)
    provider = FakeHeroProvider(tmp_path / "provider", ambiguous=True)
    target = tmp_path / "hero-manifest.json"
    h = RevisionHarness(tmp_path / "revision")
    with pytest.raises(TimeoutError):
        await h_execute(h, _plan(), manifest, stills, provider, target)
    with pytest.raises(ValueError, match="ambiguous hero"):
        await h_execute(h, _plan(), manifest, stills, provider, target)
    assert provider.posts == 1


@pytest.mark.asyncio
async def test_terminal_hero_failure_records_local_fallback_without_resubmit(tmp_path):
    manifest, stills = _manifest(tmp_path)
    provider = FakeHeroProvider(tmp_path / "provider", terminal_scene="S001")
    target = tmp_path / "hero-manifest.json"
    result = await h_execute(RevisionHarness(tmp_path / "revision"), _plan(), manifest, stills, provider, target)
    saved = read(target)
    assert result["clips"] == {}
    assert saved["fallback_scenes"] == ["S001"]
    assert saved["receipts"][0]["status"] == "FALLBACK_LOCAL"
    assert provider.posts == 1


@pytest.mark.asyncio
async def test_hero_manifest_replaces_provider_error_before_persistence(tmp_path):
    manifest, stills = _manifest(tmp_path)
    provider = FakeHeroProvider(tmp_path / "provider", terminal_scene="S001")
    target = tmp_path / "hero-manifest.json"

    async def leaking_submit(job, request_id, checkpoint):
        checkpoint(provider_id="remote-" + job.scene)
        raise RuntimeError("https://provider.invalid/clip?token=SIGNED Authorization: Bearer SECRET")

    provider.submit = leaking_submit
    await h_execute(RevisionHarness(tmp_path / "revision"), _plan(), manifest, stills, provider, target)

    persisted = target.read_text(encoding="utf-8")
    assert read(target)["receipts"][0]["terminal_reason"] == "PROVIDER_ERROR"
    for forbidden in ("https://", "SIGNED", "SECRET", "Authorization"):
        assert forbidden not in persisted


@pytest.mark.asyncio
async def test_live_hero_requires_exact_authorization_and_reserves_once_before_post(tmp_path):
    _, stills = _manifest(tmp_path)
    live_still = FrozenAsset.approve(stills[0].path, "human", "LIVE")
    sheet = tmp_path / "live-sheet.png"
    contact_sheet([live_still], sheet)
    live_manifest = Manifest.freeze([live_still], FrozenAsset.approve(sheet, "human", "LIVE"), "human", "LIVE")
    provider = FakeHeroProvider(tmp_path / "provider")
    provider.mode = "LIVE"
    executor = Executor(tmp_path / "heroes.db", Config(limit=Decimal("1")))
    calls = []

    def authorize(jobs, plan):
        calls.append(tuple(jobs))
        job = jobs[0]
        return ({job.request_id: Authorization(job.request_id, "human", 4_000_000_000, Decimal("0.25"))},
                {job.request_id: Price(job.endpoint, job.request_id, Decimal("0.25"), 4_000_000_000, "current-price")})

    baseline = Job("S001", "first", "LIVE", "runpod-live", {"input": "approved still"}, live_manifest, Decimal("0.25"))
    baseline_auth = Authorization(baseline.request_id, "human", 4_000_000_000, Decimal("0.25"))
    baseline_price = Price(baseline.endpoint, baseline.request_id, Decimal("0.25"), 4_000_000_000, "current-price")
    baseline_receipt = await executor.run(baseline, provider, baseline_auth, baseline_price)
    executor.qa(baseline.request_id, baseline_receipt["result_sha256"], True, "independent-qa")

    h = RevisionHarness(tmp_path / "revision")
    target = tmp_path / "hero-manifest.json"
    scene = {"scene_id": "S001", "importance": "HIGH", "duration": 5, "motion_intent": "slow pan"}
    await h.execute_heroes(_live_plan(), "approved-plan", live_manifest, {"S001": live_still}, [scene], provider,
                            target, executor=executor, authorize=authorize,
                            predecessors={"S001": baseline.request_id})
    await h.execute_heroes(_live_plan(), "approved-plan", live_manifest, {"S001": live_still}, [scene], provider,
                            target, executor=executor, authorize=authorize,
                            predecessors={"S001": baseline.request_id})
    assert provider.posts == 2  # one baseline and one authorized hero POST
    assert len(calls) == 1
    assert executor.inspect(calls[0][0].request_id)["charged"] == "0.25"


@pytest.mark.asyncio
async def test_live_hero_never_posts_without_authorization_or_budget_reservation(tmp_path):
    _, stills = _manifest(tmp_path)
    live_still = FrozenAsset.approve(stills[0].path, "human", "LIVE")
    sheet = tmp_path / "live-sheet.png"
    contact_sheet([live_still], sheet)
    live_manifest = Manifest.freeze([live_still], FrozenAsset.approve(sheet, "human", "LIVE"), "human", "LIVE")
    provider = FakeHeroProvider(tmp_path / "provider")
    provider.mode = "LIVE"
    h = RevisionHarness(tmp_path / "revision")
    scene = {"scene_id": "S001", "importance": "HIGH", "duration": 5, "motion_intent": "slow pan"}
    with pytest.raises(PermissionError, match="authorizer"):
        await h.execute_heroes(_live_plan(), "approved-plan", live_manifest, {"S001": live_still}, [scene], provider,
                                tmp_path / "heroes.json")
    assert provider.posts == 0
