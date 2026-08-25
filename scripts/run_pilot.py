"""Run the pilot episode: "História da Criação do Mundo" (§117-120).

§117: First pilot — 1-3 minutes, do NOT auto-publish.
§118: Majority local images + local animation, minimum 1 RunPod scene (skip for now).

This script runs the full pipeline:
1. start_episode() → research → plan → WAITING_PLAN_APPROVAL
2. continue_after_approval("plan") → script → TTS → storyboard → images → animation → assembly
"""

import asyncio
import os
import sys
import time
import json

# MUST clear PYTHONPATH to avoid Hermes venv contamination (B1)
os.environ["PYTHONPATH"] = ""

# Use the studio venv Python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    from src.agents.director import DirectorAgent

    print("=" * 60)
    print("PILOT EPISODE: História da Criação do Mundo")
    print("=" * 60)

    director = DirectorAgent()

    # Clean up orphaned RunPod pods (§56)
    orphans = director.cleanup_orphans()
    if orphans:
        print(f"Cleaned up {len(orphans)} orphaned RunPod pods")

    t_start = time.time()

    # Run the full pipeline (§98)
    result = await director.run_full_pipeline(
        theme="História da criação do mundo",
        episode_id=f"PILOT_{int(time.time())}",
    )

    t_total = time.time() - t_start

    print()
    print("=" * 60)
    print("PILOT RESULTS")
    print("=" * 60)
    print(json.dumps(result, indent=2, default=str, ensure_ascii=False))
    print()
    print(f"Total pipeline time: {t_total:.1f}s ({t_total/60:.1f} min)")

    if "final_video" in result:
        print(f"\n🎬 Final video: {result['final_video']}")
        print(f"   Duration: {result.get('final_duration_s', 0):.1f}s")
        print(f"   Images generated: {result.get('images_generated', 0)}")
        print(f"   Image gen time: {result.get('image_gen_time_s', 0):.1f}s")
        print(f"   Clips animated: {result.get('clips_animated', 0)}")
        print(f"   Animation time: {result.get('animation_time_s', 0):.1f}s")
        print(f"   Budget remaining: ${result.get('budget_remaining', 0):.2f}")
        print(f"\n§117: Pilot NOT auto-published. Awaiting human approval.")


if __name__ == "__main__":
    asyncio.run(main())
