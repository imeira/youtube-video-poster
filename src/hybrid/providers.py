"""Explicit request builders; no SDKs, credentials, network or automatic fallback.

Local paths in image fields are immutable input handles. A deployed Provider
adapter stages those exact bytes and translates handles to provider URLs. Its
transport must preserve request identity and checkpoint remote IDs immediately.
"""

from PIL import Image

from src.hybrid.execution import Job
from src.hybrid.planner import Geometry, image_cost, route_image


def image_job(
    config,
    scene,
    manifest,
    prompt,
    output,
    *,
    category="first",
    model="flux",
    quality=None,
    predecessor="",
):
    manifest.verify(manifest.mode)
    if not prompt.strip():
        raise ValueError("explicit prompt required")
    route = route_image(config, model, quality)
    if not route["endpoint"]:
        raise ValueError("verified deployment endpoint required")
    geometries = []
    for asset in manifest.assets:
        with Image.open(asset.path) as source:
            geometries.append(Geometry(*source.size))
    payload = {
        "prompt": prompt,
        "image_urls": [str(a.path) for a in manifest.assets],
        "image_size": {"width": output.width, "height": output.height},
    }
    if model == "flux":
        cost = image_cost(config, geometries, output)
    else:
        cost = route["price"]
        payload["quality"] = quality
    return Job(
        scene,
        category,
        manifest.mode,
        route["endpoint"],
        payload,
        manifest,
        cost,
        predecessor,
    )


def video_job(config, scene, manifest, prompt, *, predecessor=""):
    manifest.verify(manifest.mode)
    if not prompt.strip():
        raise ValueError("semantic motion prompt required")
    payload = {
        "input": {
            "image": str(manifest.assets[0].path),
            "prompt": prompt,
            "duration": config.clip_seconds,
            "resolution": config.resolution,
            "aspect_ratio": "16:9",
            "camera_fixed": True,
            "generate_audio": False,
        }
    }
    return Job(
        scene,
        "hero_retry" if predecessor else "hero",
        manifest.mode,
        config.video_endpoint,
        payload,
        manifest,
        config.video_per_second * config.clip_seconds,
        predecessor,
    )
