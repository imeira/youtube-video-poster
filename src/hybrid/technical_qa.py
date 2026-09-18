"""Fail-closed technical image inspection before semantic review."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, UnidentifiedImageError

from src.hybrid.artifacts import sha256

REQUIRED_FORMAT = "PNG"
REQUIRED_MODE = "RGB"
REQUIRED_SIZE = (1280, 720)


def inspect_image(path: Path) -> dict:
    """Return immutable technical facts without granting visual approval."""
    candidate = Path(path)
    packet = {
        "result_sha256": sha256(candidate),
        "format": None,
        "mode": None,
        "dimensions": None,
        "technical_pass": False,
        "promotion_authorized": False,
        "errors": [],
    }
    try:
        with Image.open(candidate) as image:
            image.load()
            packet.update(
                format=image.format,
                mode=image.mode,
                dimensions=list(image.size),
            )
    except (OSError, UnidentifiedImageError) as exc:
        packet["errors"].append(f"image decode failed: {type(exc).__name__}")
        return packet

    if packet["format"] != REQUIRED_FORMAT:
        packet["errors"].append(f"format must be {REQUIRED_FORMAT}")
    if packet["mode"] != REQUIRED_MODE:
        packet["errors"].append(f"mode must be {REQUIRED_MODE}")
    if packet["dimensions"] != list(REQUIRED_SIZE):
        packet["errors"].append("dimensions must be 1280x720")
    packet["technical_pass"] = not packet["errors"]
    return packet
