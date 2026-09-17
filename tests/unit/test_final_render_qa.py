"""Independent technical final-render QA validates delivery media and receipt bindings."""

from __future__ import annotations

import shutil
import subprocess

import pytest

from src.qa.final_render import FinalRenderQA

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="real ffmpeg/ffprobe unavailable",
)


def make_delivery_video(path):
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )


def test_final_render_qa_requires_h264_aac_no_burned_subtitles_and_closing_hold(tmp_path):
    video = tmp_path / "delivery.mp4"
    make_delivery_video(video)
    receipt = {
        "output": str(video),
        "subtitles_sha256": None,
        "hold_seconds": 4,
        "audio_operation": "derived_master",
    }

    result = FinalRenderQA(expected_geometry=(320, 180)).review(video, receipt)

    assert result.approved is True
    assert result.report["video_codec"] == "h264"
    assert result.report["audio_codec"] == "aac"


def test_final_render_qa_rejects_burned_subtitles_or_unmastered_audio_receipt(tmp_path):
    video = tmp_path / "delivery.mp4"
    make_delivery_video(video)

    result = FinalRenderQA(expected_geometry=(320, 180)).review(
        video,
        {"subtitles_sha256": "not-allowed", "hold_seconds": 2, "audio_operation": "stream_copy"},
    )

    assert result.approved is False
    assert "BURNED_SUBTITLES_FORBIDDEN" in result.findings
    assert "CLOSING_HOLD_MUST_BE_3_TO_5_SECONDS" in result.findings
    assert "DERIVED_MASTER_REQUIRED" in result.findings


def test_final_render_qa_rejects_short_video_even_when_container_duration_matches(tmp_path, monkeypatch):
    video = tmp_path / "delivery.mp4"
    video.write_bytes(b"probe supplied independently")
    monkeypatch.setattr("src.qa.final_render.probe", lambda path: {
        "format": {"duration": "10"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
             "pix_fmt": "yuv420p", "duration": "8"},
            {"codec_type": "audio", "codec_name": "aac", "duration": "10"},
        ],
    })
    result = FinalRenderQA().review(video, {"expected_duration": 10, "hold_seconds": 4,
        "subtitles_sha256": None, "audio_operation": "derived_master"})
    assert result.findings == ("STREAM_DURATION_MISMATCH",)
