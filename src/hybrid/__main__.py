"""Offline planning CLI. It cannot submit provider jobs."""

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from src.hybrid.artifacts import atomic_json
from src.hybrid.planner import Config, Hero, money, plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--local-only", action="store_true")
    parser.add_argument("--committed", default="0", help="Existing commitments, USD")
    parser.add_argument(
        "--images",
        default="0",
        help="Maximum image-capacity envelope (all 92 slots), USD",
    )
    parser.add_argument("--candidates", type=Path, help="JSON semantic Hero candidates")
    parser.add_argument(
        "--duration-seconds",
        type=int,
        help="Adaptive duration selected by biblical/story analysis (180--900)",
    )
    parser.add_argument("--essential-events", type=int)
    parser.add_argument("--narration-words", type=int)
    parser.add_argument("--scene-count", type=int)
    parser.add_argument(
        "--closing-seconds",
        type=int,
        default=4,
        help="Required emotional/theological closing beat (3--5; default 4)",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    candidates = []
    if args.candidates:
        candidates = [
            Hero(**entry)
            for entry in json.loads(args.candidates.read_text(encoding="utf-8"))
        ]
    result = plan(
        Config.load(args.config),
        candidates=candidates,
        local_only=args.local_only,
        committed=money(args.committed),
        images=money(args.images),
        recommended_duration_seconds=args.duration_seconds,
        essential_events=args.essential_events,
        narration_words=args.narration_words,
        scene_count=args.scene_count,
        closing_seconds=args.closing_seconds,
    )
    result["heroes"] = [asdict(hero) for hero in result["heroes"]]
    if args.output:
        atomic_json(args.output, result)
    print(json.dumps(result, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
