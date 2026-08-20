"""Duration Planner Agent — analyzes story complexity to recommend episode duration (§18-21).

Responsibility: Determine appropriate episode duration BEFORE expensive generation.
Input: research data (key_facts, references), theme
Output: DurationPlan with recommended duration, word count, scene count, cost estimate
Constraints:
  §18: Duration is NOT fixed — 3-15 min based on complexity, never artificially cut/padded.
  §19: Never add filler text or repeat ideas to hit a duration target.
  §20: Present pre-production report BEFORE expensive generation.
  §21: Report must show duration, words, scenes, images, local/cloud split, cost range.

Duration categories (initial guidance, not rigid rules):
  - Short/simple parable: 3-5 min (~390-750 words, ~20-35 scenes)
  - Medium complexity: 6-8 min (~780-1200 words, ~35-50 scenes)
  - Multi-event narrative: 8-12 min (~1200-1800 words, ~50-70 scenes)
  - Special/extended: 12-15 min (~1800-2250 words, ~70-90 scenes)
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
import re


# Narration pace: children's narration ~130 words/minute (slower than adult ~150 wpm)
WORDS_PER_MINUTE = 130

# Average scene duration for local-animated stills (Ken Burns style)
AVG_SCENE_DURATION_S = 8.0

# Cost model (local-first + cloud-on-demand, §3)
# Reference: Sora 2 / RunPod i2v ~$0.50 per 5s clip (~$0.10/s), per user's economic example
COST_PER_LOCAL_IMAGE = 0.0  # SD1.5 local = free
COST_PER_RUNPOD_CLIP_SECOND = 0.10
DEFAULT_RUNPOD_CLIP_DURATION_S = 5.0


@dataclass
class DurationPlan:
    """Pre-production plan for an episode (§20-21)."""
    theme: str
    complexity_tier: str  # "short" | "medium" | "long" | "special"
    recommended_duration_s: float
    duration_min: float
    duration_max: float
    word_count_target: int
    scene_count: int
    image_count: int
    local_animated_scenes: int
    runpod_candidate_scenes: int
    runpod_clip_seconds_total: float
    cost_min_usd: float
    cost_likely_usd: float
    cost_max_usd: float
    justification: str
    key_events: list[str] = field(default_factory=list)
    references: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "theme": self.theme,
            "complexity_tier": self.complexity_tier,
            "recommended_duration_s": round(self.recommended_duration_s, 1),
            "recommended_duration_formatted": self._format_duration(self.recommended_duration_s),
            "duration_range_s": [round(self.duration_min, 1), round(self.duration_max, 1)],
            "word_count_target": self.word_count_target,
            "scene_count": self.scene_count,
            "image_count": self.image_count,
            "local_animated_scenes": self.local_animated_scenes,
            "runpod_candidate_scenes": self.runpod_candidate_scenes,
            "runpod_clip_seconds_total": self.runpod_clip_seconds_total,
            "cost_min_usd": round(self.cost_min_usd, 2),
            "cost_likely_usd": round(self.cost_likely_usd, 2),
            "cost_max_usd": round(self.cost_max_usd, 2),
            "justification": self.justification,
            "key_events": self.key_events,
            "references": self.references,
        }

    @staticmethod
    def _format_duration(seconds: float) -> str:
        m = int(seconds // 60)
        s = int(seconds % 60)
        return f"{m}m{s:02d}s"

    def format_report(self, budget_hard_limit: float = 6.00) -> str:
        """Format a human-readable pre-production report for Telegram/console (§21)."""
        refs_str = "\n".join(
            f"  📖 {r.get('book', '')} {r.get('chapter', '')}:{r.get('verses', '')}"
            for r in self.references
        )
        events_str = "\n".join(f"  • {e}" for e in self.key_events[:10])

        over_budget = "⚠️ EXCEDE ORÇAMENTO" if self.cost_max_usd > budget_hard_limit else "✅ dentro do orçamento"

        return f"""📋 PLANO DE PRODUÇÃO — {self.theme}

⏱️ Duração recomendada: {self._format_duration(self.recommended_duration_s)} (faixa: {self._format_duration(self.duration_min)}-{self._format_duration(self.duration_max)})
📊 Complexidade: {self.complexity_tier.upper()}

📝 Palavras estimadas: ~{self.word_count_target}
🎬 Cenas: {self.scene_count}
🖼️ Imagens: {self.image_count}
   - Animadas localmente (FFmpeg): {self.local_animated_scenes}
   - Candidatas a vídeo generativo (RunPod): {self.runpod_candidate_scenes}
🎥 Vídeo generativo total: {self.runpod_clip_seconds_total:.0f}s

💰 CUSTO
   Mínimo: ${self.cost_min_usd:.2f}
   Provável: ${self.cost_likely_usd:.2f}
   Máximo: ${self.cost_max_usd:.2f}
   Limite configurado: ${budget_hard_limit:.2f} — {over_budget}

📚 Passagens bíblicas:
{refs_str}

🎯 Acontecimentos-chave:
{events_str}

💡 Justificativa:
{self.justification}"""


class DurationPlannerAgent:
    """Analyzes biblical story complexity to recommend episode duration (§18-21).

    Not a BaseAgent subclass — this is a synchronous planning step that runs
    BEFORE any expensive resource generation, using only research data.
    """

    def __init__(self, budget_hard_limit: float = 6.00, budget_target: float = 4.00):
        self.budget_hard_limit = budget_hard_limit
        self.budget_target = budget_target

    def plan(self, theme: str, research_data: dict) -> DurationPlan:
        """Build a DurationPlan from research data (§18-21).

        Args:
            theme: Episode theme/title.
            research_data: Output from ResearchAgent (references, key_facts via
                narrative_classification.BIBLICAL_FACT).
        """
        key_facts = research_data.get("narrative_classification", {}).get("BIBLICAL_FACT", [])
        references = research_data.get("references", [])
        n_events = max(1, len(key_facts))

        tier, duration_s, dur_min, dur_max = self._classify_complexity(n_events, references)

        # GPT-5.6-SOL may recommend a duration after reading the full chapter.
        # That recommendation is authoritative for this episode; the heuristic
        # remains the fallback for themes without a SOL plan.
        sol_scene_count = 0
        sol_plan_path = os.environ.get("STUDIO_SOL_PLAN_PATH", "")
        if sol_plan_path and os.path.exists(sol_plan_path):
            try:
                with open(sol_plan_path, encoding="utf-8") as f:
                    sol_plan = json.load(f)
                summary = sol_plan.get("complexity_summary", {})
                m = re.search(r"(\d+)\s*min", str(summary.get("recommended_duration", "")), re.I)
                if m:
                    duration_s = float(int(m.group(1)) * 60)
                    dur_min = max(180.0, duration_s - 60.0)
                    dur_max = duration_s + 60.0
                sol_scene_count = len(sol_plan.get("scenes", []))
                if sol_scene_count:
                    tier = "sol_adaptive"
            except Exception as e:
                # Planning must remain available if an external plan is malformed.
                pass

        # Word count target from duration (§19: never pad/cut artificially —
        # this is a TARGET derived from duration, script agent must respect content over count)
        word_count = int((duration_s / 60.0) * WORDS_PER_MINUTE)

        # Scene count follows the paired SOL visual plan when available.
        scene_count = sol_scene_count or max(n_events, round(duration_s / AVG_SCENE_DURATION_S))
        image_count = scene_count  # 1 image per scene (consistent characters reused across scenes)

        # RunPod candidates: high-impact scenes only (§46-48) — CRITICAL importance events
        # Heuristic: ~10-15% of scenes for tier short/medium, up to 3-5 clips max per plan
        runpod_pct = {"short": 0.10, "medium": 0.12, "long": 0.10, "special": 0.08}.get(tier, 0.10)
        runpod_scenes = min(5, max(0, round(scene_count * runpod_pct)))
        local_scenes = scene_count - runpod_scenes
        runpod_seconds = runpod_scenes * DEFAULT_RUNPOD_CLIP_DURATION_S

        # Cost estimate (§3, §21)
        cost_min = 0.0  # if all local (no RunPod scenes)
        cost_likely = runpod_seconds * COST_PER_RUNPOD_CLIP_SECOND
        cost_max = cost_likely * 1.6  # buffer for retries/re-generation (§21 pattern)

        justification = self._build_justification(tier, n_events, duration_s)

        return DurationPlan(
            theme=theme,
            complexity_tier=tier,
            recommended_duration_s=duration_s,
            duration_min=dur_min,
            duration_max=dur_max,
            word_count_target=word_count,
            scene_count=scene_count,
            image_count=image_count,
            local_animated_scenes=local_scenes,
            runpod_candidate_scenes=runpod_scenes,
            runpod_clip_seconds_total=runpod_seconds,
            cost_min_usd=cost_min,
            cost_likely_usd=cost_likely,
            cost_max_usd=cost_max,
            justification=justification,
            key_events=key_facts,
            references=references,
        )

    def _classify_complexity(self, n_events: int, references: list[dict]) -> tuple[str, float, float, float]:
        """Classify story complexity into a duration tier (§18).

        Returns (tier, recommended_seconds, min_seconds, max_seconds).
        """
        n_refs = len(references)

        # Heuristic: combine event count + reference span (chapters covered)
        if n_events <= 7 and n_refs <= 2:
            tier = "short"
            dur_min, dur_max = 180.0, 300.0  # 3-5 min
            # Scale within range by event count (7 events -> upper bound)
            recommended = dur_min + (dur_max - dur_min) * min(1.0, n_events / 7)
        elif n_events <= 10 and n_refs <= 3:
            tier = "medium"
            dur_min, dur_max = 360.0, 480.0  # 6-8 min
            recommended = dur_min + (dur_max - dur_min) * min(1.0, (n_events - 7) / 3)
        elif n_events <= 15:
            tier = "long"
            dur_min, dur_max = 480.0, 720.0  # 8-12 min
            recommended = dur_min + (dur_max - dur_min) * min(1.0, (n_events - 10) / 5)
        else:
            tier = "special"
            dur_min, dur_max = 720.0, 900.0  # 12-15 min
            recommended = dur_min + (dur_max - dur_min) * min(1.0, (n_events - 15) / 10)

        return tier, recommended, dur_min, dur_max

    def _build_justification(self, tier: str, n_events: int, duration_s: float) -> str:
        tier_labels = {
            "short": "história curta e objetiva (parábola ou evento único)",
            "medium": "história de complexidade média (múltiplos momentos narrativos)",
            "long": "narrativa com vários acontecimentos encadeados",
            "special": "episódio especial ou história extensa",
        }
        minutes = duration_s / 60.0
        return (
            f"Identificados {n_events} acontecimentos-chave na passagem bíblica, "
            f"classificando esta história como {tier_labels.get(tier, tier)}. "
            f"Duração de {minutes:.1f} minutos preserva clareza narrativa e ritmo "
            f"apropriado para crianças de 6-10 anos, sem cortes artificiais nem repetições de preenchimento (§19)."
        )

    def check_budget(self, plan: DurationPlan) -> dict:
        """Check if the plan's cost exceeds budget and suggest alternatives (§4, §7).

        Returns:
            {
                "within_budget": bool,
                "alternatives": list[dict] if over budget,
            }
        """
        within_budget = plan.cost_max_usd <= self.budget_hard_limit

        alternatives = []
        if not within_budget:
            # Alternative A: reduce RunPod clips
            reduced_runpod = max(0, plan.runpod_candidate_scenes - 2)
            reduced_cost = reduced_runpod * DEFAULT_RUNPOD_CLIP_DURATION_S * COST_PER_RUNPOD_CLIP_SECOND * 1.6
            alternatives.append({
                "option": "A",
                "title": "Reduzir clipes generativos",
                "description": f"Reduzir de {plan.runpod_candidate_scenes} para {reduced_runpod} clipes RunPod",
                "estimated_cost": round(reduced_cost, 2),
            })

            # Alternative B: all local (zero cloud cost)
            alternatives.append({
                "option": "B",
                "title": "100% animação local",
                "description": "Usar apenas imagens animadas localmente (FFmpeg), sem RunPod",
                "estimated_cost": 0.0,
            })

            # Alternative C: split into multiple episodes
            if plan.recommended_duration_s > 480:
                n_parts = 2 if plan.recommended_duration_s <= 900 else 3
                alternatives.append({
                    "option": "C",
                    "title": f"Dividir em {n_parts} episódios",
                    "description": f"Dividir a história em {n_parts} partes menores, reduzindo custo por episódio",
                    "estimated_cost": round(plan.cost_likely_usd / n_parts, 2),
                })

            # Alternative D: increase budget
            alternatives.append({
                "option": "D",
                "title": "Aumentar orçamento",
                "description": f"Aprovar gasto até ${plan.cost_max_usd:.2f} para este episódio",
                "estimated_cost": round(plan.cost_max_usd, 2),
            })

        return {
            "within_budget": within_budget,
            "alternatives": alternatives,
        }
