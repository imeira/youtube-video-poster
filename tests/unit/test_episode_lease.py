from __future__ import annotations

import pytest

from src.hybrid.lease import EpisodeLeaseStore
from src.hybrid.worker import EpisodeTaskQueue


def test_episode_lease_blocks_second_writer_until_expiry(tmp_path):
    store = EpisodeLeaseStore(tmp_path / "leases.sqlite")
    first = store.acquire("EP8", "run-a", now=100.0, ttl_seconds=30)

    with pytest.raises(RuntimeError, match="held"):
        store.acquire("EP8", "run-b", now=110.0, ttl_seconds=30)

    second = store.acquire("EP8", "run-b", now=131.0, ttl_seconds=30)
    assert second.fencing_token == first.fencing_token + 1
    with pytest.raises(RuntimeError, match="stale"):
        store.assert_active(first, now=131.0)


def test_episode_lease_heartbeat_keeps_owner_and_fencing_token(tmp_path):
    store = EpisodeLeaseStore(tmp_path / "leases.sqlite")
    first = store.acquire("EP8", "run-a", now=100.0, ttl_seconds=30)
    renewed = store.acquire("EP8", "run-a", now=120.0, ttl_seconds=30)

    assert renewed.fencing_token == first.fencing_token
    store.assert_active(renewed, now=149.0)


def test_release_keeps_monotonic_epoch_and_old_claim_cannot_complete(tmp_path):
    store = EpisodeLeaseStore(tmp_path / "leases.sqlite")
    first = store.acquire("EP8", "run-a", now=100.0, ttl_seconds=30)
    store.release(first, now=101.0)
    second = store.acquire("EP8", "run-b", now=102.0, ttl_seconds=30)
    assert second.fencing_token == first.fencing_token + 1

    queue = EpisodeTaskQueue(tmp_path / "tasks.sqlite")
    queue.enqueue("EP8", str(tmp_path), "fixture")
    first_queue = queue.leases.acquire("EP8", "worker-a:run", now=100.0, ttl_seconds=1)
    second_queue = queue.leases.acquire("EP8", "worker-b:run", now=102.0, ttl_seconds=30)
    queue.claim("EP8", "worker-b", second_queue, now=102.5)
    with pytest.raises(RuntimeError, match="stale"):
        queue.complete("EP8", "worker-a", first_queue, {"state": "DONE", "next_action": "TERMINAL"}, now=102.5)
    assert queue.complete("EP8", "worker-b", second_queue, {"state": "DONE", "next_action": "TERMINAL"}, now=102.5)["status"] == "DONE"


def test_expired_lease_is_replaced_but_tombstone_is_not_deleted(tmp_path):
    store = EpisodeLeaseStore(tmp_path / "leases.sqlite")
    first = store.acquire("EP8", "run-a", now=100.0, ttl_seconds=1)
    second = store.acquire("EP8", "run-b", now=102.0, ttl_seconds=1)
    assert second.fencing_token == first.fencing_token + 1
    with pytest.raises(RuntimeError, match="stale"):
        store.release(first, now=102.0)
