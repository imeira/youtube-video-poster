"""Structured, offline editorial contracts for episode production.

The functions in this module transform and validate plain data. They perform no
network, provider, publication, or historical-workspace writes.
"""

from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path

from src.hybrid.artifacts import digest, sha256 as file_sha256


class EditorialContractError(ValueError):
    """Raised when an editorial artifact fails a production contract."""


@dataclass(frozen=True)
class EpisodeRequest:
    episode_id: str
    channel: str
    language: str
    locale: str
    theme: str
    passage: str
    audience_ages: tuple[int, int]
    raw: str

    def to_dict(self) -> dict:
        return asdict(self)


def parse_episode_request(text: str, *, episode_id: str = "EP8") -> EpisodeRequest:
    """Parse the supported one-line production request without guessing fields."""
    raw = " ".join(str(text).split())
    if not raw:
        raise EditorialContractError("episode request is required")
    channel_match = re.search(r"@[A-Za-z0-9_]+", raw)
    theme_match = re.search(r"(?:tema|theme)\s*:\s*(.+)$", raw, re.IGNORECASE)
    if channel_match is None or theme_match is None:
        raise EditorialContractError("request must include channel and a labelled theme")
    requested = theme_match.group(1).strip(" .")
    passage_match = re.search(r"(?:—|–|-)\s*(G[eê]nesis\s+15\s*[–-]\s*18)\s*$", requested, re.IGNORECASE)
    passage = "Gênesis 15–18" if passage_match else ""
    theme = requested[: passage_match.start()].strip() if passage_match else requested
    if not theme:
        raise EditorialContractError("episode theme is required")
    language = "Português do Brasil"
    if not re.search(r"portugu[eê]s\s+do\s+brasil", raw, re.IGNORECASE):
        raise EditorialContractError("request must explicitly select Brazilian Portuguese")
    return EpisodeRequest(
        episode_id=episode_id,
        channel=channel_match.group(0),
        language=language,
        locale="pt-BR",
        theme=theme,
        passage=passage,
        audience_ages=(6, 10),
        raw=raw,
    )


def build_adaptive_plan(
    request: EpisodeRequest,
    indispensable_events: list[dict],
    *,
    budget_usd: str | Decimal,
) -> dict:
    """Build a bounded plan whose runtime follows narrative complexity."""
    if not isinstance(request, EpisodeRequest):
        raise EditorialContractError("a parsed episode request is required")
    if not indispensable_events:
        raise EditorialContractError("at least one indispensable event is required")
    event_ids: set[str] = set()
    for event in indispensable_events:
        if (
            not isinstance(event, dict)
            or not str(event.get("id", "")).strip()
            or not str(event.get("label", "")).strip()
            or event.get("importance") not in {"LOW", "NORMAL", "HIGH", "CRITICAL"}
        ):
            raise EditorialContractError("each event needs id, label, and valid importance")
        if event["id"] in event_ids:
            raise EditorialContractError("event ids must be unique")
        event_ids.add(event["id"])
    try:
        budget = Decimal(budget_usd)
    except (InvalidOperation, TypeError) as error:
        raise EditorialContractError("budget must be a decimal amount") from error
    if not budget.is_finite() or budget <= 0:
        raise EditorialContractError("budget must be positive and finite")

    count = len(indispensable_events)
    if count <= 3:
        category, seconds = "short", 240
    elif count <= 6:
        category, seconds = "medium", 420
    elif count <= 10:
        category, seconds = "long", 600
    else:
        category, seconds = "special", 780
    seconds = min(900, max(180, seconds))
    words = round(seconds * 120 / 60)
    scenes = math.ceil(seconds / 10)
    hero_candidates = [
        {
            "event_id": event["id"],
            "label": event["label"],
            "importance": event["importance"],
            "approved": False,
            "fallback": "LOCAL_ANIMATED_STILL",
        }
        for event in indispensable_events
        if event["importance"] in {"HIGH", "CRITICAL"}
    ]
    costs = {
        "stills": Decimal(scenes) * Decimal("0.01"),
        "tts": Decimal(words) / Decimal(1000) * Decimal("0.02"),
        "hero_clips": Decimal(len(hero_candidates)) * Decimal("0.25"),
        "assembly": Decimal("0"),
    }
    costs["total"] = sum(costs.values(), Decimal("0"))
    if costs["total"] > budget:
        raise EditorialContractError("estimated plan exceeds the approved budget")
    money = {key: str(value.quantize(Decimal("0.01"))) for key, value in costs.items()}
    plan = {
        "episode_id": request.episode_id,
        "request": request.to_dict(),
        "duration_category": category,
        "estimated_duration_seconds": seconds,
        "estimated_word_count": words,
        "estimated_scene_count": scenes,
        "indispensable_events": [dict(event) for event in indispensable_events],
        "hero_candidates": hero_candidates,
        "estimated_costs_usd": money,
        "budget_usd": str(budget.quantize(Decimal("0.01"))),
        "narration_words_per_minute": 120,
        "hook_seconds": 20,
        "closing_hold_seconds": 4,
    }
    plan["plan_identity"] = digest(plan)
    return plan


def bind_research_claims(segments: list[dict], claims: list[dict]) -> dict:
    """Bind every script segment to exact, hash-addressed research claims."""
    if not isinstance(claims, list) or not claims:
        raise EditorialContractError("research claims are required")
    by_id: dict[str, dict] = {}
    allowed = {"BIBLICAL_FACT", "PARAPHRASE_BASIS", "DRAMATIZATION", "CONTEXT"}
    for claim in claims:
        if (
            not isinstance(claim, dict)
            or not str(claim.get("claim_id", "")).strip()
            or not str(claim.get("text", "")).strip()
            or not str(claim.get("source_ref", "")).strip()
            or claim.get("classification") not in allowed
        ):
            raise EditorialContractError("research claim contract is incomplete")
        if claim["claim_id"] in by_id:
            raise EditorialContractError("research claim ids must be unique")
        canonical = dict(claim)
        canonical["claim_identity"] = digest(claim)
        by_id[claim["claim_id"]] = canonical
    research = [by_id[key] for key in sorted(by_id)]
    research_identity = digest(research)
    if not isinstance(segments, list) or not segments:
        raise EditorialContractError("script segments are required")
    bound_segments = []
    seen: set[str] = set()
    for segment in segments:
        segment_id = str(segment.get("id", "")).strip() if isinstance(segment, dict) else ""
        if not segment_id or not str(segment.get("narration", "")).strip():
            raise EditorialContractError("segment id and narration are required")
        if segment_id in seen:
            raise EditorialContractError("segment ids must be unique")
        seen.add(segment_id)
        claim_ids = segment.get("claim_ids")
        if not isinstance(claim_ids, list) or not claim_ids:
            raise EditorialContractError("every segment requires at least one research claim")
        if len(set(claim_ids)) != len(claim_ids) or any(item not in by_id for item in claim_ids):
            raise EditorialContractError("segment references an unknown or duplicate research claim")
        bindings = [
            {
                "claim_id": claim_id,
                "claim_identity": by_id[claim_id]["claim_identity"],
                "source_ref": by_id[claim_id]["source_ref"],
                "classification": by_id[claim_id]["classification"],
            }
            for claim_id in claim_ids
        ]
        bound = {
            **segment,
            "claim_ids": list(claim_ids),
            "claim_bindings": bindings,
            "source_refs": list(dict.fromkeys(item["source_ref"] for item in bindings)),
            "research_identity": research_identity,
        }
        bound["segment_identity"] = digest(bound)
        bound_segments.append(bound)
    return {
        "claims": research,
        "research_identity": research_identity,
        "segments": bound_segments,
        "script_identity": digest(bound_segments),
    }


def build_editorial_reports(
    bound_script: dict,
    *,
    omissions: list[str] | None = None,
    simplifications: list[str] | None = None,
    tradition_notes: list[str] | None = None,
    human_review_points: list[str] | None = None,
    licenses: list[dict] | None = None,
    prior_scripts: list[str] | None = None,
) -> dict:
    """Produce fail-closed fidelity, safety, originality, and license reports."""
    if not isinstance(bound_script, dict) or not bound_script.get("segments"):
        raise EditorialContractError("a claim-bound script is required")
    if any(not segment.get("claim_bindings") for segment in bound_script["segments"]):
        raise EditorialContractError("reporting requires claim bindings on every segment")
    claims = bound_script.get("claims", [])
    facts = [item["text"] for item in claims if item["classification"] == "BIBLICAL_FACT"]
    dramatizations = [item["text"] for item in claims if item["classification"] == "DRAMATIZATION"]
    paraphrases = [
        segment["narration"]
        for segment in bound_script["segments"]
        if segment.get("editorial_kind") == "ORIGINAL_PARAPHRASE"
    ]
    fidelity = {
        "explicit_facts": facts,
        "original_paraphrases": paraphrases,
        "dramatized_elements": dramatizations,
        "omissions": list(omissions or []),
        "age_appropriate_simplifications": list(simplifications or []),
        "tradition_notes": list(tradition_notes or []),
        "human_review_points": list(human_review_points or []),
        "research_identity": bound_script.get("research_identity"),
    }

    narrative = " ".join(segment["narration"] for segment in bound_script["segments"])
    safety_patterns = {
        "deceptive_clickbait": r"\b(segredo|proibido|chocante)\b",
        "comments_cta": r"\b(comente|comentem|deixe um coment[aá]rio)\b",
        "purchase_pressure": r"\b(compre|peça para (?:seus pais|um adulto) comprar)\b",
        "spiritual_threat": r"\b(inferno|castigo eterno|deus vai castigar você)\b",
        "personal_information": r"\b(seu endereço|seu telefone|nome completo)\b",
        "graphic_violence": r"\b(sangue jorrando|corpo mutilado|violência gráfica)\b",
    }
    violations = [
        {"rule": rule, "match": match.group(0)}
        for rule, pattern in safety_patterns.items()
        if (match := re.search(pattern, narrative, re.IGNORECASE)) is not None
    ]
    safety = {
        "audience": {"min_age": 6, "max_age": 10},
        "violations": violations,
        "status": "PASS" if not violations else "BLOCKED",
    }

    comparisons = []
    for index, prior in enumerate(prior_scripts or []):
        if not isinstance(prior, str) or not prior.strip():
            raise EditorialContractError("prior scripts must be non-empty strings")
        comparisons.append(
            {"prior_index": index, "similarity": round(SequenceMatcher(None, narrative, prior).ratio(), 4)}
        )
    maximum_similarity = max((item["similarity"] for item in comparisons), default=0.0)
    originality = {
        "script_identity": bound_script.get("script_identity") or digest(bound_script["segments"]),
        "comparisons": comparisons,
        "maximum_similarity": maximum_similarity,
        "threshold": 0.8,
        "status": "PASS" if maximum_similarity < 0.8 else "BLOCKED",
    }

    entries = []
    unresolved = []
    required_license_keys = {
        "asset_id", "license", "source", "commercial_use",
        "attribution_required", "attribution",
    }
    for entry in licenses or []:
        if not isinstance(entry, dict) or set(entry) != required_license_keys:
            raise EditorialContractError("each license entry must satisfy the exact license contract")
        if (
            not str(entry["asset_id"]).strip()
            or not str(entry["license"]).strip()
            or not str(entry["source"]).strip()
            or type(entry["commercial_use"]) is not bool
            or type(entry["attribution_required"]) is not bool
            or (entry["attribution_required"] and not str(entry["attribution"]).strip())
        ):
            raise EditorialContractError("license entry contains invalid values")
        item = dict(entry)
        entries.append(item)
        if not item["commercial_use"]:
            unresolved.append(item["asset_id"])
    license_report = {
        "entries": entries,
        "unresolved_assets": unresolved,
        "all_commercial_use_cleared": bool(entries) and not unresolved,
    }
    status = "PASS" if (
        safety["status"] == "PASS"
        and originality["status"] == "PASS"
        and license_report["all_commercial_use_cleared"]
    ) else "BLOCKED"
    result = {
        "status": status,
        "fidelity": fidelity,
        "safety": safety,
        "originality": originality,
        "licenses": license_report,
    }
    result["report_identity"] = digest(result)
    return result


def build_ep8_character_bible(approved_references: dict[str, list[dict]]) -> dict:
    """Create canonical Abraham/Sarah cards bound to approved reference bytes."""
    if not isinstance(approved_references, dict) or set(approved_references) != {"abraham", "sarah"}:
        raise EditorialContractError("approved references for Abraham and Sarah are required")
    verified: dict[str, list[dict]] = {}
    seen_hashes: set[str] = set()
    for character_id in ("abraham", "sarah"):
        references = approved_references[character_id]
        if not isinstance(references, list) or not references:
            raise EditorialContractError(f"{character_id} needs an approved reference")
        verified[character_id] = []
        for reference in references:
            required = {"path", "sha256", "status", "reviewer", "generation_method"}
            if not isinstance(reference, dict) or not required <= set(reference):
                raise EditorialContractError("approved reference contract is incomplete")
            path = Path(reference["path"]).resolve()
            expected = str(reference["sha256"])
            if not path.is_file() or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise EditorialContractError("approved reference file and SHA-256 are required")
            if file_sha256(path) != expected:
                raise EditorialContractError("approved reference hash mismatch")
            if reference["status"] != "APPROVED" or not str(reference["reviewer"]).strip():
                raise EditorialContractError("reference needs explicit human approval")
            method = str(reference["generation_method"]).strip()
            if not method or method.upper().replace("-", "_") == "TEXT_TO_IMAGE_ONLY":
                raise EditorialContractError("text-to-image-only cannot be character authority")
            if expected in seen_hashes:
                raise EditorialContractError("character references must be distinct")
            seen_hashes.add(expected)
            verified[character_id].append(
                {
                    "path": str(path),
                    "sha256": expected,
                    "status": "APPROVED",
                    "reviewer": reference["reviewer"],
                    "generation_method": method,
                }
            )

    cards = {
        "abraham": {
            "character_id": "abraham",
            "canonical_name": "Abraão",
            "names_by_scope": [
                {"scope": "Gênesis 15:1–17:4", "name": "Abrão"},
                {"scope": "Gênesis 17:5–18:15", "name": "Abraão"},
            ],
            "apparent_age": "idoso; 99 anos em Gênesis 17",
            "identity_lock": {
                "face_shape": "oval alongado, linhas gentis e reconhecíveis",
                "skin_tone": "morena quente",
                "eyes": "castanhos, expressão serena",
                "hair": "grisalho, ondulado, na altura dos ombros",
                "relative_height": "ligeiramente mais alto que Sara",
                "proportions": "3D infantil estilizado, adulto idoso não caricatural",
                "clothing": "túnica bege e manto terracota simples",
                "colors": ["bege", "terracota", "marrom"],
                "shoes": "sandálias simples",
                "accessories": ["cinto de tecido"],
                "expressions": ["esperança", "surpresa gentil", "confiança", "reflexão"],
                "visual_personality": "acolhedor, paciente e contemplativo",
                "immutable": ["estrutura facial", "olhos", "paleta da roupa", "idade aparente"],
            },
        },
        "sarah": {
            "character_id": "sarah",
            "canonical_name": "Sara",
            "names_by_scope": [
                {"scope": "Gênesis 15:1–17:14", "name": "Sarai"},
                {"scope": "Gênesis 17:15–18:15", "name": "Sara"},
            ],
            "apparent_age": "idosa; 90 anos no contexto de Gênesis 17",
            "identity_lock": {
                "face_shape": "oval suave, maçãs do rosto marcadas e gentis",
                "skin_tone": "morena quente",
                "eyes": "castanhos, vivos e expressivos",
                "hair": "grisalho escuro, preso e parcialmente coberto",
                "relative_height": "ligeiramente mais baixa que Abraão",
                "proportions": "3D infantil estilizado, adulta idosa não caricatural",
                "clothing": "vestido azul-petróleo e xale areia",
                "colors": ["azul-petróleo", "areia", "dourado suave"],
                "shoes": "sandálias simples",
                "accessories": ["xale de tecido"],
                "expressions": ["curiosidade", "surpresa gentil", "esperança", "alegria contida"],
                "visual_personality": "atenta, firme e afetuosa",
                "immutable": ["estrutura facial", "olhos", "paleta da roupa", "idade aparente"],
            },
        },
    }
    for character_id, card in cards.items():
        card["authority"] = "APPROVED_REFERENCE_IMAGES"
        card["approved_references"] = verified[character_id]
        card["card_identity"] = digest(card)
    bible = {
        "schema_version": 1,
        "episode_id": "EP8",
        "status": "APPROVED_REFERENCES_BOUND",
        "characters": cards,
    }
    bible["bible_identity"] = digest(bible)
    return bible


def validate_storyboard(
    storyboard: dict,
    bound_script: dict,
    character_bible: dict,
    *,
    audio_duration_seconds: float,
) -> dict:
    """Validate a complete one-scene-per-segment semantic storyboard."""
    try:
        audio_duration = float(audio_duration_seconds)
    except (TypeError, ValueError) as error:
        raise EditorialContractError("audio duration must be numeric") from error
    if not math.isfinite(audio_duration) or audio_duration <= 0:
        raise EditorialContractError("audio duration must be positive and finite")
    if not isinstance(storyboard, dict) or not isinstance(storyboard.get("scenes"), list):
        raise EditorialContractError("storyboard scenes are required")
    if storyboard.get("character_bible_identity") != character_bible.get("bible_identity"):
        raise EditorialContractError("storyboard character bible binding mismatch")
    if storyboard.get("burned_captions") is not False:
        raise EditorialContractError("burned captions are forbidden")
    closing = storyboard.get("closing_hold_seconds")
    if type(closing) not in {int, float} or not 3 <= closing <= 10:
        raise EditorialContractError("closing hold must be 3 to 10 seconds")
    segments = bound_script.get("segments", []) if isinstance(bound_script, dict) else []
    if not segments:
        raise EditorialContractError("claim-bound script segments are required")
    segment_map = {segment["id"]: segment for segment in segments}
    expected_order = list(segment_map)
    scenes = storyboard["scenes"]
    covered = [scene.get("segment_id") for scene in scenes if isinstance(scene, dict)]
    if covered != expected_order or len(set(covered)) != len(covered):
        raise EditorialContractError("storyboard must cover every script segment exactly once in order")

    characters = character_bible.get("characters", {})
    allowed_hashes: dict[str, set[str]] = {}
    for character_id, card in characters.items():
        references = card.get("approved_references", [])
        allowed_hashes[character_id] = {item["sha256"] for item in references}
        for reference in references:
            if file_sha256(reference["path"]) != reference["sha256"]:
                raise EditorialContractError("character reference changed after approval")

    required = {
        "scene_id", "segment_id", "narration", "start", "end", "duration",
        "characters", "location", "action", "emotion", "importance", "visual_prompt",
        "negative_prompt", "camera", "motion_intent", "transition", "sfx",
        "source_claim_ids", "character_reference_hashes", "hero_candidate",
    }
    previous_end = 0.0
    scene_ids: set[str] = set()
    hero_ids = []
    for scene in scenes:
        if not required <= set(scene):
            raise EditorialContractError("storyboard scene contract is incomplete")
        scene_id = str(scene["scene_id"]).strip()
        if not scene_id or scene_id in scene_ids:
            raise EditorialContractError("storyboard scene ids must be unique and non-empty")
        scene_ids.add(scene_id)
        segment = segment_map[scene["segment_id"]]
        if " ".join(str(scene["narration"]).split()) != " ".join(segment["narration"].split()):
            raise EditorialContractError("scene narration must match its script segment")
        try:
            start, end, duration = (float(scene[key]) for key in ("start", "end", "duration"))
        except (TypeError, ValueError) as error:
            raise EditorialContractError("scene timing must be numeric") from error
        if (
            not all(math.isfinite(value) for value in (start, end, duration))
            or abs(start - previous_end) > 1e-6
            or end <= start
            or abs(duration - (end - start)) > 1e-6
        ):
            raise EditorialContractError("storyboard timeline must be finite, contiguous, and exact")
        previous_end = end
        required_text_fields = (
            "location", "action", "emotion", "visual_prompt", "negative_prompt",
            "camera", "motion_intent", "transition",
        )
        for field in required_text_fields:
            if not str(scene[field]).strip():
                raise EditorialContractError(f"scene {field} is required")
        if scene["importance"] not in {"LOW", "NORMAL", "HIGH", "CRITICAL"}:
            raise EditorialContractError("scene importance is invalid")
        if type(scene["hero_candidate"]) is not bool:
            raise EditorialContractError("hero candidate must be explicit")
        if scene["hero_candidate"] and scene["importance"] not in {"HIGH", "CRITICAL"}:
            raise EditorialContractError("only HIGH or CRITICAL scenes may be hero candidates")
        if scene["hero_candidate"]:
            hero_ids.append(scene_id)
        if scene["source_claim_ids"] != segment["claim_ids"]:
            raise EditorialContractError("scene research claim binding mismatch")
        if not isinstance(scene["characters"], list) or not isinstance(scene["sfx"], list):
            raise EditorialContractError("scene characters and SFX must be lists")
        bindings = scene["character_reference_hashes"]
        if set(bindings) != set(scene["characters"]):
            raise EditorialContractError("every scene character needs one canonical reference")
        for character_id, reference_hash in bindings.items():
            if character_id not in allowed_hashes or reference_hash not in allowed_hashes[character_id]:
                raise EditorialContractError("scene character reference is not approved")
        visible = " ".join(str(scene[field]) for field in ("narration", "action", "visual_prompt")).lower()
        if re.search(r"(isaque|isaac).{0,20}(nasceu|nascido|beb[eê]|newborn|born)", visible):
            raise EditorialContractError("Isaac is not born in EP8")
    if abs(previous_end - audio_duration) > 1e-6:
        raise EditorialContractError("storyboard must end at the decoded audio duration")
    report = {
        "status": "PASS",
        "scene_count": len(scenes),
        "covered_segment_ids": covered,
        "hero_scene_ids": hero_ids,
        "timeline_duration_seconds": audio_duration,
        "storyboard_identity": digest(storyboard),
        "script_identity": bound_script.get("script_identity"),
        "character_bible_identity": character_bible.get("bible_identity"),
    }
    report["report_identity"] = digest(report)
    return report


def qa_audio_transcript(
    audio: dict,
    transcript: str,
    bound_script: dict,
    *,
    sidecars: dict,
) -> dict:
    """Fail closed unless decoded audio, boundaries, script, and sidecars agree."""
    if not isinstance(audio, dict) or audio.get("decoded") is not True:
        raise EditorialContractError("audio must have successful decode evidence")
    try:
        duration = float(audio.get("duration_seconds"))
    except (TypeError, ValueError) as error:
        raise EditorialContractError("decoded audio duration is required") from error
    if not math.isfinite(duration) or duration <= 0:
        raise EditorialContractError("decoded audio duration must be positive and finite")
    sample_rate = audio.get("sample_rate_hz")
    channels = audio.get("channels")
    if type(sample_rate) is not int or sample_rate < 8000 or channels not in {1, 2}:
        raise EditorialContractError("audio sample rate and channels are invalid")
    if audio.get("boundary_source") != "WordBoundary":
        raise EditorialContractError("provider WordBoundary evidence is required")
    segments = bound_script.get("segments", []) if isinstance(bound_script, dict) else []
    if not segments:
        raise EditorialContractError("claim-bound script is required for transcript QA")
    expected = " ".join(segment["narration"].strip() for segment in segments)
    normalized_transcript = " ".join(str(transcript).split())
    if normalized_transcript != " ".join(expected.split()):
        raise EditorialContractError("transcript must exactly match the structured script")
    boundaries = audio.get("word_boundaries")
    if not isinstance(boundaries, list) or not boundaries:
        raise EditorialContractError("WordBoundary timestamps are required")
    words = []
    previous_end = 0.0
    for boundary in boundaries:
        if not isinstance(boundary, dict) or set(boundary) != {"word", "start", "end"}:
            raise EditorialContractError("word boundary contract is incomplete")
        word = str(boundary["word"])
        try:
            start, end = float(boundary["start"]), float(boundary["end"])
        except (TypeError, ValueError) as error:
            raise EditorialContractError("word boundaries must be numeric") from error
        if (
            not word
            or not math.isfinite(start)
            or not math.isfinite(end)
            or start < previous_end
            or end <= start
            or end > duration + 1e-6
        ):
            raise EditorialContractError("word boundaries must be ordered within decoded audio")
        words.append(word)
        previous_end = end
    if words != normalized_transcript.split():
        raise EditorialContractError("WordBoundary tokens must exactly match the transcript")
    required_sidecars = {"transcript": True, "srt": True, "vtt": True, "burned_in_video": False}
    if sidecars != required_sidecars:
        raise EditorialContractError("transcript, SRT, VTT, and no burned captions are required")
    report = {
        "status": "PASS",
        "decoded": True,
        "decoded_duration_seconds": duration,
        "sample_rate_hz": sample_rate,
        "channels": channels,
        "boundary_source": "WordBoundary",
        "word_count": len(words),
        "transcript_matches_script": True,
        "word_boundaries_match_transcript": True,
        "sidecars": dict(sidecars),
        "script_identity": bound_script.get("script_identity"),
        "audio_evidence_identity": digest(audio),
        "transcript_identity": digest(normalized_transcript),
    }
    report["report_identity"] = digest(report)
    return report


def build_youtube_metadata(
    *,
    chapters: list[dict],
    duration_seconds: float,
    transcript_artifacts: dict[str, dict],
    thumbnail: dict,
    license_report_identity: str,
) -> dict:
    """Build complete, private-by-default EP8 YouTube metadata."""
    metadata = {
        "title": "A promessa de um filho para Abraão e Sara | Gênesis 15–18",
        "description": (
            "Abrão e Sarai esperaram por muito tempo. Nesta história, vemos como a promessa de Deus "
            "foi reafirmada e como eles aprenderam a esperar com esperança.\n\n"
            "Referências bíblicas: Gênesis 15:1–6; Gênesis 17:1–9,15–21; Gênesis 18:1–15.\n\n"
            "Converse sobre a história com sua família e escolham juntos a próxima aventura bíblica."
        ),
        "tags": [
            "bíblia para crianças", "histórias bíblicas", "Abraão e Sara", "Gênesis 15",
            "Gênesis 17", "Gênesis 18", "promessa de Deus", "fé", "esperança",
            "Aventuras do Antigo Testamento",
        ],
        "playlist": "Aventuras do Antigo Testamento",
        "category_id": "27",
        "made_for_kids": True,
        "default_language": "pt-BR",
        "default_audio_language": "pt-BR",
        "privacy_status": "private",
        "contains_paid_promotion": False,
        "embeddable": True,
        "public_stats_viewable": True,
        "chapters": [dict(chapter) for chapter in chapters],
        "captions": {
            "language": "pt-BR",
            "srt": dict(transcript_artifacts.get("srt", {})),
            "vtt": dict(transcript_artifacts.get("vtt", {})),
        },
        "transcript": dict(transcript_artifacts.get("transcript", {})),
        "thumbnail": dict(thumbnail),
        "biblical_references": [
            "Gênesis 15:1–6", "Gênesis 17:1–9,15–21", "Gênesis 18:1–15",
        ],
        "license_report_identity": license_report_identity,
    }
    metadata["metadata_identity"] = digest(metadata)
    validate_youtube_metadata(metadata, duration_seconds=duration_seconds)
    return metadata


def validate_youtube_metadata(metadata: dict, *, duration_seconds: float) -> dict:
    """Validate fields required for a safe, captioned, made-for-kids upload."""
    required = {
        "title", "description", "tags", "playlist", "category_id", "made_for_kids",
        "default_language", "default_audio_language", "privacy_status",
        "contains_paid_promotion", "embeddable", "public_stats_viewable", "chapters",
        "captions", "transcript", "thumbnail", "biblical_references",
        "license_report_identity", "metadata_identity",
    }
    if not isinstance(metadata, dict) or not required <= set(metadata):
        raise EditorialContractError("complete YouTube metadata is required")
    if metadata["made_for_kids"] is not True:
        raise EditorialContractError("made_for_kids must be true for the 6–10 audience")
    if metadata["category_id"] != "27" or not str(metadata["playlist"]).strip():
        raise EditorialContractError("Education category and playlist are required")
    if metadata["default_language"] != "pt-BR" or metadata["default_audio_language"] != "pt-BR":
        raise EditorialContractError("metadata and audio languages must be pt-BR")
    if metadata["privacy_status"] != "private":
        raise EditorialContractError("metadata must default to private before publication authorization")
    for key, expected in {
        "contains_paid_promotion": False,
        "embeddable": True,
        "public_stats_viewable": True,
    }.items():
        if metadata[key] is not expected:
            raise EditorialContractError(f"invalid YouTube setting: {key}")
    title, description = str(metadata["title"]).strip(), str(metadata["description"]).strip()
    if not 1 <= len(title) <= 100 or not 1 <= len(description) <= 5000:
        raise EditorialContractError("YouTube title or description length is invalid")
    unsafe_copy = r"\b(segredo|proibido|chocante|comente|comentem|compre)\b"
    if re.search(unsafe_copy, title + " " + description, re.IGNORECASE):
        raise EditorialContractError("metadata contains unsafe child-directed copy")
    tags = metadata["tags"]
    if (
        not isinstance(tags, list)
        or not tags
        or any(not isinstance(tag, str) or not tag.strip() for tag in tags)
        or len(",".join(tags)) > 500
        or len(set(tag.casefold() for tag in tags)) != len(tags)
    ):
        raise EditorialContractError("YouTube tags are invalid")
    expected_refs = ["Gênesis 15:1–6", "Gênesis 17:1–9,15–21", "Gênesis 18:1–15"]
    if metadata["biblical_references"] != expected_refs:
        raise EditorialContractError("complete EP8 biblical references are required")
    try:
        duration = float(duration_seconds)
    except (TypeError, ValueError) as error:
        raise EditorialContractError("metadata duration must be numeric") from error
    if not math.isfinite(duration) or duration < 30:
        raise EditorialContractError("metadata duration must be at least 30 seconds")
    chapters = metadata["chapters"]
    if not isinstance(chapters, list) or len(chapters) < 3:
        raise EditorialContractError("YouTube requires at least three chapters")
    previous = None
    for chapter in chapters:
        if not isinstance(chapter, dict) or set(chapter) != {"start_seconds", "title"}:
            raise EditorialContractError("chapter contract is incomplete")
        try:
            start = float(chapter["start_seconds"])
        except (TypeError, ValueError) as error:
            raise EditorialContractError("chapter start must be numeric") from error
        if not math.isfinite(start) or start < 0 or start >= duration or not str(chapter["title"]).strip():
            raise EditorialContractError("chapter values are invalid")
        if previous is None and start != 0:
            raise EditorialContractError("first chapter must start at zero")
        if previous is not None and start - previous < 10:
            raise EditorialContractError("YouTube chapters must be at least 10 seconds apart")
        previous = start

    def validate_artifact(value: dict, label: str) -> None:
        if (
            not isinstance(value, dict)
            or set(value) != {"path", "sha256"}
            or not str(value["path"]).strip()
            or not re.fullmatch(r"[0-9a-f]{64}", str(value["sha256"]))
        ):
            raise EditorialContractError(f"{label} artifact binding is invalid")

    captions = metadata["captions"]
    if not isinstance(captions, dict) or set(captions) != {"language", "srt", "vtt"} or captions["language"] != "pt-BR":
        raise EditorialContractError("pt-BR SRT and VTT captions are required")
    validate_artifact(captions["srt"], "SRT")
    validate_artifact(captions["vtt"], "VTT")
    validate_artifact(metadata["transcript"], "transcript")
    validate_artifact(metadata["thumbnail"], "thumbnail")
    if not re.fullmatch(r"[0-9a-f]{64}", str(metadata["license_report_identity"])):
        raise EditorialContractError("license report identity is invalid")
    without_identity = {key: value for key, value in metadata.items() if key != "metadata_identity"}
    expected_identity = digest(without_identity)
    if metadata["metadata_identity"] != expected_identity:
        raise EditorialContractError("metadata identity mismatch")
    report = {
        "status": "PASS",
        "metadata_identity": expected_identity,
        "chapter_count": len(chapters),
        "caption_languages": ["pt-BR"],
        "publication_authorized": False,
    }
    report["report_identity"] = digest(report)
    return report


def create_successor_brief(
    predecessor: dict,
    rejection_feedback: dict,
    *,
    successor_revision: int,
) -> dict:
    """Create a successor brief that cannot omit predecessor rejection feedback."""
    if not isinstance(predecessor, dict) or predecessor.get("status") not in {"REJECTED", "SUPERSEDED"}:
        raise EditorialContractError("successor requires a rejected or superseded predecessor")
    prior_revision = predecessor.get("revision")
    if (
        type(prior_revision) is not int
        or type(successor_revision) is not int
        or successor_revision != prior_revision + 1
    ):
        raise EditorialContractError("successor revision must immediately follow its predecessor")
    if not isinstance(rejection_feedback, dict):
        raise EditorialContractError("rejection feedback is required")
    reason = str(rejection_feedback.get("reason", "")).strip()
    reviewer = str(rejection_feedback.get("reviewer", "")).strip()
    directives = rejection_feedback.get("directives")
    if (
        not reason
        or not reviewer
        or not isinstance(directives, list)
        or not directives
        or any(not isinstance(item, str) or not item.strip() for item in directives)
    ):
        raise EditorialContractError("complete rejection feedback is required")
    artifacts = predecessor.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise EditorialContractError("predecessor artifact identities are required")
    kinds = set()
    identities = []
    for artifact in artifacts:
        if (
            not isinstance(artifact, dict)
            or set(artifact) != {"kind", "sha256"}
            or artifact["kind"] not in {"video", "thumbnail"}
            or not re.fullmatch(r"[0-9a-f]{64}", str(artifact["sha256"]))
        ):
            raise EditorialContractError("predecessor artifact identity is invalid")
        if artifact["kind"] in kinds:
            raise EditorialContractError("predecessor artifact kinds must be unique")
        kinds.add(artifact["kind"])
        identities.append(dict(artifact))
    if kinds != {"video", "thumbnail"}:
        raise EditorialContractError("both rejected video and thumbnail identities are required")
    feedback = {
        **rejection_feedback,
        "reason": reason,
        "directives": [item.strip() for item in directives],
        "reviewer": reviewer,
    }
    feedback_identity = digest(feedback)
    brief = {
        "episode_id": predecessor.get("episode_id", "EP8"),
        "revision": successor_revision,
        "supersedes_revision": prior_revision,
        "predecessor_identities": identities,
        "rejection_feedback": feedback,
        "feedback_identity": feedback_identity,
        "script_constraints": {
            "mandatory_feedback_identity": feedback_identity,
            "directives": list(feedback["directives"]),
            "audience": {"min_age": 6, "max_age": 10},
            "language": "pt-BR",
        },
        "thumbnail_constraints": {
            "mandatory_feedback_identity": feedback_identity,
            "directives": list(feedback["directives"]),
            "requires_new_composition": True,
            "curiosity_without_deception": True,
        },
        "publication_authorized": False,
    }
    brief["successor_identity"] = digest(brief)
    return brief


def _read_only_manifest(root: Path) -> dict[str, str]:
    manifest = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            manifest[path.relative_to(root).as_posix()] = "SYMLINK:" + digest(str(path.readlink()))
        elif path.is_file():
            manifest[path.relative_to(root).as_posix()] = file_sha256(path)
    return manifest


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError:
        return False
    return path.is_symlink() or bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def _safe_historical_path(root: Path, path: Path, label: str, *, file: bool = True) -> Path:
    """Reject critical link/reparse traversal before reading historical evidence."""
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise EditorialContractError(f"historical {label} must remain inside source root") from error
    current = root
    for component in relative.parts:
        current /= component
        if _is_link_or_reparse(current):
            raise EditorialContractError(f"historical {label} cannot be a link or reparse point")
    try:
        resolved = current.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise EditorialContractError(f"historical {label} file is missing") from error
    if root not in resolved.parents and resolved != root:
        raise EditorialContractError(f"historical {label} must remain inside source root")
    if file and not resolved.is_file():
        raise EditorialContractError(f"historical {label} file is missing")
    if not file and not resolved.is_dir():
        raise EditorialContractError(f"historical {label} directory is missing")
    return resolved


def _read_json_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EditorialContractError(f"invalid historical {label}") from error
    if not isinstance(value, dict):
        raise EditorialContractError(f"historical {label} must be a JSON object")
    return value


def bootstrap_ep8_history(source_root: str | Path) -> dict:
    """Read rejected historical EP8 evidence without modifying any source byte."""
    root = Path(source_root).resolve()
    if not root.is_dir():
        raise EditorialContractError("historical EP8 source directory is required")
    before = _read_only_manifest(root)
    state_path = _safe_historical_path(root, root / "state.json", "state")
    request_path = _safe_historical_path(root, root / "request.json", "request")
    approval_root = _safe_historical_path(root, root / "approval", "approval", file=False)
    legacy_rejection = approval_root / "rejection.json"
    rejection_candidates = [legacy_rejection] if legacy_rejection.is_file() else sorted(
        approval_root.glob("*operator_rejection.json"))
    if len(rejection_candidates) != 1:
        raise EditorialContractError("one historical rejection receipt is required")
    rejection_path = _safe_historical_path(root, rejection_candidates[0], "rejection receipt")
    state = _read_json_object(state_path, "state")
    request = _read_json_object(request_path, "request")
    rejection = _read_json_object(rejection_path, "rejection receipt")
    episode_id = str(rejection.get("episode_id") or state.get("episode_id", ""))
    revision = rejection.get("revision", state.get("revision"))
    rejection_status = str(rejection.get("status", ""))
    if (not episode_id.startswith("EP8") or type(revision) is not int or revision < 1
            or not (rejection_status == "REJECTED" or rejection_status.startswith("REJECTED_"))):
        raise EditorialContractError("historical EP8 must contain rejected revision evidence")
    real_schema = isinstance(rejection.get("human_decision"), dict)
    raw_artifacts = rejection.get("superseded_artifacts" if real_schema else "artifacts")
    if not isinstance(raw_artifacts, list):
        raise EditorialContractError("historical rejection artifact list is required")
    identities = []
    successor_artifacts = []
    kinds = set()
    for artifact in raw_artifacts:
        if not isinstance(artifact, dict) or not {"kind", "path"} <= set(artifact):
            raise EditorialContractError("historical artifact entry is invalid")
        kind = {"video_gate": "video", "thumbnail_gate": "thumbnail"}.get(artifact["kind"], artifact["kind"])
        if kind not in {"video", "thumbnail"} or kind in kinds:
            raise EditorialContractError("one historical video and thumbnail are required")
        path = _safe_historical_path(root, root / artifact["path"], "artifact")
        identity = {"kind": kind, "path": str(path), "sha256": file_sha256(path)}
        identities.append(identity)
        successor_artifacts.append({"kind": kind, "sha256": identity["sha256"]})
        kinds.add(kind)
    if kinds != {"video", "thumbnail"}:
        raise EditorialContractError("both historical video and thumbnail are required")
    if real_schema:
        human_decision = rejection["human_decision"]
        feedback = {
            "reason": human_decision.get("reason", ""),
            "directives": rejection.get("required_rebuild_dependencies", []),
            "reviewer": "operator",
        }
    else:
        feedback = {
            "reason": rejection.get("reason", ""),
            "directives": rejection.get("directives", []),
            "reviewer": rejection.get("reviewer", ""),
        }
    predecessor = {
        "episode_id": episode_id,
        "revision": revision,
        "status": "SUPERSEDED",
        "artifacts": successor_artifacts,
    }
    brief = create_successor_brief(
        predecessor,
        feedback,
        successor_revision=revision + 1,
    )
    after = _read_only_manifest(root)
    if before != after:
        raise EditorialContractError("historical EP8 source changed during read-only bootstrap")
    return {
        "read_only": True,
        "source_root": str(root),
        "source_manifest_identity": digest(before),
        "source_bindings": before,
        "historical_request": request,
        "predecessor_identities": identities,
        "brief": brief,
        "publication_authorized": False,
    }
