from __future__ import annotations

import json

from src.qa.post_production import PostProductionNarrativeQA


def _write(path, value):
    path.write_text(value, encoding="utf-8")
    return path


def test_post_production_narrative_qa_blocks_engagement_cta(tmp_path):
    script = _write(
        tmp_path / "script.json",
        json.dumps({"audience": {"min_age": 6, "max_age": 10}, "segments": [{"kind": "biblical_paraphrase", "narration": "Abraão ouviu a promessa.", "source_refs": ["Gênesis 15:1"]}]}),
    )
    captions = _write(tmp_path / "captions.vtt", "WEBVTT\n\n00:00.000 --> 00:02.000\nAbraão ouviu a promessa.\n")
    metadata = _write(tmp_path / "metadata.json", json.dumps({"description": "Peça para um adulto se inscrever no canal."}))

    result = PostProductionNarrativeQA().review(script_path=script, captions_path=captions, metadata_path=metadata)

    assert result.approved is False
    assert "ENGAGEMENT_CTA" in result.findings
