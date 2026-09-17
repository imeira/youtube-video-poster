"""Executable acceptance harness for the public fresh-revision studio route."""
import asyncio
import json
import shutil
import subprocess
import sys
import time
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, atomic_json, sha256
from src.hybrid.revision import RevisionHarness, identity, read, semantic_timeline, validate_ep8_script
from src.hybrid.revision_fixtures import TestDependencies as Fixtures


MEDIA = pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="real FFmpeg required")


def planned(root, **kwargs):
    h = RevisionHarness(root, **kwargs)
    h.__enter__()
    plan = h.create_plan(mode="TEST", request="New EP8: Abraham and Sarah")
    h.approve_plan(plan["plan_hash"], "human-test-reviewer")
    return h


def cli(root, action, *args, ok=True):
    # Apply the repository's offline audit to the CLI subprocess too. This denies
    # sockets, legacy episode reads and secret-file reads, not just HTTP mocks.
    code = """
import sys, socket
from scripts.run_offline_tests import audit, local_socketpair
socket.socketpair = local_socketpair
sys.addaudithook(audit)
from src.hybrid.revision import main
raise SystemExit(main(sys.argv[1:]))
"""
    result = subprocess.run([sys.executable, "-c", code, action, "--workspace", str(root), *args],
                            capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert result.returncode == (0 if ok else 1), result.stdout + result.stderr
    return json.loads(result.stdout)


@MEDIA
def test_public_cli_offline_end_to_end_separate_gates_and_supersession(tmp_path):
    root = tmp_path / "studio"
    first = cli(root, "create-plan")
    assert cli(root, "run", ok=False)["status"] == "BLOCKED"
    cli(root, "approve-plan", "--plan-hash", "stale", "--reviewer", "human", ok=False)
    cli(root, "approve-plan", "--plan-hash", first["plan_hash"], "--reviewer", "human")
    ready = cli(root, "run")
    assert ready["status"] == "WAITING_THUMBNAIL_APPROVAL"
    artifacts = ready["artifacts"]
    control = read(root / "revision.json")
    assert set(control["stages"]) == {"script", "audio_storyboard", "images", "encode", "sidecars", "final_qa", "telegram_thumbnail", "telegram_video"}
    assert all(s["status"] == "COMPLETE" and s["elapsed_seconds"] >= 0 for s in control["stages"].values())
    assert control["stages"]["encode"]["result"]["render_invocations"] == 1
    assert control["stages"]["final_qa"]["result"]["mode"] == "TEST"
    assert control["stages"]["telegram_thumbnail"]["result"]["message_id"] != control["stages"]["telegram_video"]["result"]["message_id"]
    assert read(root / "r001/providers/workers.json")["maximum"] <= 3
    before = (root / "revision.json").read_bytes()
    assert cli(root, "status") == ready
    assert cli(root, "resume") == ready
    assert (root / "revision.json").read_bytes() == before
    cli(root, "approve", "--kind", "video", "--artifact-hash", artifacts["video"]["sha256"], "--reviewer", "human", ok=False)
    cli(root, "approve", "--kind", "thumbnail", "--artifact-hash", "stale", "--reviewer", "human", ok=False)
    thumb = cli(root, "approve", "--kind", "thumbnail", "--artifact-hash", artifacts["thumbnail"]["sha256"], "--reviewer", "human")
    assert thumb["status"] == "WAITING_VIDEO_APPROVAL"
    final = cli(root, "approve", "--kind", "video", "--artifact-hash", artifacts["video"]["sha256"], "--reviewer", "human")
    assert final["status"] == "WAITING_FINAL_APPROVAL" and final["publication_authorized"] is False
    assert cli(root, "resume") == final
    rejected = cli(root, "reject", "--reason", "new artistic direction")
    assert rejected["revision"] == 2 and rejected["approvals"] == {}
    retired = read(root / "revision.json")["history"][0]
    assert retired["status"] == "SUPERSEDED" and retired["artifacts"] == artifacts
    cli(root, "approve-plan", "--plan-hash", first["plan_hash"], "--reviewer", "human", ok=False)
    cli(root, "approve-plan", "--plan-hash", rejected["plan_hash"], "--reviewer", "human")
    second = cli(root, "run")
    assert second["status"] == "WAITING_THUMBNAIL_APPROVAL"
    assert all(second["artifacts"][k]["sha256"] != artifacts[k]["sha256"] for k in artifacts)
    assert all(sha256(a["path"]) == a["sha256"] for a in artifacts.values())


def test_lock_plan_tampering_and_input_bindings(tmp_path):
    with RevisionHarness(tmp_path) as h:
        plan = h.create_plan(mode="TEST", request="Fresh EP8")
        with pytest.raises(ValueError, match="busy"):
            with RevisionHarness(tmp_path):
                pass
        with pytest.raises(ValueError, match="reviewer"):
            h.approve_plan(plan["plan_hash"], "")
        data = read(h.path)
        data["plan"]["image_workers"] = 7
        atomic_json(h.path, data)
        with pytest.raises(ValueError, match="plan hash"):
            h.status()
        data["plan"]["image_workers"] = 3
        atomic_json(h.path, data)
        Path(next(iter(data["plan"]["bindings"]))).write_bytes(b"tampered")
        with pytest.raises(ValueError, match="input binding"):
            h.approve_plan(plan["plan_hash"], "human")


@pytest.mark.parametrize("workers", [0, -1, 9, True])
def test_worker_limits_fail_before_plan(tmp_path, workers):
    with RevisionHarness(tmp_path) as h, pytest.raises(ValueError, match="worker"):
        h.create_plan(mode="TEST", request="Fresh EP8", image_workers=workers)


@MEDIA
def test_image_crash_recovers_without_resubmission_and_completed_tamper_blocks(tmp_path):
    h = planned(tmp_path, test_options={"crash_image_once": True})
    try:
        with pytest.raises(RuntimeError, match="injected image crash"):
            asyncio.run(h.run())
        recovered = Fixtures(tmp_path / "r001/providers")
        original = recovered.submit
        calls = []

        async def submit(job, request_id, checkpoint):
            assert not (recovered.root / f"{request_id}.png").exists(), "ambiguous request was resubmitted"
            calls.append(request_id)
            return await original(job, request_id, checkpoint)

        recovered.submit = submit
        h.dependencies = recovered
        assert asyncio.run(h.run())["status"] == "WAITING_THUMBNAIL_APPROVAL"
        image = next((tmp_path / "r001/EP8/compiled/approved_images").glob("*.png"))
        image.write_bytes(b"tampered")
        with pytest.raises(ValueError, match="completed stage hash"):
            asyncio.run(h.run())
    finally:
        h.__exit__()


@MEDIA
def test_one_correction_wave_and_bounded_overlapping_qa(tmp_path):
    class Tracking(Fixtures):
        def __init__(self, root):
            super().__init__(root, {"reject_scene": "S001"})
            self.qa_active = self.qa_max = 0
            self.waves = []

        async def visual_qa(self, packet, scene, references):
            self.qa_active += 1
            self.qa_max = max(self.qa_max, self.qa_active)
            self.waves.append(packet["wave"])
            try:
                await asyncio.sleep(.01)
                return await super().visual_qa(packet, scene, references)
            finally:
                self.qa_active -= 1

    deps = Tracking(tmp_path / "r001/providers")
    h = planned(tmp_path, dependencies=deps)
    try:
        assert asyncio.run(h.run())["status"] == "WAITING_THUMBNAIL_APPROVAL"
        assert 1 < deps.qa_max <= 2 and deps.maximum <= 3
        assert deps.waves.count(1) == 1
        assert len(list((tmp_path / "r001/EP8/compiled/qa").glob("*.json"))) == len(deps.waves)
    finally:
        h.__exit__()


@MEDIA
def test_rejected_correction_never_renders(tmp_path):
    class Reject(Fixtures):
        async def visual_qa(self, *args):
            result = await super().visual_qa(*args)
            result["approved"] = False
            return result

    h = planned(tmp_path, dependencies=Reject(tmp_path / "r001/providers"))
    try:
        for _ in range(2):
            with pytest.raises(ValueError, match="one correction wave"):
                asyncio.run(h.run())
        assert not list(tmp_path.rglob("*.mp4"))
        assert len(list((tmp_path / "r001/providers").glob("*.png"))) == 8
    finally:
        h.__exit__()


@MEDIA
@pytest.mark.parametrize("durable", [False, True])
def test_interrupted_encode_requires_receipt_never_second_encode(tmp_path, monkeypatch, durable):
    import src.hybrid.production as production
    original = production.render_once
    h = planned(tmp_path)
    calls = []

    def encode(*args):
        calls.append(1)
        result = original(*args)
        if not durable:
            raise RuntimeError("crashed after encoding")
        return result

    monkeypatch.setattr(production, "render_once", encode)
    try:
        if not durable:
            with pytest.raises(RuntimeError, match="crashed"):
                asyncio.run(h.run())
            with pytest.raises(ValueError, match="ambiguous encode"):
                asyncio.run(h.run())
        else:
            asyncio.run(h.run())
            data = read(h.path)
            # Simulate the crash window after durable encode receipt, before stage commit.
            data["stages"] = {k: v for k, v in data["stages"].items() if k in {"script", "audio_storyboard", "images"}}
            data["stages"]["encode"] = {"status": "STARTED"}
            data["status"] = "READY"
            atomic_json(h.path, data)
            # Prevent re-delivery in this simulated crash: downstream files have no authority.
            # The encode itself must be recovered without invoking render_once again.
            with pytest.raises(ValueError, match="stop after recovered encode"):
                original_stage = h.stage

                async def stage(name, *args, **kwargs):
                    if name == "sidecars":
                        raise ValueError("stop after recovered encode")
                    return await original_stage(name, *args, **kwargs)

                h.stage = stage
                asyncio.run(h.run())
            assert read(h.path)["stages"]["encode"]["status"] == "COMPLETE"
        assert len(calls) == 1
    finally:
        h.__exit__()


@MEDIA
def test_ambiguous_telegram_never_resends(tmp_path):
    class LostResponse(Fixtures):
        sends = 0

        async def send_photo(self, *args):
            self.sends += 1
            await super().send_photo(*args)
            raise RuntimeError("lost Telegram response")

    deps = LostResponse(tmp_path / "r001/providers")
    h = planned(tmp_path, dependencies=deps)
    try:
        with pytest.raises(RuntimeError, match="lost Telegram"):
            asyncio.run(h.run())
        with pytest.raises(ValueError, match="ambiguous telegram_thumbnail"):
            asyncio.run(h.run())
        assert deps.sends == 1
        assert not (deps.root / "telegram-video.json").exists()
    finally:
        h.__exit__()


def test_rejected_bytes_and_recompressed_images_are_blocked(tmp_path):
    deps = Fixtures(tmp_path / "fixtures")
    old = tmp_path / "old.png"
    deps._draw(old, "old-seed")
    predecessors = tmp_path / "predecessors.json"
    atomic_json(predecessors, [identity(old)])
    with RevisionHarness(tmp_path / "new") as h:
        h.create_plan(mode="TEST", request="Fresh EP8", predecessors=predecessors)
        with pytest.raises(ValueError, match="byte reuse"):
            h.fresh(old)
        variant = tmp_path / "variant.jpg"
        Image.open(old).resize((320, 180)).save(variant, quality=85)
        with pytest.raises(ValueError, match="perceptual reuse"):
            h.fresh(variant)


def test_mode_isolation_and_live_missing_dependencies_fail_before_work(tmp_path):
    h = planned(tmp_path)
    try:
        deps = Fixtures(tmp_path / "wrong")
        deps.mode = "LIVE"
        h.dependencies = deps
        with pytest.raises(ValueError, match="mode mismatch"):
            asyncio.run(h.run())
        assert not h.control["stages"]
        h.dependencies = None
        plan = {**h.control["plan"], "mode": "LIVE"}
        with pytest.raises(ValueError, match="deployment adapter"):
            h.get_dependencies(plan, tmp_path)
        h.dependencies = deps
        deps.preflight = lambda plan: {}
        with pytest.raises(ValueError, match="preflight incomplete"):
            h.get_dependencies(plan, tmp_path)
        assert not list(tmp_path.rglob("script.json"))
    finally:
        h.__exit__()


def test_semantic_timestamps_require_exact_words_and_actions():
    script = {"segments": [dict(id="s", narration="One two.", source_refs=[], visual_action="Look up")]}
    words = [dict(word="One", start=0, end=.2), dict(word="two.", start=.2, end=.6)]
    scenes, _ = semantic_timeline(script, words, .6)
    assert scenes[0]["start"] == 0 and scenes[0]["end"] == .6
    words[1]["word"] = "different"
    with pytest.raises(ValueError, match="text mismatch"):
        semantic_timeline(script, words, .6)
    words[1]["start"] = float("nan")
    with pytest.raises(ValueError, match="invalid WordBoundary"):
        semantic_timeline(script, words, .6)


def test_fixture_revision_audio_is_deterministic_and_not_length_only(tmp_path):
    deps = Fixtures(tmp_path / "providers")
    hashes = []
    for revision in (1, 3, 1):
        script = asyncio.run(deps.author_script({"revision": revision}, {}))
        path = tmp_path / f"audio-{len(hashes)}.wav"
        asyncio.run(deps.synthesize(script["narration"], output_path=path))
        hashes.append(sha256(path))
    assert hashes[0] == hashes[2] and hashes[0] != hashes[1]


@pytest.mark.parametrize("text,reference", [
    ("Abraão olhou as estrelas.", "Gênesis 15:1-6"),
    ("Isaque nasceu.", "Gênesis 18:1-15"),
    ("Abrão confiou.", "Gênesis 21:1-6"),
])
def test_ep8_editorial_scope_blocks_invalid_script(text, reference):
    with pytest.raises(ValueError):
        validate_ep8_script({"segments": [dict(narration=text, source_refs=[reference])]})


class LiveSpy(Fixtures):
    """Injected LIVE interface spy, never a real production success fixture."""
    mode = "LIVE"
    prior_spend = Decimal("0")
    visual_license = "unit-test stub"
    failure = "price"

    def __init__(self, root):
        super().__init__(root)
        self.script_calls = self.image_calls = self.review_calls = 0

    def preflight(self, plan):
        keys = ("credentials", "script_authority", "tts_authority", "visual_qa_authority", "telegram", "current_prices", "budget")
        return {k: self.failure != "preflight" for k in keys}

    async def author_script(self, *args):
        self.script_calls += 1
        script = await super().author_script(*args)
        script.pop("evidence_mode")
        return script

    async def synthesize(self, *args, **kwargs):
        result = await super().synthesize(*args, **kwargs)
        result.metadata["boundary_source"] = "WordBoundary"
        return result

    def authorize(self, jobs, plan):
        from src.hybrid.execution import Authorization, Price
        return ({j.request_id: Authorization(j.request_id, "human", time.time()+60, j.cost) for j in jobs},
                {j.request_id: Price(j.endpoint, j.request_id, j.cost,
                 time.time() - 1 if self.failure == "price" else time.time()+60, "test quote") for j in jobs})

    async def submit(self, *args):
        self.image_calls += 1
        if self.failure == "visual":
            return await super().submit(*args)
        raise AssertionError("LIVE network must not be called by these negative tests")

    async def visual_qa(self, *args):
        self.review_calls += 1
        raise RuntimeError("lost visual QA response")


@MEDIA
@pytest.mark.parametrize("failure", ["preflight", "price", "contract", "budget", "visual"])
def test_live_preflight_price_and_budget_fail_closed(tmp_path, failure):
    refs_dir = tmp_path / "refs"
    refs_dir.mkdir()
    refs = Fixtures(tmp_path / "fixtures").canonical(refs_dir)
    original = Manifest.load(refs["manifest"])
    live = Manifest.freeze([FrozenAsset.approve(a.path, "human", "LIVE") for a in original.assets],
        FrozenAsset.approve(original.sheet.path, "human", "LIVE"), "human", "LIVE")
    live.save(refs["manifest"])
    atomic_json(tmp_path / "refs.json", refs)
    deps = LiveSpy(tmp_path / "run/r001/providers")
    deps.failure = failure
    if failure == "budget":
        deps.prior_spend = Decimal("5.9999")
    contract = dict(adapter="deployment:factory", endpoint=deps.endpoint, image_cost=str(deps.image_cost),
                    non_image_reserve=str(deps.prior_spend))
    atomic_json(tmp_path / "deployment.json", contract)
    with RevisionHarness(tmp_path / "run", dependencies=deps) as h:
        plan = h.create_plan(mode="LIVE", request="New EP8", references=tmp_path / "refs.json",
            deployment=tmp_path / "deployment.json", chat_id="explicit-test-destination")
        h.approve_plan(plan["plan_hash"], "human")
        if failure == "contract":
            deps.endpoint = "unapproved-endpoint"
        error = {"preflight": "preflight incomplete", "contract": "approved deployment contract",
                 "price": "fresh live price", "budget": "budget reservation", "visual": "lost visual QA"}[failure]
        with pytest.raises((ValueError, PermissionError, RuntimeError), match=error):
            asyncio.run(h.run())
        if failure == "visual":
            calls = (deps.image_calls, deps.review_calls)
            with pytest.raises(ValueError, match="ambiguous LIVE visual QA"):
                asyncio.run(h.run())
            assert (deps.image_calls, deps.review_calls) == calls
            assert deps.image_calls > 0 and deps.review_calls > 0
        else:
            assert deps.image_calls == 0
        if failure in {"preflight", "contract"}:
            assert deps.script_calls == 0
        assert not list((tmp_path / "run").rglob("*.mp4"))
