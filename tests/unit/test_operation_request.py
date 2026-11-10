"""Fail-closed validation for the checked-in José editorial fixture."""
from __future__ import annotations

import json
import subprocess
from hashlib import sha256
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parents[1] / "fixtures" / "jose_gen37_v3"
REPOSITORY = Path(__file__).parents[2]
BASE_SHA = "8a4f3921b8ea8caef4caf15506aff19b8aff3116"


def _raw(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _read_bundle() -> dict[str, bytes]:
    root = FIXTURE / "postproduction_plan_v1"
    return {
        "timeline": (root / "timeline.json").read_bytes(),
        "assets": (root / "asset_requirements.json").read_bytes(),
        "subtitles": (root / "subtitles.srt").read_bytes(),
        "parent": (root / "parent_manifest.json").read_bytes(),
        "manifest": (FIXTURE / "request_manifest_v2.json").read_bytes(),
    }


def _rehash_parent(bundle: dict[str, bytes]) -> dict[str, bytes]:
    parent = json.loads(bundle["parent"])
    parent["output_hashes"].update({
        "timeline.json": sha256(bundle["timeline"]).hexdigest(),
        "asset_requirements.json": sha256(bundle["assets"]).hexdigest(),
        "subtitles.srt": sha256(bundle["subtitles"]).hexdigest(),
    })
    bundle["parent"] = _raw(parent)
    return bundle


def _parse(bundle: dict[str, bytes]):
    from src.hybrid.operation_request import parse_canonical_editorial_bundle

    return parse_canonical_editorial_bundle(
        timeline_bytes=bundle["timeline"],
        asset_requirements_bytes=bundle["assets"],
        subtitles_bytes=bundle["subtitles"],
        parent_manifest_bytes=bundle["parent"],
        request_manifest_bytes=bundle["manifest"],
    )


def test_actual_checked_in_bundle_binds_metadata_counts_and_natural_gaps():
    bundle = _parse(_read_bundle())

    assert bundle.audio_duration_seconds == "283.104"
    assert (bundle.caption_cue_count, bundle.beat_count, bundle.shot_count) == (63, 15, 45)
    assert bundle.channel == "@EraUmaVezBibliaAnimada"
    assert bundle.playlist == "Aventuras do Antigo Testamento"
    assert bundle.language == "pt-BR"
    assert bundle.title == "José e sua túnica especial"
    assert bundle.caption_gap_report == {
        "inter_cue_gaps_seconds": ["0.094"],
        "trailing_gap_seconds": "0.059",
        "policy": "bounded natural/provider pause; captions are not stretched",
    }


def test_rejects_duplicate_json_keys_nonfinite_constants_and_unknown_schema_keys():
    from src.hybrid.operation_request import OperationRequestError

    for raw in {
        "duplicate": b'{"schema":"jose_postproduction_timeline/v1","schema":"jose_postproduction_timeline/v1"}',
        "constant": b'{"schema":"jose_postproduction_timeline/v1","duration_seconds":NaN}',
    }.values():
        bundle = _read_bundle()
        bundle["timeline"] = raw
        with pytest.raises(OperationRequestError, match="strict JSON"):
            _parse(bundle)

    bundle = _read_bundle()
    timeline = json.loads(bundle["timeline"])
    timeline["unapproved"] = True
    bundle["timeline"] = _raw(timeline)
    with pytest.raises(OperationRequestError, match="closed schema"):
        _parse(_rehash_parent(bundle))


def test_rejects_boolean_nested_counts_and_metadata_omission():
    from src.hybrid.operation_request import OperationRequestError

    bundle = _read_bundle()
    timeline = json.loads(bundle["timeline"])
    timeline["counts"]["captions"] = True
    bundle["timeline"] = _raw(timeline)
    with pytest.raises(OperationRequestError, match="caption"):
        _parse(_rehash_parent(bundle))

    bundle = _read_bundle()
    manifest = json.loads(bundle["manifest"])
    del manifest["scope"]["channel"]
    bundle["manifest"] = _raw(manifest)
    with pytest.raises(OperationRequestError, match="closed schema"):
        _parse(bundle)


@pytest.mark.parametrize("target", ["beat", "shot", "asset"])
def test_rejects_duplicate_ids_and_foreign_relationships(target: str):
    from src.hybrid.operation_request import OperationRequestError

    bundle = _read_bundle()
    timeline, assets = json.loads(bundle["timeline"]), json.loads(bundle["assets"])
    if target == "beat":
        timeline["beats"][1]["id"] = timeline["beats"][0]["id"]
    elif target == "shot":
        timeline["beats"][0]["shots"][1]["id"] = timeline["beats"][0]["shots"][0]["id"]
    else:
        assets["asset_requirements"][1]["id"] = assets["asset_requirements"][0]["id"]
    bundle["timeline"], bundle["assets"] = _raw(timeline), _raw(assets)
    with pytest.raises(OperationRequestError, match="unique"):
        _parse(_rehash_parent(bundle))

    bundle = _read_bundle()
    timeline = json.loads(bundle["timeline"])
    timeline["beats"][0]["shots"][0]["asset_requirement_id"] = "AR-NOT-IN-BUNDLE"
    bundle["timeline"] = _raw(timeline)
    with pytest.raises(OperationRequestError, match="relationship"):
        _parse(_rehash_parent(bundle))


def test_rehashed_parent_still_rejects_structural_tampering():
    from src.hybrid.operation_request import OperationRequestError

    bundle = _read_bundle()
    timeline = json.loads(bundle["timeline"])
    timeline["beats"][0]["shots"] = []
    bundle["timeline"] = _raw(timeline)
    with pytest.raises(OperationRequestError, match="shot count"):
        _parse(_rehash_parent(bundle))


@pytest.mark.parametrize("mutation", ["overlap", "order", "range"])
def test_real_srt_parser_rejects_bad_times(mutation: str):
    from src.hybrid.operation_request import OperationRequestError

    bundle = _read_bundle()
    text = bundle["subtitles"].decode("utf-8")
    if mutation == "overlap":
        text = text.replace("00:00:05,026 --> 00:00:09,810", "00:00:05,000 --> 00:00:09,810", 1)
    elif mutation == "order":
        text = text.replace("00:00:05,026 --> 00:00:09,810", "00:00:09,810 --> 00:00:05,026", 1)
    else:
        text = text.replace("00:04:36,225 --> 00:04:43,045", "00:04:36,225 --> 00:04:43,105", 1)
    bundle["subtitles"] = text.encode("utf-8")
    with pytest.raises(OperationRequestError, match="SRT"):
        _parse(_rehash_parent(bundle))


@pytest.mark.parametrize("target", [
    "timeline.counts", "timeline.audio_bindings", "parent.source_hashes",
    "parent.counts", "parent.output_hashes", "request.scope",
])
def test_rehashed_bundle_rejects_unexpected_nested_contract_keys(target: str):
    from src.hybrid.operation_request import OperationRequestError

    bundle = _read_bundle()
    timeline, parent, manifest = (json.loads(bundle[key]) for key in ("timeline", "parent", "manifest"))
    container, field = target.split(".")
    value = {"timeline": timeline, "parent": parent, "request": manifest}[container][field]
    value["unexpected_nested"] = "not a contract field"
    bundle["timeline"], bundle["parent"], bundle["manifest"] = _raw(timeline), _raw(parent), _raw(manifest)

    with pytest.raises(OperationRequestError, match="closed schema"):
        _parse(_rehash_parent(bundle))


@pytest.mark.parametrize("target, replacement", [
    ("timeline.counts.captions", True),
    ("timeline.audio_bindings.approved_input_sha256", 7),
    ("parent.source_hashes.approved_input_sha256", 7),
    ("parent.counts.semantic_beats", False),
    ("parent.output_hashes.subtitles.vtt", 7),
    ("request.scope.roadmap_episode", "eight"),
])
def test_rehashed_bundle_rejects_typed_nested_contract_claims(target: str, replacement: object):
    from src.hybrid.operation_request import OperationRequestError

    bundle = _read_bundle()
    timeline, parent, manifest = (json.loads(bundle[key]) for key in ("timeline", "parent", "manifest"))
    container, field, leaf = target.split(".", 2)
    value = {"timeline": timeline, "parent": parent, "request": manifest}[container][field]
    value[leaf] = replacement
    bundle["timeline"], bundle["parent"], bundle["manifest"] = _raw(timeline), _raw(parent), _raw(manifest)

    with pytest.raises(OperationRequestError):
        _parse(_rehash_parent(bundle))


@pytest.mark.parametrize("target, replacement", [
    ("beat.source_verse", 7),
    ("beat.sentence_range", [True, 5]),
    ("shot.focus", 7),
    ("asset.safety", 7),
])
def test_rehashed_bundle_rejects_typed_nested_editorial_references(target: str, replacement: object):
    from src.hybrid.operation_request import OperationRequestError

    bundle = _read_bundle()
    timeline, assets = json.loads(bundle["timeline"]), json.loads(bundle["assets"])
    container, field = target.split(".")
    if container == "beat":
        timeline["beats"][0][field] = replacement
    elif container == "shot":
        timeline["beats"][0]["shots"][0][field] = replacement
    else:
        assets["asset_requirements"][0][field] = replacement
    bundle["timeline"], bundle["assets"] = _raw(timeline), _raw(assets)

    with pytest.raises(OperationRequestError):
        _parse(_rehash_parent(bundle))


def test_v3_preflight_remains_non_authorizing_compatibility_shape():
    from src.hybrid.operation_request import build_v3_preflight_artifact

    artifact = build_v3_preflight_artifact(
        canonical_bundle=_parse(_read_bundle()),
        base_revision=BASE_SHA,
        code_revision=BASE_SHA,
    )
    assert artifact["schema"] == "jose-operation-preflight/v3"
    assert artifact["parent_manifest_sha256"] == _parse(_read_bundle()).parent_manifest_sha256
    assert artifact["gate"] == "INDEPENDENT_QA_REQUIRED_NOT_HUMAN_APPROVAL"
    assert artifact["request"]["scope"] == {
        "channel": "@EraUmaVezBibliaAnimada",
        "playlist": "Aventuras do Antigo Testamento",
        "language": "pt-BR",
        "title": "José e sua túnica especial",
    }


def test_v4_generator_uses_verified_checkout_git_outputs(monkeypatch: pytest.MonkeyPatch):
    from src.hybrid.operation_request import generate_v4_preflight_artifact

    replies = iter(["f" * 40, BASE_SHA])
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: next(replies))

    artifact = generate_v4_preflight_artifact(canonical_bundle=_parse(_read_bundle()), checkout_root=REPOSITORY)

    assert artifact["schema"] == "jose-operation-preflight/v4"
    assert artifact["request"]["base_revision"] == BASE_SHA
    assert artifact["request"]["code_revision"] == "f" * 40
    assert artifact["supersedes"] == {
        "schema": "jose-operation-preflight/v3",
        "reason": "v3 accepted caller-supplied revision strings; v4 captures Git HEAD and merge-base origin/master from the verified checkout.",
    }


def test_v4_generator_reads_the_actual_checkout_git_revisions():
    from src.hybrid.operation_request import generate_v4_preflight_artifact

    artifact = generate_v4_preflight_artifact(canonical_bundle=_parse(_read_bundle()), checkout_root=REPOSITORY)
    expected_head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPOSITORY, text=True).strip()
    expected_base = subprocess.check_output(["git", "merge-base", "HEAD", "origin/master"], cwd=REPOSITORY, text=True).strip()

    assert artifact["request"]["code_revision"] == expected_head
    assert artifact["request"]["base_revision"] == expected_base
    assert BASE_SHA == "8a4f3921b8ea8caef4caf15506aff19b8aff3116"
