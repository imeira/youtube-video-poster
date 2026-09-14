"""Real local FFmpeg rendering with immutable approved audio stream-copy.

MKV supports approved PCM WAV without rewriting the source audio. MP4 requires
an already approved MP4-compatible audio codec; no implicit transcoding occurs.
"""

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from src.hybrid.artifacts import FrozenAsset, atomic_json, sha256


def command(args, *, cwd=None):
    result = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=1800, check=False
    )
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr[-4000:]}")
    return result.stdout


def probe(path):
    return json.loads(
        command(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ]
        )
    )


def _publish_new(source, output):
    """Atomically publish within one filesystem without replacing another render."""
    source, output = Path(source), Path(output)
    os.link(source, output)
    source.unlink()


@dataclass(frozen=True)
class Scene:
    image: FrozenAsset
    seconds: float
    clip: FrozenAsset | None = None

    def __post_init__(self):
        if not math.isfinite(self.seconds) or self.seconds <= 0.25:
            raise ValueError("scene duration must exceed transition")
        if self.clip is not None and self.seconds != 5:
            raise ValueError("hero slot must be exactly five seconds")

    def verify(self, manifest):
        self.image.verify(manifest.mode)
        if self.image.sha256 not in {asset.sha256 for asset in manifest.assets}:
            raise ValueError("scene missing from approved contact sheet")
        if self.clip is not None:
            self.clip.verify(manifest.mode)


def timing(scenes, *, hold=4, fps=None):
    if not scenes or not math.isfinite(hold) or not 3 <= hold <= 5:
        raise ValueError("scenes and final hold of 3-5 seconds required")
    if fps is not None and (type(fps) is not int or fps <= 0):
        raise ValueError("positive integer fps required")
    offsets = []
    elapsed = 0
    boundaries = []
    for scene in scenes:
        elapsed += scene.seconds
        boundaries.append(round(elapsed * fps) / fps if fps else elapsed)
    offsets = boundaries[:-1]
    starts = [0, *boundaries[:-1]]
    frame_seconds = [end - start for start, end in zip(starts, boundaries)]
    return {
        "render_seconds": [
            seconds + (0.25 if i < len(scenes) - 1 else 0)
            for i, seconds in enumerate(frame_seconds)
        ],
        "offsets": offsets,
        "frame_seconds": frame_seconds,
        "final_seconds": boundaries[-1] + hold,
    }


def validate_srt(path, duration):
    text = Path(path).read_text(encoding="utf-8-sig").strip()
    blocks = re.split(r"\r?\n\s*\r?\n", text)
    stamp = r"(\d{2}):([0-5]\d):([0-5]\d),(\d{3})"
    previous = 0.0
    for index, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if len(lines) < 3 or lines[0] != str(index) or not "".join(lines[2:]).strip():
            raise ValueError("invalid approved SRT block")
        match = re.fullmatch(stamp + r" --> " + stamp, lines[1])
        if not match:
            raise ValueError("invalid SRT timestamps")
        fields = list(map(int, match.groups()))

        def seconds(values):
            h, m, s, ms = values
            return h * 3600 + m * 60 + s + ms / 1000

        start, end = seconds(fields[:4]), seconds(fields[4:])
        if start < previous or end <= start or end > duration + 0.05:
            raise ValueError("SRT outside approved narration timeline")
        previous = end


class LocalRenderer:
    def __init__(self, width=1920, height=1080, fps=30, *, filter_complex_threads=None):
        if (
            any(type(n) is not int or n <= 0 for n in (width, height, fps))
            or width % 2
            or height % 2
        ):
            raise ValueError("positive fps and even geometry required")
        if filter_complex_threads is not None and (
            type(filter_complex_threads) is not int or filter_complex_threads < 1
        ):
            raise ValueError("positive filter-complex thread budget required")
        self.width, self.height, self.fps = width, height, fps
        self.filter_complex_threads = filter_complex_threads

    def render_compiled(self, production, scenes, manifest, audio, srt, output, *, hold=4):
        """Render only after the compiled control plane has immutable QA receipts."""
        if not production.render_ready():
            raise ValueError("compiled QA receipts are incomplete; rendering blocked")
        return self.render(scenes, manifest, audio, srt, output, hold=hold)

    def render(self, scenes, manifest, audio, srt, output, *, hold=4):
        timeline = timing(scenes, hold=hold, fps=self.fps)
        mode = manifest.mode
        manifest.verify(mode)
        audio.verify(mode)
        if srt is not None:
            srt.verify(mode)
        for scene in scenes:
            scene.verify(manifest)
        duration = sum(s.seconds for s in scenes)
        audio_info = probe(audio.path)
        audio_streams = [s for s in audio_info["streams"] if s["codec_type"] == "audio"]
        if len(audio_streams) != 1:
            raise ValueError("exactly one approved audio stream required")
        if abs(float(audio_info["format"]["duration"]) - duration) > 1 / self.fps:
            raise ValueError("scene timeline must match approved audio duration")
        if srt is not None:
            validate_srt(srt.path, duration)
        output = Path(output).resolve()
        protected = {a.path for a in (*manifest.assets, manifest.sheet, audio)}
        if srt is not None:
            protected.add(srt.path)
        protected.update(s.clip.path for s in scenes if s.clip is not None)
        if output in protected or output.exists():
            raise ValueError(
                "output must be new; approved assets cannot be overwritten"
            )
        output.parent.mkdir(parents=True, exist_ok=True)
        render_seconds = timeline["render_seconds"]
        with tempfile.TemporaryDirectory(
            prefix="hybrid-render-", dir=output.parent
        ) as directory:
            root = Path(directory)
            # Snapshot exact approved bytes so rendering never reads mutable originals.
            if srt is not None:
                shutil.copyfile(srt.path, root / "approved.srt")
                if sha256(root / "approved.srt") != srt.sha256:
                    raise ValueError("SRT snapshot hash mismatch")
            audio_copy = root / ("approved_audio" + audio.path.suffix)
            shutil.copyfile(audio.path, audio_copy)
            if sha256(audio_copy) != audio.sha256:
                raise ValueError("audio snapshot hash mismatch")
            for index, (scene, seconds) in enumerate(zip(scenes, render_seconds)):
                if scene.clip is not None:
                    source = root / f"hero{index}{scene.clip.path.suffix}"
                    shutil.copyfile(scene.clip.path, source)
                    if sha256(source) != scene.clip.sha256:
                        raise ValueError("hero snapshot hash mismatch")
                    info = probe(source)
                    videos = [s for s in info["streams"] if s["codec_type"] == "video"]
                    if (
                        len(videos) != 1
                        or abs(float(info["format"]["duration"]) - 5) > 0.1
                    ):
                        raise ValueError("hero must be real five-second video")
                    if mode == "LIVE" and (videos[0]["width"], videos[0]["height"]) != (
                        1280,
                        720,
                    ):
                        raise ValueError("LIVE hero must be 720p")
                    filters = (
                        f"scale={self.width}:{self.height},setsar=1,fps={self.fps},"
                        "tpad=stop_mode=clone:stop_duration=0.25,format=yuv420p"
                    )
                    command(
                        [
                            "ffmpeg",
                            "-v",
                            "error",
                            "-i",
                            str(source),
                            "-vf",
                            filters,
                            "-t",
                            str(seconds),
                            "-an",
                            "-c:v",
                            "libx264",
                            "-preset",
                            "veryfast",
                            str(root / f"scene{index}.mp4"),
                        ]
                    )
                    continue
                source = root / f"source{index}{scene.image.path.suffix}"
                shutil.copyfile(scene.image.path, source)
                if sha256(source) != scene.image.sha256:
                    raise ValueError("image snapshot hash mismatch")
                frames = round(seconds * self.fps)
                motion = (
                    f"scale={self.width}:{self.height}:force_original_aspect_ratio=increase,"
                    f"crop={self.width}:{self.height},"
                    f"zoompan=z='min(1+on*0.0002,1.04)':x='iw/2-iw/zoom/2':"
                    f"y='ih/2-ih/zoom/2':d={frames}:s={self.width}x{self.height}:fps={self.fps},"
                    "setsar=1,format=yuv420p"
                )
                command(
                    [
                        "ffmpeg",
                        "-v",
                        "error",
                        "-i",
                        str(source),
                        "-vf",
                        motion,
                        "-frames:v",
                        str(frames),
                        "-an",
                        "-c:v",
                        "libx264",
                        "-preset",
                        "veryfast",
                        str(root / f"scene{index}.mp4"),
                    ]
                )
            args = ["ffmpeg", "-v", "error"]
            if self.filter_complex_threads is not None:
                args += ["-filter_complex_threads", str(self.filter_complex_threads)]
            for index in range(len(scenes)):
                args += ["-i", str(root / f"scene{index}.mp4")]
            filters = [
                f"[{i}:v]settb=AVTB,setpts=PTS-STARTPTS[v{i}]"
                for i in range(len(scenes))
            ]
            last = "v0"
            for index in range(1, len(scenes)):
                target = f"mix{index}"
                filters.append(
                    f"[{last}][v{index}]xfade=transition=fade:duration=0.25:"
                    f"offset={timeline['offsets'][index - 1]}[{target}]"
                )
                last = target
            subtitle_filter = (
                ",subtitles=filename=approved.srt" if srt is not None else ""
            )
            filters.append(
                f"[{last}]tpad=stop_mode=clone:stop_duration={hold}"
                f"{subtitle_filter},format=yuv420p[final]"
            )
            args += [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[final]",
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-t",
                str(timeline["final_seconds"]),
                str(root / "visual.mp4"),
            ]
            command(args, cwd=root)
            master = root / ("master" + output.suffix)
            command(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(root / "visual.mp4"),
                    "-i",
                    str(audio_copy),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "copy",
                    str(master),
                ]
            )
            final_info = probe(master)
            if (
                abs(float(final_info["format"]["duration"]) - timeline["final_seconds"])
                > 1 / self.fps + 0.02
            ):
                raise ValueError("rendered duration failed verification")
            manifest.verify(mode)
            for scene in scenes:
                scene.verify(manifest)
            audio.verify(mode)
            if srt is not None:
                srt.verify(mode)
            _publish_new(master, output)
        receipt = {
            "output": str(output),
            "output_sha256": sha256(output),
            "mode": mode,
            "manifest_sha256": manifest.checksum,
            "audio_sha256": audio.sha256,
            "subtitles_sha256": srt.sha256 if srt is not None else None,
            "api_cost": 0,
            "transition_seconds": 0.25,
            "scene_render_seconds": render_seconds,
            "hold_seconds": hold,
            "audio_operation": "stream_copy",
            "hero_sha256": [s.clip.sha256 for s in scenes if s.clip is not None],
            "filter_complex_threads": self.filter_complex_threads,
        }
        atomic_json(output.with_name(output.name + ".receipt.json"), receipt)
        return receipt
