"""Concrete LIVE contracts with synthetic credentials/transports; no remote I/O."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import importlib.machinery
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest
from PIL import Image

from src.hybrid import revision_live as live
from src.hybrid.artifacts import FrozenAsset, Manifest, atomic_json, contact_sheet, sha256
from src.hybrid.compiled import compile_storyboard, ProductionRun
from src.hybrid.execution import Executor
from src.hybrid.planner import Config
from src.hybrid.revision import RevisionHarness, read, semantic_timeline, validate_ep8_script


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(live.time, "time", lambda: 1000.0)
    for name in ("FAL_KEY", "TELEGRAM_BOT_TOKEN", "EP8_OPENROUTER_REVIEW_KEY"):
        monkeypatch.setenv(name, "synthetic-unit-test-placeholder")
    fal = ModuleType("fal_client")
    fal.__spec__ = importlib.machinery.ModuleSpec("fal_client", loader=None)
    monkeypatch.setitem(sys.modules, "fal_client", fal)
    monkeypatch.setattr(live.shutil, "which", lambda name: "mock-" + name)
    assets = []
    for name, color in (("abraham", "brown"), ("sarah", "teal")):
        path = tmp_path / (name + ".png")
        Image.new("RGB", (64, 64), color).save(path)
        assets.append(FrozenAsset.approve(path, "mock human canonical reviewer", "LIVE"))
    sheet = tmp_path / "sheet.png"
    contact_sheet(assets, sheet)
    manifest = Manifest.freeze(assets, FrozenAsset.approve(sheet, "mock human", "LIVE"), "mock human", "LIVE")
    manifest.save(tmp_path / "manifest.json")
    refs = dict(manifest=str(tmp_path / "manifest.json"), authority="mock human",
                characters=dict(zip(("abraham", "sarah"), [a.sha256 for a in assets])))
    atomic_json(tmp_path / "refs.json", refs)
    evidence = dict(authority="mock price reviewer", source_url="https://example.test/price-evidence",
                    observed_at=990, valid_until=2000)
    c = dict(schema_version=1, adapter=live.ADAPTER, endpoint=live.ENDPOINT, image_cost="0.022",
        non_image_reserve="0.50", visual_license="mock licensed original artwork",
        script=dict(sha256=sha256(live.SCRIPT), authority="mock human editor"),
        image_price=dict(**evidence, endpoint=live.ENDPOINT, amount="0.022", manifest_checksum=manifest.checksum,
            width=1280, height=720, num_images=1, output_format="png", includes_reference_inputs=True),
        tts=dict(provider="edge-tts", voice="pt-BR-ThalitaNeural", rate="-8%", pitch="+1Hz",
                 boundary="WordBoundary", cost="0", authority="mock deployment authority"),
        telegram=dict(chat_id="-12345", authority="mock destination owner", cost="0"),
        reviewer=dict(url=live.REVIEW_URL, key_env="EP8_OPENROUTER_REVIEW_KEY", model="mock/vision-v1",
            provider="mock-provider", authority="mock independent reviewer deployment",
            supports_images=True, supports_json_schema=True, context_tokens=8192, max_tokens=1024,
            price=dict(**evidence, prompt_per_million="1", completion_per_million="2",
                       image_per_item="0.001", request="0")))
    atomic_json(tmp_path / "deployment.json", c)
    with RevisionHarness(tmp_path / "run") as h:
        result = h.create_plan(mode="LIVE", request="New source-bound EP8", deployment=tmp_path / "deployment.json",
            references=tmp_path / "refs.json", chat_id="-12345")
        h.approve_plan(result["plan_hash"], "mock human plan approver")
    return tmp_path / "run", c, fal


def construct(deployment):
    root, _, _ = deployment
    return live.factory(root / "r001", read(root / "revision.json")["plan"])


def change_plan(deployment, change):
    from src.hybrid.artifacts import digest
    root, _, _ = deployment
    control = read(root / "revision.json")
    change(control["plan"])
    # Explicitly simulate a new human approval of the altered plan.
    control["plan_hash"] = digest(control["plan"])
    control["plan_approval"]["plan_hash"] = control["plan_hash"]
    atomic_json(root / "revision.json", control)


def compiled(deps):
    script = asyncio.run(deps.author_script(deps.plan, {}))
    words = [dict(word=w, start=i / 5, end=(i + 1) / 5) for i, w in enumerate(script["narration"].split())]
    duration = len(words) / 5
    scenes, _ = semantic_timeline(script, words, duration)
    audio = deps.root / "EP8/audio/narration.wav"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"synthetic non-playable timing fixture")
    timeline = audio.with_name("timeline.json")
    atomic_json(timeline, dict(words=words, duration=duration))
    episode = compile_storyboard("EP8", FrozenAsset.approve(audio, "mock boundary reviewer", "LIVE"), scenes)
    episode.save(deps.root / "EP8/compiled/compiled_episode.json")
    control_path = deps.root.parent / "revision.json"
    control = read(control_path)
    control["stages"]["audio_storyboard"] = dict(status="COMPLETE",
        outputs={str(p): sha256(p) for p in (audio, timeline)})
    atomic_json(control_path, control)
    executor = Executor(deps.root / "executor.sqlite3", Config(), prior_spend=deps.prior_spend)
    run = ProductionRun(episode, executor, Manifest.load(deps.plan["references"]["manifest"]),
                        endpoint=deps.endpoint, image_cost=deps.image_cost)
    return run, scenes


def test_dynamic_factory_constructs_real_components_and_script(deployment):
    root, _, _ = deployment
    with RevisionHarness(root) as harness:
        plan = harness.load()
        deps = harness.get_dependencies(plan, root / "r001")
    assert isinstance(deps.images, live.FalFluxProvider)
    assert isinstance(deps.tts, live.EdgeTTSProvider)
    assert isinstance(deps.messenger, live.TelegramNotificationProvider)
    assert deps.messenger.chat_id == "-12345"
    assert (deps.tts.voice, deps.tts.rate, deps.tts.pitch) == ("pt-BR-ThalitaNeural", "-8%", "+1Hz")
    script = asyncio.run(deps.author_script(plan, {}))
    validate_ep8_script(script)
    assert live.ScriptQAAgent().review(script).approved
    assert len(script["segments"]) == 25 and len(script["narration"].split()) >= 800
    assert all(s["visual_action"] and s["characters"] for s in script["segments"])
    assert script == asyncio.run(deps.author_script(plan, {}))


@pytest.mark.parametrize("key", ["FAL_KEY", "TELEGRAM_BOT_TOKEN", "EP8_OPENROUTER_REVIEW_KEY"])
def test_missing_credentials_before_any_provider_construction(deployment, monkeypatch, key):
    monkeypatch.delenv(key)
    monkeypatch.setattr(live, "FalFluxProvider", lambda *a: pytest.fail("constructed before preflight"))
    with pytest.raises(ValueError, match="credential"):
        construct(deployment)


@pytest.mark.parametrize("case", ["stale", "future", "long_expiry", "endpoint", "price", "reference", "chat", "script", "tts", "reviewer", "budget", "reserve", "missing"])
def test_bad_deployment_fails_offline_before_clients(deployment, monkeypatch, case):
    def mutate(plan):
        c = plan["deployment"]
        if case == "stale": c["image_price"]["valid_until"] = 999
        elif case == "future": c["reviewer"]["price"]["observed_at"] = 1001
        elif case == "long_expiry": c["image_price"]["valid_until"] = 999999
        elif case == "endpoint": c["endpoint"] = "fal-ai/wrong"
        elif case == "price": c["image_price"]["amount"] = "0.05"
        elif case == "reference": c["image_price"]["manifest_checksum"] = "a" * 64
        elif case == "chat": c["telegram"]["chat_id"] = "12346"
        elif case == "script": c["script"]["sha256"] = "b" * 64
        elif case == "tts": c["tts"]["boundary"] = "SentenceBoundary"
        elif case == "reviewer": c["reviewer"]["supports_images"] = False
        elif case == "budget": c["image_cost"] = c["image_price"]["amount"] = "0.20"
        elif case == "reserve": c["non_image_reserve"] = "0.01"
        elif case == "missing": del c["reviewer"]
    change_plan(deployment, mutate)
    monkeypatch.setattr(live, "FalFluxProvider", lambda *a: pytest.fail("constructed before preflight"))
    with pytest.raises(ValueError): construct(deployment)


def test_plan_approval_and_bound_asset_required(deployment):
    root, _, _ = deployment
    control = read(root / "revision.json")
    control.pop("plan_approval")
    atomic_json(root / "revision.json", control)
    with pytest.raises(ValueError, match="approval"): construct(deployment)


def test_authorize_exact_fresh_budget_and_resume(deployment):
    deps = construct(deployment)
    run, _ = compiled(deps)
    jobs = run.baseline_jobs()
    auth, prices = deps.authorize(jobs, deps.plan)
    assert set(auth) == set(prices) == {j.request_id for j in jobs}
    for job in jobs:
        assert auth[job.request_id].request_id == prices[job.request_id].request_id == job.request_id
        assert auth[job.request_id].maximum_cost == prices[job.request_id].amount == job.cost
        assert prices[job.request_id].endpoint == live.ENDPOINT
        assert 1000 < prices[job.request_id].valid_until <= 1300
    assert sum(a.maximum_cost for a in auth.values()) + deps.prior_spend <= Decimal(deps.plan["budget_usd"])
    assert deps.authorize(jobs, deps.plan) == (auth, prices)
    assert len(read(deps.root / "live_authorizations.json")["jobs"]) == 25
    for modified in (replace(jobs[0], cost=Decimal("0.001")), replace(jobs[0], endpoint="wrong"),
                     replace(jobs[0], payload={**jobs[0].payload, "prompt": "unapproved"}),
                     replace(jobs[0], mode="TEST")):
        with pytest.raises(ValueError, match="exact approved"): deps.authorize([modified], deps.plan)
    with pytest.raises(ValueError, match="unique"): deps.authorize([jobs[0], jobs[0]], deps.plan)
    ledger = read(deps.root / "live_authorizations.json")
    ledger["jobs"]["pending-request"] = "5.99"
    atomic_json(deps.root / "live_authorizations.json", ledger)
    with pytest.raises(ValueError, match="budget"): deps.authorize(jobs, deps.plan)


def response(r, scene="S001", checksum="a" * 64, verdict="PASS"):
    return dict(model=r["model"], usage={"cost": 0.001}, choices=[dict(finish_reason="stop", message=dict(
        content=json.dumps(dict(scene_id=scene, result_sha256=checksum, verdict=verdict, reason="All pixel checks satisfied"))))])


@pytest.mark.parametrize("verdict,approved", [("PASS", True), ("FAIL", False)])
def test_review_boolean_hash_contract(deployment, verdict, approved):
    r = deployment[1]["reviewer"]
    result = live.parse_review(response(r, verdict=verdict), "S001", "a" * 64, r, Decimal("0.02"))
    assert result["approved"] is approved and result["result_sha256"] == "a" * 64
    assert r["model"] in result["reviewer"]


@pytest.mark.parametrize("case", ["ambiguous", "string_bool", "hash", "fence", "duplicate", "truncated", "refusal", "choices", "cost", "model", "missing_cost", "null", "empty_reason"])
def test_review_rejects_ambiguous_malformed_or_unbound(deployment, case):
    r = deployment[1]["reviewer"]
    raw = response(r)
    message = raw["choices"][0]["message"]
    if case == "ambiguous": message["content"] = message["content"].replace('"PASS"', '"PASS/FAIL"')
    elif case == "string_bool": message["content"] = message["content"].replace('"PASS"', '"true"')
    elif case == "hash": message["content"] = message["content"].replace("a" * 64, "b" * 64)
    elif case == "fence": message["content"] = "```json\n" + message["content"] + "\n```"
    elif case == "duplicate": message["content"] = message["content"][:-1] + ',"verdict":"FAIL"}'
    elif case == "truncated": raw["choices"][0]["finish_reason"] = "length"
    elif case == "refusal": message["refusal"] = "Cannot inspect"
    elif case == "choices": raw["choices"].append(deepcopy(raw["choices"][0]))
    elif case == "cost": raw["usage"]["cost"] = 100
    elif case == "model": raw["model"] = "other/model"
    elif case == "missing_cost": raw.pop("usage")
    elif case == "null": raw = None
    elif case == "empty_reason": message["content"] = message["content"].replace("All pixel checks satisfied", "")
    with pytest.raises(ValueError, match="malformed"): live.parse_review(raw, "S001", "a" * 64, r, Decimal("0.02"))


def test_fal_exact_wire_submit_recovery_qa_and_independent_delivery(deployment, monkeypatch):
    deps = construct(deployment)
    run, scenes = compiled(deps)
    job = run.baseline_jobs()[0]
    auth, prices = deps.authorize([job], deps.plan)
    calls = []
    fal = deployment[2]
    fal.upload_file = lambda path: calls.append(("upload", path)) or "https://v3.fal.media/ref.png"
    fal.result = lambda endpoint, remote: calls.append(("get", remote)) or {"images": [{"url": "https://v3.fal.media/result.png"}]}
    deps.images.submitter = SimpleNamespace(submit_once=lambda endpoint, arguments:
        calls.append(("post", arguments)) or SimpleNamespace(request_id="remote-1"))
    deps.images.downloader = lambda url, stream: Image.new("RGB", (1280, 720), "orange").save(stream, format="PNG")
    async def exercise():
        receipt = await run.executor.run(job, deps.images, authorization=auth[job.request_id], price=prices[job.request_id])
        await deps.images.recover(job, job.request_id, "remote-1", receipt["result"], lambda **kw: None)
        assert len([c for c in calls if c[0] == "post"]) == 1
        wire = next(c[1] for c in calls if c[0] == "post")
        assert set(wire) == {"image_urls", "image_size", "prompt", "num_images", "output_format", "enable_safety_checker"}
        assert len(wire["image_urls"]) == 2 and wire["num_images"] == 1
        packet = dict(candidate_path=receipt["result"], result_sha256=receipt["result_sha256"])
        bodies = []
        def review(body):
            bodies.append(body)
            return response(deps.contract["reviewer"], checksum=packet["result_sha256"])
        monkeypatch.setattr(deps, "_request_review", review)
        result = await deps.visual_qa(packet, scenes[0], deps.plan["references"])
        assert result["approved"] is True
        assert len(bodies[0]["messages"][1]["content"]) == 4  # text + candidate + BOTH portraits
        assert bodies[0]["provider"]["allow_fallbacks"] is False
        assert bodies[0]["provider"]["max_price"] == {
            "prompt": "1", "completion": "2", "image": "0.001", "request": "0"
        }
        with pytest.raises(ValueError, match="ambiguous"):
            await deps.visual_qa(packet, scenes[0], deps.plan["references"])
        run.executor.qa(job.request_id, receipt["result_sha256"], False, "mock rejection")
        correction = run.remediation_job(job.scene, job.payload["prompt"] + live.CORRECTION)
        assert correction.request_id in deps.authorize([correction], deps.plan)[0]
    asyncio.run(exercise())
    sends = []
    def urlopen(request, **kwargs):
        sends.append(request.full_url.rsplit("/", 1)[-1])
        return __import__("io").BytesIO(json.dumps({"ok": True, "result": {"message_id": len(sends)}}).encode())
    monkeypatch.setattr(live.urllib.request, "urlopen", urlopen)
    media = deps.root / "dummy.mp4"
    media.write_bytes(b"offline fixture")
    photo = deps.root / "quarantine" / job.request_id / "result.png"
    assert asyncio.run(deps.messenger.send_photo("-12345", str(photo), "thumbnail")) == 1
    assert asyncio.run(deps.messenger.send_video("-12345", str(media), "video")) == 2
    assert sends == ["sendPhoto", "sendVideo"]


def test_review_network_failure_sanitized_and_not_retried(deployment, monkeypatch):
    deps = construct(deployment)
    def fail(*args, **kwargs): raise RuntimeError("secret must not escape")
    monkeypatch.setattr(live, "_opener", lambda: SimpleNamespace(open=fail))
    with pytest.raises(ValueError, match="reconciliation") as error:
        deps._request_review({})
    assert "secret must not escape" not in str(error.value)


def test_expiry_rechecked_and_canonical_tampering_blocks(deployment, monkeypatch):
    deps = construct(deployment)
    run, _ = compiled(deps)
    monkeypatch.setattr(live.time, "time", lambda: 2001)
    with pytest.raises(ValueError, match="non-expired"):
        deps.authorize(run.baseline_jobs(), deps.plan)
    monkeypatch.setattr(live.time, "time", lambda: 1000)
    bound = next(Path(path) for path in deps.plan["bindings"] if deps.root.parent.parent in Path(path).parents)
    bound.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="binding"):
        deps.preflight(deps.plan)


def test_preflight_requires_word_boundary_sdk(deployment, monkeypatch):
    import edge_tts
    monkeypatch.setattr(edge_tts, "Communicate", lambda text, voice: None)
    with pytest.raises(ValueError, match="WordBoundary"):
        construct(deployment)


def test_telegram_does_not_substitute_message_or_change_destination(deployment, monkeypatch):
    deps = construct(deployment)
    deps.root.mkdir(parents=True)
    video = deps.root / "oversized.mp4"
    with video.open("wb") as stream:
        stream.truncate(50 * 1024 * 1024 + 1)
    monkeypatch.setattr(live.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("unexpected Telegram I/O"))
    with pytest.raises(ValueError, match="upload contract"):
        asyncio.run(deps.messenger.send_video("-12345", str(video)))
    with pytest.raises(ValueError, match="destination"):
        asyncio.run(deps.messenger.send_photo("wrong-chat", "absent.png"))
