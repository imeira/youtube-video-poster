"""RunPod GPU Compute Provider — on-demand cloud GPU (§53, §55-56).

B6 finding: community cloud containers don't start reliably → use SECURE cloud.
§55: Lifecycle: ALLOCATE → LOAD → RUN → SAVE → VERIFY → SHUTDOWN
§56: Prevent GPU idle: timeout, watchdog, finally/cleanup, orphan check.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import runpod

from src.providers.base import GPU, GPUComputeProvider, PodHandle
from src.providers.gpu.runpod_lifecycle import (
    OWNERSHIP_TAG_KEY,
    pod_ownership_tag,
    valid_ownership_tag,
)

logger = logging.getLogger(__name__)


class RunPodGPUProvider(GPUComputeProvider):
    """RunPod GPU compute provider (§53).

    Uses the official runpod Python SDK (v1.12.0).
    SECURE cloud preferred (B6: community is unreliable).
    """

    def __init__(
        self,
        api_key: str | None = None,
        ownership_tag: str | None = None,
    ):
        if ownership_tag is not None and not valid_ownership_tag(ownership_tag):
            raise ValueError("ownership_tag must be a SHA-256 identifier")
        if api_key is None:
            # Read from .env
            env_path = os.path.expanduser("~/AppData/Local/hermes/.env")
            if os.path.exists(env_path):
                with open(env_path) as f:
                    for line in f:
                        if line.strip().startswith("RUNPOD_API_KEY="):
                            api_key = line.split("=", 1)[1].strip()
                            break
        if not api_key:
            raise ValueError("RUNPOD_API_KEY not found in .env")
        runpod.api_key = api_key
        self._api_key = api_key
        self._ownership_tag = ownership_tag
        self._termination_claimed: set[str] = set()

    def list_gpus(self) -> list[GPU]:
        """List all available GPU types."""
        raw = runpod.get_gpus()
        return [
            GPU(
                id=g["id"],
                display_name=g.get("displayName", g["id"]),
                memory_in_gb=g.get("memoryInGb", 0),
            )
            for g in raw
        ]

    def get_gpu(self, gpu_id: str) -> GPU:
        """Get details for a specific GPU, including pricing."""
        g = runpod.get_gpu(gpu_id)
        return GPU(
            id=g["id"],
            display_name=g.get("displayName", g["id"]),
            memory_in_gb=g.get("memoryInGb", 0),
            secure_price=g.get("securePrice", 0.0),
            community_price=g.get("communityPrice", g.get("cheapPrice", 0.0)),
        )

    def provision(
        self,
        gpu_id: str,
        image: str,
        cloud: str = "SECURE",
        container_disk_gb: int = 40,
        **opts,
    ) -> PodHandle:
        """§55: ALLOCATE — provision a GPU pod."""
        if self._ownership_tag is None:
            raise PermissionError("strong ownership tag required before provisioning")
        env = opts.get("env", {})
        if not isinstance(env, dict):
            raise ValueError("pod environment must be a mapping")
        env = dict(env)
        existing_owner = env.get(OWNERSHIP_TAG_KEY)
        if existing_owner is not None and existing_owner != self._ownership_tag:
            raise ValueError("pod environment has a conflicting ownership tag")
        env[OWNERSHIP_TAG_KEY] = self._ownership_tag
        logger.info(f"Provisioning {gpu_id} on {cloud} cloud (image={image})")
        pod = runpod.create_pod(
            name=opts.get("name", "hermes-studio"),
            image_name=image,
            gpu_type_id=gpu_id,
            gpu_count=1,
            container_disk_in_gb=container_disk_gb,
            cloud_type=cloud,
            start_ssh=opts.get("start_ssh", False),
            volume_in_gb=opts.get("volume_in_gb", 0),
            docker_args=opts.get("docker_args", ""),
            ports=opts.get("ports"),
            env=env,
        )
        pod_id = pod.get("id")
        if not pod_id:
            raise RuntimeError(f"Failed to provision pod: {pod}")
        gpu = self.get_gpu(gpu_id)
        price = gpu.secure_price if cloud == "SECURE" else gpu.community_price
        return PodHandle(
            pod_id=pod_id,
            gpu=gpu_id,
            cloud=cloud,
            hourly_price=price,
            status="provisioning",
        )

    def get_pod(self, pod_id: str) -> dict[str, Any]:
        """Get pod status."""
        return runpod.get_pod(pod_id)

    def wait_for_running(
        self,
        pod_id: str,
        timeout_seconds: int = 300,
        poll_interval: int = 10,
    ) -> bool:
        """Wait for pod to reach RUNNING status."""
        start = time.time()
        while time.time() - start < timeout_seconds:
            info = self.get_pod(pod_id)
            runtime = info.get("runtime", {})
            rts = runtime.get("status", "N/A") if isinstance(runtime, dict) else "N/A"
            if rts == "RUNNING":
                logger.info(f"Pod {pod_id} is RUNNING")
                return True
            time.sleep(poll_interval)
        logger.warning(f"Pod {pod_id} did not reach RUNNING in {timeout_seconds}s")
        return False

    def terminate_pod(self, pod_id: str) -> bool:
        """§55: SHUTDOWN — terminate a pod. Always called in finally block."""
        if pod_id in self._termination_claimed:
            return False
        self._termination_claimed.add(pod_id)
        try:
            runpod.terminate_pod(pod_id)
            logger.info(f"Pod {pod_id} terminated")
            return True
        except Exception as e:
            self._termination_claimed.discard(pod_id)
            logger.error(f"Failed to terminate pod {pod_id}: {e}")
            return False

    def cleanup_orphans(self) -> list[str]:
        """§56: Find and terminate orphaned pods.

        Called on Director Agent startup to prevent idle GPU billing.
        """
        if self._ownership_tag is None:
            return []
        orphans = []
        pods = runpod.get_pods()
        for pod in pods:
            pod_id = pod.get("id")
            status = pod.get("desiredStatus", "")
            if (
                pod_id
                and pod_ownership_tag(pod) == self._ownership_tag
                and status not in ("EXITED", "TERMINATED")
                and self.terminate_pod(pod_id)
            ):
                logger.warning(f"Owned orphan pod found: {pod_id} (status={status}) — terminated")
                orphans.append(pod_id)
        return orphans

    def estimate_cost(self, gpu_id: str, duration_seconds: float, cloud: str = "SECURE") -> float:
        """Estimate cost for a job on a specific GPU."""
        gpu = self.get_gpu(gpu_id)
        price = gpu.secure_price if cloud == "SECURE" else gpu.community_price
        return (duration_seconds / 3600) * price

    async def execute(self, **params) -> Any:
        """Execute a job (provision → run → terminate pattern)."""
        raise NotImplementedError("Use provision/wait_for_running/terminate_pod pattern instead")
