"""José operation requests derive inventory from validated editorial artifacts."""
from __future__ import annotations

import json
from hashlib import sha256

import pytest

BASE_SHA = "8a4f3921b8ea8caef4caf15506aff19b8aff3116"
CODE_SHA = "61515c6ac990f349e70d462eb7676cf2cff9b0c6"
NARRATION_SHA = "ca88f3f6f1a7a451482b09ee5324cc52527d1937aaf6db9b31d7cea401f81db4"


def _raw(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _sha(value: object) -> str:
    return sha256(_raw(value)).hexdigest()


def _actual_shape_bytes() -> tuple[bytes, bytes, bytes, bytes]:
    timeline = {
        "schema": "jose_postproduction_timeline/v1", "duration_seconds": 283.104,
        "audio_bindings": {"approved_input_sha256": NARRATION_SHA},
        "counts": {"captions": 63, "semantic_beats": 15, "shot_needs": 45},
        "beats": [{"id": f"B{index:02}", "shots": [{"id": f"S{index:02}-{shot}"} for shot in range(1, 4)]} for index in range(1, 16)],
    }
    assets = {"schema": "jose_asset_requirements/v1", "asset_requirements": [{"id": f"AR-{index:02}"} for index in range(1, 46)]}
    timeline_raw, assets_raw = _raw(timeline), _raw(assets)
    subtitles_raw = "\n\n".join(f"{index}\n00:00:00,000 --> 00:00:01,000\ncue {index}" for index in range(1, 64)).encode()
    parent = {
        "schema": "jose_postproduction_parent_manifest/v1", "source_hashes": {"approved_input_sha256": NARRATION_SHA},
        "output_hashes": {"timeline.json": sha256(timeline_raw).hexdigest(), "asset_requirements.json": sha256(assets_raw).hexdigest(), "subtitles.srt": sha256(subtitles_raw).hexdigest()},
        "counts": {"captions": 63, "semantic_beats": 15, "shot_needs": 45, "asset_requirements_missing": 45},
    }
    return timeline_raw, assets_raw, subtitles_raw, _raw(parent)


def _bundle():
    from src.hybrid.operation_request import parse_canonical_editorial_bundle

    timeline_raw, assets_raw, subtitles_raw, parent_raw = _actual_shape_bytes()
    return parse_canonical_editorial_bundle(timeline_bytes=timeline_raw, asset_requirements_bytes=assets_raw,
        subtitles_bytes=subtitles_raw, parent_manifest_bytes=parent_raw)


def test_actual_postproduction_shapes_bind_correct_inventory_not_caller_counts():
    from src.hybrid.operation_request import assemble_operation_request

    request = assemble_operation_request(
        operation_id="JOSE-GEN37-20261110",
        episode_id="JOSE-GEN37",
        title="José e sua túnica especial",
        base_revision=BASE_SHA,
        code_revision=CODE_SHA,
        canonical_bundle=_bundle(),
        voice_publication_license="UNKNOWN",
    )

    assert request.request()["editorial_inventory"] == {
        "audio_duration_seconds": "283.104",
        "caption_cue_count": 63,
        "beat_count": 15,
        "shot_count": 45,
        "image_quota": None,
    }
    assert request.request()["source"] == {
        "repository": "https://github.com/imeira/youtube-video-poster.git",
        "base_revision": BASE_SHA,
        "code_revision": CODE_SHA,
        "narration_input_sha256": NARRATION_SHA,
        "editorial_artifact_hashes": _bundle().artifact_hashes(),
    }


def test_assembler_is_keyword_only_and_does_not_accept_redundant_timeline_count():
    from src.hybrid.operation_request import assemble_operation_request

    with pytest.raises(TypeError):
        assemble_operation_request(  # type: ignore[call-arg]
            "JOSE-GEN37-20261110", "JOSE-GEN37", "José e sua túnica especial", BASE_SHA, CODE_SHA, _bundle(), "UNKNOWN"
        )
    with pytest.raises(TypeError):
        assemble_operation_request(  # type: ignore[call-arg]
            operation_id="JOSE-GEN37-20261110", episode_id="JOSE-GEN37", title="José e sua túnica especial",
            base_revision=BASE_SHA, code_revision=CODE_SHA, canonical_bundle=_bundle(),
            voice_publication_license="UNKNOWN", timeline_count=63,
        )


def test_rejects_permuted_parent_hashes_before_inventory_is_available():
    from src.hybrid.operation_request import (
        OperationRequestError,
        parse_canonical_editorial_bundle,
    )

    timeline_raw, assets_raw, subtitles_raw, parent_raw = _actual_shape_bytes()
    parent = json.loads(parent_raw)
    parent["output_hashes"]["timeline.json"], parent["output_hashes"]["asset_requirements.json"] = (
        parent["output_hashes"]["asset_requirements.json"], parent["output_hashes"]["timeline.json"]
    )

    with pytest.raises(OperationRequestError, match="hash"):
        parse_canonical_editorial_bundle(
            timeline_bytes=timeline_raw, asset_requirements_bytes=assets_raw, subtitles_bytes=subtitles_raw, parent_manifest_bytes=_raw(parent)
        )


def test_rejects_inconsistent_actual_counts_and_boolean_duration():
    from src.hybrid.operation_request import (
        OperationRequestError,
        parse_canonical_editorial_bundle,
    )

    timeline_raw, assets_raw, subtitles_raw, parent_raw = _actual_shape_bytes()
    timeline = json.loads(timeline_raw)
    timeline["counts"]["captions"] = 15  # Regression: previous positional misordering.
    parent = json.loads(parent_raw)
    parent["output_hashes"]["timeline.json"] = sha256(_raw(timeline)).hexdigest()
    with pytest.raises(OperationRequestError, match="caption"):
        parse_canonical_editorial_bundle(
            timeline_bytes=_raw(timeline), asset_requirements_bytes=assets_raw, subtitles_bytes=subtitles_raw, parent_manifest_bytes=_raw(parent)
        )

    timeline_raw, assets_raw, subtitles_raw, parent_raw = _actual_shape_bytes()
    timeline = json.loads(timeline_raw)
    timeline["duration_seconds"] = True
    parent = json.loads(parent_raw)
    parent["output_hashes"]["timeline.json"] = sha256(_raw(timeline)).hexdigest()
    with pytest.raises(OperationRequestError, match="duration"):
        parse_canonical_editorial_bundle(
            timeline_bytes=_raw(timeline), asset_requirements_bytes=assets_raw, subtitles_bytes=subtitles_raw, parent_manifest_bytes=_raw(parent)
        )


def test_preflight_accepts_parsed_envelope_only_and_self_hash_is_not_authority():
    from src.hybrid.operation_request import (
        OperationRequestError,
        assemble_operation_request,
        preflight_operation_request,
    )

    envelope = json.loads(
        assemble_operation_request(
            operation_id="JOSE-GEN37-20261110", episode_id="JOSE-GEN37", title="José e sua túnica especial",
            base_revision=BASE_SHA, code_revision=CODE_SHA, canonical_bundle=_bundle(), voice_publication_license="UNKNOWN",
        ).canonical_bytes()
    )
    report = preflight_operation_request(envelope, canonical_bundle=_bundle(), expected_code_revision=CODE_SHA)
    assert report["status"] == "PREPRODUCTION_READY"
    assert report["dispatch_permitted"] is False
    assert report["paid_generation"] is False

    envelope["request"]["blocked"]["trusted_issuer_verifier"] = "SELF_HASHED_APPROVED"
    envelope["request_sha256"] = _sha(envelope["request"])
    with pytest.raises(OperationRequestError, match="invalid"):
        preflight_operation_request(envelope, canonical_bundle=_bundle(), expected_code_revision=CODE_SHA)
