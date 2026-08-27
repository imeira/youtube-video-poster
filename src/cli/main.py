"""CLI entry point — 'studio' command.

Usage:
    python -m src.cli.main "História da Criação do Mundo — Gênesis 1–2"
    studio "História de Davi e Golias — 1 Samuel 17"

§5: User provides theme, language, channel — everything else is automatic.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from src.agents.director import DirectorAgent
from src.config.loader import get_config


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Hybrid AI Animation Studio — produce a children's Bible YouTube video",
    )
    parser.add_argument(
        "theme",
        help='Biblical story theme and passage (e.g., "História da Criação do Mundo — Gênesis 1–2")',
    )
    parser.add_argument(
        "--language", "-l",
        default="pt-BR",
        help="Language code (default: pt-BR)",
    )
    parser.add_argument(
        "--channel", "-c",
        default="@EraUmaVezBibliaAnimada",
        help="YouTube channel (default: @EraUmaVezBibliaAnimada)",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Skip HITL approvals (for testing only)",
    )
    parser.add_argument(
        "--output", "-o",
        help="Output JSON file for results",
    )

    args = parser.parse_args()

    config = get_config()
    director = DirectorAgent(config)

    print(f"Starting episode: '{args.theme}'")
    print(f"Language: {args.language}")
    print(f"Channel: {args.channel}")
    print()

    results = asyncio.run(director.produce_episode(
        theme=args.theme,
        language=args.language,
        channel=args.channel,
        auto_approve=args.auto_approve,
    ))

    # Print summary
    print()
    print("=" * 60)
    print("EPISODE PRODUCTION SUMMARY")
    print("=" * 60)
    print(json.dumps(results, indent=2, ensure_ascii=False, default=str))

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        print(f"\nResults saved to: {args.output}")

    return 0 if results.get("status") == "COMPLETE" else 1


if __name__ == "__main__":
    sys.exit(main())