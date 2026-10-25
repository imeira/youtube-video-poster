"""Offline, quality-first selection of hash-bound motion routes.

This module contains policy only.  It neither constructs providers nor submits media.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable

from src.hybrid.artifacts import digest


class MotionSelectionError(ValueError):
    """A motion-selection input is incomplete or ambiguous."""


_REQUIREMENTS = {"NONE", "CAMERA", "ARTICULATED"}
_ROUTE_KINDS = {"LOCAL_STILL", "I2V"}
_QUALITIES = ("identity", "scene_adherence", "temporal_stability")
_HEX_64 = re.compile(r"[0-9a-f]{64}")


def _hash(value: object) -> str:
    return digest(value)


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _scene(scene: dict) -> dict:
    if not isinstance(scene, dict):
        raise MotionSelectionError("motion scene must be an object")
    scene_id = _text(scene.get("scene_id"))
    requirement = scene.get("motion_requirement")
    if not scene_id or requirement not in _REQUIREMENTS:
        raise MotionSelectionError("scene id and closed motion requirement are required")
    reason = _text(scene.get("motion_reason"))
    if requirement != "NONE" and not reason:
        raise MotionSelectionError("semantic motion reason is required")
    hero = scene.get("hero", False)
    if type(hero) is not bool:
        raise MotionSelectionError("hero must be boolean")
    hero_reason = _text(scene.get("hero_reason"))
    if hero and not hero_reason:
        raise MotionSelectionError("hero requires a semantic reason")
    return {
        "scene_id": scene_id,
        "motion_requirement": requirement,
        "motion_reason": reason,
        "hero": hero,
        "hero_reason": hero_reason,
    }


def _qualification(route: dict) -> tuple[dict, dict] | None:
    """Return route evidence/quality only for a fully qualified closed record."""
    if not isinstance(route, dict):
        return None
    route_id = _text(route.get("route_id"))
    route_kind = route.get("route_kind")
    endpoint = _text(route.get("endpoint"))
    model = _text(route.get("model"))
    geometry = route.get("geometry")
    evidence_mode = route.get("evidence_mode")
    if (
        not route_id
        or route_kind not in _ROUTE_KINDS
        or not endpoint
        or not model
        # Caller-authored LIVE records are not independent evidence.  A trusted
        # adapter has not been installed, so LIVE stays in REQUIRED state.
        or evidence_mode != "TEST"
        or not isinstance(geometry, dict)
        or set(geometry) != {"width", "height"}
        or type(geometry["width"]) is not int
        or type(geometry["height"]) is not int
        or geometry["width"] <= 0
        or geometry["height"] <= 0
        or type(route.get("paid", False)) is not bool
    ):
        return None
    clip_contract = None
    if route_kind == "I2V":
        clip_fps, duration_s, audio_policy = route.get("clip_fps"), route.get("duration_s"), route.get("audio_policy")
        if (
            type(clip_fps) is not int or clip_fps <= 0
            or type(duration_s) not in {int, float} or not math.isfinite(duration_s) or duration_s <= 0
            or audio_policy not in {"REQUIRED", "FORBIDDEN"}
        ):
            return None
        clip_contract = {"clip_fps": clip_fps, "duration_s": duration_s, "audio_policy": audio_policy}
    quality = {}
    evidence = {}
    for key in _QUALITIES:
        dimension = route.get(key)
        if not isinstance(dimension, dict) or set(dimension) != {"status", "score", "evidence_hash"}:
            return None
        score = dimension["score"]
        evidence_hash = dimension["evidence_hash"]
        if (
            dimension["status"] != "PASS"
            or type(score) not in {int, float}
            or not math.isfinite(score)
            or not 0 <= score <= 1
            or not isinstance(evidence_hash, str)
            or _HEX_64.fullmatch(evidence_hash) is None
        ):
            return None
        quality[key] = float(score)
        evidence[key] = evidence_hash
    price_evidence = route.get("price_evidence")
    if route["paid"]:
        if not _price_evidence(price_evidence, route_id, endpoint, model, geometry, clip_contract["duration_s"] if clip_contract else None):
            price_evidence = None
        else:
            price_evidence = dict(price_evidence)
    elif price_evidence is not None:
        # Zero-cost routes do not acquire spend authority from optional data.
        return None
    canonical = {
        "route_id": route_id,
        "route_kind": route_kind,
        "endpoint": endpoint,
        "model": model,
        "evidence_mode": evidence_mode,
        "geometry": dict(geometry),
        "clip_contract": clip_contract,
        "paid": route.get("paid", False),
        "price_evidence": price_evidence,
        "quality": quality,
        "evidence": evidence,
    }
    return canonical, {"quality": quality, "route_evidence_hash": _hash(canonical)}


def _price_evidence(value: object, route_id: str, endpoint: str, model: str, geometry: dict, duration_s: float | None) -> bool:
    """Accept only a closed, TEST-only route-bound price fixture."""
    fields = {
        "provenance", "amount", "currency", "route_id", "endpoint", "model", "geometry", "evidence_hash"
    }
    if duration_s is not None:
        fields.add("duration_s")
    if not isinstance(value, dict) or set(value) != fields:
        return False
    return (
        value["provenance"] == "TEST_FIXTURE"
        and type(value["amount"]) in {int, float}
        and math.isfinite(value["amount"])
        and value["amount"] >= 0
        and value["currency"] == "USD"
        and value["route_id"] == route_id
        and value["endpoint"] == endpoint
        and value["model"] == model
        and value["geometry"] == geometry
        and (duration_s is None or value["duration_s"] == duration_s)
        and isinstance(value["evidence_hash"], str)
        and _HEX_64.fullmatch(value["evidence_hash"]) is not None
    )


def _eligible(requirement: str, route: dict) -> bool:
    if requirement == "ARTICULATED":
        return route["route_kind"] == "I2V"
    return route["route_kind"] == "LOCAL_STILL"


def select_motion_inventory(scenes: Iterable[dict], routes: Iterable[dict]) -> dict:
    """Return a deterministic, non-executable selection inventory.

    Invalid route evidence never gets promoted to a fallback.  It simply leaves
    the semantic scene in REQUIRED state.
    """
    normalized_scenes = tuple(_scene(scene) for scene in scenes)
    if not normalized_scenes or len({scene["scene_id"] for scene in normalized_scenes}) != len(normalized_scenes):
        raise MotionSelectionError("at least one uniquely identified motion scene is required")
    qualified = [item for route in routes if (item := _qualification(route)) is not None]
    canonical_routes = [(route, metadata) for route, metadata in qualified]
    items = []
    for scene in sorted(normalized_scenes, key=lambda item: item["scene_id"]):
        if scene["motion_requirement"] == "NONE":
            items.append({**scene, "status": "SELECTED", "route_kind": "NO_MOTION"})
            continue
        candidates = [
            (route, metadata)
            for route, metadata in canonical_routes
            if _eligible(scene["motion_requirement"], route)
        ]
        if not candidates:
            items.append({**scene, "status": "REQUIRED", "reason": "NO_QUALIFIED_ROUTE"})
            continue
        route, metadata = min(
            candidates,
            key=lambda item: (
                -item[1]["quality"]["identity"],
                -item[1]["quality"]["scene_adherence"],
                -item[1]["quality"]["temporal_stability"],
                item[0]["route_id"],
            ),
        )
        if route["paid"] and route["price_evidence"] is None:
            items.append({
                **scene,
                "status": "REQUIRED",
                "reason": "PAID_PRICE_UNKNOWN",
                "route_id": route["route_id"],
                "route_evidence_hash": metadata["route_evidence_hash"],
            })
            continue
        items.append({
            **scene,
            "status": "SELECTED",
            "route_id": route["route_id"],
            "route_kind": route["route_kind"],
            "evidence_mode": route["evidence_mode"],
            "endpoint": route["endpoint"],
            "model": route["model"],
            "geometry": route["geometry"],
            **(route["clip_contract"] or {}),
            "quality": metadata["quality"],
            "evidence": route["evidence"],
            "route_evidence_hash": metadata["route_evidence_hash"],
            **({"price_evidence": route["price_evidence"]} if route["paid"] else {}),
        })
    inventory = {"schema_version": 1, "status": "PASS" if all(item["status"] == "SELECTED" for item in items) else "REQUIRED", "items": items}
    return {**inventory, "inventory_hash": _hash(inventory)}


def quality_first_motion_binding(inventory: dict) -> dict:
    """Wrap a validated inventory for the generic compiled source binding."""
    if not isinstance(inventory, dict) or inventory.get("inventory_hash") != _hash({
        "schema_version": inventory.get("schema_version"),
        "status": inventory.get("status"),
        "items": inventory.get("items"),
    }):
        raise MotionSelectionError("motion inventory hash mismatch")
    return {"quality_first_motion": dict(inventory)}


def read_quality_first_motion_binding(source_binding: dict | None) -> dict | None:
    """Read optional bridge data without reclassifying legacy packets."""
    if source_binding is None:
        return None
    if not isinstance(source_binding, dict):
        raise MotionSelectionError("compiled source binding is malformed")
    inventory = source_binding.get("quality_first_motion")
    if inventory is None:
        return None
    return quality_first_motion_binding(inventory)["quality_first_motion"]


def validate_quality_first_motion_inventory(inventory: dict, scene_ids, mode: str) -> dict:
    """Validate an opt-in inventory against its exact compiled packet."""
    inventory = quality_first_motion_binding(inventory)["quality_first_motion"]
    expected = {str(scene_id) for scene_id in scene_ids}
    items = inventory.get("items")
    if mode != "TEST":
        raise MotionSelectionError("quality-first motion is TEST-only until trusted LIVE evidence exists")
    if inventory.get("schema_version") != 1 or inventory.get("status") not in {"PASS", "REQUIRED"}:
        raise MotionSelectionError("motion inventory schema or status is invalid")
    if not isinstance(items, list) or len(items) != len(expected) or {
        item.get("scene_id") for item in items if isinstance(item, dict)
    } != expected:
        raise MotionSelectionError("motion inventory must cover every compiled scene exactly once")
    if len([item for item in items if isinstance(item, dict)]) != len(items):
        raise MotionSelectionError("motion inventory item is malformed")
    selected = []
    for item in items:
        if not isinstance(item, dict) or item.get("status") not in {"SELECTED", "REQUIRED"}:
            raise MotionSelectionError("motion inventory status is invalid")
        if item["status"] == "REQUIRED":
            continue
        selected.append(item)
        if item.get("motion_requirement") == "NONE":
            if item.get("route_kind") != "NO_MOTION":
                raise MotionSelectionError("no-motion scene has an executable route")
            continue
        if item.get("evidence_mode") != mode or not _HEX_64.fullmatch(str(item.get("route_evidence_hash", ""))):
            raise MotionSelectionError("motion route mode or evidence binding is invalid")
        if item.get("motion_requirement") == "ARTICULATED" and item.get("route_kind") != "I2V":
            raise MotionSelectionError("articulated scene requires selected I2V route")
        required_fields = {"route_id", "route_kind", "endpoint", "model", "geometry", "quality", "route_evidence_hash"}
        if not required_fields <= set(item):
            raise MotionSelectionError("selected motion route is incomplete")
        canonical = {
            "route_id": item["route_id"], "route_kind": item["route_kind"], "endpoint": item["endpoint"],
            "model": item["model"], "evidence_mode": item["evidence_mode"], "geometry": item["geometry"],
            "clip_contract": ({"clip_fps": item.get("clip_fps"), "duration_s": item.get("duration_s"), "audio_policy": item.get("audio_policy")}
                              if item["route_kind"] == "I2V" else None),
            "paid": "price_evidence" in item, "price_evidence": item.get("price_evidence"),
            "quality": item["quality"], "evidence": {},
        }
        # The original dimension hashes are intentionally part of the digest but
        # not useful to execution; a selected record must carry them to rederive it.
        evidence = item.get("evidence")
        if not isinstance(evidence, dict) or set(evidence) != set(_QUALITIES):
            raise MotionSelectionError("selected route evidence is incomplete")
        canonical["evidence"] = evidence
        if _hash(canonical) != item["route_evidence_hash"]:
            raise MotionSelectionError("selected route evidence hash mismatch")
        if canonical["paid"] and not _price_evidence(item["price_evidence"], canonical["route_id"], canonical["endpoint"], canonical["model"], canonical["geometry"], canonical["clip_contract"]["duration_s"] if canonical["clip_contract"] else None):
            raise MotionSelectionError("selected paid route price evidence is invalid")
        if item["route_kind"] == "I2V" and (
            type(item.get("clip_fps")) is not int or item["clip_fps"] <= 0
            or type(item.get("duration_s")) not in {int, float} or not math.isfinite(item["duration_s"]) or item["duration_s"] <= 0
            or item.get("audio_policy") not in {"REQUIRED", "FORBIDDEN"}
        ):
            raise MotionSelectionError("selected I2V clip contract is incomplete")
    aggregate = "PASS" if len(selected) == len(items) else "REQUIRED"
    if inventory["status"] != aggregate:
        raise MotionSelectionError("motion inventory aggregate status is inconsistent")
    return inventory
