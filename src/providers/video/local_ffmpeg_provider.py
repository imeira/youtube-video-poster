"""Local FFmpeg Video Provider — Ken Burns, parallax, transitions.

§49-52: Local animation engine using ffmpeg.
B0: libx264 CPU only (NVENC broken, QSV slower).
Phase 0 benchmark: Ken Burns 3.56s/5s clip, parallax 15s/5s, concat 0.77s.

Motion presets (§52):
  slow_push_in, slow_pull_out, pan_left, pan_right, vertical_reveal,
  hero_reveal, dramatic_zoom, gentle_float, parallax_walk,
  storm_motion, fire_glow, water_motion
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import time
import logging
from pathlib import Path
from typing import Any

from src.providers.base import VideoProvider, VideoResult

logger = logging.getLogger(__name__)


# Motion presets (§52)
MOTION_PRESETS = {
    "slow_push_in": {
        "description": "Slow zoom into the center",
        # zoompan: z goes from 1.0 to 1.15 over the clip duration
        "zoom_expr": "min(zoom+0.0007,1.15)",
        "x_expr": "iw/2-(iw/zoom/2)",
        "y_expr": "ih/2-(ih/zoom/2)",
    },
    "slow_pull_out": {
        "description": "Slow zoom out from center",
        "zoom_expr": "if(eq(on,0),1.15,max(zoom-0.0007,1.0))",
        "x_expr": "iw/2-(iw/zoom/2)",
        "y_expr": "ih/2-(ih/zoom/2)",
    },
    "pan_left": {
        "description": "Pan from right to left",
        "zoom_expr": "1.1",
        "x_expr": "(iw-iw/zoom)*(1-on/{frames})",
        "y_expr": "ih/2-(ih/zoom/2)",
    },
    "pan_right": {
        "description": "Pan from left to right",
        "zoom_expr": "1.1",
        "x_expr": "(iw-iw/zoom)*(on/{frames})",
        "y_expr": "ih/2-(ih/zoom/2)",
    },
    "dramatic_zoom": {
        "description": "Fast dramatic zoom in",
        "zoom_expr": "min(zoom+0.002,1.3)",
        "x_expr": "iw/2-(iw/zoom/2)",
        "y_expr": "ih/2-(ih/zoom/2)",
    },
    "gentle_float": {
        "description": "Gentle vertical floating motion",
        "zoom_expr": "1.05",
        "x_expr": "iw/2-(iw/zoom/2)",
        "y_expr": "ih/2-(ih/zoom/2)+sin(on/{frames}*PI*2)*10",
    },
}


class LocalFFmpegVideoProvider(VideoProvider):
    """Local video generation using ffmpeg motion presets.

    Uses libx264 CPU encoder (B0: NVENC broken, QSV slower).
    Generates Ken Burns, pan, zoom, and parallax effects from still images.
    """

    def __init__(
        self,
        output_w: int = 1920,
        output_h: int = 1080,
        fps: int = 30,
        preset: str = "veryfast",
        crf: int = 20,
    ):
        self.output_w = output_w
        self.output_h = output_h
        self.fps = fps
        self.preset = preset  # libx264 preset
        self.crf = crf

    def estimate_cost(self, **params) -> float:
        """Local ffmpeg is free."""
        return 0.0

    def _build_zoompan_filter(self, motion: str, duration_s: float) -> str:
        """Build the ffmpeg zoompan filter string for a motion preset."""
        frames = int(duration_s * self.fps)
        preset = MOTION_PRESETS.get(motion, MOTION_PRESETS["slow_push_in"])

        # Upscale to 4K for smooth zoom, then zoompan to output size
        upscale_w = self.output_w * 2  # 3840
        upscale_h = self.output_h * 2  # 2160

        zoom = preset["zoom_expr"]
        x = preset["x_expr"].replace("{frames}", str(frames))
        y = preset["y_expr"].replace("{frames}", str(frames))

        return (
            f"scale={upscale_w}:{upscale_h},"
            f"zoompan=z='{zoom}':d={frames}:x='{x}':y='{y}':"
            f"s={self.output_w}x{self.output_h}:fps={self.fps},"
            f"format=yuv420p"
        )

    async def image_to_video(
        self,
        image_path: str,
        prompt: str = "",
        duration: int = 5,
        motion: str = "slow_push_in",
    ) -> VideoResult:
        """Animate a still image using ffmpeg motion presets.

        Args:
            image_path: Path to the source still image.
            prompt: Unused for local generation (kept for interface compat).
            duration: Clip duration in seconds.
            motion: Motion preset name (§52).

        Returns:
            VideoResult with video_path and generation_time.
        """
        output_dir = os.environ.get(
            "STUDIO_ANIMATION_DIR",
            os.path.expanduser("~/AppData/Local/Temp/studio_animation"),
        )
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"anim_{int(time.time())}.mp4")

        vf = self._build_zoompan_filter(motion, duration)

        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-loop", "1", "-i", image_path,
            "-t", str(duration),
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", self.preset,
            "-crf", str(self.crf),
            output_path,
        ]

        t_start = time.time()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            gen_time = time.time() - t_start

            if proc.returncode != 0:
                return VideoResult(
                    success=False,
                    error=f"ffmpeg failed (exit {proc.returncode}): {proc.stderr[:500]}",
                )

            return VideoResult(
                success=True,
                video_path=output_path,
                duration_seconds=duration,
                generation_time=gen_time,
                cost=0.0,
                metadata={
                    "motion": motion,
                    "resolution": f"{self.output_w}x{self.output_h}",
                    "fps": self.fps,
                    "encoder": "libx264",
                    "preset": self.preset,
                    "crf": self.crf,
                },
            )
        except subprocess.TimeoutExpired:
            return VideoResult(success=False, error="ffmpeg timeout (300s)")
        except Exception as e:
            return VideoResult(success=False, error=str(e))

    async def concat_clips(self, clip_paths: list[str], output_path: str) -> VideoResult:
        """Concatenate clips using ffmpeg (stream copy, very fast).

        Phase 0: concat 8 clips measured at 0.77s.
        """
        list_file = output_path + ".txt"
        with open(list_file, "w") as f:
            for clip in clip_paths:
                # Use forward slashes for ffmpeg compatibility
                f.write(f"file '{clip.replace(chr(92), '/')}'\n")

        t_start = time.time()
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            output_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        gen_time = time.time() - t_start

        os.unlink(list_file)

        if proc.returncode != 0:
            return VideoResult(success=False, error=f"concat failed: {proc.stderr[:500]}")

        return VideoResult(
            success=True,
            video_path=output_path,
            generation_time=gen_time,
            cost=0.0,
            metadata={"clips": len(clip_paths)},
        )

    async def add_transition(
        self,
        clip_a: str,
        clip_b: str,
        output_path: str,
        transition: str = "fade",
        duration_s: float = 1.0,
    ) -> VideoResult:
        """Add an xfade transition between two clips."""
        # Get duration of clip A
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", clip_a],
            capture_output=True, text=True, timeout=10,
        )
        clip_a_dur = float(probe.stdout.strip()) if probe.stdout.strip() else 5.0
        offset = max(0, clip_a_dur - duration_s)

        t_start = time.time()
        cmd = [
            "ffmpeg", "-y", "-v", "error",
            "-i", clip_a, "-i", clip_b,
            "-filter_complex",
            f"[0:v][1:v]xfade=transition={transition}:duration={duration_s}:offset={offset},format=yuv420p",
            "-c:v", "libx264", "-preset", self.preset, "-crf", str(self.crf),
            output_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        gen_time = time.time() - t_start

        if proc.returncode != 0:
            return VideoResult(success=False, error=f"xfade failed: {proc.stderr[:500]}")

        return VideoResult(
            success=True,
            video_path=output_path,
            generation_time=gen_time,
            cost=0.0,
            metadata={"transition": transition, "duration": duration_s},
        )

    async def execute(self, **params) -> VideoResult:
        """Execute video generation."""
        return await self.image_to_video(**params)
