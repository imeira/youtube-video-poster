import asyncio
import time
from dataclasses import replace
from decimal import Decimal

import pytest

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.execution import Authorization, Executor, Job, Price, ProviderResult
from src.hybrid.planner import Config


def manifest(tmp_path, mode="TEST"):
    from PIL import Image

    asset = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), "green").save(asset)
    frozen = FrozenAsset.approve(asset, "reviewer", mode)
    sheet = tmp_path / "sheet.png"
    contact_sheet((frozen,), sheet)
    return Manifest.freeze(
        (frozen,), FrozenAsset.approve(sheet, "reviewer", mode), "reviewer", mode
    )


class Provider:
    mode = "TEST"

    def __init__(self, root):
        self.root = root
        self.calls = 0
        self.recoveries = 0
        self.active = 0
        self.maximum = 0
        self.crash = False

    async def submit(self, job, request_id, checkpoint):
        self.calls += 1
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            checkpoint(provider_id="provider-" + request_id)
            await asyncio.sleep(0.01)
            output = self.root / (request_id + ".txt")
            output.write_text("real test result", encoding="utf-8")
            checkpoint(partial=str(output))
            if self.crash:
                raise TimeoutError("interrupted after partial output")
            return ProviderResult(output, job.cost)
        finally:
            self.active -= 1

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        self.recoveries += 1
        assert provider_id or partial
        return ProviderResult(self.root / (request_id + ".txt"), job.cost)


def job(tmp_path, **changes):
    base = Job(
        "scene",
        "first",
        "TEST",
        "fal-ai/flux-2/klein/9b/edit",
        {"prompt": "A tree"},
        manifest(tmp_path),
        Decimal(".022"),
    )
    return replace(base, **changes)


def test_individual_freeze_and_contact_sheet_gate(tmp_path):
    item = manifest(tmp_path)
    item.verify("TEST")
    with pytest.raises(ValueError, match="mode"):
        item.verify("LIVE")
    item.assets[0].path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash"):
        item.verify("TEST")


@pytest.mark.asyncio
async def test_concurrency_stable_ids_and_receipts(tmp_path):
    executor = Executor(tmp_path / "jobs.db", Config(concurrency=2))
    provider = Provider(tmp_path)
    first = job(tmp_path)
    jobs = [replace(first, scene=str(i)) for i in range(6)]
    results = await asyncio.gather(*(executor.run(j, provider) for j in jobs + jobs))
    assert provider.calls == 6
    assert provider.maximum == 2
    assert all(r["mode"] == "TEST" and r["result_sha256"] for r in results)
    resumed = Executor(tmp_path / "jobs.db", Config(concurrency=2))
    assert await resumed.run(jobs[0], provider) == results[0]
    assert provider.calls == 6


@pytest.mark.asyncio
async def test_partial_recovery_never_resubmits(tmp_path):
    executor = Executor(tmp_path / "jobs.db", Config())
    provider = Provider(tmp_path)
    provider.crash = True
    request = job(tmp_path)
    with pytest.raises(TimeoutError):
        await executor.run(request, provider)
    provider.crash = False
    result = await Executor(tmp_path / "jobs.db", Config()).run(request, provider)
    assert result["request_id"] == request.request_id
    assert provider.calls == provider.recoveries == 1


@pytest.mark.asyncio
async def test_intent_exists_before_submit_and_unknown_intent_blocks(tmp_path):
    request = job(tmp_path)
    executor = Executor(tmp_path / "jobs.db", Config())
    provider = Provider(tmp_path)

    async def crash(job, request_id, checkpoint):
        assert executor.inspect(request_id)["status"] == "INTENT"
        raise TimeoutError("unknown remote acceptance")

    provider.submit = crash
    with pytest.raises(TimeoutError):
        await executor.run(request, provider)
    with pytest.raises(RuntimeError, match="reconciliation"):
        await executor.run(request, provider)
    assert provider.recoveries == 0


@pytest.mark.asyncio
async def test_live_requires_bound_authorization_and_fresh_price(tmp_path):
    request = job(tmp_path, mode="LIVE", manifest=manifest(tmp_path, "LIVE"))
    executor = Executor(tmp_path / "jobs.db", Config())
    provider = Provider(tmp_path)
    provider.mode = "LIVE"
    with pytest.raises(PermissionError):
        await executor.run(request, provider)
    price = Price(
        request.endpoint,
        request.request_id,
        request.cost,
        time.time() + 60,
        "official receipt",
    )
    auth = Authorization(request.request_id, "human", time.time() + 60, request.cost)
    with pytest.raises(PermissionError):
        await executor.run(request, provider, auth, replace(price, valid_until=0))
    with pytest.raises(PermissionError):
        await executor.run(request, provider, replace(auth, request_id="wrong"), price)
    with pytest.raises(PermissionError):
        await executor.run(replace(request, mode="TEST"), provider, auth, price)
    assert provider.calls == 0
    first_receipt = await executor.run(request, provider, auth, price)
    assert first_receipt["mode"] == "LIVE"
    expired_auth = replace(auth, valid_until=0)
    expired_price = replace(price, valid_until=0)
    assert (
        await executor.run(request, provider, expired_auth, expired_price)
        == first_receipt
    )
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_live_partial_recovery_does_not_require_new_submission_authority(
    tmp_path,
):
    request = job(tmp_path, mode="LIVE", manifest=manifest(tmp_path, "LIVE"))
    executor = Executor(tmp_path / "jobs.db", Config())
    provider = Provider(tmp_path)
    provider.mode = "LIVE"
    provider.crash = True
    price = Price(
        request.endpoint,
        request.request_id,
        request.cost,
        time.time() + 60,
        "official receipt",
    )
    auth = Authorization(request.request_id, "human", time.time() + 60, request.cost)
    with pytest.raises(TimeoutError):
        await executor.run(request, provider, auth, price)

    provider.crash = False
    receipt = await executor.run(request, provider)

    assert receipt["status"] == "COMPLETE"
    assert provider.calls == provider.recoveries == 1


@pytest.mark.asyncio
async def test_budget_reservation_is_atomic_and_durable(tmp_path):
    executor = Executor(tmp_path / "jobs.db", Config(limit=Decimal(".03")))
    request = job(tmp_path)
    provider = Provider(tmp_path)
    results = await asyncio.gather(
        executor.run(request, provider),
        executor.run(replace(request, scene="other"), provider),
        return_exceptions=True,
    )
    assert sum(isinstance(r, RuntimeError) for r in results) == 1
    assert provider.calls == 1
    with pytest.raises(RuntimeError, match="budget"):
        await Executor(tmp_path / "jobs.db", Config(limit=Decimal(".03"))).run(
            replace(request, scene="third"), provider
        )


@pytest.mark.asyncio
async def test_retries_require_exact_failed_qa_and_shared_reserve(tmp_path):
    executor = Executor(tmp_path / "jobs.db", Config())
    provider = Provider(tmp_path)
    request = job(tmp_path, category="hero", cost=Decimal(".26"))
    retry = replace(request, category="hero_retry", predecessor=request.request_id)
    with pytest.raises(ValueError, match="QA"):
        await executor.run(retry, provider)
    receipt = await executor.run(request, provider)
    executor.qa(request.request_id, receipt["result_sha256"], False, "reviewer")
    for _ in range(3):
        receipt = await executor.run(retry, provider)
        executor.qa(retry.request_id, receipt["result_sha256"], False, "reviewer")
        retry = replace(retry, predecessor=retry.request_id)
    with pytest.raises(RuntimeError, match="capacity"):
        await executor.run(retry, provider)
    assert provider.calls == 4


@pytest.mark.asyncio
async def test_receipt_tampering_and_actual_cost_overrun_block(tmp_path):
    executor = Executor(tmp_path / "jobs.db", Config())
    provider = Provider(tmp_path)
    request = job(tmp_path)
    receipt = await executor.run(request, provider)
    (tmp_path / (request.request_id + ".txt")).write_text("tamper")
    with pytest.raises(ValueError, match="hash"):
        await executor.run(request, provider)
    with pytest.raises(ValueError):
        executor.qa(request.request_id, receipt["result_sha256"], False, "reviewer")


@pytest.mark.asyncio
async def test_changed_first_pass_cannot_bypass_retry_qa(tmp_path):
    executor = Executor(tmp_path / "jobs.db", Config())
    provider = Provider(tmp_path)
    request = job(tmp_path)
    await executor.run(request, provider)
    with pytest.raises(ValueError, match="QA"):
        await executor.run(replace(request, payload={"prompt": "changed"}), provider)


@pytest.mark.asyncio
async def test_shared_database_concurrency_across_executors(tmp_path):
    provider = Provider(tmp_path)
    first = job(tmp_path)
    executors = [
        Executor(tmp_path / "jobs.db", Config(concurrency=2)) for _ in range(2)
    ]
    await asyncio.gather(
        *(
            executors[i % 2].run(replace(first, scene=str(i)), provider)
            for i in range(8)
        )
    )
    assert provider.maximum <= 2


@pytest.mark.asyncio
async def test_actual_overrun_is_persisted_and_blocks_further_work(tmp_path):
    executor = Executor(tmp_path / "jobs.db", Config())
    provider = Provider(tmp_path)
    request = job(tmp_path)
    submit = provider.submit

    async def overrun(*args):
        result = await submit(*args)
        return replace(result, actual_cost=Decimal(11))

    provider.submit = overrun
    with pytest.raises(RuntimeError, match="overrun"):
        await executor.run(request, provider)
    assert executor.inspect(request.request_id)["actual_cost"] == "11"
    with pytest.raises(RuntimeError, match="overrun"):
        await executor.run(replace(request, scene="other"), provider)
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_actual_overrun_is_persisted_even_when_result_is_missing(tmp_path):
    executor = Executor(tmp_path / "jobs.db", Config())
    provider = Provider(tmp_path)
    request = job(tmp_path)

    async def missing_result(job, request_id, checkpoint):
        checkpoint(provider_id="provider-" + request_id)
        return ProviderResult(tmp_path / "missing.txt", Decimal(11))

    provider.submit = missing_result
    with pytest.raises(RuntimeError, match="overrun"):
        await executor.run(request, provider)

    row = executor.inspect(request.request_id)
    assert row["status"] == "OVERRUN"
    assert row["actual_cost"] == "11"
    with pytest.raises(RuntimeError, match="overrun"):
        await executor.run(replace(request, scene="blocked"), provider)


def test_manifest_persistence_reuses_references_and_rejects_wrong_sheet(tmp_path):
    frozen = manifest(tmp_path)
    frozen.save(tmp_path / "manifest.json")
    assert Manifest.load(tmp_path / "manifest.json") == frozen
    from PIL import Image

    other = tmp_path / "other.png"
    Image.new("RGB", (64, 64), "blue").save(other)
    with pytest.raises(ValueError, match="binding"):
        Manifest.freeze(
            (FrozenAsset.approve(other, "human", "TEST"),),
            frozen.sheet,
            "human",
            "TEST",
        )
