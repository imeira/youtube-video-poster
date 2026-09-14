"""Offline planning CLI. It cannot submit provider jobs."""

import argparse
import json
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path

from src.hybrid.artifacts import atomic_json
from src.hybrid.fastlane import OneHourSLA, schedule
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
    parser.add_argument(
        "--one-hour-sla",
        action="store_true",
        help="Add a fail-closed 60-minute schedule; never authorizes execution.",
    )
    parser.add_argument("--provider-concurrency", type=int, default=8)
    parser.add_argument("--qa-concurrency", type=int)
    parser.add_argument("--baseline-seconds", type=int, default=45)
    parser.add_argument("--qa-seconds", type=int, default=30)
    parser.add_argument("--remediation-slots", type=int, default=4)
    parser.add_argument("--remediation-seconds", type=int, default=90)
    parser.add_argument("--render-seconds", type=int, default=600)
    parser.add_argument("--final-qa-seconds", type=int, default=180)
    parser.add_argument("--buffer-seconds", type=int, default=120)
    parser.add_argument("--sla-estimated-paid-cost", default="0")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    candidates = []
    if args.candidates:
        candidates = [
            Hero(**entry)
            for entry in json.loads(args.candidates.read_text(encoding="utf-8"))
        ]
    config = Config.load(args.config)
    result = plan(
        config,
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
    if args.one_hour_sla:
        if args.scene_count is None:
            parser.error("--one-hour-sla requires --scene-count")
        result["one_hour_sla"] = schedule(
            OneHourSLA(
                scene_count=args.scene_count,
                provider_concurrency=args.provider_concurrency,
                qa_concurrency=args.qa_concurrency,
                baseline_seconds=args.baseline_seconds,
                qa_seconds=args.qa_seconds,
                remediation_slots=args.remediation_slots,
                remediation_seconds=args.remediation_seconds,
                render_seconds=args.render_seconds,
                final_qa_seconds=args.final_qa_seconds,
                buffer_seconds=args.buffer_seconds,
            ),
            estimated_paid_cost=Decimal(args.sla_estimated_paid_cost),
            hard_limit=config.limit,
        )
    if args.output:
        atomic_json(args.output, result)
    print(json.dumps(result, indent=2, default=str, allow_nan=False))


if __name__ == "__main__":
    main()
