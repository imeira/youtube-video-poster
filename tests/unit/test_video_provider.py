"""Tests for Local FFmpeg Video Provider (§49-52, B0).

Tests:
- Motion preset filter generation
- Image-to-video (Ken Burns) with real ffmpeg
- Concat clips
- Transition between clips
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from src.providers.video.local_ffmpeg_provider import (
    LocalFFmpegVideoProvider,
    MOTION_PRESETS,
)


@pytest.fixture
def test_image(tmp_path: Path) -> str:
    """Create a test image using ffmpeg."""
    img = tmp_path / "test_still.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=blue:s=1920x1080:d=1", "-frames:v", "1", str(img)],
        check=True, timeout=15,
    )
    return str(img)


@pytest.fixture
def video_provider():
    return LocalFFmpegVideoProvider(output_w=1920, output_h=1080, fps=30)


class TestMotionPresets:
    """§52: Motion presets."""

    def test_presets_exist(self):
        """All required motion presets (§52) should be defined."""
        required = [
            "slow_push_in", "slow_pull_out", "pan_left", "pan_right",
            "dramatic_zoom", "gentle_float",
        ]
        for name in required:
            assert name in MOTION_PRESETS, f"Missing preset: {name}"

    def test_preset_has_zoom_and_position(self):
        """Each preset should have zoom, x, and y expressions."""
        for name, preset in MOTION_PRESETS.items():
            assert "zoom_expr" in preset, f"{name} missing zoom_expr"
            assert "x_expr" in preset, f"{name} missing x_expr"
            assert "y_expr" in preset, f"{name} missing y_expr"


class TestImageToVideo:
    """Generate video from still image."""

    @pytest.mark.asyncio
    async def test_ken_burns_5s(self, test_image, video_provider, tmp_path):
        """Generate a 5s Ken Burns clip."""
        output = str(tmp_path / "kb.mp4")
        provider = LocalFFmpegVideoProvider()
        result = await provider.image_to_video(
            image_path=test_image,
            duration=5,
            motion="slow_push_in",
        )
        assert result.success
        assert os.path.exists(result.video_path)
        assert result.duration_seconds == 5
        assert result.metadata["motion"] == "slow_push_in"
        assert result.metadata["encoder"] == "libx264"

    @pytest.mark.asyncio
    async def test_pan_left_3s(self, test_image, video_provider):
        """Generate a 3s pan-left clip."""
        result = await video_provider.image_to_video(
            image_path=test_image,
            duration=3,
            motion="pan_left",
        )
        assert result.success
        assert result.metadata["motion"] == "pan_left"

    @pytest.mark.asyncio
    async def test_concat(self, test_image, video_provider, tmp_path):
        """Concatenate two clips."""
        # Generate two clips
        clip_a = await video_provider.image_to_video(
            image_path=test_image, duration=3, motion="slow_push_in",
        )
        clip_b = await video_provider.image_to_video(
            image_path=test_image, duration=3, motion="pan_right",
        )
        output = str(tmp_path / "concat.mp4")
        result = await video_provider.concat_clips(
            [clip_a.video_path, clip_b.video_path], output
        )
        assert result.success
        assert os.path.exists(output)


class TestCost:
    """Local ffmpeg is free."""

    def test_cost_zero(self, video_provider):
        assert video_provider.estimate_cost() == 0.0
