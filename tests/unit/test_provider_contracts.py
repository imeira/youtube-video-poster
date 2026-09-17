"""Global provider surface includes storage and music without concrete coupling."""

from __future__ import annotations

from src.providers.base import PublishProvider
from src.providers.contracts import MusicProvider, StorageProvider


def test_music_and_storage_provider_interfaces_are_declared():
    assert MusicProvider.__abstractmethods__ == {"compose"}
    assert StorageProvider.__abstractmethods__ == {"put", "get"}


def test_publish_provider_requires_remote_readback_before_a_publication_can_complete():
    assert "readback" in PublishProvider.__abstractmethods__
