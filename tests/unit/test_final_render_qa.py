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
            "-pix_fmt", "yuv420p", "-af", "loudnorm=I=-16:TP=-2", "-c:a", "aac", "-shortest", str(path),
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



def test_final_render_qa_rejects_mismatched_stream_duration(tmp_path):
    video = tmp_path / "short-video.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=320x180:d=1",
        "-f", "lavfi", "-i", "sine=duration=2", "-af", "loudnorm=I=-16:TP=-2",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)],
        check=True, capture_output=True)
    result = FinalRenderQA(expected_geometry=(320, 180)).review(video, receipt(expected_duration=2))
    assert not result.approved
    assert "STREAM_DURATION_MISMATCH" in result.findings


def receipt(**kwargs):
    return dict(subtitles_sha256=None, hold_seconds=4, audio_operation="derived_master", **kwargs)


@pytest.mark.parametrize("audio,filter_name,finding", [
    ("sine=duration=3", "volume=0.1", "INTEGRATED_LOUDNESS_OUT_OF_RANGE"),
    ("sine=duration=3", "volume=5", "INTEGRATED_LOUDNESS_OUT_OF_RANGE"),
    ("aevalsrc=0.98*sin(2*PI*997*t):d=3:s=48000", "anull", "TRUE_PEAK_OUT_OF_RANGE"),
    ("anullsrc=r=48000:cl=mono:d=3", "anull", "FINAL_AUDIO_ANALYSIS_FAILED"),
])
def test_measured_encoded_audio_rejects_bad_levels(tmp_path, audio, filter_name, finding):
    video = tmp_path / 'bad.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=s=320x180:d=3',
        '-f', 'lavfi', '-i', audio, '-af', filter_name, '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-c:a', 'aac', '-shortest', str(video)], check=True, capture_output=True)
    result = FinalRenderQA(expected_geometry=(320, 180)).review(video, receipt())
    assert not result.approved and finding in result.findings


def test_corrupt_media_and_subtitle_stream_fail_closed(tmp_path):
    corrupt = tmp_path / 'corrupt.mp4'
    corrupt.write_bytes(b'not a media container')
    assert not FinalRenderQA().review(corrupt, receipt()).approved
    clean = tmp_path / 'clean.mp4'
    make_delivery_video(clean)
    srt = tmp_path / 'subtitle.srt'
    srt.write_text('1\n00:00:00,000 --> 00:00:00,900\nForbidden embedded subtitle\n')
    embedded = tmp_path / 'embedded.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-i', str(clean), '-i', str(srt),
        '-map', '0', '-map', '1', '-c', 'copy', '-c:s', 'mov_text', str(embedded)],
        check=True, capture_output=True)
    result = FinalRenderQA(expected_geometry=(320, 180)).review(embedded, receipt())
    assert not result.approved and 'SUBTITLE_STREAMS_FORBIDDEN' in result.findings


def test_measured_receipt_cannot_claim_other_levels(tmp_path):
    video = tmp_path / 'good.mp4'
    make_delivery_video(video)
    from src.hybrid.render import measure_loudness
    measured = measure_loudness(video)
    qa = FinalRenderQA(expected_geometry=(320, 180))
    assert qa.review(video, receipt(loudness=measured)).approved
    result = qa.review(video, receipt(loudness=dict(integrated_lufs=-16, true_peak_dbtp=0)))
    assert not result.approved and 'LOUDNESS_RECEIPT_MISMATCH' in result.findings

@pytest.mark.parametrize('graph', ['[0:v]subtitles=captions.srt[v]', '[0:v]ass=captions.ass[v]', '[0:v]drawtext=text=caption[v]'])
def test_zero_burn_filter_contract(tmp_path, graph):
    video = tmp_path / 'clean.mp4'
    make_delivery_video(video)
    result = FinalRenderQA(expected_geometry=(320, 180)).review(video, receipt(filtergraph=graph))
    assert not result.approved and 'BURNED_SUBTITLES_FORBIDDEN' in result.findings
