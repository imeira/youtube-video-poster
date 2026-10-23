"""TEST-only prompt-to-staged-media harness; it never calls media providers or LIVE adapters."""
from __future__ import annotations

import argparse
import json
import subprocess
from hashlib import sha256
from pathlib import Path

from PIL import Image, ImageDraw

from src.hybrid.autonomous import _atomic_json


def parse_prompt(prompt: str) -> dict[str, str]:
    lines = prompt.splitlines()
    fields = {}
    for line in lines[1:]:
        key, separator, value = line.partition(":")
        if separator:
            fields[key.strip().casefold()] = value.strip()
    if not lines or not lines[0].startswith("Poste um video no ") or not all(fields.get(k) for k in ("idioma", "playlist", "tema")):
        raise ValueError("literal Portuguese prompt requires channel, Idioma, Playlist and Tema")
    return {"prompt": prompt, "channel": lines[0].removeprefix("Poste um video no "), "language": fields["idioma"], "playlist": fields["playlist"], "theme": fields["tema"]}


def start(workspace: Path, prompt: str) -> dict:
    request = parse_prompt(prompt)
    raw = prompt.encode("utf-8")
    request_hash = sha256(raw).hexdigest()
    root = workspace.resolve() / "TEST" / request_hash
    if root.exists():
        raise ValueError("request namespace already exists; never overwrite a previous episode")
    root.mkdir(parents=True)
    (root / "prompt.txt").write_bytes(raw)
    thumb = root / "thumbnail.png"
    image = Image.new("RGB", (640, 360), "#14213d")
    ImageDraw.Draw(image).multiline_text((20, 20), "TEST ONLY — NOT HUMAN APPROVED\n" + request["theme"], fill="white")
    image.save(thumb)
    video = root / "video.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i", "color=c=navy:s=640x360:d=1", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video)], check=True)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(video)], capture_output=True, text=True, check=True)
    if not any(stream.get("codec_type") == "video" for stream in json.loads(probe.stdout)["streams"]):
        raise ValueError("FFmpeg did not create a decodable test video")
    result = {"mode": "TEST", "request": request, "prompt_sha256": request_hash, "artifacts": {"thumbnail": str(thumb), "video": str(video)}, "state": "WAITING_THUMBNAIL_APPROVAL", "live": "BLOCKED: no qualified LIVE adapter/policy", "note": "Artifacts are staged TEST media, not human-approved and not an episode delivery."}
    _atomic_json(root / "harness.json", result)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "status", "resume"))
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--request-sha256")
    args = parser.parse_args(argv)
    try:
        if args.action == "start":
            if args.prompt_file is None:
                parser.error("--prompt-file required for start")
            result = start(args.workspace, args.prompt_file.read_text(encoding="utf-8"))
        else:
            if not args.request_sha256:
                parser.error("--request-sha256 required")
            result = json.loads((args.workspace / "TEST" / args.request_sha256 / "harness.json").read_text(encoding="utf-8"))
            if args.action == "resume":
                result["resume"] = "No provider work to resume; TEST artifacts remain staged awaiting real human gates."
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(json.dumps({"status": "BLOCKED", "error": str(error)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
