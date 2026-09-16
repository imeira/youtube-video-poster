"""Independent deterministic review of structured child-safe scripts."""

from __future__ import annotations

from src.agents.script_qa import ScriptQAAgent


def packet(*segments):
    return {"audience": {"min_age": 6, "max_age": 10}, "segments": list(segments), "closing_duration_s": 4}


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
