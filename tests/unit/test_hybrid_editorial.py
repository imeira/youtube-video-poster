"""Contracts for the reusable editorial and production layer."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.hybrid.editorial import (
    EditorialContractError,
    bind_research_claims,
    bootstrap_ep8_history,
    build_adaptive_plan,
    build_editorial_reports,
    build_ep8_character_bible,
    build_youtube_metadata,
    create_successor_brief,
    parse_episode_request,
    qa_audio_transcript,
    validate_storyboard,
    validate_youtube_metadata,
)


def test_parse_one_line_episode_request_is_structured_and_ep8_scoped():
    request = parse_episode_request(
        "Poste um vídeo no @EraUmaVezBibliaAnimada no idioma português do Brasil "
        "com o tema: A promessa de um filho para Abraão e Sara — Gênesis 15–18"
    )

    assert request.channel == "@EraUmaVezBibliaAnimada"
    assert request.language == "Português do Brasil"
    assert request.locale == "pt-BR"
    assert request.theme == "A promessa de um filho para Abraão e Sara"
    assert request.passage == "Gênesis 15–18"
    assert request.audience_ages == (6, 10)
    assert request.episode_id == "EP8"


def test_adaptive_plan_has_bounded_duration_words_scenes_costs_and_hero_candidates():
    request = parse_episode_request(
        "Poste um vídeo no @EraUmaVezBibliaAnimada no idioma português do Brasil "
        "com o tema: A promessa de um filho para Abraão e Sara — Gênesis 15–18"
    )
    events = [
        {"id": "E1", "label": "A promessa sob as estrelas", "importance": "HIGH"},
        {"id": "E2", "label": "Abrão confia", "importance": "NORMAL"},
        {"id": "E3", "label": "Novos nomes", "importance": "NORMAL"},
        {"id": "E4", "label": "Sara ouve a promessa", "importance": "CRITICAL"},
    ]

    plan = build_adaptive_plan(request, events, budget_usd="6.00")

    assert plan["duration_category"] == "medium"
    assert 180 <= plan["estimated_duration_seconds"] <= 900
    assert plan["estimated_word_count"] == 840
    assert plan["estimated_scene_count"] == 42
    assert [item["event_id"] for item in plan["hero_candidates"]] == ["E1", "E4"]
    assert plan["hero_candidates"][0]["approved"] is False
    assert set(plan["estimated_costs_usd"]) == {
        "stills", "tts", "hero_clips", "assembly", "total"
    }
    assert float(plan["estimated_costs_usd"]["total"]) <= 6
    assert plan["closing_hold_seconds"] == 4


def test_every_script_segment_is_bound_to_immutable_research_claims():
    claims = [
        {
            "claim_id": "C15-1",
            "text": "Deus conduz Abrão a olhar as estrelas.",
            "source_ref": "Gênesis 15:5",
            "classification": "BIBLICAL_FACT",
        },
        {
            "claim_id": "C18-1",
            "text": "Sara ouve a promessa de um filho.",
            "source_ref": "Gênesis 18:10-12",
            "classification": "BIBLICAL_FACT",
        },
    ]
    segments = [
        {"id": "S1", "narration": "Abrão olhou para o céu.", "claim_ids": ["C15-1"]},
        {"id": "S2", "narration": "Sara ouviu a promessa.", "claim_ids": ["C18-1"]},
    ]

    bound = bind_research_claims(segments, claims)

    assert all(segment["claim_bindings"] for segment in bound["segments"])
    assert bound["segments"][0]["source_refs"] == ["Gênesis 15:5"]
    assert bound["segments"][0]["segment_identity"] != bound["segments"][1]["segment_identity"]
    changed = bind_research_claims(
        segments, [{**claims[0], "text": "Texto de pesquisa alterado."}, claims[1]]
    )
    assert changed["research_identity"] != bound["research_identity"]
    assert changed["segments"][0]["segment_identity"] != bound["segments"][0]["segment_identity"]
    with pytest.raises(EditorialContractError, match="at least one research claim"):
        bind_research_claims([{"id": "S3", "narration": "Sem fonte", "claim_ids": []}], claims)


def test_editorial_reports_cover_fidelity_safety_originality_and_licenses():
    bound = bind_research_claims(
        [
            {
                "id": "S1",
                "narration": "Abrão confiou em Deus.",
                "claim_ids": ["C1"],
                "editorial_kind": "ORIGINAL_PARAPHRASE",
            }
        ],
        [
            {
                "claim_id": "C1",
                "text": "Abrão creu no Senhor.",
                "source_ref": "Gênesis 15:6",
                "classification": "BIBLICAL_FACT",
            }
        ],
    )
    licenses = [
        {
            "asset_id": "bible-source",
            "license": "public-domain-reference",
            "source": "Gênesis 15:6",
            "commercial_use": True,
            "attribution_required": False,
            "attribution": "",
        },
        {
            "asset_id": "voice",
            "license": "provider-commercial-terms",
            "source": "approved-tts-provider",
            "commercial_use": True,
            "attribution_required": True,
            "attribution": "Voice provider credit",
        },
    ]

    reports = build_editorial_reports(
        bound,
        omissions=["O nascimento de Isaque, que ocorre depois do escopo."],
        simplifications=["A passagem é contada com frases curtas."],
        tradition_notes=["A dramatização não é tratada como fato bíblico."],
        human_review_points=["Confirmar a paráfrase final."],
        licenses=licenses,
        prior_scripts=["Uma história totalmente diferente sobre o jardim."],
    )

    assert reports["status"] == "PASS"
    assert reports["fidelity"]["explicit_facts"] == ["Abrão creu no Senhor."]
    assert reports["fidelity"]["omissions"]
    assert reports["safety"]["audience"] == {"min_age": 6, "max_age": 10}
    assert reports["safety"]["violations"] == []
    assert reports["originality"]["script_identity"] == bound["script_identity"]
    assert reports["licenses"]["all_commercial_use_cleared"] is True
    assert len(reports["licenses"]["entries"]) == 2


def test_editorial_reports_block_unsafe_copy_and_incomplete_licenses():
    bound = bind_research_claims(
        [{"id": "S1", "narration": "Comente este segredo proibido!", "claim_ids": ["C1"]}],
        [{"claim_id": "C1", "text": "Fonte", "source_ref": "Gênesis 15:1", "classification": "CONTEXT"}],
    )
    license_entry = {
        "asset_id": "source",
        "license": "unknown",
        "source": "unknown",
        "commercial_use": False,
        "attribution_required": False,
        "attribution": "",
    }

    reports = build_editorial_reports(bound, licenses=[license_entry])

    assert reports["status"] == "BLOCKED"
    assert {item["rule"] for item in reports["safety"]["violations"]} >= {
        "comments_cta", "deceptive_clickbait"
    }
    assert reports["licenses"]["all_commercial_use_cleared"] is False


def test_ep8_character_bible_requires_hash_verified_approved_references(tmp_path: Path):
    references = {}
    for character_id in ("abraham", "sarah"):
        path = tmp_path / f"{character_id}.png"
        path.write_bytes(f"approved-{character_id}".encode())
        references[character_id] = [
            {
                "path": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "status": "APPROVED",
                "reviewer": "human-art-director",
                "generation_method": "reference-image-and-controlnet",
            }
        ]

    bible = build_ep8_character_bible(references)

    assert set(bible["characters"]) == {"abraham", "sarah"}
    assert bible["characters"]["abraham"]["names_by_scope"][0]["name"] == "Abrão"
    assert bible["characters"]["abraham"]["names_by_scope"][1]["name"] == "Abraão"
    assert bible["characters"]["sarah"]["names_by_scope"][0]["name"] == "Sarai"
    for card in bible["characters"].values():
        assert card["identity_lock"]["face_shape"]
        assert card["identity_lock"]["clothing"]
        assert card["approved_references"][0]["sha256"]
        assert card["authority"] == "APPROVED_REFERENCE_IMAGES"
    assert bible["bible_identity"]

    Path(references["sarah"][0]["path"]).write_bytes(b"tampered")
    with pytest.raises(EditorialContractError, match="hash mismatch"):
        build_ep8_character_bible(references)


def test_storyboard_validation_requires_complete_semantic_timeline_and_references(tmp_path: Path):
    references = {}
    hashes = {}
    for character_id in ("abraham", "sarah"):
        path = tmp_path / f"story-{character_id}.png"
        path.write_bytes(character_id.encode())
        hashes[character_id] = hashlib.sha256(path.read_bytes()).hexdigest()
        references[character_id] = [{
            "path": str(path),
            "sha256": hashes[character_id],
            "status": "APPROVED",
            "reviewer": "human",
            "generation_method": "reference-image",
        }]
    bible = build_ep8_character_bible(references)
    bound = bind_research_claims(
        [
            {"id": "S1", "narration": "Abrão olhou as estrelas.", "claim_ids": ["C1"]},
            {"id": "S2", "narration": "Sara ouviu a promessa.", "claim_ids": ["C2"]},
        ],
        [
            {"claim_id": "C1", "text": "Estrelas", "source_ref": "Gênesis 15:5", "classification": "BIBLICAL_FACT"},
            {"claim_id": "C2", "text": "Promessa", "source_ref": "Gênesis 18:10", "classification": "BIBLICAL_FACT"},
        ],
    )

    def scene(scene_id, segment_id, narration, start, end, character, claim_id, importance):
        return {
            "scene_id": scene_id,
            "segment_id": segment_id,
            "narration": narration,
            "start": start,
            "end": end,
            "duration": end - start,
            "characters": [character],
            "location": "acampamento",
            "action": "ação diretamente ligada à narração",
            "emotion": "esperança",
            "importance": importance,
            "visual_prompt": "Cinematic child-safe scene matching the narration",
            "negative_prompt": "no text, no violence, no infant",
            "camera": "slow push-in",
            "motion_intent": "gentle parallax",
            "transition": "short dissolve",
            "sfx": ["vento suave"],
            "source_claim_ids": [claim_id],
            "character_reference_hashes": {character: hashes[character]},
            "hero_candidate": importance in {"HIGH", "CRITICAL"},
        }

    storyboard = {
        "character_bible_identity": bible["bible_identity"],
        "burned_captions": False,
        "closing_hold_seconds": 4,
        "scenes": [
            scene("SC001", "S1", "Abrão olhou as estrelas.", 0.0, 4.0, "abraham", "C1", "HIGH"),
            scene("SC002", "S2", "Sara ouviu a promessa.", 4.0, 8.0, "sarah", "C2", "NORMAL"),
        ],
    }

    report = validate_storyboard(storyboard, bound, bible, audio_duration_seconds=8.0)

    assert report["status"] == "PASS"
    assert report["scene_count"] == 2
    assert report["covered_segment_ids"] == ["S1", "S2"]
    assert report["hero_scene_ids"] == ["SC001"]
    with pytest.raises(EditorialContractError, match="cover every script segment"):
        validate_storyboard({**storyboard, "scenes": storyboard["scenes"][:1]}, bound, bible, audio_duration_seconds=4.0)


def test_audio_transcript_qa_requires_decoding_word_boundaries_and_exact_text():
    bound = bind_research_claims(
        [{"id": "S1", "narration": "Abrão confiou.", "claim_ids": ["C1"]}],
        [{"claim_id": "C1", "text": "Abrão creu.", "source_ref": "Gênesis 15:6", "classification": "BIBLICAL_FACT"}],
    )
    audio = {
        "decoded": True,
        "duration_seconds": 1.25,
        "sample_rate_hz": 48000,
        "channels": 1,
        "boundary_source": "WordBoundary",
        "word_boundaries": [
            {"word": "Abrão", "start": 0.0, "end": 0.5},
            {"word": "confiou.", "start": 0.55, "end": 1.2},
        ],
    }
    sidecars = {"transcript": True, "srt": True, "vtt": True, "burned_in_video": False}

    report = qa_audio_transcript(audio, "Abrão confiou.", bound, sidecars=sidecars)

    assert report["status"] == "PASS"
    assert report["transcript_matches_script"] is True
    assert report["word_boundaries_match_transcript"] is True
    assert report["decoded_duration_seconds"] == 1.25
    assert report["sidecars"] == sidecars
    with pytest.raises(EditorialContractError, match="transcript must exactly match"):
        qa_audio_transcript(audio, "Abrão duvidou.", bound, sidecars=sidecars)


def test_complete_youtube_metadata_is_built_and_validated():
    metadata = build_youtube_metadata(
        chapters=[
            {"start_seconds": 0, "title": "A promessa sob as estrelas"},
            {"start_seconds": 60, "title": "Abrão e Sarai recebem novos nomes"},
            {"start_seconds": 120, "title": "Sara ouve uma notícia"},
        ],
        duration_seconds=180,
        transcript_artifacts={
            "transcript": {"path": "transcript.txt", "sha256": "a" * 64},
            "srt": {"path": "captions.srt", "sha256": "b" * 64},
            "vtt": {"path": "captions.vtt", "sha256": "c" * 64},
        },
        thumbnail={"path": "thumbnail.png", "sha256": "d" * 64},
        license_report_identity="e" * 64,
    )

    report = validate_youtube_metadata(metadata, duration_seconds=180)

    assert report["status"] == "PASS"
    assert metadata["made_for_kids"] is True
    assert metadata["category_id"] == "27"
    assert metadata["playlist"] == "Aventuras do Antigo Testamento"
    assert metadata["default_language"] == "pt-BR"
    assert metadata["privacy_status"] == "private"
    assert set(metadata["captions"]) == {"language", "srt", "vtt"}
    assert metadata["transcript"]["path"] == "transcript.txt"
    assert metadata["thumbnail"]["sha256"] == "d" * 64
    assert metadata["biblical_references"] == [
        "Gênesis 15:1–6", "Gênesis 17:1–9,15–21", "Gênesis 18:1–15"
    ]
    assert metadata["metadata_identity"] == report["metadata_identity"]

    with pytest.raises(EditorialContractError, match="made_for_kids"):
        validate_youtube_metadata({**metadata, "made_for_kids": False}, duration_seconds=180)


def test_rejection_feedback_is_a_mandatory_identity_bound_successor_input():
    predecessor = {
        "episode_id": "EP8",
        "revision": 1,
        "status": "REJECTED",
        "artifacts": [
            {"kind": "video", "sha256": "1" * 64},
            {"kind": "thumbnail", "sha256": "2" * 64},
        ],
    }
    rejection = {
        "reason": "Refazer vídeo e thumbnail.",
        "directives": [
            "Usar linguagem mais simples para crianças de 6 a 10 anos.",
            "Criar uma composição de thumbnail realmente nova e guiada por curiosidade.",
        ],
        "reviewer": "human-editor",
    }

    brief = create_successor_brief(predecessor, rejection, successor_revision=2)

    assert brief["revision"] == 2
    assert brief["supersedes_revision"] == 1
    assert brief["rejection_feedback"] == rejection
    assert brief["script_constraints"]["mandatory_feedback_identity"] == brief["feedback_identity"]
    assert brief["thumbnail_constraints"]["mandatory_feedback_identity"] == brief["feedback_identity"]
    assert brief["predecessor_identities"] == predecessor["artifacts"]
    changed = create_successor_brief(
        predecessor,
        {**rejection, "reason": "Outra direção editorial."},
        successor_revision=2,
    )
    assert changed["successor_identity"] != brief["successor_identity"]
    with pytest.raises(EditorialContractError, match="rejection feedback"):
        create_successor_brief(predecessor, {**rejection, "reason": ""}, successor_revision=2)


def test_historical_ep8_bootstrap_is_read_only_and_emits_brief_and_identities(tmp_path: Path):
    history = tmp_path / "EP8_PROMISE_SON_20260901"
    (history / "approval").mkdir(parents=True)
    (history / "renders").mkdir()
    (history / "thumbnails").mkdir()
    video = history / "renders" / "final.mp4"
    thumbnail = history / "thumbnails" / "thumbnail.png"
    video.write_bytes(b"historical-rejected-video")
    thumbnail.write_bytes(b"historical-rejected-thumbnail")
    (history / "state.json").write_text(
        json.dumps({"episode_id": "EP8_PROMISE_SON_20260901", "revision": 1, "status": "REJECTED"}),
        encoding="utf-8",
    )
    (history / "request.json").write_text(
        json.dumps({"theme": "A promessa de um filho para Abraão e Sara", "language": "pt-BR"}),
        encoding="utf-8",
    )
    rejection = {
        "status": "REJECTED",
        "reason": "Refazer vídeo e thumbnail.",
        "directives": [
            "Usar linguagem mais simples para crianças de 6 a 10 anos.",
            "Criar uma composição de thumbnail realmente nova e guiada por curiosidade.",
        ],
        "reviewer": "human-editor",
        "artifacts": [
            {"kind": "video", "path": "renders/final.mp4"},
            {"kind": "thumbnail", "path": "thumbnails/thumbnail.png"},
        ],
    }
    (history / "approval" / "rejection.json").write_text(json.dumps(rejection), encoding="utf-8")
    before = {path.relative_to(history).as_posix(): path.read_bytes() for path in history.rglob("*") if path.is_file()}

    result = bootstrap_ep8_history(history)

    after = {path.relative_to(history).as_posix(): path.read_bytes() for path in history.rglob("*") if path.is_file()}
    assert after == before
    assert result["read_only"] is True
    assert result["brief"]["revision"] == 2
    assert result["brief"]["rejection_feedback"]["directives"] == rejection["directives"]
    assert {item["kind"] for item in result["predecessor_identities"]} == {"video", "thumbnail"}
    for identity in result["predecessor_identities"]:
        assert len(identity["sha256"]) == 64
        assert Path(identity["path"]).is_file()
    assert result["source_manifest_identity"]
    assert result["historical_request"]["language"] == "pt-BR"
