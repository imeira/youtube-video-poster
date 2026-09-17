"""Independent deterministic validation of the final delivery container."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import subprocess

from src.hybrid.artifacts import sha256
from src.hybrid.render import probe, measure_loudness


@dataclass(frozen=True)
class FinalRenderQAResult:
    approved: bool
    findings: tuple[str, ...]
    report: dict[str, Any]


class FinalRenderQA:
    """Checks the independently inspectable MP4 contract before human video approval."""

    def __init__(self, *, expected_geometry: tuple[int, int] = (1920, 1080)):
        self.expected_geometry = expected_geometry

    def review(self, video_path: Path | str, render_receipt: dict[str, Any]) -> FinalRenderQAResult:
        findings: list[str] = []
        video_path = Path(video_path)
        if not video_path.is_file():
            return FinalRenderQAResult(False, ("FINAL_VIDEO_MISSING",), {})
        try:
            media = probe(video_path)
        except (RuntimeError, ValueError, OSError, subprocess.SubprocessError):
            return FinalRenderQAResult(False, ("FINAL_MEDIA_INVALID",), {})
        videos = [stream for stream in media["streams"] if stream.get("codec_type") == "video"]
        audios = [stream for stream in media["streams"] if stream.get("codec_type") == "audio"]
        report: dict[str, Any] = {"path": str(video_path.resolve())}
        if any(stream.get("codec_type") == "subtitle" for stream in media["streams"]):
            findings.append("SUBTITLE_STREAMS_FORBIDDEN")
        if render_receipt.get("output_sha256") is not None and render_receipt["output_sha256"] != sha256(video_path):
            findings.append("RENDER_HASH_MISMATCH")
        try:
            measured = measure_loudness(video_path)
            report["loudness"] = measured
            if not -17 <= measured["integrated_lufs"] <= -15:
                findings.append("INTEGRATED_LOUDNESS_OUT_OF_RANGE")
            if measured["true_peak_dbtp"] > -1:
                findings.append("TRUE_PEAK_OUT_OF_RANGE")
            if "loudness" in render_receipt and render_receipt["loudness"] != measured:
                findings.append("LOUDNESS_RECEIPT_MISMATCH")
        except (ValueError, RuntimeError, OSError, subprocess.SubprocessError):
            findings.append("FINAL_AUDIO_ANALYSIS_FAILED")
        if len(videos) != 1:
            findings.append("EXACTLY_ONE_VIDEO_STREAM_REQUIRED")
        if len(audios) != 1:
            findings.append("EXACTLY_ONE_AUDIO_STREAM_REQUIRED")
        if videos:
            video = videos[0]
            report["video_codec"] = video.get("codec_name")
            report["geometry"] = [video.get("width"), video.get("height")]
            if video.get("codec_name") != "h264":
                findings.append("H264_VIDEO_REQUIRED")
            if tuple(report["geometry"]) != self.expected_geometry:
                findings.append("DELIVERY_GEOMETRY_MISMATCH")
            if video.get("pix_fmt") != "yuv420p":
                findings.append("YUV420P_REQUIRED")
        if audios:
            audio = audios[0]
            report["audio_codec"] = audio.get("codec_name")
            if audio.get("codec_name") != "aac":
                findings.append("AAC_AUDIO_REQUIRED")
        report["duration_s"] = float(media["format"].get("duration", 0))
        expected = render_receipt.get("expected_duration")
        if expected is not None:
            if abs(report["duration_s"] - expected) > .08 or any(
                abs(float(stream.get("duration", 0)) - expected) > .08 for stream in (*videos, *audios)
            ):
                findings.append("STREAM_DURATION_MISMATCH")
        if render_receipt.get("subtitles_sha256", "missing") is not None or any(
                term in render_receipt.get("filtergraph", "").lower() for term in ("subtitles=", "ass=", "drawtext=")):
            findings.append("BURNED_SUBTITLES_FORBIDDEN")
        if render_receipt.get("hold_seconds") not in (3, 4, 5):
            findings.append("CLOSING_HOLD_MUST_BE_3_TO_5_SECONDS")
        if render_receipt.get("audio_operation") != "derived_master":
            findings.append("DERIVED_MASTER_REQUIRED")
        return FinalRenderQAResult(not findings, tuple(findings), report)
