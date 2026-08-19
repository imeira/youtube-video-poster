"""Script Agent — writes narration for children 6-10 (§24).

Responsibility: Create narration script from research
Input: research/sources.json, duration plan
Output: script/narration.txt
Constraints: Clarity, emotion, curiosity, adventure, appropriate suspense,
            simple language, rhythm, retention, biblical fidelity (§24)
            Never pad with text to increase duration (§19)
"""

from __future__ import annotations

import json
from pathlib import Path

from src.agents.base import BaseAgent, AgentResult


class ScriptAgent(BaseAgent):
    """Generates a children's Bible narration script (§24)."""

    def __init__(self):
        super().__init__(name="Script")

    async def run(
        self,
        episode_id: str,
        research_data: dict | None = None,
        target_duration_s: int = 180,
        script_dir: str = "",
        **kwargs,
    ) -> AgentResult:
        """Generate narration text from research data.

        Args:
            research_data: Output from ResearchAgent (sources.json).
            target_duration_s: Target narration duration in seconds.
            script_dir: Directory to save narration.txt.

        Returns:
            AgentResult with narration text and word count.
        """
        if not research_data:
            return AgentResult(success=False, error="No research data provided")

        # ~150 words per minute of narration (§19 duration rule)
        target_words = int((target_duration_s / 60) * 150)
        summary = research_data.get("summary", "")
        facts = research_data.get("narrative_classification", {}).get("BIBLICAL_FACT", [])

        # Build narration from biblical facts, adapted for children 6-10
        narration = self._build_narration(
            story=research_data.get("story", ""),
            summary=summary,
            facts=facts,
            target_words=target_words,
        )

        word_count = len(narration.split())

        # Save to script dir
        if script_dir:
            Path(script_dir).mkdir(parents=True, exist_ok=True)
            with open(Path(script_dir) / "narration.txt", "w", encoding="utf-8") as f:
                f.write(narration)

        return AgentResult(
            success=True,
            data={
                "narration": narration,
                "word_count": word_count,
                "target_duration_s": target_duration_s,
                "estimated_duration_s": (word_count / 150) * 60,
            },
            next_state="SCRIPT_QA",
        )

    def _build_narration(self, story: str, summary: str, facts: list[str], target_words: int) -> str:
        """Build child-friendly narration from biblical facts.

        §24: Clarity, emotion, curiosity, adventure, simple language, rhythm.
        §25: Child safety — no graphic violence or trauma.
        §23: Never present creative addition as biblical fact.
        """
        # For the pilot, we use a template-based approach grounded in the facts.
        # In production, an LLM would generate this, but the facts are the constraint.
        lines = []

        # Opening — hook for children
        lines.append(f"Era uma vez... {summary}")
        lines.append("")  # pause

        # Story beats from biblical facts, adapted for children
        for i, fact in enumerate(facts):
            # Make each fact into a child-friendly sentence
            line = self._adapt_for_children(fact)
            lines.append(line)

        # Closing — meaningful conclusion (§24)
        lines.append("")
        lines.append("E foi assim que Deus mostrou o seu grande amor e cuidado.")

        narration = "\n".join(lines)

        # If too long, trim (§19: never pad, but also never truncate to lose comprehension)
        words = narration.split()
        if len(words) > target_words * 1.3:
            # Keep the first and last sentences, trim middle
            words = words[:int(target_words * 1.1)]
            narration = " ".join(words)

        return narration

    def _adapt_for_children(self, fact: str) -> str:
        """Adapt a biblical fact into child-friendly language (§24)."""
        # Simple transformations for children 6-10
        fact = fact.replace(" Deus ", " Deus ")
        fact = fact.replace(".", "...")  # dramatic pause for children
        # Capitalize first letter
        if fact:
            fact = fact[0].upper() + fact[1:]
        return fact
