"""Independent deterministic review of structured child-safe scripts."""

from __future__ import annotations

from src.agents.script_qa import ScriptQAAgent
from src.content.narrator import LORENA, closing_fields


def packet(*segments):
    segment_list = [dict(segment) for segment in segments]
    if not segment_list or segment_list[-1].get("kind") != "family_reflection":
        segment_list.append({
            "id": "CLOSING",
            "kind": "family_reflection",
            "narration": "Podemos conversar em família sobre a lição desta história.",
            "source_refs": [],
            **closing_fields(),
        })
    else:
        segment_list[-1].update(closing_fields())
    return {
        "audience": {"min_age": 6, "max_age": 10},
        "segments": segment_list,
        "narration": "\n\n".join(segment["narration"] for segment in segment_list),
        "closing_duration_s": 4,
        "recurring_narrator": dict(LORENA),
    }


def test_script_qa_accepts_sourced_paraphrase_and_family_reflection():
    result = ScriptQAAgent().review(packet(
        {"id": "S001", "kind": "biblical_paraphrase", "narration": "Abraão ouviu uma promessa.", "source_refs": ["Gênesis 15:1-6"]},
        {"id": "S002", "kind": "family_reflection", "narration": "Podemos conversar sobre confiança com a família.", "source_refs": []},
    ))
    assert result.approved is True


def test_script_qa_blocks_unsafe_language_before_tts():
    result = ScriptQAAgent().review(packet(
        {"id": "S001", "kind": "biblical_paraphrase", "narration": "Foi um segredo proibido e assustador.", "source_refs": ["Gênesis 15:1-6"]},
    ))
    assert result.approved is False
    assert "SEGREDO" in result.findings


def test_script_qa_rejects_biblical_paraphrase_without_source():
    result = ScriptQAAgent().review(packet(
        {"id": "S001", "kind": "biblical_paraphrase", "narration": "Abraão ouviu uma promessa.", "source_refs": []},
    ))
    assert result.approved is False
    assert "S001:SOURCE_REQUIRED" in result.findings


def test_script_qa_allows_factual_age_but_blocks_personal_age_request():
    safe = ScriptQAAgent().review(packet(
        {"id": "S001", "kind": "biblical_paraphrase", "narration": "Abraão pensou na idade dele e de Sara.", "source_refs": ["Gênesis 17:17"]},
    ))
    unsafe = ScriptQAAgent().review(packet(
        {"id": "S001", "kind": "family_reflection", "narration": "Diga sua idade.", "source_refs": []},
    ))
    assert safe.approved is True
    assert unsafe.approved is False
    assert "S001:PERSONAL_DATA_REQUEST" in unsafe.findings


def test_script_qa_measures_each_sentence_not_whole_semantic_segment():
    result = ScriptQAAgent().review(packet(
        {"id": "S001", "kind": "biblical_paraphrase", "narration": "Abraão ouviu uma promessa e ficou atento ao que Deus lhe disse. Ele confiou em Deus e guardou esperança mesmo quando a espera parecia longa. Sara também esperou com esperança e ouviu a promessa no tempo certo.", "source_refs": ["Gênesis 15:1-6"]},
    ))
    assert result.approved is True
