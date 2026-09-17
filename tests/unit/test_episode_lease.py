from __future__ import annotations

import pytest

from src.hybrid.lease import EpisodeLeaseStore


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
