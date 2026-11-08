"""Actual ``AutonomousTwoGateRunner`` TEST throughput benchmark.

This deliberately exercises the public ``start`` and ``resume`` APIs.  It uses
only a bounded local fake transport with explicit asyncio latency; it does not
contact a provider, create a billing record, or measure production cost.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import shutil
import statistics
import time
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.autonomous import AutonomousPolicy, AutonomousTwoGateRunner
from src.hybrid.execution import Executor, Job, ProviderResult
from src.hybrid.planner import Config
from src.hybrid.standing_budget import StandingBudgetLedger

_POLICY = {
    "schema": 1,
    "version": "actual-runner-benchmark-v1",
    "mode": "TEST",
    "episode_cap_usd": "1.00",
    "month_cap_usd": "3.00",
    "experiment_cap_usd": "0.20",
    "allowed_providers": ["test-image"],
    "publication": {
        "enabled": False,
        "channel": "test-only-benchmark",
        "visibility": "private",
    },
}
_PACKET = {"benchmark": "actual-autonomous-two-gate-runner-v1", "mode": "TEST"}


def _manifest(inputs: Path) -> Manifest:
    reference = inputs / "reference.png"
    if not reference.exists():
        Image.new("RGB", (16, 16), "green").save(reference)
    frozen = FrozenAsset.approve(reference, "benchmark-fixture", "TEST")
    sheet = inputs / "sheet.png"
    if not sheet.exists():
        contact_sheet((frozen,), sheet)
    return Manifest.freeze(
        (frozen,),
        FrozenAsset.approve(sheet, "benchmark-fixture", "TEST"),
        "benchmark-fixture",
        "TEST",
    )


def _jobs(inputs: Path) -> list[Job]:
    assets = _manifest(inputs)
    return [
        Job(
            scene,
            "first",
            "TEST",
            "test-image",
            {"prompt": f"benchmark-{scene}"},
            assets,
            Decimal("0.10"),
        )
        for scene in ("slow", "fast")
    ]


def _ledger(root: Path, policy: AutonomousPolicy) -> StandingBudgetLedger:
    return StandingBudgetLedger(
        root / "shared-budget.sqlite3",
        policy_sha256=policy.digest,
        episode_limit_usd=policy.episode_cap_usd,
        month_limit_usd=policy.month_cap_usd,
        experiment_limit_usd=policy.experiment_cap_usd,
        mode="TEST",
    )


class _LatencyFixtureTransport:
    """Bounded TEST transport which records actual calls and monotonic events."""

    mode = "TEST"

    def __init__(self, root: Path, started: float, latency_s: dict[str, float]):
        self.root = root
        self.started = started
        self.latency_s = latency_s
        self.calls = 0
        self.active = 0
        self.max_inflight = 0
        self.submit_started_s: dict[str, float] = {}
        self.submit_completed_s: dict[str, float] = {}

    def _now(self) -> float:
        return time.monotonic() - self.started

    async def submit(self, job, request_id, checkpoint):
        self.calls += 1
        self.active += 1
        self.max_inflight = max(self.max_inflight, self.active)
        self.submit_started_s[job.scene] = self._now()
        try:
            checkpoint(provider_id="TEST-BENCHMARK-" + request_id)
            await asyncio.sleep(self.latency_s[job.scene])
            result = self.root / f"{request_id}.bin"
            result.write_bytes(job.scene.encode("utf-8"))
            return ProviderResult(result, job.cost)
        finally:
            self.submit_completed_s[job.scene] = self._now()
            self.active -= 1

    async def recover(self, *args, **kwargs):
        raise AssertionError("completed TEST benchmark requests must not recover")


def _qa(receipt: dict[str, Any], started: float, times: dict[str, float]) -> dict[str, Any]:
    times[receipt["scene"]] = time.monotonic() - started
    return {
        "approved": True,
        "reviewer": "benchmark-independent-qa",
        "reviewer_identity": {"subject": "benchmark-qa", "issuer": "TEST"},
        "request_id": receipt["request_id"],
        "result_sha256": receipt["result_sha256"],
        "codes": [],
    }


async def _sample(
    root: Path,
    inputs: Path,
    *,
    provider_concurrency: int,
    fake_latency_s: dict[str, float],
) -> dict[str, Any]:
    policy = AutonomousPolicy.from_dict(_POLICY)
    jobs = _jobs(inputs)
    runner = AutonomousTwoGateRunner(
        root / "runner",
        "TEST-RUNNER-BENCHMARK",
        policy,
        Executor(root / "executor.sqlite3", Config(limit=Decimal("1.00"), concurrency=2)),
        _ledger(root, policy),
        provider_concurrency=provider_concurrency,
        cpu_concurrency=1,
    )
    runner.start(_PACKET, jobs)
    started = time.monotonic()
    transport = _LatencyFixtureTransport(root / "provider-results", started, fake_latency_s)
    transport.root.mkdir(parents=True, exist_ok=True)
    qa_times: dict[str, float] = {}
    completed = await runner.resume(
        transport, lambda receipt: _qa(receipt, started, qa_times)
    )
    wall_time_s = time.monotonic() - started
    assert completed["state"] == "MEDIA_PENDING"

    # A fresh runner is required to prove persisted exact-hash completed work is
    # reused through the public resume API, rather than through in-memory state.
    cached_transport = _LatencyFixtureTransport(
        root / "cached-transport", time.monotonic(), fake_latency_s
    )
    cached = AutonomousTwoGateRunner(
        root / "runner",
        "TEST-RUNNER-BENCHMARK",
        policy,
        Executor(root / "executor.sqlite3", Config(limit=Decimal("1.00"), concurrency=2)),
        _ledger(root, policy),
        provider_concurrency=provider_concurrency,
        cpu_concurrency=1,
    )
    cached_document = await cached.resume(
        cached_transport,
        lambda _receipt: (_ for _ in ()).throw(AssertionError("QA must be cached")),
    )
    assert cached_document["state"] == "MEDIA_PENDING"

    # Changed completed bytes invalidate the prior QA.  The actual runner must
    # fail closed and never blindly submit a fresh provider request.
    receipt = cached.executor.inspect(jobs[0].request_id)
    Path(receipt["result"]).write_bytes(b"tampered-benchmark-asset")
    tamper_transport = _LatencyFixtureTransport(
        root / "tamper-transport", time.monotonic(), fake_latency_s
    )
    tampered = AutonomousTwoGateRunner(
        root / "runner",
        "TEST-RUNNER-BENCHMARK",
        policy,
        Executor(root / "executor.sqlite3", Config(limit=Decimal("1.00"), concurrency=2)),
        _ledger(root, policy),
        provider_concurrency=provider_concurrency,
        cpu_concurrency=1,
    )
    try:
        await tampered.resume(tamper_transport, lambda _receipt: {})
    except ValueError as error:
        if "persisted QA" not in str(error):
            raise
    else:
        raise AssertionError("tampered completed asset must reject persisted QA")

    queue_wait_s = {
        scene: transport.submit_started_s[scene] for scene in sorted(transport.submit_started_s)
    }
    return {
        "provider_concurrency": provider_concurrency,
        "wall_time_s": wall_time_s,
        "queue_wait_s": queue_wait_s,
        "max_inflight": transport.max_inflight,
        "submit_count": transport.calls,
        "qa_times_s": dict(sorted(qa_times.items())),
        "qa_before_unrelated_slow_end": (
            qa_times.get("fast", float("inf")) < transport.submit_completed_s.get("slow", 0.0)
        ),
        "exact_hash_resume_submit_count": cached_transport.calls,
        "changed_asset_invalidates_cached_qa": tampered._load()["state"] == "REJECTED",
        "tamper_rejected_without_submit": tamper_transport.calls == 0,
    }


async def run_actual_runner_benchmark(
    root: Path,
    *,
    repeats: int = 3,
    fake_latency_s: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Run bounded TEST fixture measurements and persist a self-described JSON."""
    if type(repeats) is not int or not 1 <= repeats <= 10:
        raise ValueError("repeats must be a bounded integer from 1 through 10")
    # Default fixture latency keeps the bounded-concurrency signal larger than
    # local filesystem and scheduler noise; it remains TEST-only.
    latencies = fake_latency_s or {"fast": 0.5, "slow": 1.0}
    if set(latencies) != {"fast", "slow"} or any(
        type(value) not in {int, float} or value < 0 or not math.isfinite(value)
        for value in latencies.values()
    ):
        raise ValueError("fake latency requires finite nonnegative fast and slow seconds")
    root = Path(root)
    work_root = root / ".work"
    if work_root.exists():
        shutil.rmtree(work_root)
    inputs = work_root / "same-dag-input-assets"
    inputs.mkdir(parents=True, exist_ok=True)
    input_hashes = {
        path.name: sha256(path.read_bytes()).hexdigest()
        for path in (_manifest(inputs).assets[0].path, _manifest(inputs).sheet.path)
    }
    samples: dict[str, list[dict[str, Any]]] = {}
    dag_request_ids = None
    for concurrency in (1, 2):
        key = f"provider_concurrency_{concurrency}"
        samples[key] = []
        for index in range(repeats):
            sample_root = work_root / key / f"run-{index + 1}"
            sample_root.mkdir(parents=True, exist_ok=True)
            sample = await _sample(
                sample_root,
                inputs,
                provider_concurrency=concurrency,
                fake_latency_s=latencies,
            )
            current_ids = tuple(job.request_id for job in _jobs(inputs))
            if dag_request_ids is None:
                dag_request_ids = current_ids
            elif current_ids != dag_request_ids:
                raise AssertionError("benchmark scenarios must use the same DAG")
            samples[key].append(sample)
    medians = {
        str(concurrency): {
            "wall_time_s": statistics.median(
                sample["wall_time_s"]
                for sample in samples[f"provider_concurrency_{concurrency}"]
            )
        }
        for concurrency in (1, 2)
    }
    result = {
        "schema": "autonomous-two-gate-runner-benchmark/v1",
        "mode": "TEST_ONLY_FAKE_PROVIDER_LATENCY",
        "measurement_scope": "LOCAL_MONOTONIC_WALL_TIME",
        "provider": "LOCAL_TEST_FIXTURE_TRANSPORT",
        "fake_provider_latency_s": dict(sorted(latencies.items())),
        "repeats": repeats,
        "same_dag_input_assets": True,
        "input_asset_sha256": input_hashes,
        "dag_request_ids": list(dag_request_ids or ()),
        "samples": samples,
        "median_by_provider_concurrency": medians,
        "real_cost_gain": "NOT_MEASURED",
        "production_claim": "NONE",
    }
    output = root / "actual_runner_benchmark.json"
    root.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(work_root)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result["result_path"] = str(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmarks/actual_runner_benchmark"))
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()
    result = asyncio.run(run_actual_runner_benchmark(args.output_dir, repeats=args.repeats))
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
