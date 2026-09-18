import asyncio
import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.execution import Executor, Job, ProviderResult
from src.hybrid.planner import Config


def test_technical_image_qa_fails_closed_unless_png_rgb_1280x720(tmp_path):
    from src.hybrid.technical_qa import inspect_image

    valid = tmp_path / "valid.png"
    Image.new("RGB", (1280, 720), "blue").save(valid)
    assert inspect_image(valid)["technical_pass"] is True

    invalid_cases = (
        ("wrong-size.png", "RGB", (1279, 720), "PNG"),
        ("wrong-mode.png", "RGBA", (1280, 720), "PNG"),
        ("wrong-format.jpg", "RGB", (1280, 720), "JPEG"),
    )
    for name, mode, size, image_format in invalid_cases:
        candidate = tmp_path / name
        Image.new(mode, size, "blue").save(candidate, format=image_format)
        packet = inspect_image(candidate)
        assert packet["technical_pass"] is False
        assert packet["errors"]

    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    packet = inspect_image(corrupt)
    assert packet["technical_pass"] is False
    assert packet["format"] is None


@pytest.mark.asyncio
async def test_semantic_reviewer_is_blocked_until_exact_technical_contract_passes(tmp_path):
    from src.hybrid.compiled import OperationalPipeline, compile_storyboard

    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "green").save(source)
    frozen = FrozenAsset.approve(source, "source-qa", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((frozen,), sheet)
    manifest = Manifest.freeze(
        (frozen,), FrozenAsset.approve(sheet, "source-qa", "TEST"), "source-qa", "TEST"
    )
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    episode = compile_storyboard(
        "EP8",
        FrozenAsset.approve(audio, "audio-qa", "TEST"),
        [{"scene_id": "S1", "start": 0, "end": 1, "image_prompt": "sky", "action": "look"}],
    )

    class InvalidProvider:
        mode = "TEST"

        async def submit(self, job, request_id, checkpoint):
            checkpoint(provider_id=f"provider-{request_id}")
            result = tmp_path / f"{request_id}.png"
            Image.new("RGB", (64, 64), "blue").save(result)
            return ProviderResult(result, job.cost)

        async def recover(self, *args):
            raise AssertionError("unexpected recovery")

    pipeline = OperationalPipeline(
        episode,
        Executor(tmp_path / "jobs.db", Config()),
        manifest,
        workspace=tmp_path / "workspace",
        endpoint="flux",
        image_cost=Decimal(".02"),
    )
    semantic_called = False

    async def semantic_review(_receipt):
        nonlocal semantic_called
        semantic_called = True

    receipts = await pipeline.dispatch_baselines(
        InvalidProvider(), on_completed=semantic_review
    )

    assert semantic_called is False
    job = pipeline.baseline_jobs()[0]
    receipt = receipts["S1"]
    assert pipeline.run.executor.inspect(job.request_id)["qa"] is False
    packet = pipeline.prepare_qa_packets()["S1"]
    assert packet["technical_pass"] is False
    with pytest.raises(ValueError, match="technical QA"):
        pipeline.record_visual_qa("S1", receipt["result_sha256"], True, "semantic-reviewer")
    assert pipeline.run.executor.inspect(receipt["request_id"])["qa"] is False
    correction = pipeline.run.remediation_job("S1", "repair technical contract")
    assert correction.predecessor == receipt["request_id"]


def test_cas_key_is_path_independent_and_reuses_exact_bytes_and_normalized_parameters(tmp_path):
    from src.hybrid.cas import ContentAddressedStore, content_key

    first_workspace = tmp_path / "one"
    second_workspace = tmp_path / "two"
    first_workspace.mkdir()
    second_workspace.mkdir()
    first = first_workspace / "reference.png"
    second = second_workspace / "renamed.png"
    first.write_bytes(b"identical immutable bytes")
    second.write_bytes(first.read_bytes())
    parameters_a = {"seed": 7, "resolution": [1280, 720], "guidance": Decimal("1.500")}
    parameters_b = {"guidance": Decimal("1.5"), "resolution": (1280, 720), "seed": 7}

    key_a = content_key((first,), parameters_a)
    key_b = content_key((second,), parameters_b)
    assert key_a == key_b

    store = ContentAddressedStore(tmp_path / "shared-cas")
    generated = first_workspace / "generated.png"
    generated.write_bytes(b"provider result")
    stored = store.put(key_a, generated)
    restored = second_workspace / "restored.png"
    assert store.materialize(key_b, restored) is True
    assert restored.read_bytes() == generated.read_bytes()
    assert stored.read_bytes() == generated.read_bytes()

    second.write_bytes(b"one-byte-different!")
    assert content_key((second,), parameters_b) != key_a


def test_cas_never_overwrites_partially_published_immutable_object(tmp_path):
    from src.hybrid.cas import ContentAddressedStore, content_key

    store = ContentAddressedStore(tmp_path / "cas")
    key = content_key((b"input",), {"mode": "TEST"})
    target = store.object_path(key)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"first concurrent output")
    competing = tmp_path / "competing.bin"
    competing.write_bytes(b"different concurrent output")

    with pytest.raises(ValueError, match="collision"):
        store.put(key, competing)

    assert target.read_bytes() == b"first concurrent output"


@pytest.mark.asyncio
async def test_executor_reuses_cas_across_workspace_paths_without_provider_call(tmp_path):
    from src.hybrid.cas import ContentAddressedStore, job_content_key

    def workspace_job(root: Path, reviewer: str) -> Job:
        root.mkdir()
        reference = root / "reference.png"
        Image.new("RGB", (64, 64), "green").save(reference)
        asset = FrozenAsset.approve(reference, reviewer, "TEST")
        sheet = root / "sheet.png"
        contact_sheet((asset,), sheet)
        manifest = Manifest.freeze(
            (asset,), FrozenAsset.approve(sheet, reviewer, "TEST"), reviewer, "TEST"
        )
        return Job(
            scene="S1",
            category="first",
            mode="TEST",
            endpoint="image-model-v1",
            payload={
                "prompt": "same prompt",
                "seed": 11,
                "image_urls": [str(reference.resolve())],
            },
            manifest=manifest,
            cost=Decimal(".02"),
        )

    class CountingProvider:
        mode = "TEST"

        def __init__(self):
            self.calls = 0

        async def submit(self, job, request_id, checkpoint):
            self.calls += 1
            checkpoint(provider_id=f"provider-{request_id}")
            result = tmp_path / f"result-{self.calls}.png"
            Image.new("RGB", (1280, 720), "blue").save(result)
            return ProviderResult(result, job.cost)

        async def recover(self, *args):
            raise AssertionError("unexpected recovery")

    store = ContentAddressedStore(tmp_path / "shared-cas")
    provider = CountingProvider()
    first = workspace_job(tmp_path / "workspace-a", "reviewer-a")
    second = workspace_job(tmp_path / "workspace-b", "reviewer-b")

    cold = await Executor(tmp_path / "a.db", Config(), cas=store).run(first, provider)
    warm = await Executor(
        tmp_path / "b.db", Config(limit=Decimal(0)), cas=store
    ).run(second, provider)

    assert first.request_id != second.request_id
    assert provider.calls == 1
    assert cold["cache_hit"] is False
    assert warm["cache_hit"] is True
    assert warm["result_sha256"] == cold["result_sha256"]
    assert warm["actual_cost"] == "0.000"
    assert job_content_key(first) != job_content_key(replace(first, mode="LIVE"))


@pytest.mark.asyncio
async def test_executor_persists_cas_key_before_recovering_ambiguous_submission(tmp_path):
    from src.hybrid.cas import ContentAddressedStore, job_content_key

    reference = tmp_path / "reference.png"
    Image.new("RGB", (64, 64), "green").save(reference)
    asset = FrozenAsset.approve(reference, "source-qa", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze(
        (asset,), FrozenAsset.approve(sheet, "source-qa", "TEST"), "source-qa", "TEST"
    )
    job = Job(
        scene="S1",
        category="first",
        mode="TEST",
        endpoint="image-model-v1",
        payload={"prompt": "same prompt", "seed": 11},
        manifest=manifest,
        cost=Decimal(".02"),
    )

    class RecoveringProvider:
        mode = "TEST"

        def __init__(self):
            self.submissions = 0
            self.recoveries = 0

        async def submit(self, job, request_id, checkpoint):
            self.submissions += 1
            checkpoint(provider_id=f"provider-{request_id}")
            raise RuntimeError("ambiguous provider interruption")

        async def recover(self, job, request_id, provider_id, partial, checkpoint):
            self.recoveries += 1
            output = tmp_path / f"recovered-{request_id}.png"
            Image.new("RGB", (1280, 720), "blue").save(output)
            return ProviderResult(output, job.cost)

    database = tmp_path / "jobs.db"
    store = ContentAddressedStore(tmp_path / "cas")
    provider = RecoveringProvider()
    with pytest.raises(RuntimeError, match="ambiguous provider interruption"):
        await Executor(database, Config(), cas=store).run(job, provider)

    receipt = await Executor(database, Config(), cas=store).run(job, provider)

    assert provider.submissions == 1
    assert provider.recoveries == 1
    assert receipt["status"] == "COMPLETE"
    assert receipt["cache_key"] == job_content_key(job)
    assert store.get(receipt["cache_key"]) is not None


def test_durable_queue_is_bounded_fifo_with_prefetch_and_closed_states(tmp_path):
    from src.hybrid.throughput import DurableQueue

    queue = DurableQueue(tmp_path / "queue.db", workers=2, prefetch=1)
    assert queue.enqueue("first", {"scene": "S1"}, priority=5)["status"] == "QUEUED"
    assert queue.enqueue("second", {"scene": "S2"}, priority=5)["status"] == "QUEUED"
    assert queue.enqueue("third", {"scene": "S3"}, priority=5)["status"] == "QUEUED"
    with pytest.raises(RuntimeError, match="prefetch"):
        queue.enqueue("fourth", {"scene": "S4"}, priority=5)

    first = queue.claim("worker-1")
    second = queue.claim("worker-2")
    assert [first["id"], second["id"]] == ["first", "second"]
    assert queue.claim("worker-3") is None
    queue.complete("first", {"result": "ok"})
    queue.fail("second", "provider failed")
    assert queue.enqueue("fourth", {"scene": "S4"}, priority=5)["status"] == "QUEUED"

    reopened = DurableQueue(tmp_path / "queue.db", workers=2, prefetch=1)
    states = {row["id"]: row["status"] for row in reopened.items()}
    assert states == {
        "first": "COMPLETE",
        "second": "FAILED",
        "third": "QUEUED",
        "fourth": "QUEUED",
    }
    assert reopened.claim("worker-1")["id"] == "third"


@pytest.mark.asyncio
async def test_bounded_wave_returns_persisted_complete_result_without_rerunning_handler(tmp_path):
    from src.hybrid.throughput import DurableQueue, run_bounded_wave

    database = tmp_path / "completed.db"
    first_queue = DurableQueue(database, workers=1)
    first_calls = 0

    async def first_handler(item):
        nonlocal first_calls
        first_calls += 1
        return {"result_sha256": "a" * 64, "actual_cost": "0.12", "payload": item}

    expected = {"result_sha256": "a" * 64, "actual_cost": "0.12", "payload": "work"}
    assert await run_bounded_wave(first_queue, (("request-1", "work"),), first_handler) == {"request-1": expected}
    assert first_calls == 1

    second_queue = DurableQueue(database, workers=1)

    async def should_not_run(_item):
        raise AssertionError("COMPLETE queue item must return its durable result")

    assert await run_bounded_wave(second_queue, (("request-1", "work"),), should_not_run) == {"request-1": expected}
    assert second_queue.inspect("request-1")["result"]["handler_result"] == expected


def test_failed_queue_item_cannot_requeue_beyond_materialized_limit(tmp_path):
    from src.hybrid.throughput import DurableQueue

    queue = DurableQueue(tmp_path / "capacity.db", workers=1, prefetch=0)
    queue.enqueue("failed", {"scene": "S1"})
    queue.claim("worker-1")
    queue.fail("failed", "provider failed")
    queue.enqueue("queued", {"scene": "S2"})

    with pytest.raises(RuntimeError, match="capacity"):
        queue.requeue_failed("failed")


@pytest.mark.asyncio
async def test_bounded_wave_cancellation_stops_workers_and_requeues_claims(tmp_path):
    from src.hybrid.throughput import DurableQueue, run_bounded_wave

    queue = DurableQueue(tmp_path / "queue.db", workers=2)
    started = asyncio.Event()
    release = asyncio.Event()
    active = 0

    async def handler(_item):
        nonlocal active
        active += 1
        if active == 2:
            started.set()
        try:
            await release.wait()
        finally:
            active -= 1

    wave = asyncio.create_task(
        run_bounded_wave(queue, (("first", "first"), ("second", "second")), handler)
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    wave.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await wave
        assert active == 0
        assert [row["status"] for row in queue.items()] == ["QUEUED", "QUEUED"]
    finally:
        release.set()
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_bounded_wave_child_cancellation_cleans_siblings_and_claims(tmp_path):
    from src.hybrid.throughput import DurableQueue, run_bounded_wave

    queue = DurableQueue(tmp_path / "child-cancel.db", workers=2)
    sibling_started = asyncio.Event()
    sibling_active = False

    async def handler(item):
        nonlocal sibling_active
        if item == "cancel":
            await sibling_started.wait()
            raise asyncio.CancelledError
        sibling_active = True
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            sibling_active = False

    with pytest.raises(asyncio.CancelledError):
        await run_bounded_wave(
            queue,
            (("cancel", "cancel"), ("sibling", "sibling")),
            handler,
        )

    assert sibling_active is False
    assert [row["status"] for row in queue.items()] == ["QUEUED", "QUEUED"]


@pytest.mark.asyncio
async def test_overlapping_waves_do_not_steal_durable_claims(tmp_path):
    from src.hybrid.throughput import DurableQueue, run_bounded_wave

    queue = DurableQueue(tmp_path / "overlap.db", workers=1)
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def first_handler(item):
        first_started.set()
        await release_first.wait()
        return {"value": item}

    async def second_handler(item):
        return {"value": item}

    first = asyncio.create_task(
        run_bounded_wave(queue, (("first", "one"),), first_handler)
    )
    await first_started.wait()
    second = asyncio.create_task(
        run_bounded_wave(queue, (("second", "two"),), second_handler)
    )
    await asyncio.sleep(0.02)
    assert second.done() is False
    release_first.set()

    assert await first == {"first": {"value": "one"}}
    assert await second == {"second": {"value": "two"}}
    assert [row["status"] for row in queue.items()] == ["COMPLETE", "COMPLETE"]


@pytest.mark.asyncio
async def test_six_corrections_run_as_one_concurrent_wave_under_worker_budget(tmp_path):
    from src.hybrid.compiled import CompiledEpisode, FrameSpec, ProductionRun
    from src.hybrid.observability import StructuredEventLog
    from src.hybrid.throughput import DurableQueue

    source = tmp_path / "source.png"
    Image.new("RGB", (64, 64), "green").save(source)
    asset = FrozenAsset.approve(source, "source-qa", "TEST")
    sheet = tmp_path / "sheet.png"
    contact_sheet((asset,), sheet)
    manifest = Manifest.freeze(
        (asset,), FrozenAsset.approve(sheet, "source-qa", "TEST"), "source-qa", "TEST"
    )
    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    episode = CompiledEpisode.compile(
        "EP8",
        FrozenAsset.approve(audio, "audio-qa", "TEST"),
        tuple(
            FrameSpec(f"S{index}", index, index + 1, f"prompt {index}", f"action {index}")
            for index in range(6)
        ),
    )

    class WaveProvider:
        mode = "TEST"

        def __init__(self):
            self.active = 0
            self.maximum = 0

        async def submit(self, job, request_id, checkpoint):
            checkpoint(provider_id=f"provider-{request_id}")
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            try:
                await asyncio.sleep(0.01)
                result = tmp_path / f"{request_id}.png"
                Image.new("RGB", (1280, 720), "blue").save(result)
                return ProviderResult(result, job.cost)
            finally:
                self.active -= 1

        async def recover(self, *args):
            raise AssertionError("unexpected recovery")

    events_path = tmp_path / "events.jsonl"
    executor = Executor(
        tmp_path / "jobs.db",
        Config(concurrency=2),
        events=StructuredEventLog(events_path),
    )
    run = ProductionRun(
        episode, executor, manifest, endpoint="flux", image_cost=Decimal(".02")
    )
    provider = WaveProvider()
    baselines = await run.dispatch_baselines(provider, prefetch=1)
    baseline_queue = DurableQueue(run.baseline_queue_path, workers=2, prefetch=1)
    assert [row["status"] for row in baseline_queue.items()] == ["COMPLETE"] * 6
    for scene, receipt in baselines.items():
        run.record_visual_qa(scene, receipt["result_sha256"], False, "reviewer")
    provider.maximum = 0

    corrections = await run.dispatch_remediation_wave(
        {f"S{index}": f"corrected prompt {index}" for index in range(6)},
        provider,
        prefetch=1,
    )

    assert set(corrections) == {f"S{index}" for index in range(6)}
    assert provider.maximum == 2
    queue = DurableQueue(run.correction_queue_path, workers=2, prefetch=1)
    assert [row["status"] for row in queue.items()] == ["COMPLETE"] * 6
    events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert sum(event["status"] == "COMPLETE" for event in events) == 12
    assert all(event["episode"] == "EP8" for event in events)
    assert all("prompt" not in event for event in events)


def test_structured_events_are_allowlisted_and_redact_secrets_and_signed_urls(tmp_path):
    from src.hybrid.observability import StructuredEventLog

    path = tmp_path / "events.jsonl"
    log = StructuredEventLog(path, clock=lambda: 123.5)
    event = log.emit(
        status="RUNNING",
        episode="EP8",
        revision="R2",
        scene="S1",
        stage="image_generation",
        provider="provider",
        model="model-v1",
        prompt="raw prompt must never be logged",
        prompt_hash="a" * 64,
        api_key="super-secret-key",
        result_url="https://provider.test/result?signature=signed-secret",
        error=(
            "GET https://provider.test/result?token=signed-secret "
            'Authorization: "Bearer FAKE_SECRET" '
            'Authorization: Basic BASIC_SECRET {"api_key": "JSON_SECRET"} '
            "{'token': 'PY_SECRET'}"
        ),
        resolution={
            "width": 1280,
            "nested": {"api_key": "NESTED_SECRET"},
        },
    )

    raw = path.read_text(encoding="utf-8")
    assert "raw prompt" not in raw
    assert "signed-secret" not in raw
    for secret in (
        "FAKE_SECRET",
        "BASIC_SECRET",
        "JSON_SECRET",
        "PY_SECRET",
        "NESTED_SECRET",
    ):
        assert secret not in raw
    assert "result_url" not in event
    assert "api_key" not in event
    assert event["prompt_hash"] == "a" * 64
    assert event["timestamp"] == 123.5
    assert json.loads(raw) == event


def test_structured_events_retry_short_os_writes_until_complete(tmp_path, monkeypatch):
    import src.hybrid.observability as observability

    path = tmp_path / "events.jsonl"
    real_write = observability.os.write
    calls = []

    def short_write(descriptor, payload):
        calls.append(len(payload))
        return real_write(descriptor, payload[:3])

    monkeypatch.setattr(observability.os, "write", short_write)
    observability.StructuredEventLog(path, clock=lambda: 1).emit(status="COMPLETE", episode="EP8")

    assert len(calls) > 1
    assert json.loads(path.read_text(encoding="utf-8")) == {"episode": "EP8", "status": "COMPLETE", "timestamp": 1}


def test_durable_queue_emits_structured_lifecycle_events(tmp_path):
    from src.hybrid.observability import StructuredEventLog
    from src.hybrid.throughput import DurableQueue

    events_path = tmp_path / "events.jsonl"
    events = StructuredEventLog(events_path, clock=lambda: 42)
    queue = DurableQueue(
        tmp_path / "queue.db",
        workers=1,
        prefetch=1,
        events=events,
        event_context={"episode": "EP8", "revision": "R2", "stage": "images"},
    )
    queue.enqueue(
        "request-1",
        {
            "scene": "S1",
            "provider": "image-v1",
            "prompt_hash": "b" * 64,
            "prompt": "must not be logged",
        },
    )
    queue.claim("worker-1")
    queue.complete("request-1", {"result_sha256": "c" * 64, "bytes": 123})

    records = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines()]
    assert [record["status"] for record in records] == ["QUEUED", "RUNNING", "COMPLETE"]
    assert all(record["episode"] == "EP8" for record in records)
    assert records[-1]["result_sha256"] == "c" * 64
    assert "must not be logged" not in events_path.read_text(encoding="utf-8")


def test_throughput_benchmark_writes_deterministic_cold_warm_and_correction_json(tmp_path):
    from src.hybrid.benchmark import run_benchmark

    output = tmp_path / "benchmark.json"
    first = run_benchmark(output, workers=2, prefetch=1, image_count=6)
    first_bytes = output.read_bytes()
    second = run_benchmark(output, workers=2, prefetch=1, image_count=6)

    assert second == first
    assert output.read_bytes() == first_bytes
    assert first["cold"] == {
        "cache_hits": 0,
        "jobs": 6,
        "max_active_workers": 2,
        "provider_submissions": 6,
        "queue_complete": 6,
    }
    assert first["warm"] == {
        "cache_hits": 6,
        "jobs": 6,
        "max_active_workers": 0,
        "provider_submissions": 0,
        "queue_complete": 6,
    }
    assert first["corrections"] == {
        "cache_hits": 0,
        "jobs": 6,
        "max_active_workers": 2,
        "provider_submissions": 6,
        "queue_complete": 6,
    }
    assert first["worker_budget"] == {"image_workers": 2, "materialized_limit": 3, "prefetch": 1}
