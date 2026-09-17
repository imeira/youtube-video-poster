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
    if sys.argv[1:2] == ["production"]:
        from src.hybrid.production import main as production_main
        return production_main(sys.argv[2:])
    if sys.argv[1:2] == ["offline"]:
        from src.hybrid.offline import main as offline_main
        return offline_main(sys.argv[2:])
    parser = argparse.ArgumentParser(
        description="Hybrid AI Animation Studio — produce a children's Bible YouTube video",
    )
    parser.add_argument(
        "theme",
        nargs="?",
        help='Biblical story theme and passage (e.g., "História da Criação do Mundo — Gênesis 1–2")',
    )
    parser.add_argument("--episode-id", help="Stable episode id; required to resume an existing episode")
    parser.add_argument("--resume", action="store_true", help="Resume an existing episode without overwriting its request")
    parser.add_argument(
        "--approve-plan", action="store_true",
        help="Continue only after a separately persisted human plan-approval receipt",
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
        "--output", "-o",
        help="Output JSON file for results",
    )

    args = parser.parse_args()

    config = get_config()
    director = DirectorAgent(config)
    if args.resume:
        if not args.episode_id or not args.approve_plan:
            parser.error("--resume requires --episode-id and --approve-plan")
        results = asyncio.run(director.continue_after_approval(args.episode_id, "plan"))
    else:
        if not args.theme:
            parser.error("theme is required unless --resume is used")
        print(f"Starting episode: '{args.theme}'")
        print(f"Language: {args.language}")
        print(f"Channel: {args.channel}")
        print()
        results = asyncio.run(director.start_episode(
            theme=args.theme,
            language=args.language,
            channel=args.channel,
            episode_id=args.episode_id or "",
            require_telegram_approval=True,
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

    successful_states = {
        "WAITING_PLAN_APPROVAL", "GENERATING_IMAGES", "WAITING_FINAL_APPROVAL",
    }
    return 0 if results.get("status") == "COMPLETE" or results.get("state") in successful_states else 1


if __name__ == "__main__":
    sys.exit(main())
