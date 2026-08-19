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

logger = logging.getLogger(__name__)


class RunPodGPUProvider(GPUComputeProvider):
    """RunPod GPU compute provider (§53).

    Uses the official runpod Python SDK (v1.12.0).
    SECURE cloud preferred (B6: community is unreliable).
    """

    def __init__(self, api_key: str | None = None):
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

    def terminate_pod(self, pod_id: str) -> None:
        """§55: SHUTDOWN — terminate a pod. Always called in finally block."""
        try:
            runpod.terminate_pod(pod_id)
            logger.info(f"Pod {pod_id} terminated")
        except Exception as e:
            logger.error(f"Failed to terminate pod {pod_id}: {e}")

    def cleanup_orphans(self) -> list[str]:
        """§56: Find and terminate orphaned pods.

        Called on Director Agent startup to prevent idle GPU billing.
        """
        orphans = []
        pods = runpod.get_pods()
        for pod in pods:
            pod_id = pod.get("id")
            name = pod.get("name", "")
            status = pod.get("desiredStatus", "")
            # Consider any pod with "hermes" in the name as ours
            if pod_id and "hermes" in name.lower() and status not in ("EXITED",):
                logger.warning(f"Orphan pod found: {pod_id} ({name}, status={status}) — terminating")
                self.terminate_pod(pod_id)
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
