"""Bounded values that are safe to persist across provider boundaries."""

from __future__ import annotations


def provider_error(_error: object) -> str:
    """Do not persist provider-controlled text, which can contain credentials."""
    return "PROVIDER_ERROR"