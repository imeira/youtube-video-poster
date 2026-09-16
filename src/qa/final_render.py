"""Independent deterministic validation of the final delivery container."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.hybrid.render import probe


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
        media = probe(video_path)
        videos = [stream for stream in media["streams"] if stream.get("codec_type") == "video"]
        audios = [stream for stream in media["streams"] if stream.get("codec_type") == "audio"]
        report: dict[str, Any] = {"path": str(video_path.resolve())}
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
        if render_receipt.get("subtitles_sha256") is not None:
            findings.append("BURNED_SUBTITLES_FORBIDDEN")
        if render_receipt.get("hold_seconds") not in (3, 4, 5):
            findings.append("CLOSING_HOLD_MUST_BE_3_TO_5_SECONDS")
        if render_receipt.get("audio_operation") != "derived_master":
            findings.append("DERIVED_MASTER_REQUIRED")
        return FinalRenderQAResult(not findings, tuple(findings), report)
