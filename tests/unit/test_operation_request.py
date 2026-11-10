"""Offline José operation-request assembly stays outside LIVE admission."""
from __future__ import annotations

import json

import pytest

BASE_SHA = "8a4f3921b8ea8caef4caf15506aff19b8aff3116"


def _descriptor() -> dict:
    return {
        "operation_id": "JOSE-GEN37-20261110",
        "episode_id": "JOSE-GEN37",
        "title": "José — Gênesis 37",
        "source_repository": "https://github.com/imeira/youtube-video-poster.git",
        "source_revision": BASE_SHA,
        "audio_duration_seconds": "283.104",
        "timeline_count": 63,
        "caption_cue_count": 15,
        "beat_count": 45,
        "shot_count": 45,
        "voice_publication_license": "UNKNOWN",
    }


def test_assembles_a_source_bound_offline_jose_request(tmp_path):
    from src.hybrid.operation_request import (
        assemble_operation_request,
        preflight_operation_request,
    )

    request = assemble_operation_request(_descriptor())
    path = tmp_path / "jose-operation-request-v1.json"
    path.write_bytes(request.canonical_bytes())

    report = preflight_operation_request(path, expected_source_revision=BASE_SHA)

    assert report["status"] == "PREPRODUCTION_READY"
    assert report["dispatch_permitted"] is False
    assert report["paid_generation"] is False
    assert report["request"]["editorial_inventory"]["image_quota"] is None
    assert report["request"]["provider_route"] == {
        "endpoint": "blackforestlabs/flux-3/text-to-image",
        "status": "UNQUALIFIED",
    }
    assert report["request"]["human_gates"] == [
        "INDEPENDENT_VIDEO_QA_REQUIRED",
        "INDEPENDENT_THUMBNAIL_QA_REQUIRED",
    ]
    assert report["blocked"]["trusted_issuer_verifier"] == "UNPROVISIONED"


def test_rejects_any_attempt_to_treat_shots_as_an_image_quota(tmp_path):
    from src.hybrid.operation_request import (
        OperationRequestError,
        assemble_operation_request,
    )

    descriptor = _descriptor() | {"image_quota": 45}
    with pytest.raises(OperationRequestError, match="image_quota"):
        assemble_operation_request(descriptor)


def test_preflight_rejects_a_request_tampered_after_assembly(tmp_path):
    from src.hybrid.operation_request import (
        OperationRequestError,
        assemble_operation_request,
        preflight_operation_request,
    )

    path = tmp_path / "jose-operation-request-v1.json"
    path.write_bytes(assemble_operation_request(_descriptor()).canonical_bytes())
    value = json.loads(path.read_text(encoding="utf-8"))
    value["request"]["paid_generation"] = True
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8")

    with pytest.raises(OperationRequestError, match="immutable hash mismatch"):
        preflight_operation_request(path, expected_source_revision=BASE_SHA)
