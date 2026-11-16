"""Fail-closed technical image inspection before semantic review."""

from __future__ import annotations

import json
import math
import subprocess
from importlib.metadata import version
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from src.hybrid.artifacts import atomic_json, digest, sha256

REQUIRED_FORMAT = "PNG"
REQUIRED_MODE = "RGB"
REQUIRED_SIZE = (1280, 720)


def _image_tool_identity() -> dict:
    """Versioned decoder identity is part of a reusable technical verdict."""
    return {"decoder": "Pillow", "version": version("Pillow")}


def _inspect_image_uncached(path: Path) -> dict:
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


def _valid_cached_packet(packet: object, *, result_sha256: str) -> bool:
    """Accept a closed, digest-bound technical packet only."""
    required = {
        "result_sha256", "format", "mode", "dimensions", "technical_pass",
        "promotion_authorized", "errors",
    }
    return (
        isinstance(packet, dict)
        and set(packet) == required
        and packet.get("result_sha256") == result_sha256
        and (packet["format"] is None or isinstance(packet["format"], str))
        and (packet["mode"] is None or isinstance(packet["mode"], str))
        and (packet["dimensions"] is None or (
            isinstance(packet["dimensions"], list)
            and len(packet["dimensions"]) == 2
            and all(type(value) is int for value in packet["dimensions"])
        ))
        and type(packet["technical_pass"]) is bool
        and packet["promotion_authorized"] is False
        and isinstance(packet["errors"], list)
        and all(isinstance(error, str) for error in packet["errors"])
    )


def inspect_image_cached(path: Path, *, cache_path: Path, contract: dict | None = None) -> dict:
    """Reuse only an exact technical inspection, never independent visual QA."""
    candidate = Path(path)
    result_sha256 = sha256(candidate)
    contract = dict(contract or {})
    contract.update(
        required_format=REQUIRED_FORMAT,
        required_mode=REQUIRED_MODE,
        required_size=list(REQUIRED_SIZE),
    )
    binding = {
        "schema": "technical-image-cache-v1",
        "result_sha256": result_sha256,
        "contract_sha256": digest(contract),
        "tool": _image_tool_identity(),
    }
    cache_path = Path(cache_path)
    if cache_path.exists() and not cache_path.is_file():
        raise ValueError("technical cache path must be a file")
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        cached = None
    expected_keys = {*binding, "contract", "packet", "packet_sha256"}
    if (
        isinstance(cached, dict)
        and set(cached) == expected_keys
        and all(cached.get(key) == value for key, value in binding.items())
        and cached.get("contract") == contract
        and _valid_cached_packet(cached.get("packet"), result_sha256=result_sha256)
        and cached.get("packet_sha256") == digest(cached["packet"])
    ):
        return cached["packet"]
    packet = _inspect_image_uncached(candidate)
    packet["promotion_authorized"] = False
    atomic_json(cache_path, {
        **binding, "contract": contract, "packet": packet,
        "packet_sha256": digest(packet),
    })
    return packet


def inspect_image(path: Path) -> dict:
    """Return immutable technical facts without granting visual approval."""
    return _inspect_image_uncached(path)


def inspect_clip(path: Path, *, expected_sha256: str, geometry: dict, duration_s: float,
                 fps: int, audio_policy: str, predecessor: str, receipt_predecessor: str) -> dict:
    """Decode and verify an exact selected I2V clip without granting semantic approval."""
    candidate = Path(path)
    packet = {"result_sha256": sha256(candidate), "technical_pass": False,
              "promotion_authorized": False, "errors": [], "predecessor": receipt_predecessor}
    if packet["result_sha256"] != expected_sha256:
        packet["errors"].append("clip hash does not match receipt")
    if not predecessor or predecessor != receipt_predecessor:
        packet["errors"].append("clip predecessor does not match approved source")
    if (not isinstance(geometry, dict) or set(geometry) != {"width", "height"}
            or type(fps) is not int or fps <= 0 or audio_policy not in {"REQUIRED", "FORBIDDEN"}
            or type(duration_s) not in {int, float} or not math.isfinite(duration_s) or duration_s <= 0):
        packet["errors"].append("selected clip contract is invalid")
        return packet
    try:
        facts = json.loads(subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(candidate)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout)
        videos = [stream for stream in facts["streams"] if stream["codec_type"] == "video"]
        audios = [stream for stream in facts["streams"] if stream["codec_type"] == "audio"]
        if len(videos) != 1:
            packet["errors"].append("clip must contain exactly one video stream")
        else:
            video = videos[0]
            numerator, denominator = (int(part) for part in video.get("avg_frame_rate", "0/0").split("/", 1))
            actual_fps = numerator / denominator if denominator else 0
            packet.update(dimensions=[video.get("width"), video.get("height")], fps=actual_fps,
                          duration=float(facts["format"]["duration"]), audio_streams=len(audios))
            if packet["dimensions"] != [geometry["width"], geometry["height"]]:
                packet["errors"].append("clip geometry does not match selected route")
            if abs(actual_fps - fps) > 0.01:
                packet["errors"].append("clip fps does not match selected route")
            if abs(packet["duration"] - duration_s) > 1 / fps + 0.02:
                packet["errors"].append("clip duration does not match selected route")
        if (audio_policy == "REQUIRED" and len(audios) != 1) or (audio_policy == "FORBIDDEN" and audios):
            packet["errors"].append("clip audio policy does not match selected route")
        try:
            subprocess.run(
                ["ffmpeg", "-xerror", "-v", "error", "-nostdin", "-i", str(candidate),
                 "-map", "0:v:0", "-f", "null", "-"],
                capture_output=True, text=True, timeout=180, check=True,
            )
        except subprocess.SubprocessError:
            packet["errors"].append("clip video decode failed")
        if audio_policy == "REQUIRED" and len(audios) == 1:
            try:
                subprocess.run(
                    ["ffmpeg", "-xerror", "-v", "error", "-nostdin", "-i", str(candidate),
                     "-map", "0:a:0", "-f", "null", "-"],
                    capture_output=True, text=True, timeout=180, check=True,
                )
            except subprocess.SubprocessError:
                packet["errors"].append("clip audio decode failed")
    except (OSError, KeyError, ValueError, json.JSONDecodeError, subprocess.SubprocessError) as exc:
        packet["errors"].append(f"clip probe/decode failed: {type(exc).__name__}")
    packet["technical_pass"] = not packet["errors"]
    return packet
