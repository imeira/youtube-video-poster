"""Offline preproduction execution uses fixed production adapters only."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from hashlib import sha256
from pathlib import Path

import pytest


def _source(path: Path, *, mode: str = "TEST") -> Path:
    source = {
        "schema": "local-biblical-source-v1",
        "mode": mode,
        "provenance": {
            "kind": "TEST_LOCAL_SOURCE" if mode == "TEST" else "LOCAL_SOURCE",
            "origin": "unit-test",
            "retrieved_at": "offline",
        },
        "research": {
            "story": "A promessa de um filho para Abraão e Sara",
            "references": [{"book": "Gênesis", "chapter": 15, "verses": "1-6"}],
            "narrative_classification": {
                "BIBLICAL_FACT": [
                    "Abrão confiou na promessa de Deus, mesmo sem ver todos os detalhes",
                    "Deus mudou o nome de Abrão para Abraão e o nome de Sarai para Sara",
                ]
            },
        },
    }
    path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    return path


def _audio(path: Path, duration: float) -> Path:
    """A labelled TEST fixture is a real decodable audio input, never narration."""
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}", "-ac", "2", str(path),
        ],
        check=True,
    )
    return path


def test_preproduction_executes_fixed_research_plan_script_qa_and_resumes_storyboard(tmp_path):
    from src.hybrid.preproduction import (
        advance_preproduction,
        load_preproduction,
        start_preproduction,
    )

    created = start_preproduction(tmp_path, b"Tema: promessa para criancas\n")
    source = _source(tmp_path / "source.json")

    first = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    assert first["status"] == "TTS_REQUIRED"
    revision = Path(first["active_revision_path"])
    request = json.loads((tmp_path / "PREPRODUCTION" / created["request_id"] / "request.json").read_text(encoding="utf-8"))
    assert request["execution"]["active_revision"] == f"revisions/{sha256(source.read_bytes()).hexdigest()[:16]}"
    assert (revision / "research" / "sources.json").is_file()
    assert (revision / "plan" / "editorial-plan.json").is_file()
    assert (revision / "script" / "script.json").is_file()
    assert (revision / "qa" / "script-qa.json").is_file()
    assert first["prices_usd"] is None
    assert first["paid_execution"] == "BLOCKED"

    script = json.loads((revision / "script" / "script.json").read_text(encoding="utf-8"))
    sentences = [segment["narration"] for segment in script["segments"]]
    timeline = tmp_path / "timeline.json"
    audio = _audio(tmp_path / "narration.TEST.wav", len(sentences) * 2.0)
    timeline.write_text(json.dumps({
        "schema": "local-timeline-evidence-v1", "mode": "TEST",
        "provenance": {"kind": "TEST_LOCAL_SOURCE", "origin": "unit-test", "retrieved_at": "offline"},
        "sentence_timestamps": [
            {"start": index * 2.0, "end": (index + 1) * 2.0, "duration": 2.0, "text": text}
            for index, text in enumerate(sentences)
        ],
        "audio_duration_s": len(sentences) * 2.0,
        "audio_sha256": sha256(audio.read_bytes()).hexdigest(),
        "script_sha256": sha256((revision / "script" / "script.json").read_bytes()).hexdigest(),
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
    }, ensure_ascii=False), encoding="utf-8")

    from tests.unit.test_autonomous_cli import run_cli

    code, completed = run_cli(
        "advance", "--workspace", str(tmp_path), "--request-id", created["request_id"],
        "--source-file", str(source), "--timeline-file", str(timeline), "--audio-file", str(audio),
        "--mode", "TEST",
    )
    assert code == 0, completed
    assert completed["status"] == "PREPRODUCTION_VALIDATED"
    assert (revision / "storyboard" / "scenes.json").is_file()
    assert (revision / "qa" / "storyboard.json").is_file()
    assert completed["stages"]["script"]["attempts"] == 1
    assert load_preproduction(tmp_path, created["request_id"])["status"] == "PREPRODUCTION_VALIDATED"

    script_path = revision / "script" / "script.json"
    original_script = script_path.read_bytes()
    script_path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="script artifact was altered"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    script_path.write_bytes(original_script)

    changed_source = json.loads(source.read_text(encoding="utf-8"))
    changed_source["research"]["narrative_classification"]["BIBLICAL_FACT"].append(
        "Sara ouviu a promessa com surpresa.",
    )
    source.write_text(json.dumps(changed_source, ensure_ascii=False), encoding="utf-8")
    successor = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    assert successor["status"] == "TTS_REQUIRED"
    assert successor["active_revision_path"] != str(revision)
    assert (revision / "storyboard" / "scenes.json").is_file()


def test_preproduction_plan_measures_generated_words_and_keeps_provider_prices_unknown(tmp_path):
    """Offline editorial evidence must not turn a legacy estimate into a quote."""
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    created = start_preproduction(tmp_path, "Jacó em Gênesis 28\n".encode())
    source = _source(tmp_path / "source.json", mode="PREPRODUCTION")
    result = advance_preproduction(tmp_path, created["request_id"], source, mode="PREPRODUCTION")

    plan_path = Path(result["active_revision_path"]) / "plan" / "editorial-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    recommendation = plan["recommendation"]
    assert recommendation["word_count_measured"] > 0
    assert recommendation["estimated_narration_seconds"] == pytest.approx(
        recommendation["word_count_measured"] / 130 * 60
    )
    assert recommendation["scene_count"] == recommendation["semantic_segment_count"]
    assert recommendation["timing_policy"] == "semantic_segments_only"
    assert recommendation["provider_candidates"] == []
    assert "cost_min_usd" not in recommendation
    assert "cost_likely_usd" not in recommendation
    assert "cost_max_usd" not in recommendation
    assert plan["prices_usd"] is None
    script = json.loads((Path(result["active_revision_path"]) / "script" / "script.json").read_text(encoding="utf-8"))
    assert "target_duration_s" not in script
    assert script["estimated_narration_seconds"] == pytest.approx(
        recommendation["estimated_narration_seconds"]
    )


def test_preproduction_preserves_source_bound_narrative_and_full_candidate_contract(tmp_path):
    """A source-authorized narrative is complete prose, never duration-shaped facts."""
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    source = json.loads(_source(tmp_path / "jaco-source.json", mode="PREPRODUCTION").read_text(encoding="utf-8"))
    research = source["research"]
    research["narrative_classification"]["BIBLICAL_FACT"] = [
        "Isaque enviou Jacó a Padã-Arã e recordou a bênção dada a Abraão.",
        "Jacó saiu de Berseba para Harã, dormiu com uma pedra como apoio e sonhou.",
        "No sonho, Deus prometeu presença, cuidado e retorno; Jacó chamou o lugar de Betel e fez um voto.",
    ]
    research["narrative_segments"] = [
        {
            "id": "S001", "kind": "biblical_paraphrase", "verses": "Gênesis 28:1-5",
            "facts": ["envio a Padã-Arã", "bênção de Abraão"],
            "narration": "Isaque abençoou Jacó e o enviou a Padã-Arã. Ele recordou a bênção que Deus tinha dado a Abraão.",
            "motion_need": "despedida serena, composição local",
        },
        {
            "id": "S002", "kind": "biblical_paraphrase", "verses": "Gênesis 28:10-15",
            "facts": ["partida de Berseba", "sono", "visão", "promessa de presença, cuidado e retorno"],
            "narration": "Jacó saiu de Berseba para Harã. Ao anoitecer, descansou com uma pedra como apoio para a cabeça e dormiu. No sonho, viu a escada e os anjos; Deus lhe prometeu presença, cuidado e retorno.",
            "motion_need": "transição de descanso para sonho; anjos em movimento no sonho",
        },
        {
            "id": "S003", "kind": "biblical_paraphrase", "verses": "Gênesis 28:16-22",
            "facts": ["despertar reverente", "Betel", "voto"],
            "narration": "Ao despertar, Jacó ficou admirado e reverente. Pela manhã, ergueu a pedra, derramou óleo sobre ela e chamou aquele lugar de Betel. Seu voto falou de reconhecer o cuidado de Deus; não era uma barganha.",
            "motion_need": "manhã, pedra erguida e óleo; contemplação",
        },
        {
            "id": "S004", "kind": "family_reflection", "facts": ["reflexão separada"],
            "narration": "O voto de Jacó não controlou Deus. A promessa foi para Jacó. Conversem em família sobre confiar em Deus.",
            "motion_need": "fechamento separado, sem apresentar como fala bíblica",
        },
    ]
    research["provider_candidates"] = [
        {"route": "route-a", "capability": "imagem", "qualification_status": "UNQUALIFIED", "commercial_status": "UNKNOWN"},
        {"route": "route-b", "capability": "vídeo", "qualification_status": "UNQUALIFIED", "commercial_status": "UNKNOWN"},
        {"route": "route-c", "capability": "vídeo", "qualification_status": "UNQUALIFIED", "commercial_status": "UNKNOWN"},
    ]
    path = tmp_path / "jaco-source.json"
    path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    created = start_preproduction(tmp_path, "Jacó\n".encode())

    result = advance_preproduction(tmp_path, created["request_id"], path, mode="PREPRODUCTION")

    revision = Path(result["active_revision_path"])
    script = json.loads((revision / "script" / "script.json").read_text(encoding="utf-8"))
    plan = json.loads((revision / "plan" / "editorial-plan.json").read_text(encoding="utf-8"))
    assert result["status"] == "TTS_REQUIRED"
    assert script["narration"] == "\n\n".join(item["narration"] for item in research["narrative_segments"])
    assert "..." not in script["narration"]
    assert "target_duration_s" not in script
    assert script["estimated_narration_seconds"] > 0
    assert plan["prices_usd"] is None
    assert len(plan["recommendation"]["provider_candidates"]) == 3
    assert all(candidate["qualification_status"] == "UNQUALIFIED" for candidate in plan["recommendation"]["provider_candidates"])
    assert [entry["segment_id"] for entry in plan["recommendation"]["segment_fact_matrix"]] == ["S001", "S002", "S003", "S004"]
    assert plan["recommendation"]["duration_status"] == "ESTIMATE_PENDING_MEASURED_AUDIO"
    qa = json.loads((revision / "qa" / "script-qa.json").read_text(encoding="utf-8"))
    assert qa["verdict"] == "PENDING_INDEPENDENT_EDITORIAL_REVIEW"
    assert result["stages"]["script_qa"]["status"] == "PENDING_INDEPENDENT_EDITORIAL_REVIEW"


def test_source_bound_closing_requires_one_final_reflection_and_never_invents_media_duration(tmp_path):
    """A pre-audio closing has an estimate only, not a fictional audio/video fact."""
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    source = json.loads(_source(tmp_path / "source.json", mode="PREPRODUCTION").read_text(encoding="utf-8"))
    source["research"]["narrative_segments"] = [
        {
            "id": "S001", "kind": "biblical_paraphrase", "verses": "Gênesis 28:1",
            "facts": ["bênção"], "narration": "Isaque abençoou Jacó.", "motion_need": "imagem local",
        },
        {
            "id": "S002", "kind": "family_reflection", "facts": ["reflexão"],
            "narration": "O voto de Jacó não controlou Deus. A promessa foi para Jacó. Conversem em família sobre confiar em Deus.",
            "motion_need": "fechamento editorial sem áudio ou visual aprovado",
        },
    ]
    source_path = tmp_path / "source-bound.json"
    source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    created = start_preproduction(tmp_path, "Jacó\n".encode())

    result = advance_preproduction(tmp_path, created["request_id"], source_path, mode="PREPRODUCTION")

    script = json.loads((Path(result["active_revision_path"]) / "script" / "script.json").read_text(encoding="utf-8"))
    closing = script["segments"][-1]
    assert result["status"] == "TTS_REQUIRED"
    assert [segment["kind"] for segment in script["segments"]].count("family_reflection") == 1
    assert closing["narration"] == source["research"]["narrative_segments"][-1]["narration"]
    assert script["closing_estimated_duration_s"] == pytest.approx(19 / 130 * 60)
    assert script["duration_measurement"] == "PENDING_MEASURED_AUDIO"
    assert "closing_duration_s" not in script
    assert "duration_s" not in closing
    assert "presenter" not in closing
    assert "visual_mode" not in closing
    assert "recurring_narrator" not in script


@pytest.mark.parametrize("bad_segments, finding", [
    ([
        {"id": "S001", "kind": "biblical_paraphrase", "verses": "Gênesis 28:1", "facts": ["x"], "narration": "Texto.", "motion_need": "local"},
    ], "EXACTLY_ONE_FINAL_FAMILY_REFLECTION_REQUIRED"),
    ([
        {"id": "S001", "kind": "family_reflection", "facts": ["x"], "narration": "Primeira.", "motion_need": "local"},
        {"id": "S002", "kind": "family_reflection", "facts": ["y"], "narration": "Segunda.", "motion_need": "local"},
    ], "EXACTLY_ONE_FINAL_FAMILY_REFLECTION_REQUIRED"),
    ([
        {"id": "S001", "kind": "biblical_paraphrase", "verses": "Gênesis 28:1", "facts": ["x"], "narration": "Texto…", "motion_need": "local"},
        {"id": "S002", "kind": "family_reflection", "facts": ["y"], "narration": "Reflexão.", "motion_need": "local"},
    ], "source-bound narration must be complete prose"),
])
def test_source_bound_preproduction_rejects_missing_multiple_or_unicode_incomplete_reflection(tmp_path, bad_segments, finding):
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    source = json.loads(_source(tmp_path / "source.json", mode="PREPRODUCTION").read_text(encoding="utf-8"))
    source["research"]["narrative_segments"] = bad_segments
    source_path = tmp_path / "bad-source.json"
    source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    created = start_preproduction(tmp_path, "Jacó\n".encode())

    with pytest.raises(ValueError, match=finding):
        advance_preproduction(tmp_path, created["request_id"], source_path, mode="PREPRODUCTION")


def test_source_bound_preproduction_blocks_closing_estimate_over_ten_seconds(tmp_path):
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    source = json.loads(_source(tmp_path / "source.json", mode="PREPRODUCTION").read_text(encoding="utf-8"))
    source["research"]["narrative_segments"] = [
        {"id": "S001", "kind": "biblical_paraphrase", "verses": "Gênesis 28:1", "facts": ["x"], "narration": "Texto.", "motion_need": "local"},
        {"id": "S002", "kind": "family_reflection", "facts": ["y"], "narration": " ".join(["palavra"] * 22) + ".", "motion_need": "local"},
    ]
    source_path = tmp_path / "long-closing.json"
    source_path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    created = start_preproduction(tmp_path, "Jacó\n".encode())

    with pytest.raises(ValueError, match="SOURCE_BOUND_CLOSING_ESTIMATE_EXCEEDS_10_SECONDS"):
        advance_preproduction(tmp_path, created["request_id"], source_path, mode="PREPRODUCTION")


@pytest.mark.parametrize("forbidden_key", ("price", "cost", "usd", "quote"))
def test_preproduction_rejects_nested_candidate_price_claims(tmp_path, forbidden_key):
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    source = json.loads(_source(tmp_path / "source.json", mode="PREPRODUCTION").read_text(encoding="utf-8"))
    source["research"]["provider_candidates"] = [{
        "route": "unqualified", "capability": "none", "qualification_status": "UNQUALIFIED",
        "commercial_status": "UNKNOWN", "nested": {f"{forbidden_key}_evidence": "invented"},
    }]
    path = tmp_path / "priced-source.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    created = start_preproduction(tmp_path, b"No price\n")

    with pytest.raises(ValueError, match="prices"):
        advance_preproduction(tmp_path, created["request_id"], path, mode="PREPRODUCTION")


def test_preproduction_rejects_price_claim_nested_in_candidate_array(tmp_path):
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    source = json.loads(_source(tmp_path / "source.json", mode="PREPRODUCTION").read_text(encoding="utf-8"))
    source["research"]["provider_candidates"] = [{
        "route": "unqualified", "capability": "none", "qualification_status": "UNQUALIFIED",
        "commercial_status": "UNKNOWN", "evidence": [{"quote": "invented"}],
    }]
    path = tmp_path / "array-priced-source.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    created = start_preproduction(tmp_path, b"No price array\n")

    with pytest.raises(ValueError, match="prices"):
        advance_preproduction(tmp_path, created["request_id"], path, mode="PREPRODUCTION")


def test_preproduction_rejects_unlabelled_test_source_and_invalid_storyboard_evidence(tmp_path):
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    created = start_preproduction(tmp_path, b"Tema\n")
    source = _source(tmp_path / "source.json", mode="PREPRODUCTION")
    with pytest.raises(ValueError, match="TEST source"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")

    source = _source(tmp_path / "source-test.json")
    advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    audio = _audio(tmp_path / "narration.TEST.wav", 2)
    bad_timeline = tmp_path / "bad-timeline.json"
    bad_timeline.write_text(json.dumps({
        "schema": "local-timeline-evidence-v1", "mode": "TEST",
        "provenance": {"kind": "TEST_LOCAL_SOURCE", "origin": "unit-test", "retrieved_at": "offline"},
        "sentence_timestamps": [{"start": 0, "end": 2, "duration": 2, "text": "texto adulterado"}],
        "audio_duration_s": 2,
        "audio_sha256": sha256(audio.read_bytes()).hexdigest(),
        "script_sha256": sha256((tmp_path / "PREPRODUCTION" / created["request_id"] / "revisions" / sha256(source.read_bytes()).hexdigest()[:16] / "script" / "script.json").read_bytes()).hexdigest(),
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="timeline"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST", timeline_path=bad_timeline, audio_path=audio)


def test_preproduction_imports_hash_bound_audio_and_never_materializes_compiled_assets(tmp_path, monkeypatch):
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    # Defaults must ignore an ambient plan file: it is neither source evidence nor a transport double.
    ambient = tmp_path / "ambient-sol.json"
    ambient.write_text('{"scenes": [{"visual_prompt_en": "unbound"}]}', encoding="utf-8")
    monkeypatch.setenv("STUDIO_SOL_PLAN_PATH", str(ambient))
    created = start_preproduction(tmp_path, b"Tema\n")
    source = _source(tmp_path / "source.json")
    first = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    revision = Path(first["active_revision_path"])
    script_path = revision / "script" / "script.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    audio = _audio(tmp_path / "narration.TEST.wav", len(script["segments"]) * 2)
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps({
        "schema": "local-timeline-evidence-v1", "mode": "TEST",
        "provenance": {"kind": "TEST_LOCAL_SOURCE", "origin": "unit-test", "retrieved_at": "offline"},
        "sentence_timestamps": [{"start": i * 2, "end": (i + 1) * 2, "duration": 2, "text": segment["narration"]} for i, segment in enumerate(script["segments"])],
        "audio_duration_s": len(script["segments"]) * 2,
        "audio_sha256": sha256(audio.read_bytes()).hexdigest(),
        "script_sha256": sha256(script_path.read_bytes()).hexdigest(),
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
    }, ensure_ascii=False), encoding="utf-8")

    result = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST", timeline_path=timeline, audio_path=audio)

    assert result["status"] == "PREPRODUCTION_VALIDATED"
    assert result["compilation"]["status"] == "ASSETS_REQUIRED"
    assert (revision / "inputs" / "audio.wav").read_bytes() == audio.read_bytes()
    assert not (revision / "compiled.json").exists()
    assert not (revision / "manifest.json").exists()
    audio.write_bytes(b"altered")
    with pytest.raises(ValueError, match="audio"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST", timeline_path=timeline, audio_path=audio)


def test_preproduction_pending_qa_artifact_blocks_timeline_and_audio_before_reads(tmp_path, monkeypatch):
    """A forged stage PASS cannot override a pending immutable QA artifact."""
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    source_document = json.loads(_source(tmp_path / "source.json").read_text(encoding="utf-8"))
    source_document["research"]["narrative_segments"] = [
        {
            "id": "S001", "kind": "biblical_paraphrase", "verses": "Gênesis 15:1-6",
            "facts": ["promessa"], "narration": "Deus fez uma promessa a Abrão.",
            "motion_need": "composição local",
        },
        {
            "id": "S002", "kind": "family_reflection", "facts": ["reflexão"],
            "narration": "A promessa foi para Abrão. Conversem em família sobre confiar em Deus.",
            "motion_need": "fechamento editorial",
        },
    ]
    source = tmp_path / "source-bound.TEST.json"
    source.write_text(json.dumps(source_document, ensure_ascii=False), encoding="utf-8")
    created = start_preproduction(tmp_path, b"Pending QA boundary\n")
    initial = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    revision = Path(initial["active_revision_path"])
    stages_path = revision / "stages.json"
    stages = json.loads(stages_path.read_text(encoding="utf-8"))
    assert stages["script_qa"]["status"] == "PENDING_INDEPENDENT_EDITORIAL_REVIEW"
    stages["script_qa"]["status"] = "PASS"
    stages_path.write_text(json.dumps(stages), encoding="utf-8")

    timeline = tmp_path / "unread-timeline.json"
    audio = tmp_path / "unread-audio.wav"
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text

    def forbid_timeline_or_audio_bytes(self, *args, **kwargs):
        if self in {timeline, audio}:
            raise AssertionError("timeline or audio was read before QA rejection")
        return original_read_bytes(self, *args, **kwargs)

    def forbid_timeline_or_audio_text(self, *args, **kwargs):
        if self in {timeline, audio}:
            raise AssertionError("timeline or audio was read before QA rejection")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", forbid_timeline_or_audio_bytes)
    monkeypatch.setattr(Path, "read_text", forbid_timeline_or_audio_text)
    with pytest.raises(ValueError, match="independent editorial review is pending"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST", timeline_path=timeline, audio_path=audio)


def test_preproduction_qa_hash_mismatch_blocks_timeline_and_audio_before_reads(tmp_path, monkeypatch):
    """Tampered QA bytes are rejected before timeline or audio evidence is consumed."""
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    created = start_preproduction(tmp_path, b"QA hash boundary\n")
    source = _source(tmp_path / "source.json")
    initial = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    revision = Path(initial["active_revision_path"])
    qa_path = revision / "qa" / "script-qa.json"
    qa_path.write_text("{}", encoding="utf-8")

    timeline = tmp_path / "unread-timeline.json"
    audio = tmp_path / "unread-audio.wav"
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text

    def forbid_timeline_or_audio_bytes(self, *args, **kwargs):
        if self in {timeline, audio}:
            raise AssertionError("timeline or audio was read before QA rejection")
        return original_read_bytes(self, *args, **kwargs)

    def forbid_timeline_or_audio_text(self, *args, **kwargs):
        if self in {timeline, audio}:
            raise AssertionError("timeline or audio was read before QA rejection")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", forbid_timeline_or_audio_bytes)
    monkeypatch.setattr(Path, "read_text", forbid_timeline_or_audio_text)
    with pytest.raises(ValueError, match="script_qa artifact was altered"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST", timeline_path=timeline, audio_path=audio)


def test_preproduction_resume_preserves_validated_storyboard_without_requiring_tts_again(tmp_path):
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    created = start_preproduction(tmp_path, b"Tema\n")
    source = _source(tmp_path / "source.json")
    first = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    revision = Path(first["active_revision_path"])
    script = json.loads((revision / "script" / "script.json").read_text(encoding="utf-8"))
    audio = _audio(tmp_path / "narration.TEST.wav", len(script["segments"]) * 2)
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps({
        "schema": "local-timeline-evidence-v1", "mode": "TEST",
        "provenance": {"kind": "TEST_LOCAL_SOURCE", "origin": "unit-test", "retrieved_at": "offline"},
        "sentence_timestamps": [{"start": i * 2, "end": (i + 1) * 2, "duration": 2, "text": segment["narration"]} for i, segment in enumerate(script["segments"])],
        "audio_duration_s": len(script["segments"]) * 2,
        "audio_sha256": sha256(audio.read_bytes()).hexdigest(),
        "script_sha256": sha256((revision / "script" / "script.json").read_bytes()).hexdigest(),
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
    }, ensure_ascii=False), encoding="utf-8")
    completed = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST", timeline_path=timeline, audio_path=audio)

    resumed = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")

    assert completed["status"] == resumed["status"] == "PREPRODUCTION_VALIDATED"
    assert resumed["stages"]["storyboard"]["attempts"] == 1

    storyboard_qa = revision / "qa" / "storyboard.json"
    storyboard_qa.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="storyboard_qa artifact was altered"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")


def test_audio_preflight_requires_trusted_hash_bound_review_and_authorization_before_provider(tmp_path):
    """A local self-emitted report never authorizes TTS or even provider construction."""
    from src.hybrid.audio_preproduction import preflight_audio_execution
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    created = start_preproduction(tmp_path, b"Audio boundary\n")
    source = _source(tmp_path / "source.json")
    initial = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    revision = Path(initial["active_revision_path"])
    original_qa = json.loads((revision / "qa" / "script-qa.json").read_text(encoding="utf-8"))
    script_sha = sha256((revision / "script" / "script.json").read_bytes()).hexdigest()
    source_sha = sha256(source.read_bytes()).hexdigest()
    local_report = tmp_path / "local-report.json"
    local_report.write_text(json.dumps({"schema": "independent-editorial-review-record-v1", "editorial_verdict": "NO_NARRATIVE_BLOCKER"}), encoding="utf-8")
    authorization = tmp_path / "authorization.json"
    authorization.write_text(json.dumps({"schema": "audio-execution-authorization-v1", "mode": "TEST", "authorized": True, "provider": "edge-tts", "source_sha256": source_sha, "script_sha256": script_sha}), encoding="utf-8")
    constructed = False
    blocked = preflight_audio_execution(tmp_path, created["request_id"], local_report, authorization, provider_factory=lambda: (_ for _ in ()).throw(AssertionError("provider constructed")))
    assert blocked["status"] == "EXTERNAL_AUDIO_SYNTHESIS_BLOCKED"
    assert constructed is False

    review = tmp_path / "review.json"
    review.write_text(json.dumps({
        "schema": "source-bound-independent-editorial-review-v1", "mode": "TEST",
        "reviewer": "test-independent-editorial-reviewer-v1", "verdict": "PASS",
        "source_sha256": source_sha, "script_sha256": script_sha,
    }), encoding="utf-8")
    blocked_auth = tmp_path / "blocked-auth.json"
    blocked_auth.write_text(json.dumps({"schema": "audio-execution-authorization-v1", "mode": "TEST", "authorized": False, "provider": "edge-tts", "source_sha256": source_sha, "script_sha256": script_sha}), encoding="utf-8")
    blocked_bad_auth = preflight_audio_execution(tmp_path, created["request_id"], review, blocked_auth, provider_factory=lambda: (_ for _ in ()).throw(AssertionError("provider constructed")))
    assert blocked_bad_auth["status"] == "EXTERNAL_AUDIO_SYNTHESIS_BLOCKED"

    ready = preflight_audio_execution(tmp_path, created["request_id"], review, authorization, provider_factory=lambda: (_ for _ in ()).throw(AssertionError("preflight must not construct provider")))
    assert ready["status"] == "EXTERNAL_AUDIO_SYNTHESIS_BLOCKED"
    stages = json.loads((revision / "stages.json").read_text(encoding="utf-8"))
    assert stages["script_qa"]["status"] == "PASS"
    assert "machine_audio_qa" not in stages
    assert json.loads((revision / "qa" / "script-qa.json").read_text(encoding="utf-8")) == original_qa

    from tests.unit.test_autonomous_cli import run_cli
    code, cli_ready = run_cli("audio-preflight", "--workspace", str(tmp_path), "--request-id", created["request_id"], "--review-file", str(review), "--audio-authorization-file", str(authorization))
    assert code == 0 and cli_ready["status"] == "EXTERNAL_AUDIO_SYNTHESIS_BLOCKED"


def test_authorized_audio_execution_caches_segments_and_advances_with_measured_wav_and_wordboundaries(tmp_path):
    from types import SimpleNamespace

    from src.hybrid.audio_preproduction import execute_authorized_audio
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    created = start_preproduction(tmp_path, b"Audio execution\n")
    source = _source(tmp_path / "source.json")
    initial = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    revision = Path(initial["active_revision_path"])
    source_sha = sha256(source.read_bytes()).hexdigest()
    script_sha = sha256((revision / "script" / "script.json").read_bytes()).hexdigest()
    review = tmp_path / "review.json"
    review.write_text(json.dumps({"schema": "source-bound-independent-editorial-review-v1", "mode": "TEST", "reviewer": "test-independent-editorial-reviewer-v1", "verdict": "PASS", "source_sha256": source_sha, "script_sha256": script_sha}), encoding="utf-8")
    auth = tmp_path / "auth.json"
    auth.write_text(json.dumps({"schema": "audio-execution-authorization-v1", "mode": "TEST", "authorized": True, "provider": "edge-tts", "source_sha256": source_sha, "script_sha256": script_sha}), encoding="utf-8")

    class FixtureTransport:
        calls = 0
        async def synthesize(self, text, *, output_path, **_kwargs):
            type(self).calls += 1
            duration = len(text.replace(".", "").split()) * 0.1 + 0.1
            _audio(Path(output_path), duration)
            words = [{"word": word, "start": index * 0.1, "end": (index + 1) * 0.1} for index, word in enumerate(text.replace(".", "").split())]
            return SimpleNamespace(success=True, audio_path=str(output_path), duration_seconds=duration, word_timestamps=words, metadata={"boundary_source": "TEST_WordBoundary"})

    with pytest.raises(RuntimeError, match="EXTERNAL_AUDIO_SYNTHESIS_BLOCKED"):
        execute_authorized_audio(tmp_path, created["request_id"], review, auth, provider_factory=FixtureTransport)
    assert FixtureTransport.calls == 0
    assert not (revision / "audio").exists()


def test_preproduction_load_and_advance_reject_reparse_inputs_before_any_read(tmp_path, monkeypatch):
    """Every preproduction read must reject lexical links before consuming bytes."""
    from src.hybrid.preproduction import (
        advance_preproduction,
        load_preproduction,
        start_preproduction,
    )

    created = start_preproduction(tmp_path, b"Boundary\n")
    source = _source(tmp_path / "source.json")
    root = tmp_path / "PREPRODUCTION" / created["request_id"]
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    prompt = root / "prompt.txt"
    prompt.unlink()
    prompt.symlink_to(outside)

    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text

    def forbid_outside_read(self, *args, **kwargs):
        if self in {prompt, outside}:
            raise AssertionError("forbidden linked input was read")
        return original_read_bytes(self, *args, **kwargs)

    def forbid_outside_text(self, *args, **kwargs):
        if self in {prompt, outside}:
            raise AssertionError("forbidden linked input was read")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", forbid_outside_read)
    monkeypatch.setattr(Path, "read_text", forbid_outside_text)
    with pytest.raises(ValueError, match="reparse"):
        load_preproduction(tmp_path, created["request_id"])
    with pytest.raises(ValueError, match="reparse"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")


def test_preproduction_advance_rejects_linked_source_before_read(tmp_path, monkeypatch):
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    created = start_preproduction(tmp_path, b"Boundary source\n")
    outside = tmp_path / "outside-source.json"
    _source(outside)
    source = tmp_path / "source.json"
    source.symlink_to(outside)
    original_read_bytes = Path.read_bytes

    def forbid_linked_source(self, *args, **kwargs):
        if self in {source, outside}:
            raise AssertionError("forbidden linked source was read")
        return original_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", forbid_linked_source)
    with pytest.raises(ValueError, match="reparse"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions are native reparse points")
def test_preproduction_load_rejects_native_windows_junction_before_read(tmp_path, monkeypatch):
    from src.hybrid.preproduction import load_preproduction, start_preproduction

    target = tmp_path / "target-workspace"
    created = start_preproduction(target, b"Native junction boundary\n")
    junction = tmp_path / "junction-workspace"
    created_junction = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        check=False,
    )
    if created_junction.returncode:
        pytest.skip("native junction unavailable")
    request = junction / "PREPRODUCTION" / created["request_id"] / "request.json"
    original_read_text = Path.read_text

    def forbid_junction_read(self, *args, **kwargs):
        if self == request:
            raise AssertionError("forbidden junction input was read")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbid_junction_read)
    with pytest.raises(ValueError, match="reparse"):
        load_preproduction(junction, created["request_id"])


def test_preproduction_advance_serializes_same_request_and_preserves_one_progression(tmp_path, monkeypatch):
    from src.hybrid import preproduction
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    created = start_preproduction(tmp_path, b"Concurrent progression\n")
    source = _source(tmp_path / "source.json")
    original_run = preproduction.ScriptAgent.run
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    async def delayed_run(self, *args, **kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        entered.set()
        assert release.wait(timeout=5)
        return await original_run(self, *args, **kwargs)

    monkeypatch.setattr(preproduction.ScriptAgent, "run", delayed_run)
    results: list[dict] = []
    errors: list[BaseException] = []

    def advance():
        try:
            results.append(advance_preproduction(tmp_path, created["request_id"], source, mode="TEST"))
        except Exception as error:  # noqa: BLE001 - propagate worker failures to the assertion
            errors.append(error)

    first = threading.Thread(target=advance)
    second = threading.Thread(target=advance)
    first.start()
    assert entered.wait(timeout=5)
    second.start()
    time.sleep(0.1)
    release.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not errors
    assert len(results) == 2
    assert calls == 1
    assert {result["status"] for result in results} == {"TTS_REQUIRED"}
    assert {result["stages"]["script"]["attempts"] for result in results} == {1}
    request = json.loads((tmp_path / "PREPRODUCTION" / created["request_id"] / "request.json").read_text(encoding="utf-8"))
    assert request["package_sha256"] == sha256(json.dumps(
        {key: value for key, value in request.items() if key != "package_sha256"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def test_compile_preproduction_creates_hash_bound_test_packet_for_existing_compiled_start(tmp_path):
    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.preproduction import (
        advance_preproduction,
        compile_preproduction,
        start_preproduction,
    )

    created = start_preproduction(tmp_path, b"Tema\n")
    source = _source(tmp_path / "source.json")
    first = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    revision = Path(first["active_revision_path"])
    script = json.loads((revision / "script" / "script.json").read_text(encoding="utf-8"))
    audio = _audio(tmp_path / "narration.TEST.wav", len(script["segments"]) * 2)
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps({
        "schema": "local-timeline-evidence-v1", "mode": "TEST",
        "provenance": {"kind": "TEST_LOCAL_SOURCE", "origin": "unit-test", "retrieved_at": "offline"},
        "sentence_timestamps": [{"start": i * 2, "end": (i + 1) * 2, "duration": 2, "text": segment["narration"]} for i, segment in enumerate(script["segments"])],
        "audio_duration_s": len(script["segments"]) * 2,
        "audio_sha256": sha256(audio.read_bytes()).hexdigest(),
        "script_sha256": sha256((revision / "script" / "script.json").read_bytes()).hexdigest(),
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
    }, ensure_ascii=False), encoding="utf-8")
    advance_preproduction(tmp_path, created["request_id"], source, mode="TEST", timeline_path=timeline, audio_path=audio)

    assets = tmp_path / "assets"
    assets.mkdir()
    reference = assets / "reference.TEST.png"
    Image.new("RGB", (64, 64), "navy").save(reference)
    frozen = FrozenAsset.approve(reference, "TEST local fixture", "TEST")
    sheet = assets / "sheet.TEST.png"
    contact_sheet((frozen,), sheet)
    manifest = Manifest.freeze((frozen,), FrozenAsset.approve(sheet, "TEST local fixture", "TEST"), "TEST local fixture", "TEST")
    manifest_path = assets / "manifest.json"
    manifest.save(manifest_path)

    packet = compile_preproduction(tmp_path, created["request_id"], manifest_path)
    packet_root = Path(packet["packet_path"])
    assert packet["mode"] == "TEST"
    assert (packet_root / "compiled.json").is_file()
    assert (packet_root / "manifest.json").is_file()
    assert (packet_root / "policy.json").is_file()
    receipt = json.loads((packet_root / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["compilation_input_sha256"] == packet["compilation_input_sha256"]
    assert receipt["script_qa_sha256"] == sha256((revision / "qa" / "script-qa.json").read_bytes()).hexdigest()

    from tests.unit.test_autonomous_cli import run_cli

    code, compiled = run_cli("compile-preproduction", "--workspace", str(tmp_path), "--request-id", created["request_id"], "--manifest-file", str(manifest_path))
    assert code == 0 and compiled["receipt_sha256"] == packet["receipt_sha256"]
    code, started = run_cli("start", "--workspace", str(tmp_path / "compiled-run"), "--compiled-input", str(packet_root))
    assert code == 0 and started["state"] == "WAITING_THUMBNAIL_APPROVAL"
    code, thumb = run_cli("approve", "--workspace", str(tmp_path / "compiled-run"), "--request-id", started["request_id"], "--kind", "thumbnail", "--test-approval")
    assert code == 0 and thumb["state"] == "WAITING_VIDEO_APPROVAL"
    code, video = run_cli("approve", "--workspace", str(tmp_path / "compiled-run"), "--request-id", started["request_id"], "--kind", "video", "--test-approval")
    assert code == 0 and video["state"] == "READY_TO_PUBLISH"

    (revision / "inputs" / "source.json").write_bytes(b"mutated")
    with pytest.raises(ValueError, match="source"):
        compile_preproduction(tmp_path, created["request_id"], manifest_path)


def _validated_compile_request(tmp_path):
    """Create a real TEST revision and frozen local manifest for compiler boundaries."""
    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    created = start_preproduction(tmp_path, b"Boundary fixture\n")
    source = _source(tmp_path / "source.json")
    first = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    revision = Path(first["active_revision_path"])
    script = json.loads((revision / "script" / "script.json").read_text(encoding="utf-8"))
    audio = _audio(tmp_path / "narration.TEST.wav", len(script["segments"]) * 2)
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps({
        "schema": "local-timeline-evidence-v1", "mode": "TEST",
        "provenance": {"kind": "TEST_LOCAL_SOURCE", "origin": "unit-test", "retrieved_at": "offline"},
        "sentence_timestamps": [{"start": i * 2, "end": (i + 1) * 2, "duration": 2, "text": segment["narration"]} for i, segment in enumerate(script["segments"])],
        "audio_duration_s": len(script["segments"]) * 2,
        "audio_sha256": sha256(audio.read_bytes()).hexdigest(),
        "script_sha256": sha256((revision / "script" / "script.json").read_bytes()).hexdigest(),
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
    }, ensure_ascii=False), encoding="utf-8")
    advance_preproduction(tmp_path, created["request_id"], source, mode="TEST", timeline_path=timeline, audio_path=audio)
    assets = tmp_path / "assets"
    assets.mkdir()
    reference = assets / "reference.TEST.png"
    Image.new("RGB", (64, 64), "navy").save(reference)
    frozen = FrozenAsset.approve(reference, "TEST local fixture", "TEST")
    sheet = assets / "sheet.TEST.png"
    contact_sheet((frozen,), sheet)
    manifest_path = assets / "manifest.json"
    Manifest.freeze((frozen,), FrozenAsset.approve(sheet, "TEST local fixture", "TEST"), "TEST local fixture", "TEST").save(manifest_path)
    return created, revision, manifest_path


@pytest.mark.parametrize("relative", ("prompt.txt", "inputs/audio.wav"))
def test_compile_preproduction_rejects_linked_pre_read_inputs(tmp_path, relative, monkeypatch):
    from src.hybrid.preproduction import compile_preproduction

    created, revision, manifest_path = _validated_compile_request(tmp_path)
    target = tmp_path / "outside-bytes"
    target.write_bytes(b"outside")
    path = tmp_path / "PREPRODUCTION" / created["request_id"] / relative if relative == "prompt.txt" else revision / relative
    path.unlink()
    path.symlink_to(target)
    original_read_bytes = Path.read_bytes

    def forbid_link_read(self):
        if self == path:
            raise AssertionError("forbidden linked input was read")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", forbid_link_read)
    with pytest.raises(ValueError, match="reparse"):
        compile_preproduction(tmp_path, created["request_id"], manifest_path)


def test_compile_preproduction_rejects_manifest_binding_before_manifest_load(tmp_path, monkeypatch):
    from src.hybrid.preproduction import compile_preproduction

    created, _revision, manifest_path = _validated_compile_request(tmp_path)
    binding = tmp_path / "assets" / "sheet.TEST.binding.json"
    target = tmp_path / "outside-binding.json"
    target.write_text("{}", encoding="utf-8")
    binding.unlink()
    binding.symlink_to(target)
    original_read_text = Path.read_text

    def forbid_binding_read(self, *args, **kwargs):
        if self == binding:
            raise AssertionError("forbidden linked binding was read")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbid_binding_read)
    with pytest.raises(ValueError, match="reparse"):
        compile_preproduction(tmp_path, created["request_id"], manifest_path)


def test_compile_preproduction_rejects_linked_existing_receipt_before_read(tmp_path, monkeypatch):
    from src.hybrid.preproduction import compile_preproduction

    created, revision, manifest_path = _validated_compile_request(tmp_path)
    compile_preproduction(tmp_path, created["request_id"], manifest_path)
    receipt = revision / "compiled-test" / "receipt.json"
    target = tmp_path / "outside-receipt.json"
    target.write_text("{}", encoding="utf-8")
    receipt.unlink()
    receipt.symlink_to(target)
    original_read_text = Path.read_text

    def forbid_receipt_read(self, *args, **kwargs):
        if self == receipt:
            raise AssertionError("forbidden linked receipt was read")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbid_receipt_read)
    with pytest.raises(ValueError, match="reparse"):
        compile_preproduction(tmp_path, created["request_id"], manifest_path)


def test_compile_preproduction_crash_stages_then_resumes_without_overwriting_final(tmp_path, monkeypatch):
    from src.hybrid import preproduction
    from src.hybrid.preproduction import compile_preproduction

    created, revision, manifest_path = _validated_compile_request(tmp_path)
    original_replace = preproduction.os.replace

    def crash_on_packet_publish(source, destination):
        if Path(destination).name == "compiled-test":
            raise RuntimeError("simulated crash before publish")
        return original_replace(source, destination)

    monkeypatch.setattr(preproduction.os, "replace", crash_on_packet_publish)
    with pytest.raises(RuntimeError, match="simulated crash"):
        compile_preproduction(tmp_path, created["request_id"], manifest_path)
    packet = revision / "compiled-test"
    assert not packet.exists()
    assert any(path.name.startswith(".compiled-test.staging-") for path in revision.iterdir())
    monkeypatch.setattr(preproduction.os, "replace", original_replace)
    resumed = compile_preproduction(tmp_path, created["request_id"], manifest_path)
    assert Path(resumed["packet_path"]) == packet
    receipt = (packet / "receipt.json").read_bytes()
    assert compile_preproduction(tmp_path, created["request_id"], manifest_path)["receipt_sha256"] == resumed["receipt_sha256"]
    assert (packet / "receipt.json").read_bytes() == receipt
