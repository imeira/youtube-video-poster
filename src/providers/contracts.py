"""Additional provider contracts that keep business logic provider-agnostic."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class MusicProvider(ABC):
    """Create licensed music or return a safe no-music result."""

    @abstractmethod
    async def compose(self, *, mood: str, duration_s: float, output_path: Path) -> Path | None:
        """Produce a track without mutating approved dialogue audio."""


class StorageProvider(ABC):
    """Persist and retrieve non-secret production artifacts."""

    @abstractmethod
    async def put(self, local_path: Path, key: str) -> str:
        """Store an artifact and return a non-secret identifier."""

    @abstractmethod
    async def get(self, key: str, destination: Path) -> Path:
        """Retrieve an artifact into a new destination path."""
