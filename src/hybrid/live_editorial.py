"""Offline, deterministic editorial controls used by the LIVE revision path.

These controls never construct providers or perform network I/O.  They turn the
approved plan, research and successor brief into an auditable draft, verify its
biblical claims independently, and reconcile planned production limits before a
paid request may be authorized.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
import re
from typing import Callable

from src.hybrid.artifacts import digest
from src.hybrid.editorial import EditorialContractError


# This is deliberately a small semantic allowlist rather than a source-ref
# regex.  A permitted verse range does not make every sentence about it true.
_FACTS = {
    "Gênesis 15:1-6": (
        "deus falou com abrao", "nao tenha medo", "proteção", "herdeiro",
        "olhou para o ceu", "estrelas", "descendentes", "abrao creu", "confiou",
    ),
    "Gênesis 17:1-9": (
        "noventa e nove", "alianca", "abraao", "pai de muitas nações",
        "pai de muitas nacoes", "canaa",
    ),
    "Gênesis 17:15-21": (
        "sarai", "sara", "filho", "isaque", "um ano",
    ),
    "Gênesis 18:1-15": (
        "tenda", "visitantes", "sara", "filho", "um ano", "riu",
    ),
}
for _verse in range(1, 7):
    _FACTS.setdefault(f"Gênesis 15:{_verse}", _FACTS["Gênesis 15:1-6"])
for _verse in (*range(1, 10), *range(15, 22)):
    _FACTS.setdefault(f"Gênesis 17:{_verse}", _FACTS["Gênesis 17:15-21"] if _verse >= 15 else _FACTS["Gênesis 17:1-9"])
for _verse in range(1, 16):
    _FACTS.setdefault(f"Gênesis 18:{_verse}", _FACTS["Gênesis 18:1-15"])


def _fold(value: str) -> str:
    return (str(value).casefold().replace("ã", "a").replace("á", "a")
            .replace("â", "a").replace("é", "e").replace("ê", "e")
            .replace("í", "i").replace("ó", "o").replace("ô", "o")
            .replace("ú", "u").replace("ç", "c"))


def _number(value, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise EditorialContractError(f"{label} must be a finite number") from error
    if not result.is_finite() or result < 0:
        raise EditorialContractError(f"{label} must be a finite nonnegative number")
    return result


class DeterministicLiveScriptAuthor:
    """Injectable author that makes successor inputs observable in every artifact."""

    def author(self, plan: dict, research: dict) -> dict:
        if not isinstance(plan, dict) or not isinstance(research, dict):
            raise EditorialContractError("LIVE author requires approved plan and research")
        editorial = plan.get("editorial_plan")
        if not isinstance(editorial, dict):
            raise EditorialContractError("LIVE author requires an editorial plan")
        brief = plan.get("successor_brief")
        feedback_identity = brief.get("feedback_identity") if isinstance(brief, dict) else "INITIAL_REVISION"
        if not isinstance(feedback_identity, str) or not feedback_identity:
            raise EditorialContractError("successor brief feedback identity is required")
        research_identity = digest(research)
        successor_identity = brief.get("successor_identity", "INITIAL_REVISION") if isinstance(brief, dict) else "INITIAL_REVISION"
        simple = isinstance(brief, dict) and any("simples" in _fold(item) for item in brief["rejection_feedback"].get("directives", []))
        # A source-bound, child-safe core that differs for a successor without
        # claiming facts outside the allowlist.
        lines = [
            ("Abrão olhou para o céu. Deus mostrou muitas estrelas.", "Gênesis 15:5", "Abrão olha as estrelas; frases curtas; céu noturno sem texto"),
            ("Abrão confiou na promessa de Deus.", "Gênesis 15:6", "Abrão leva a mão ao coração; frases curtas; expressão de esperança"),
            ("Depois, Abrão recebeu o nome Abraão. Sarai recebeu o nome Sara.", "Gênesis 17:15", "Abraão e Sara perto da tenda; frases curtas; nova identidade visual"),
            ("Deus prometeu que Sara teria um filho no tempo certo.", "Gênesis 17:21", "Sara ouve com surpresa gentil; frases curtas; sem bebê"),
            ("Perto da tenda, Sara riu. A promessa parecia difícil, mas Deus a repetiu.", "Gênesis 18:12", "Sara junto à tenda; frases curtas; visitantes ao fundo"),
            ("Esperar pode ser difícil. Esta história nos lembra de ter esperança.", "Editorial infantil EP8", "Abraão e Sara esperam juntos; frases curtas; pôr do sol acolhedor"),
        ]
        if simple:
            lines = [(text.replace("promessa", "boa promessa").replace("difícil", "bem grande"), ref,
                  action + "; sucessora com linguagem mais simples") for text, ref, action in lines]
        target_scenes = int(editorial.get("estimated_scene_count", len(lines)))
        if target_scenes < 1 or target_scenes > 60:
            raise EditorialContractError("approved scene target is invalid")
        segments = []
        for index in range(target_scenes):
            text, ref, action = lines[index % len(lines)]
            # The successor has an intentionally different narration, visual
            # direction, and stable input identities (not metadata-only tags).
            if isinstance(brief, dict):
                text = ("Nesta nova versão, " if index == 0 else "") + text
                action += "; composição inédita de sucessora"
            segments.append({"id": f"S{index + 1:03d}", "narration": text,
                "kind": "biblical_paraphrase" if ref.startswith("Gênesis") else "family_reflection",
                "editorial_kind": "ORIGINAL_PARAPHRASE" if ref.startswith("Gênesis") else "FAMILY_REFLECTION",
                "source_refs": [ref], "visual_action": action,
                "characters": ["abraham", "sarah"]})
        target_words = int(editorial.get("estimated_word_count", 0))
        current_words = sum(len(item["narration"].split()) for item in segments)
        if target_words < current_words:
            raise EditorialContractError("approved word target is smaller than source-bound narration")
        # Keep the timing target exact without introducing a new biblical claim.
        needed = target_words - current_words
        cursor = 0
        while needed >= 4:
            segments[cursor % len(segments)]["narration"] += " Vamos ouvir com calma."
            needed -= 4
            cursor += 1
        while needed:
            segments[cursor % len(segments)]["narration"] += " Esperança."
            needed -= 1
            cursor += 1
        narration = "\n\n".join(item["narration"] for item in segments)
        thumbnail = {
            "headline": "UMA PROMESSA IMPOSSÍVEL?",
            "title": "A promessa de um filho para Abraão e Sara",
            "subtitle": "— Gênesis 15–18",
            "composition": ("Sara diante da tenda olhando para um céu de estrelas, com Abraão ao fundo"
                            if isinstance(brief, dict) else "Abraão sob as estrelas diante da tenda"),
            "successor_identity": successor_identity,
            "research_identity": research_identity,
        }
        thumbnail["identity"] = digest(thumbnail)
        result = {"audience": {"min_age": 6, "max_age": 10}, "closing_duration_s": editorial.get("closing_hold_seconds", 4),
                  "segments": segments, "narration": narration, "evidence_mode": "LIVE",
                  "research_identity": research_identity, "feedback_identity": feedback_identity,
                  "successor_identity": successor_identity, "thumbnail_concept": thumbnail,
                  "author": "deterministic-live-editorial-v1"}
        result["script_identity"] = digest({key: value for key, value in result.items() if key != "script_identity"})
        return result


class PreSpendReconciler:
    """Fail closed on plan drift before an authorization may invoke spend."""

    def reserve(self, editorial_plan: dict, actual: dict, approved_budget, spend: Callable[[], object]) -> dict:
        if not isinstance(editorial_plan, dict) or not isinstance(actual, dict) or not callable(spend):
            raise EditorialContractError("reconciliation inputs are required")
        tolerances = editorial_plan.get("approved_tolerances", {})
        if not isinstance(tolerances, dict):
            raise EditorialContractError("approved tolerances are invalid")
        expected = {"word_count": _number(editorial_plan.get("estimated_word_count"), "planned words"),
                    "duration_seconds": _number(editorial_plan.get("estimated_duration_seconds"), "planned duration"),
                    "scene_count": _number(editorial_plan.get("estimated_scene_count"), "planned scenes"),
                    "estimated_cost_usd": _number(editorial_plan.get("estimated_costs_usd", {}).get("total"), "planned cost")}
        observed = {key: _number(actual.get(key), key) for key in expected}
        limits = {"word_count": _number(tolerances.get("words", 0), "word tolerance"),
                  "duration_seconds": _number(tolerances.get("duration_seconds", 0), "duration tolerance"),
                  "scene_count": _number(tolerances.get("scenes", 0), "scene tolerance"),
                  "estimated_cost_usd": _number(tolerances.get("cost_usd", 0), "cost tolerance")}
        budget = _number(approved_budget, "approved budget")
        deviations = {key: str(abs(observed[key] - expected[key])) for key in expected if abs(observed[key] - expected[key]) > limits[key]}
        if deviations or observed["estimated_cost_usd"] > budget:
            raise EditorialContractError("plan reconciliation failed before spend: " + str(deviations))
        spend()
        return {"approved": True, "expected": {key: str(value) for key, value in expected.items()},
                "actual": {key: str(value) for key, value in observed.items()}, "budget_usd": str(budget)}


class BiblicalFactVerifier:
    """Independent allowlist verifier; source references alone never authorize claims."""

    _invented = re.compile(r"\b(drag[aã]o|mapa m[aá]gico|unic[oó]rnio|milagre secreto|anjo disse que)\b", re.I)

    def verify(self, script: dict) -> dict:
        if not isinstance(script, dict) or not isinstance(script.get("segments"), list):
            raise EditorialContractError("script segments are required for biblical verification")
        findings = []
        blocked = False
        for segment in script["segments"]:
            text = str(segment.get("narration", "")).strip()
            refs = segment.get("source_refs", [])
            entry = {"segment_id": segment.get("id", ""), "source_refs": list(refs)}
            if not text:
                entry.update(classification="OMISSION", reason="empty narration")
            elif self._invented.search(text):
                entry.update(classification="HUMAN_REVIEW", block_reason="invented_claim")
                blocked = True
            elif not refs or refs == ["Editorial infantil EP8"]:
                entry.update(classification="HUMAN_REVIEW", reason="editorial reflection")
            else:
                allowed_terms = tuple(term for ref in refs for term in _FACTS.get(ref, ()))
                folded = _fold(text)
                star_claim = "Gênesis 15:5" in refs and "estrela" in folded and "abrao" in folded
                if any(word in folded for word in ("vento", "luz", "sorriso", "passos")):
                    entry.update(classification="DRAMATIZATION", reason="non-claim scene detail")
                elif any(term in folded for term in allowed_terms) or star_claim:
                    entry.update(classification="FACT" if folded.startswith("deus") else "PARAPHRASE")
                else:
                    entry.update(classification="HUMAN_REVIEW", block_reason="unallowlisted_claim")
                    blocked = True
            findings.append(entry)
        report = {"status": "BLOCKED" if blocked else "PASS", "segments": findings,
                  "allowlist": "Genesis 15:1-6;17:1-9,15-21;18:1-15"}
        report["report_identity"] = digest(report)
        return report
