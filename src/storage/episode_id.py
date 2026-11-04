"""Literal episode identifier validation for filesystem-backed episode state."""

from __future__ import annotations

import re

_LITERAL_EPISODE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}


def validate_episode_id(episode_id: str) -> str:
    """Accept one literal episode storage key without rewriting caller input."""
    if type(episode_id) is not str or not _LITERAL_EPISODE_ID.fullmatch(episode_id):
        raise ValueError("episode_id must be a literal filesystem-safe identifier")
    if episode_id.upper() in _WINDOWS_RESERVED_NAMES:
        raise ValueError("episode_id must not be a Windows reserved device name")
    return episode_id
