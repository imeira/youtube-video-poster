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
import os
import shutil
import statistics
import subprocess
import sys
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


_PUBLIC_CLI_LATENCIES = {"SLOW": 0.35, "FAST": 0.15}


def _public_cli_packet(root: Path) -> Path:
    """Build the existing two-scene TEST compiled fixture, with no provider."""
    from src.hybrid.autonomous_cli import compiled_policy_document
    from src.hybrid.compiled import compile_storyboard

    root.mkdir(parents=True, exist_ok=True)
    audio = root / "audio.wav"
    subprocess.run([
        "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
        "sine=frequency=440:sample_rate=48000:duration=2", "-ac", "2", str(audio),
    ], check=True, cwd=Path(__file__).resolve().parents[2])
    reference = root / "reference.png"
    Image.new("RGB", (64, 64), "navy").save(reference)
    asset = FrozenAsset.approve(reference, "public CLI benchmark TEST fixture", "TEST")
    sheet = root / "sheet.png"
    contact_sheet((asset,), sheet)
    Manifest.freeze((asset,), FrozenAsset.approve(sheet, "public CLI benchmark TEST fixture", "TEST"), "public CLI benchmark TEST fixture", "TEST").save(root / "manifest.json")
    compile_storyboard("TEST-PUBLIC-CLI-BENCHMARK", FrozenAsset.approve(audio, "public CLI benchmark TEST fixture", "TEST"), [
        {"scene_id": "SLOW", "start": 0, "end": 1, "image_prompt": "slow fixture sky", "action": "slow fixture sky"},
        {"scene_id": "FAST", "start": 1, "end": 2, "image_prompt": "fast fixture sky", "action": "fast fixture sky"},
    ]).save(root / "compiled.json")
    (root / "policy.json").write_text(json.dumps(compiled_policy_document(), sort_keys=True), encoding="utf-8")
    return root


def _public_cli_command(arguments: list[str], env: dict[str, str]) -> dict[str, Any]:
    started = time.monotonic()
    completed = subprocess.run([sys.executable, "-m", "src.cli.main", "next-video", *arguments], cwd=Path(__file__).resolve().parents[2], env=env, text=True, capture_output=True, check=False)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("public next-video CLI did not emit JSON") from error
    return {"argv": ["studio", "next-video", *arguments], "exit_code": completed.returncode, "wall_time_s": time.monotonic() - started, "stdout_sha256": sha256(completed.stdout.encode()).hexdigest(), "result": payload}


def _public_cli_events(
    workspace: Path, request_id: str
) -> tuple[dict[str, None], dict[str, float], dict[str, float], int, str]:
    event_file = workspace / "TC" / request_id[:16] / "provider" / "fixture-events.jsonl"
    events = [json.loads(line) for line in event_file.read_text(encoding="utf-8").splitlines() if line]
    clock_domains = {event.get("clock_domain") for event in events}
    if clock_domains != {"fixture-process-monotonic-relative/v1"}:
        raise AssertionError("public CLI fixture events must share one relative clock domain")
    tempdirs = {event.get("tempdir") for event in events}
    if len(tempdirs) != 1 or not isinstance(next(iter(tempdirs)), str):
        raise AssertionError("public CLI fixture events must record one child tempdir")
    starts = {
        event["scene"]: event["process_elapsed_s"]
        for event in events
        if event["event"] == "submit_started"
    }
    ends = {
        event["scene"]: event["process_elapsed_s"]
        for event in events
        if event["event"] == "submit_completed"
    }
    qa = {
        event["scene"]: event["process_elapsed_s"] - ends[event["scene"]]
        for event in events
        if event["event"] == "qa_completed"
    }
    if set(starts) != {"SLOW", "FAST"} or set(qa) != {"SLOW", "FAST"}:
        raise AssertionError("public CLI fixture evidence is incomplete")
    active = peak = 0
    for event in sorted(events, key=lambda event: event["sequence"]):
        if event["event"] == "submit_started":
            active += 1; peak = max(peak, active)
        elif event["event"] == "submit_completed":
            active -= 1
    # The command runner measures each subprocess separately.  It has no
    # shared admission timestamp with the worker's monotonic clock, so queue
    # wait cannot be derived across process clock domains.  Keep it explicit
    # rather than publishing the worker's absolute monotonic uptime as wait.
    return {scene: None for scene in sorted(starts)}, starts, qa, peak, next(iter(tempdirs))


def _public_cli_sample(root: Path, packet: Path, concurrency: int) -> dict[str, Any]:
    workspace, scratch = root / "workspace", root / "scratch"
    workspace.mkdir(parents=True); scratch.mkdir(parents=True)
    environment = {**os.environ, "PYTHONPATH": "", "TMP": str(scratch), "TEMP": str(scratch), "TMPDIR": str(scratch), "HYBRID_TEST_FIXTURE_LATENCY_BY_SCENE_JSON": json.dumps(_PUBLIC_CLI_LATENCIES, sort_keys=True)}
    commands: dict[str, dict[str, Any]] = {}
    commands["start"] = _public_cli_command(["start", "--workspace", str(workspace), "--compiled-input", str(packet), "--concurrency", str(concurrency)], environment)
    request_id = commands["start"]["result"]["request_id"]
    commands["worker"] = _public_cli_command(["worker", "--once", "--workspace", str(workspace), "--request-id", request_id, "--concurrency", str(concurrency)], environment)
    commands["status"] = _public_cli_command(["status", "--workspace", str(workspace), "--request-id", request_id], environment)
    commands["fresh_resume"] = _public_cli_command(["resume", "--workspace", str(workspace), "--request-id", request_id, "--concurrency", str(concurrency)], environment)
    if any(command["exit_code"] != 0 for command in commands.values()):
        raise AssertionError(commands)
    provider = workspace / "TC" / request_id[:16] / "provider"
    initial_posts = json.loads((provider / "fixture-post-count.json").read_text(encoding="utf-8"))["posts"]
    queue_wait, worker_elapsed, qa_durations, peak, child_tempdir = _public_cli_events(workspace, request_id)
    child_tempdir_verified = Path(child_tempdir).resolve() == scratch.resolve()
    if not child_tempdir_verified:
        raise AssertionError("public CLI child did not honor the explicit scratch tempdir")
    runner = json.loads((workspace / "TC" / request_id[:16] / "runner" / "runner.json").read_text(encoding="utf-8"))
    Path(next(provider.glob("*.TEST.png"))).write_bytes(b"tampered-public-cli-benchmark")
    commands["tamper_resume"] = _public_cli_command(["resume", "--workspace", str(workspace), "--request-id", request_id, "--concurrency", str(concurrency)], environment)
    posts_after = json.loads((provider / "fixture-post-count.json").read_text(encoding="utf-8"))["posts"]
    return {"concurrency": concurrency, "request_id": request_id, "commands": commands, "worker_wall_time_s": commands["worker"]["wall_time_s"], "queue_wait_s": queue_wait, "queue_wait_status": "UNKNOWN_CROSS_PROCESS_CLOCK_DOMAIN", "worker_elapsed_to_submit_s": worker_elapsed, "fixture_event_clock_domain": "fixture-process-monotonic-relative/v1", "child_tempdir": child_tempdir, "child_tempdir_verified": child_tempdir_verified, "qa_durations_s": qa_durations, "peak_inflight": peak, "initial_submit_count": initial_posts, "fresh_resume_submit_count": 0, "cache_hits": len(runner["qa"]), "status_snapshot": commands["status"]["result"]["queue"].get("snapshot"), "tamper_cache_rejected": commands["tamper_resume"]["exit_code"] == 1, "tamper_submit_count": posts_after - initial_posts}


def run_public_cli_benchmark(
    root: Path, *, repeats: int = 3, scratch_root: Path | None = None
) -> dict[str, Any]:
    """Measure public CLI subprocesses; never claim a production speedup."""
    if type(repeats) is not int or not 1 <= repeats <= 10:
        raise ValueError("repeats must be a bounded integer from 1 through 10")
    root = Path(root).resolve()
    if str(root).replace("\\", "/").casefold().startswith("c:/hermesstudio/production"):
        raise ValueError("benchmark rejects the production root")
    # Keep subprocess workspace below Windows' legacy path limit; the requested
    # output root receives only the final JSON artifact.
    scratch_root = (root / ".work" if scratch_root is None else scratch_root).resolve()
    if str(scratch_root).replace("\\", "/").casefold().startswith("c:/hermesstudio/production"):
        raise ValueError("benchmark rejects the production scratch root")
    scratch_root.mkdir(parents=True, exist_ok=True)
    work = scratch_root / ("public-cli-" + sha256(str(root).encode()).hexdigest()[:16])
    if work.exists(): shutil.rmtree(work)
    packet = _public_cli_packet(work / "same-hash-packet")
    samples = {f"concurrency_{concurrency}": [_public_cli_sample(work / f"concurrency_{concurrency}" / f"sample-{index}", packet, concurrency) for index in range(1, repeats + 1)] for concurrency in (1, 2)}
    result = {"schema": "next-video-public-cli-benchmark/v1", "mode": "TEST_ONLY_EXPLICIT_FIXTURE_PROVIDER", "measurement_scope": "LOCAL_MONOTONIC_WALL_TIME", "fixture_latency_s": _PUBLIC_CLI_LATENCIES, "repeats": repeats, "same_hash_packet": True, "packet_sha256": {path.name: sha256(path.read_bytes()).hexdigest() for path in sorted(packet.glob("*.json"))}, "samples": samples, "median_by_concurrency": {str(concurrency): {"worker_wall_time_s": statistics.median(sample["worker_wall_time_s"] for sample in samples[f"concurrency_{concurrency}"])} for concurrency in (1, 2)}, "real_cost_gain": "NOT_MEASURED", "production_claim": "NONE"}
    root.mkdir(parents=True, exist_ok=True)
    output = root / "public_cli_benchmark.json"
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result["result_path"] = str(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("benchmarks/public_cli_benchmark"))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--scratch-root", type=Path)
    args = parser.parse_args()
    result = run_public_cli_benchmark(
        args.output_dir, repeats=args.repeats, scratch_root=args.scratch_root
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
