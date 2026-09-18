from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from decimal import Decimal

import pytest

from src.hybrid.execution import ProviderResult
from src.hybrid.planner import Config
from src.hybrid.providers import video_job
from src.providers.video.runpod_serverless import (
    PollPolicy,
    RunPodCancelled,
    RunPodFailed,
    RunPodHeroProvider,
    RunPodState,
    RunPodTimeout,
)
from tests.unit.test_hybrid_execution import manifest


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeTransport:
    def __init__(self, statuses: list[dict], content: bytes = b"verified-video") -> None:
        self.statuses = list(statuses)
        self.content = content
        self.calls: list[tuple] = []

    async def submit(self, endpoint_id: str, payload: dict) -> dict:
        self.calls.append(("POST", endpoint_id, payload))
        return {"id": "job_123", "status": "IN_QUEUE"}

    async def status(self, endpoint_id: str, job_id: str) -> dict:
        self.calls.append(("GET", endpoint_id, job_id))
        response = self.statuses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def cancel(self, endpoint_id: str, job_id: str) -> None:
        self.calls.append(("CANCEL", endpoint_id, job_id))

    async def download(self, url: str) -> bytes:
        self.calls.append(("DOWNLOAD", url))
        return self.content


def completed(content: bytes = b"verified-video") -> dict:
    return {
        "id": "job_123",
        "status": "COMPLETED",
        "output": {
            "url": "https://results.runpod.test/job_123.mp4?signature=secret",
            "sha256": hashlib.sha256(content).hexdigest(),
        },
        "actual_cost": "0.20",
    }


def build_provider(tmp_path, transport, clock, *, timeout=30.0):
    return RunPodHeroProvider(
        endpoint_id="endpoint_abc",
        transport=transport,
        clock=clock,
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "output",
        poll_policy=PollPolicy(
            initial_delay=1.0,
            multiplier=2.0,
            maximum_delay=4.0,
            timeout=timeout,
        ),
        result_hosts={"results.runpod.test"},
    )


def build_job(tmp_path):
    return video_job(
        Config(video_endpoint="endpoint_abc"),
        "S01",
        manifest(tmp_path),
        "leaves moving in wind",
    )


def test_explicit_empty_result_allowlist_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="result hosts"):
        RunPodHeroProvider(
            endpoint_id="endpoint_abc",
            transport=FakeTransport([]),
            clock=FakeClock(),
            state_dir=tmp_path / "state",
            output_dir=tmp_path / "output",
            result_hosts=set(),
        )


@pytest.mark.asyncio
async def test_submit_posts_exact_payload_once_and_checkpoints_before_polling(tmp_path):
    clock = FakeClock()
    transport = FakeTransport([
        {"id": "job_123", "status": "RUNNING"},
        completed(),
    ])
    provider = build_provider(tmp_path, transport, clock)
    job = build_job(tmp_path)
    checkpoints: list[tuple[str, int]] = []

    async def run():
        return await provider.submit(
            job,
            job.request_id,
            lambda **data: checkpoints.append((data["provider_id"], len(transport.calls))),
        )

    result = await run()

    assert isinstance(result, ProviderResult)
    assert result.actual_cost == Decimal("0.20")
    assert result.path.read_bytes() == b"verified-video"
    assert transport.calls[0] == ("POST", "endpoint_abc", job.payload)
    assert sum(call[0] == "POST" for call in transport.calls) == 1
    assert checkpoints == [("job_123", 1)]
    assert clock.sleeps == [1.0, 2.0]
    state = json.loads((tmp_path / "state" / f"{job.request_id}.json").read_text())
    assert state["status"] == RunPodState.COMPLETED.value
    assert "signature" not in json.dumps(state)


@pytest.mark.asyncio
async def test_recovery_uses_only_get_and_downloads_verified_output_once(tmp_path):
    clock = FakeClock()
    transport = FakeTransport([RuntimeError("worker crash"), completed()])
    provider = build_provider(tmp_path, transport, clock)
    job = build_job(tmp_path)
    checkpoint: dict[str, str] = {}

    with pytest.raises(RuntimeError, match="worker crash"):
        await provider.submit(job, job.request_id, lambda **data: checkpoint.update(data))

    call_boundary = len(transport.calls)
    result = await provider.recover(
        job,
        job.request_id,
        checkpoint["provider_id"],
        "",
        lambda **data: None,
    )
    recovered_calls = transport.calls[call_boundary:]
    assert recovered_calls[0][0] == "GET"
    assert all(call[0] != "POST" for call in recovered_calls)
    assert result.path.read_bytes() == b"verified-video"

    second_boundary = len(transport.calls)
    second = await provider.recover(
        job,
        job.request_id,
        checkpoint["provider_id"],
        "",
        lambda **data: None,
    )
    assert second == result
    assert transport.calls[second_boundary:] == []
    assert sum(call[0] == "DOWNLOAD" for call in transport.calls) == 1


@pytest.mark.asyncio
async def test_timeout_is_deadline_bounded_cancelled_once_and_persisted(tmp_path):
    clock = FakeClock()
    transport = FakeTransport([
        {"id": "job_123", "status": "RUNNING"},
        {"id": "job_123", "status": "RUNNING"},
    ])
    provider = build_provider(tmp_path, transport, clock, timeout=2.5)
    job = build_job(tmp_path)
    checkpoint: dict[str, str] = {}

    with pytest.raises(RunPodTimeout):
        await provider.submit(job, job.request_id, lambda **data: checkpoint.update(data))

    assert clock.sleeps == [1.0, 1.5]
    assert sum(call[0] == "CANCEL" for call in transport.calls) == 1
    state = json.loads((tmp_path / "state" / f"{job.request_id}.json").read_text())
    assert state["status"] == RunPodState.CANCELLED.value
    assert state["terminal_reason"] == "TIMEOUT"
    assert state["cancel_claimed"] is True

    boundary = len(transport.calls)
    with pytest.raises(RunPodTimeout):
        await provider.recover(
            job,
            job.request_id,
            checkpoint["provider_id"],
            "",
            lambda **data: None,
        )
    assert transport.calls[boundary:] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("remote_status", "error_type"),
    [("FAILED", RunPodFailed), ("CANCELLED", RunPodCancelled)],
)
async def test_remote_terminal_states_are_persisted(tmp_path, remote_status, error_type):
    clock = FakeClock()
    transport = FakeTransport([
        {"id": "job_123", "status": remote_status, "error": "remote terminal"}
    ])
    provider = build_provider(tmp_path, transport, clock)
    job = build_job(tmp_path)

    with pytest.raises(error_type, match="remote terminal"):
        await provider.submit(job, job.request_id, lambda **data: None)

    state = json.loads((tmp_path / "state" / f"{job.request_id}.json").read_text())
    assert state["status"] == remote_status
    assert state["terminal_reason"] == "remote terminal"


@pytest.mark.asyncio
async def test_explicit_cancel_is_persisted_and_sent_at_most_once(tmp_path):
    clock = FakeClock()
    transport = FakeTransport([RuntimeError("pause after checkpoint")])
    provider = build_provider(tmp_path, transport, clock)
    job = build_job(tmp_path)

    with pytest.raises(RuntimeError, match="pause after checkpoint"):
        await provider.submit(job, job.request_id, lambda **data: None)

    await provider.cancel(job.request_id)
    await provider.cancel(job.request_id)

    assert sum(call[0] == "CANCEL" for call in transport.calls) == 1
    state = json.loads((tmp_path / "state" / f"{job.request_id}.json").read_text())
    assert state["status"] == RunPodState.CANCELLED.value
    assert state["terminal_reason"] == "CALLER_CANCELLED"
    assert state["cancel_claimed"] is True


@pytest.mark.asyncio
async def test_failed_remote_cancel_stays_recoverable_until_reconciliation_succeeds(tmp_path):
    class FlakyCancelTransport(FakeTransport):
        def __init__(self):
            super().__init__([RuntimeError("pause after checkpoint")])
            self.cancel_attempts = 0

        async def cancel(self, endpoint_id: str, job_id: str) -> None:
            self.calls.append(("CANCEL", endpoint_id, job_id))
            self.cancel_attempts += 1
            if self.cancel_attempts == 1:
                raise RuntimeError("remote cancel unavailable")

    clock = FakeClock()
    transport = FlakyCancelTransport()
    provider = build_provider(tmp_path, transport, clock)
    job = build_job(tmp_path)

    with pytest.raises(RuntimeError, match="pause after checkpoint"):
        await provider.submit(job, job.request_id, lambda **data: None)
    with pytest.raises(RuntimeError, match="remote cancel unavailable"):
        await provider.cancel(job.request_id)

    state_path = tmp_path / "state" / f"{job.request_id}.json"
    state = json.loads(state_path.read_text())
    assert state["status"] == RunPodState.IN_QUEUE.value
    assert state["cancel_claimed"] is True
    assert state["terminal_reason"] == "CALLER_CANCELLED"

    with pytest.raises(RunPodCancelled, match="CALLER_CANCELLED"):
        await provider.recover(job, job.request_id, "job_123", "", lambda **data: None)

    assert [call[0] for call in transport.calls[-1:]] == ["CANCEL"]
    assert transport.cancel_attempts == 2
    state = json.loads(state_path.read_text())
    assert state["status"] == RunPodState.CANCELLED.value


@pytest.mark.asyncio
async def test_bad_download_is_failed_persistently_without_second_download(tmp_path):
    clock = FakeClock()
    transport = FakeTransport([completed()], content=b"corrupt-video")
    provider = build_provider(tmp_path, transport, clock)
    job = build_job(tmp_path)

    with pytest.raises(ValueError, match="hash mismatch"):
        await provider.submit(job, job.request_id, lambda **data: None)

    state = json.loads((tmp_path / "state" / f"{job.request_id}.json").read_text())
    assert state["status"] == RunPodState.FAILED.value
    assert state["terminal_reason"] == "RESULT_VERIFICATION_FAILED"
    boundary = len(transport.calls)
    with pytest.raises(RunPodFailed, match="RESULT_VERIFICATION_FAILED"):
        await provider.recover(job, job.request_id, "job_123", "", lambda **data: None)
    assert transport.calls[boundary:] == []
    assert sum(call[0] == "DOWNLOAD" for call in transport.calls) == 1


@pytest.mark.asyncio
async def test_invalid_or_double_wrapped_payload_is_rejected_before_transport(tmp_path):
    clock = FakeClock()
    transport = FakeTransport([])
    provider = build_provider(tmp_path, transport, clock)
    job = build_job(tmp_path)
    invalid = replace(job, payload={"input": job.payload})

    with pytest.raises(ValueError, match="input contract"):
        await provider.submit(invalid, invalid.request_id, lambda **data: None)

    assert transport.calls == []


@pytest.mark.asyncio
async def test_job_endpoint_must_match_bound_serverless_endpoint(tmp_path):
    clock = FakeClock()
    transport = FakeTransport([])
    provider = build_provider(tmp_path, transport, clock)
    job = replace(build_job(tmp_path), endpoint="different_endpoint")

    with pytest.raises(ValueError, match="endpoint binding"):
        await provider.submit(job, job.request_id, lambda **data: None)

    assert transport.calls == []


@pytest.mark.asyncio
async def test_recovery_honors_original_persisted_deadline(tmp_path):
    clock = FakeClock()
    transport = FakeTransport([RuntimeError("crash after checkpoint")])
    provider = build_provider(tmp_path, transport, clock, timeout=2.0)
    job = build_job(tmp_path)

    with pytest.raises(RuntimeError, match="crash after checkpoint"):
        await provider.submit(job, job.request_id, lambda **data: None)
    clock.now = 103.0
    boundary = len(transport.calls)

    with pytest.raises(RunPodTimeout):
        await provider.recover(job, job.request_id, "job_123", "", lambda **data: None)

    assert [call[0] for call in transport.calls[boundary:]] == ["CANCEL"]
    state = json.loads((tmp_path / "state" / f"{job.request_id}.json").read_text())
    assert state["deadline_at"] == 102.0
    assert state["status"] == RunPodState.CANCELLED.value
    assert state["terminal_reason"] == "TIMEOUT"


@pytest.mark.asyncio
async def test_recovery_refetches_completed_status_when_download_was_interrupted(tmp_path):
    class InterruptedDownloadTransport(FakeTransport):
        def __init__(self):
            super().__init__([completed(), completed()])
            self.download_attempts = 0

        async def download(self, url: str) -> bytes:
            self.calls.append(("DOWNLOAD", url))
            self.download_attempts += 1
            if self.download_attempts == 1:
                raise RuntimeError("download interrupted")
            return self.content

    clock = FakeClock()
    transport = InterruptedDownloadTransport()
    provider = build_provider(tmp_path, transport, clock)
    job = build_job(tmp_path)

    with pytest.raises(RuntimeError, match="download interrupted"):
        await provider.submit(job, job.request_id, lambda **data: None)

    state = json.loads((tmp_path / "state" / f"{job.request_id}.json").read_text())
    assert state["status"] == RunPodState.COMPLETED.value
    assert state["result_path"] == ""
    clock.now = 200.0
    boundary = len(transport.calls)

    result = await provider.recover(
        job, job.request_id, "job_123", "", lambda **data: None
    )

    assert result.path.read_bytes() == b"verified-video"
    assert [call[0] for call in transport.calls[boundary:]] == ["GET", "DOWNLOAD"]
    assert sum(call[0] == "POST" for call in transport.calls) == 1


@pytest.mark.asyncio
async def test_deadline_does_not_allow_a_status_get_after_final_sleep(tmp_path):
    clock = FakeClock()
    transport = FakeTransport([])
    provider = build_provider(tmp_path, transport, clock, timeout=1.0)
    job = build_job(tmp_path)

    with pytest.raises(RunPodTimeout):
        await provider.submit(job, job.request_id, lambda **data: None)

    assert clock.sleeps == [1.0]
    assert [call[0] for call in transport.calls] == ["POST", "CANCEL"]


@pytest.mark.asyncio
async def test_ambiguous_submit_is_claimed_durably_and_never_reposted(tmp_path):
    class AmbiguousTransport(FakeTransport):
        async def submit(self, endpoint_id: str, payload: dict) -> dict:
            self.calls.append(("POST", endpoint_id, payload))
            raise TimeoutError("outcome unknown")

    clock = FakeClock()
    transport = AmbiguousTransport([])
    provider = build_provider(tmp_path, transport, clock)
    job = build_job(tmp_path)

    with pytest.raises(TimeoutError, match="outcome unknown"):
        await provider.submit(job, job.request_id, lambda **data: None)
    with pytest.raises(RuntimeError, match="already checkpointed"):
        await provider.submit(job, job.request_id, lambda **data: None)

    assert sum(call[0] == "POST" for call in transport.calls) == 1
    state = json.loads((tmp_path / "state" / f"{job.request_id}.json").read_text())
    assert state["status"] == "SUBMITTING"
    assert state["terminal_reason"] == "SUBMISSION_OUTCOME_UNKNOWN"


@pytest.mark.asyncio
async def test_separate_instances_racing_same_request_make_one_post_and_second_fails_closed(tmp_path):
    class BlockingTransport(FakeTransport):
        def __init__(self):
            super().__init__([])
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def submit(self, endpoint_id: str, payload: dict) -> dict:
            self.calls.append(("POST", endpoint_id, payload))
            self.started.set()
            await self.release.wait()
            return {"id": "job_123", "status": "FAILED", "error": "stop"}

    clock = FakeClock()
    transport = BlockingTransport()
    job = build_job(tmp_path)
    first = build_provider(tmp_path, transport, clock)
    second = build_provider(tmp_path, transport, clock)
    first_task = asyncio.create_task(first.submit(job, job.request_id, lambda **data: None))
    await transport.started.wait()

    with pytest.raises(RuntimeError, match="already checkpointed"):
        await asyncio.wait_for(second.submit(job, job.request_id, lambda **data: None), timeout=0.05)
    assert [call[0] for call in transport.calls] == ["POST"]

    transport.release.set()
    with pytest.raises(RunPodFailed):
        await first_task


@pytest.mark.asyncio
async def test_completed_output_rejects_a_preexisting_symlink_without_download(tmp_path):
    clock = FakeClock()
    transport = FakeTransport([completed()])
    provider = build_provider(tmp_path, transport, clock)
    job = build_job(tmp_path)
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    target = tmp_path / "output" / f"{job.request_id}.mp4"
    target.symlink_to(outside)

    with pytest.raises(ValueError, match="unsafe final output"):
        await provider.submit(job, job.request_id, lambda **data: None)

    assert [call[0] for call in transport.calls] == ["POST", "GET"]
    assert outside.read_bytes() == b"outside"


@pytest.mark.asyncio
async def test_download_rejects_result_host_outside_explicit_allowlist(tmp_path):
    response = completed()
    response["output"]["url"] = "https://attacker.example/video.mp4"
    clock = FakeClock()
    transport = FakeTransport([response])
    provider = build_provider(tmp_path, transport, clock)
    job = build_job(tmp_path)

    with pytest.raises(ValueError, match="unsafe or unverifiable"):
        await provider.submit(job, job.request_id, lambda **data: None)

    assert all(call[0] != "DOWNLOAD" for call in transport.calls)


@pytest.mark.asyncio
async def test_local_checkpoint_reconciles_executor_callback_crash_without_post(tmp_path):
    clock = FakeClock()
    transport = FakeTransport([completed()])
    provider = build_provider(tmp_path, transport, clock)
    job = build_job(tmp_path)

    def crash_checkpoint(**data):
        raise RuntimeError("executor checkpoint interrupted")

    with pytest.raises(RuntimeError, match="checkpoint interrupted"):
        await provider.submit(job, job.request_id, crash_checkpoint)

    boundary = len(transport.calls)
    reconciled: dict[str, str] = {}
    result = await provider.recover_local(
        job, job.request_id, lambda **data: reconciled.update(data)
    )

    assert reconciled == {"provider_id": "job_123"}
    assert [call[0] for call in transport.calls[boundary:]] == ["GET", "DOWNLOAD"]
    assert sum(call[0] == "POST" for call in transport.calls) == 1
    assert result.path.read_bytes() == b"verified-video"


@pytest.mark.asyncio
async def test_cancel_during_poll_sleep_is_monotonic_and_skips_status_get(tmp_path):
    class CancellingClock(FakeClock):
        provider = None

        async def sleep(self, seconds: float) -> None:
            await super().sleep(seconds)
            await self.provider.cancel(job.request_id)

    clock = CancellingClock()
    transport = FakeTransport([completed()])
    provider = build_provider(tmp_path, transport, clock)
    clock.provider = provider
    job = build_job(tmp_path)

    with pytest.raises(RunPodCancelled, match="CALLER_CANCELLED"):
        await provider.submit(job, job.request_id, lambda **data: None)

    assert [call[0] for call in transport.calls] == ["POST", "CANCEL"]
    state = json.loads((tmp_path / "state" / f"{job.request_id}.json").read_text())
    assert state["status"] == RunPodState.CANCELLED.value
    assert state["cancel_claimed"] is True


@pytest.mark.asyncio
async def test_status_returning_after_deadline_cannot_promote_completion(tmp_path):
    class LateStatusTransport(FakeTransport):
        async def status(self, endpoint_id: str, job_id: str) -> dict:
            self.calls.append(("GET", endpoint_id, job_id))
            clock.now = 103.0
            return completed()

    clock = FakeClock()
    transport = LateStatusTransport([])
    provider = build_provider(tmp_path, transport, clock, timeout=2.0)
    job = build_job(tmp_path)

    with pytest.raises(RunPodTimeout):
        await provider.submit(job, job.request_id, lambda **data: None)

    assert [call[0] for call in transport.calls] == ["POST", "GET", "CANCEL"]
    assert all(call[0] != "DOWNLOAD" for call in transport.calls)


@pytest.mark.asyncio
async def test_stalled_status_call_is_bounded_and_persists_timeout(tmp_path):
    class StationaryClock(FakeClock):
        async def sleep(self, seconds: float) -> None:
            self.sleeps.append(seconds)

    class StalledTransport(FakeTransport):
        async def status(self, endpoint_id: str, job_id: str) -> dict:
            self.calls.append(("GET", endpoint_id, job_id))
            await asyncio.Event().wait()

    clock = StationaryClock()
    transport = StalledTransport([])
    provider = RunPodHeroProvider(
        endpoint_id="endpoint_abc",
        transport=transport,
        clock=clock,
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "output",
        poll_policy=PollPolicy(
            initial_delay=0.001,
            multiplier=2.0,
            maximum_delay=0.01,
            timeout=0.03,
        ),
        result_hosts={"results.runpod.test"},
    )
    job = build_job(tmp_path)

    with pytest.raises(RunPodTimeout):
        await asyncio.wait_for(
            provider.submit(job, job.request_id, lambda **data: None),
            timeout=0.5,
        )

    assert [call[0] for call in transport.calls] == ["POST", "GET", "CANCEL"]
    state = json.loads((tmp_path / "state" / f"{job.request_id}.json").read_text())
    assert state["status"] == RunPodState.CANCELLED.value
    assert state["terminal_reason"] == "TIMEOUT"
