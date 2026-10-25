"""Fail-closed technical image inspection before semantic review."""

from __future__ import annotations

import json
import math
import subprocess
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
