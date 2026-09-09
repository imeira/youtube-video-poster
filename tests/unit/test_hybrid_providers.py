from decimal import Decimal

import pytest

from src.hybrid.planner import Config, Geometry
from src.hybrid.providers import image_job, video_job
from tests.unit.test_hybrid_execution import manifest


def test_explicit_image_request_uses_every_frozen_reference_and_geometry(tmp_path):
    refs = manifest(tmp_path)
    request = image_job(
        Config(), "scene", refs, "approved prompt", Geometry(1000, 1000)
    )
    assert request.endpoint == "fal-ai/flux-2/klein/9b/edit"
    assert request.payload["image_urls"] == [str(a.path) for a in refs.assets]
    assert request.payload["image_size"] == {"width": 1000, "height": 1000}
    assert request.cost == Decimal(".022")
    with pytest.raises(ValueError, match="endpoint"):
        image_job(
            Config(flare_prices={"high": Decimal(".2")}),
            "scene",
            refs,
            "prompt",
            Geometry(1000, 1000),
            model="gpt-image-2.5-flare",
            quality="high",
        )
    flare = image_job(
        Config(
            flare_endpoint="deployment-approved-route",
            flare_prices={"high": Decimal(".2")},
        ),
        "scene",
        refs,
        "prompt",
        Geometry(1000, 1000),
        model="gpt-image-2.5-flare",
        quality="high",
    )
    assert flare.cost == Decimal(".2") and flare.payload["quality"] == "high"


def test_hero_payload_is_explicit_five_seconds_720p_without_audio(tmp_path):
    refs = manifest(tmp_path)
    request = video_job(Config(), "scene", refs, "leaves moving in wind")
    assert request.endpoint == "seedance-v1-5-pro-i2v"
    assert request.payload["input"]["generate_audio"] is False
    assert request.payload["input"]["resolution"] == "720p"
    assert request.payload["input"]["duration"] == 5
    assert request.cost == Decimal(".260")
    assert request.category == "hero"
    assert (
        video_job(
            Config(), "scene", refs, "wind", predecessor=request.request_id
        ).category
        == "hero_retry"
    )
