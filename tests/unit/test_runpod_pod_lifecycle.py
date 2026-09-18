from __future__ import annotations

import asyncio

import pytest

from src.providers.gpu.runpod_lifecycle import (
    OWNERSHIP_TAG_KEY,
    RunPodPodLifecycle,
    ownership_tag,
)


class FakePodTransport:
    def __init__(self, pods=None):
        self.pods = list(pods or [])
        self.calls: list[tuple] = []

    async def create_pod(self, spec: dict) -> dict:
        self.calls.append(("CREATE", spec))
        return {"id": "pod_123"}

    async def terminate_pod(self, pod_id: str) -> None:
        self.calls.append(("TERMINATE", pod_id))

    async def list_pods(self) -> list[dict]:
        self.calls.append(("LIST",))
        return self.pods


def lifecycle(tmp_path, transport):
    return RunPodPodLifecycle(
        transport=transport,
        owner_tag=ownership_tag("hermes-studio", "EP8", "revision-1"),
        state_dir=tmp_path / "pods",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["success", "error", "timeout", "cancel"])
async def test_owned_pod_is_terminated_exactly_once_for_every_exit(tmp_path, outcome):
    transport = FakePodTransport()
    manager = lifecycle(tmp_path, transport)

    async def operation(pod_id: str):
        assert pod_id == "pod_123"
        if outcome == "error":
            raise RuntimeError("boom")
        if outcome == "timeout":
            raise TimeoutError("deadline")
        if outcome == "cancel":
            raise asyncio.CancelledError
        return "ok"

    error = {
        "error": RuntimeError,
        "timeout": TimeoutError,
        "cancel": asyncio.CancelledError,
    }.get(outcome)
    if error:
        with pytest.raises(error):
            await manager.run({"gpu_type_id": "gpu_1"}, operation)
    else:
        assert await manager.run({"gpu_type_id": "gpu_1"}, operation) == "ok"

    created = next(call[1] for call in transport.calls if call[0] == "CREATE")
    assert created["tags"] == {OWNERSHIP_TAG_KEY: manager.owner_tag}
    assert sum(call == ("TERMINATE", "pod_123") for call in transport.calls) == 1

    resumed = lifecycle(tmp_path, transport)
    await resumed.terminate_once("pod_123")
    assert sum(call == ("TERMINATE", "pod_123") for call in transport.calls) == 1


@pytest.mark.asyncio
async def test_orphan_cleanup_matches_exact_ownership_tag_not_name_or_substring(tmp_path):
    owner = ownership_tag("hermes-studio", "EP8", "revision-1")
    transport = FakePodTransport(
        [
            {"id": "ours", "name": "anything", "status": "RUNNING", "tags": {OWNERSHIP_TAG_KEY: owner}},
            {"id": "name-only", "name": "hermes-studio-EP8", "status": "RUNNING", "tags": {}},
            {"id": "substring", "name": "anything", "status": "RUNNING", "tags": {OWNERSHIP_TAG_KEY: f"prefix-{owner}"}},
            {"id": "other", "name": "anything", "status": "RUNNING", "tags": {OWNERSHIP_TAG_KEY: ownership_tag("other", "EP8", "revision-1")}},
            {"id": "exited", "name": "anything", "status": "EXITED", "tags": {OWNERSHIP_TAG_KEY: owner}},
        ]
    )
    manager = lifecycle(tmp_path, transport)

    assert await manager.cleanup_orphans() == ["ours"]
    assert [call for call in transport.calls if call[0] == "TERMINATE"] == [
        ("TERMINATE", "ours")
    ]


def test_legacy_sdk_cleanup_also_requires_exact_ownership_tag(monkeypatch):
    from src.providers.gpu import runpod_provider as sdk_module

    owner = ownership_tag("hermes-studio", "EP8", "revision-1")
    pods = [
        {
            "id": "ours",
            "name": "unrelated",
            "desiredStatus": "RUNNING",
            "env": [f"{OWNERSHIP_TAG_KEY}={owner}"],
        },
        {"id": "name-only", "name": "hermes-studio", "desiredStatus": "RUNNING", "tags": {}},
        {"id": "substring", "name": "x", "desiredStatus": "RUNNING", "tags": {OWNERSHIP_TAG_KEY: f"x{owner}"}},
    ]
    terminated: list[str] = []
    monkeypatch.setattr(sdk_module.runpod, "get_pods", lambda: pods)
    monkeypatch.setattr(sdk_module.runpod, "terminate_pod", terminated.append)
    provider = sdk_module.RunPodGPUProvider(api_key="test", ownership_tag=owner)

    assert provider.cleanup_orphans() == ["ours"]
    assert provider.cleanup_orphans() == []
    assert terminated == ["ours"]


def test_legacy_sdk_provision_uses_supported_env_ownership_tag(monkeypatch):
    from src.providers.gpu import runpod_provider as sdk_module

    owner = ownership_tag("hermes-studio", "EP8", "revision-1")
    captured: dict = {}

    def create_pod(
        *, name, image_name, gpu_type_id, gpu_count, container_disk_in_gb,
        cloud_type, start_ssh, volume_in_gb, docker_args, ports, env
    ):
        captured.update(env=env, name=name)
        return {"id": "pod_123"}

    monkeypatch.setattr(sdk_module.runpod, "create_pod", create_pod)
    monkeypatch.setattr(
        sdk_module.RunPodGPUProvider,
        "get_gpu",
        lambda self, gpu_id: sdk_module.GPU(
            id=gpu_id, display_name=gpu_id, memory_in_gb=24, secure_price=0.5
        ),
    )
    provider = sdk_module.RunPodGPUProvider(api_key="test", ownership_tag=owner)

    handle = provider.provision(
        "gpu_1", "image:sha", env={"EXISTING": "preserved"}
    )

    assert handle.pod_id == "pod_123"
    assert captured["env"] == {"EXISTING": "preserved", OWNERSHIP_TAG_KEY: owner}


def test_legacy_sdk_failed_termination_can_reconcile_on_next_cleanup(monkeypatch):
    from src.providers.gpu import runpod_provider as sdk_module

    owner = ownership_tag("hermes-studio", "EP8", "revision-1")
    attempts = 0

    def terminate(pod_id):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise ConnectionError("outcome unknown")

    monkeypatch.setattr(sdk_module.runpod, "terminate_pod", terminate)
    provider = sdk_module.RunPodGPUProvider(api_key="test", ownership_tag=owner)

    assert provider.terminate_pod("pod_123") is False
    assert provider.terminate_pod("pod_123") is True
    assert attempts == 2


@pytest.mark.asyncio
async def test_orphan_cleanup_reconciles_failed_termination_claim(tmp_path):
    owner = ownership_tag("hermes-studio", "EP8", "revision-1")

    class FlakyPodTransport(FakePodTransport):
        def __init__(self):
            super().__init__([
                {
                    "id": "pod_123",
                    "status": "RUNNING",
                    "env": [f"{OWNERSHIP_TAG_KEY}={owner}"],
                }
            ])
            self.terminate_attempts = 0

        async def terminate_pod(self, pod_id: str) -> None:
            self.calls.append(("TERMINATE", pod_id))
            self.terminate_attempts += 1
            if self.terminate_attempts == 1:
                raise ConnectionError("outcome unknown")

    transport = FlakyPodTransport()
    manager = lifecycle(tmp_path, transport)

    with pytest.raises(ConnectionError, match="outcome unknown"):
        await manager.run({"gpu_type_id": "gpu_1"}, lambda pod_id: asyncio.sleep(0))

    resumed = lifecycle(tmp_path, transport)
    assert await resumed.cleanup_orphans() == ["pod_123"]
    assert transport.terminate_attempts == 2


@pytest.mark.asyncio
async def test_separate_lifecycles_racing_create_make_one_pod(tmp_path):
    class BlockingCreateTransport(FakePodTransport):
        def __init__(self):
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def create_pod(self, spec: dict) -> dict:
            self.calls.append(("CREATE", spec))
            self.started.set()
            await self.release.wait()
            return {"id": "pod_123"}

    transport = BlockingCreateTransport()
    first = lifecycle(tmp_path, transport)
    second = lifecycle(tmp_path, transport)
    first_task = asyncio.create_task(first.run({"gpu_type_id": "gpu_1"}, lambda pod_id: asyncio.sleep(0)))
    await transport.started.wait()

    with pytest.raises(RuntimeError, match="creation already claimed"):
        await asyncio.wait_for(
            second.run({"gpu_type_id": "gpu_1"}, lambda pod_id: asyncio.sleep(0)), timeout=0.05
        )
    assert [call[0] for call in transport.calls] == ["CREATE"]

    transport.release.set()
    await first_task
    assert sum(call[0] == "TERMINATE" for call in transport.calls) == 1


@pytest.mark.asyncio
async def test_checkpoint_failure_after_create_immediately_cleans_up_once(tmp_path, monkeypatch):
    transport = FakePodTransport()
    manager = lifecycle(tmp_path, transport)
    original_save = manager._save
    calls = 0

    def fail_first_save(state):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("disk full")
        original_save(state)

    monkeypatch.setattr(manager, "_save", fail_first_save)
    with pytest.raises(OSError, match="disk full"):
        await manager.run({"gpu_type_id": "gpu_1"}, lambda pod_id: asyncio.sleep(0))

    assert [call[0] for call in transport.calls] == ["CREATE", "TERMINATE"]


@pytest.mark.asyncio
async def test_separate_lifecycles_racing_termination_make_one_call(tmp_path):
    transport = FakePodTransport()
    manager = lifecycle(tmp_path, transport)
    manager._save({"schema_version": 1, "pod_id": "pod_123", "owner_tag": manager.owner_tag,
                   "terminate_claimed": False, "terminated": False})
    first = lifecycle(tmp_path, transport)
    second = lifecycle(tmp_path, transport)

    results = await asyncio.gather(first.terminate_once("pod_123"), second.terminate_once("pod_123"))

    assert sorted(results) == [False, True]
    assert [call for call in transport.calls if call[0] == "TERMINATE"] == [("TERMINATE", "pod_123")]
