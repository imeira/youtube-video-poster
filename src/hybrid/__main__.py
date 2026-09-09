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
        "--images", default="0", help="Explicit image request estimate, USD"
    )
    parser.add_argument("--candidates", type=Path, help="JSON semantic Hero candidates")
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
    )
    result["heroes"] = [asdict(hero) for hero in result["heroes"]]
    if args.output:
        atomic_json(args.output, result)
    print(json.dumps(result, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
