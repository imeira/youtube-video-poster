"""Motion Presets Library — reusable animation presets for local animation (§69).

Each preset is a set of ffmpeg filter parameters that produce a specific
camera movement or visual effect from a still image.

§67: Local Animation Engine strategy — prioritize FFmpeg and open source.
§69: Library of motion presets with configurable parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MotionPreset(str, Enum):
    """§69: Reusable motion preset library."""
    SLOW_PUSH_IN = "slow_push_in"
    SLOW_PULL_OUT = "slow_pull_out"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    VERTICAL_REVEAL = "vertical_reveal"
    HERO_REVEAL = "hero_reveal"
    DRAMATIC_ZOOM = "dramatic_zoom"
    GENTLE_FLOAT = "gentle_float"
    PARALLAX_WALK = "parallax_walk"
    STORM_MOTION = "storm_motion"
    FIRE_GLOW = "fire_glow"
    WATER_MOTION = "water_motion"


@dataclass
class MotionParams:
    """Parameters for a motion preset (§69: configurable)."""
    name: str
    zoom_start: float = 1.0
    zoom_end: float = 1.15
    zoom_speed: float = 0.0007
    x_expr: str = "iw/2-(iw/zoom/2)"
    y_expr: str = "ih/2-(ih/zoom/2)"
    # Special effects
    shake_intensity: float = 0.0  # 0 = no shake
    blur_amount: float = 0.0  # motion blur
    overlay_filter: str = ""  # additional ffmpeg filter for FX
    description: str = ""

    def to_zoompan(self, duration_s: float, fps: int = 30, output_w: int = 1920, output_h: int = 1080) -> str:
        """Convert to ffmpeg zoompan filter string."""
        frames = max(1, int(duration_s * fps))
        upscale_w = output_w * 2
        upscale_h = output_h * 2

        zoom = f"min(zoom+{self.zoom_speed},{self.zoom_end})"

        parts = [
            f"scale={upscale_w}:{upscale_h}",
            f"zoompan=z='{zoom}':d={frames}:x='{self.x_expr}':y='{self.y_expr}':"
            f"s={output_w}x{output_h}:fps={fps}",
        ]

        if self.shake_intensity > 0:
            parts.append(f"sendcmd=0.0,{self.shake_intensity}")

        if self.overlay_filter:
            parts.append(self.overlay_filter)

        parts.append("format=yuv420p")
        return ",".join(parts)


# ── Preset Library ────────────────────────────────────────────────────────────

PRESETS: dict[str, MotionParams] = {
    MotionPreset.SLOW_PUSH_IN.value: MotionParams(
        name="Slow Push In",
        zoom_speed=0.0007,
        zoom_end=1.15,
        description="Slow zoom into the center — default for most scenes",
    ),
    MotionPreset.SLOW_PULL_OUT.value: MotionParams(
        name="Slow Pull Out",
        zoom_start=1.15,
        zoom_end=1.0,
        zoom_speed=-0.0007,
        x_expr="iw/2-(iw/zoom/2)",
        y_expr="ih/2-(ih/zoom/2)",
        description="Reveal scene by pulling back",
    ),
    MotionPreset.PAN_LEFT.value: MotionParams(
        name="Pan Left",
        zoom_end=1.3,
        zoom_speed=0.0005,
        x_expr="iw-(iw/zoom)",
        y_expr="ih/2-(ih/zoom/2)",
        description="Camera pans from right to left",
    ),
    MotionPreset.PAN_RIGHT.value: MotionParams(
        name="Pan Right",
        zoom_end=1.3,
        zoom_speed=0.0005,
        x_expr="0",
        y_expr="ih/2-(ih/zoom/2)",
        description="Camera pans from left to right",
    ),
    MotionPreset.VERTICAL_REVEAL.value: MotionParams(
        name="Vertical Reveal",
        zoom_end=1.2,
        zoom_speed=0.0005,
        x_expr="iw/2-(iw/zoom/2)",
        y_expr="0",
        description="Reveal from top to bottom",
    ),
    MotionPreset.HERO_REVEAL.value: MotionParams(
        name="Hero Reveal",
        zoom_start=1.5,
        zoom_end=1.0,
        zoom_speed=-0.001,
        x_expr="iw/2-(iw/zoom/2)",
        y_expr="ih/2-(ih/zoom/2)",
        description="Start zoomed in on hero, pull back to reveal scene",
    ),
    MotionPreset.DRAMATIC_ZOOM.value: MotionParams(
        name="Dramatic Zoom",
        zoom_end=2.0,
        zoom_speed=0.002,
        x_expr="iw/2-(iw/zoom/2)",
        y_expr="ih/2-(ih/zoom/2)",
        description="Fast dramatic zoom for CRITICAL scenes",
    ),
    MotionPreset.GENTLE_FLOAT.value: MotionParams(
        name="Gentle Float",
        zoom_end=1.05,
        zoom_speed=0.0003,
        x_expr="iw/2-(iw/zoom/2)+sin(on/{30})*5",
        y_expr="ih/2-(ih/zoom/2)+cos(on/{30})*3",
        description="Subtle floating motion for peaceful scenes",
    ),
    MotionPreset.PARALLAX_WALK.value: MotionParams(
        name="Parallax Walk",
        zoom_end=1.1,
        zoom_speed=0.0003,
        x_expr="iw/2-(iw/zoom/2)+on/2",
        y_expr="ih/2-(ih/zoom/2)",
        description="Simulated walking movement with horizontal pan",
    ),
    MotionPreset.STORM_MOTION.value: MotionParams(
        name="Storm Motion",
        zoom_end=1.2,
        zoom_speed=0.001,
        x_expr="iw/2-(iw/zoom/2)+sin(on/3)*8",
        y_expr="ih/2-(ih/zoom/2)+sin(on/5)*5",
        shake_intensity=2.0,
        description="Shaky camera for storm/tension scenes",
    ),
    MotionPreset.FIRE_GLOW.value: MotionParams(
        name="Fire Glow",
        zoom_end=1.1,
        zoom_speed=0.0005,
        x_expr="iw/2-(iw/zoom/2)",
        y_expr="ih/2-(ih/zoom/2)+sin(on/8)*2",
        overlay_filter="eq=brightness=0.03:saturation=1.2",
        description="Warm flickering glow for fire/divine scenes",
    ),
    MotionPreset.WATER_MOTION.value: MotionParams(
        name="Water Motion",
        zoom_end=1.08,
        zoom_speed=0.0003,
        x_expr="iw/2-(iw/zoom/2)+sin(on/12)*3",
        y_expr="ih/2-(ih/zoom/2)+cos(on/10)*2",
        description="Gentle wave-like motion for water/ocean scenes",
    ),
}


def get_preset(name: str) -> MotionParams:
    """Get a motion preset by name."""
    return PRESETS.get(name, PRESETS[MotionPreset.SLOW_PUSH_IN.value])


def select_motion_for_scene(
    importance: str,
    emotion: str,
    location: str,
    camera_hint: str = "",
) -> str:
    """Auto-select motion preset based on scene characteristics (§67).

    Returns the preset name string.
    """
    # CRITICAL scenes get dramatic zoom
    if importance == "CRITICAL":
        return MotionPreset.DRAMATIC_ZOOM.value

    # Emotion-based selection
    emotion_lower = emotion.lower()
    if any(w in emotion_lower for w in ["suspense", "medo", "tempestade"]):
        return MotionPreset.STORM_MOTION.value
    if any(w in emotion_lower for w in ["joy", "alegria", "vitória"]):
        return MotionPreset.GENTLE_FLOAT.value
    if any(w in emotion_lower for w in ["awe", "milagre", "criou"]):
        return MotionPreset.FIRE_GLOW.value

    # Location-based
    loc_lower = location.lower()
    if any(w in loc_lower for w in ["mar", "água", "ocean", "rio"]):
        return MotionPreset.WATER_MOTION.value
    if any(w in loc_lower for w in ["fogo", "fire", "montanha"]):
        return MotionPreset.FIRE_GLOW.value

    # Camera hint override
    if camera_hint in PRESETS:
        return camera_hint

    # Default
    return MotionPreset.SLOW_PUSH_IN.value
