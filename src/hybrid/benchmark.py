"""Deterministic offline benchmark for cold, warm, and correction image flow."""

from __future__ import annotations

import argparse
import asyncio
import tempfile
from decimal import Decimal
from pathlib import Path

from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, atomic_json, contact_sheet
from src.hybrid.cas import ContentAddressedStore
from src.hybrid.compiled import CompiledEpisode, FrameSpec, ProductionRun
from src.hybrid.execution import Executor, ProviderResult
from src.hybrid.planner import Config
from src.hybrid.throughput import DurableQueue


class _BenchmarkProvider:
    mode = "TEST"

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.submissions = 0
        self.active = 0
        self.maximum = 0

    async def submit(self, job, request_id, checkpoint):
        self.submissions += 1
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            checkpoint(provider_id=f"benchmark-{request_id}")
            await asyncio.sleep(0.001)
            output = self.root / f"{request_id}.png"
            Image.new("RGB", (1280, 720), "blue").save(output)
            return ProviderResult(output, job.cost)
        finally:
            self.active -= 1

    async def recover(self, *args):
        raise AssertionError("deterministic benchmark never recovers")


def _production(root: Path, *, workers: int, image_count: int, cas) -> ProductionRun:
    root.mkdir(parents=True, exist_ok=True)
    source = root / "reference.png"
    Image.new("RGB", (64, 64), "green").save(source)
    approved = FrozenAsset.approve(source, f"source-{root.name}", "TEST")
    sheet = root / "sheet.png"
    contact_sheet((approved,), sheet)
    manifest = Manifest.freeze(
        (approved,),
        FrozenAsset.approve(sheet, f"source-{root.name}", "TEST"),
        f"source-{root.name}",
        "TEST",
    )
    audio = root / "audio.wav"
    audio.write_bytes(b"deterministic approved audio")
    episode = CompiledEpisode.compile(
        "BENCHMARK",
        FrozenAsset.approve(audio, "audio-qa", "TEST"),
        tuple(
            FrameSpec(
                f"S{index:03}",
                float(index),
                float(index + 1),
                f"deterministic prompt {index}",
                f"deterministic action {index}",
            )
            for index in range(image_count)
        ),
    )
    executor = Executor(
        root / "jobs.db",
        Config(concurrency=workers),
        cas=cas,
    )
    return ProductionRun(
        episode,
        executor,
        manifest,
        endpoint="benchmark-image-v1",
        image_cost=Decimal(".02"),
    )


def _metrics(receipts, provider_submissions, maximum, queue: DurableQueue) -> dict:
    return {
        "cache_hits": sum(receipt.get("cache_hit") is True for receipt in receipts.values()),
        "jobs": len(receipts),
        "max_active_workers": maximum,
        "provider_submissions": provider_submissions,
        "queue_complete": sum(row["status"] == "COMPLETE" for row in queue.items()),
    }


async def _run(root: Path, *, workers: int, prefetch: int, image_count: int) -> dict:
    store = ContentAddressedStore(root / "cas")
    provider = _BenchmarkProvider(root / "provider-results")
    cold_run = _production(root / "cold", workers=workers, image_count=image_count, cas=store)

    cold_receipts = await cold_run.dispatch_baselines(provider, prefetch=prefetch)
    cold_queue = DurableQueue(
        cold_run.baseline_queue_path, workers=workers, prefetch=prefetch
    )
    cold = _metrics(
        cold_receipts,
        provider_submissions=provider.submissions,
        maximum=provider.maximum,
        queue=cold_queue,
    )

    for scene, receipt in cold_receipts.items():
        cold_run.record_visual_qa(scene, receipt["result_sha256"], False, "benchmark-qa")
    before_corrections = provider.submissions
    provider.maximum = 0
    correction_receipts = await cold_run.dispatch_remediation_wave(
        {
            f"S{index:03}": f"deterministic corrected prompt {index}"
            for index in range(image_count)
        },
        provider,
        prefetch=prefetch,
    )
    correction_queue = DurableQueue(
        cold_run.correction_queue_path, workers=workers, prefetch=prefetch
    )
    corrections = _metrics(
        correction_receipts,
        provider_submissions=provider.submissions - before_corrections,
        maximum=provider.maximum,
        queue=correction_queue,
    )

    warm_run = _production(root / "warm", workers=workers, image_count=image_count, cas=store)
    before_warm = provider.submissions
    provider.maximum = 0
    warm_receipts = await warm_run.dispatch_baselines(provider, prefetch=prefetch)
    warm_queue = DurableQueue(
        warm_run.baseline_queue_path, workers=workers, prefetch=prefetch
    )
    warm = _metrics(
        warm_receipts,
        provider_submissions=provider.submissions - before_warm,
        maximum=provider.maximum,
        queue=warm_queue,
    )

    return {
        "schema": 1,
        "cold": cold,
        "warm": warm,
        "corrections": corrections,
        "worker_budget": {
            "image_workers": workers,
            "prefetch": prefetch,
            "materialized_limit": workers + prefetch,
        },
    }


def run_benchmark(
    output: Path,
    *,
    workers: int = 3,
    prefetch: int = 2,
    image_count: int = 6,
) -> dict:
    """Execute the offline benchmark and write stable JSON without wall-clock noise."""
    if type(image_count) is not int or not 1 <= image_count <= 10:
        raise ValueError("image_count must fit the correction reserve (1..10)")
    with tempfile.TemporaryDirectory(prefix="hybrid-throughput-") as temporary:
        result = asyncio.run(
            _run(
                Path(temporary),
                workers=workers,
                prefetch=prefetch,
                image_count=image_count,
            )
        )
    atomic_json(output, result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--prefetch", type=int, default=2)
    parser.add_argument("--images", type=int, default=6)
    arguments = parser.parse_args(argv)
    run_benchmark(
        arguments.output,
        workers=arguments.workers,
        prefetch=arguments.prefetch,
        image_count=arguments.images,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
