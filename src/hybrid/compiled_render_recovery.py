"""Pure, reparse-safe recovery validation for compiled Director renders."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from decimal import Decimal
from pathlib import Path
from typing import Any

from src.hybrid.locks import reject_reparse_path

DIRECTOR_RECEIPT_KEYS = {
    "schema", "intent_sha256", "completion_sha256", "render_contract",
    "render_contract_sha256", "renderer_receipt",
}
INTENT_KEYS = {"schema", "render_contract", "render_contract_sha256"}
COMPLETION_KEYS = {
    "schema", "intent_sha256", "render_contract_sha256", "output_sha256",
    "renderer_receipt", "renderer_receipt_sha256",
}


def canonical_render_json(value: dict[str, Any]) -> bytes:
    """Encode the exact canonical JSON bytes used by render evidence."""
    def json_value(item: object) -> float:
        if isinstance(item, Decimal):
            return float(item)
        raise TypeError(f"render evidence cannot encode {type(item).__name__}")

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=json_value,
    ).encode("utf-8")


def render_contract_sha256(contract: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(contract, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def read_canonical_render_json(
    path: Path, required_keys: set[str], label: str, *, canonical_episode_root: Path,
) -> tuple[dict[str, Any], str]:
    """Read one canonical evidence document from an immutable byte snapshot."""
    from src.hybrid.external_trust_root import (
        TrustRootVerificationError,
        load_strict_json_snapshot,
    )

    try:
        value, file_sha256, raw = load_strict_json_snapshot(
            path, label, contained_by=canonical_episode_root, include_bytes=True,
        )
    except TrustRootVerificationError as error:
        raise ValueError(f"{label} is unavailable") from error
    if not isinstance(value, dict) or set(value) != required_keys or raw != canonical_render_json(value):
        raise ValueError(f"{label} is not strict canonical evidence")
    return value, file_sha256


def _regular_digest(path: Path, root: Path, label: str) -> str:
    """Hash a known canonical artifact only after strict lexical containment."""
    try:
        root = reject_reparse_path(Path(root))
        candidate = reject_reparse_path(Path(path))
        candidate.relative_to(root)
        if not stat.S_ISREG(os.lstat(candidate).st_mode):
            raise ValueError("not regular")
        candidate = reject_reparse_path(candidate)
        return hashlib.sha256(candidate.read_bytes()).hexdigest()
    except (OSError, ValueError) as error:
        raise ValueError(f"{label} is unavailable") from error


def _expected_input_bindings(pipeline) -> tuple[str, str, list[dict[str, str | None]]]:
    manifest = pipeline.approved_manifest()
    audio = pipeline.episode.audio
    scenes = pipeline.render_scenes(manifest)
    scene_hashes = []
    for scene in scenes:
        image_sha256 = getattr(getattr(scene, "image", None), "sha256", None)
        clip = getattr(scene, "clip", None)
        clip_sha256 = getattr(clip, "sha256", None)
        if not isinstance(image_sha256, str) or (clip is not None and not isinstance(clip_sha256, str)):
            raise ValueError("compiled render scene hash is unavailable")
        scene_hashes.append({"image_sha256": image_sha256, "clip_sha256": clip_sha256})
    if not isinstance(getattr(manifest, "checksum", None), str) or not isinstance(
        getattr(audio, "sha256", None), str
    ):
        raise TypeError("compiled render immutable input hash is unavailable")
    return manifest.checksum, audio.sha256, scene_hashes


def _validate_contract(contract: object, canonical_root: Path, pipeline) -> dict[str, Any]:
    if not isinstance(contract, dict) or set(contract) != {
        "schema", "canonical_episode_root", "manifest_sha256", "audio_sha256",
        "scene_hashes", "renderer_config", "hold_seconds",
    }:
        raise ValueError("compiled render contract is malformed")
    manifest_sha256, audio_sha256, scene_hashes = _expected_input_bindings(pipeline)
    expected_root = str(Path(canonical_root).absolute())
    if (
        contract.get("schema") != "compiled-render-contract-v1"
        or contract.get("canonical_episode_root") != expected_root
        or contract.get("manifest_sha256") != manifest_sha256
        or contract.get("audio_sha256") != audio_sha256
        or contract.get("scene_hashes") != scene_hashes
        or type(contract.get("hold_seconds")) is not int
        or contract["hold_seconds"] not in (3, 4, 5)
        or not isinstance(contract.get("renderer_config"), dict)
        or set(contract["renderer_config"]) != {
            "width", "height", "fps", "filter_complex_threads", "scene_workers", "encoder_threads",
        }
    ):
        raise ValueError("compiled render contract does not bind current immutable inputs")
    return contract


def reopen_compiled_director_receipt(
    canonical_episode_root: Path, episode_id: str, pipeline,
) -> tuple[dict[str, Any], Path]:
    """Reopen and cross-validate canonical intent, completion, receipt and final bytes.

    All paths are derived from the adapter's configured EpisodeFS root and episode
    identity.  No caller-supplied receipt, path, callback, or token participates.
    """
    root = reject_reparse_path(Path(canonical_episode_root))
    if not isinstance(episode_id, str) or not episode_id:
        raise ValueError("compiled recovery episode identity is invalid")
    receipt_path = root / "qa" / "compiled_render_receipt.json"
    receipt, _receipt_sha256 = read_canonical_render_json(
        receipt_path, DIRECTOR_RECEIPT_KEYS, "compiled Director receipt", canonical_episode_root=root,
    )
    if receipt.get("schema") != "compiled-director-render-receipt-v1":
        raise ValueError("compiled Director receipt is malformed")
    contract = _validate_contract(receipt.get("render_contract"), root, pipeline)
    contract_sha256 = render_contract_sha256(contract)
    if receipt.get("render_contract_sha256") != contract_sha256:
        raise ValueError("compiled Director receipt contract hash is invalid")
    intent, intent_sha256 = read_canonical_render_json(
        root / "qa" / "compiled_render_intent.json", INTENT_KEYS, "compiled render intent",
        canonical_episode_root=root,
    )
    if (
        intent.get("schema") != "compiled-render-intent-v1"
        or intent.get("render_contract") != contract
        or intent.get("render_contract_sha256") != contract_sha256
        or receipt.get("intent_sha256") != intent_sha256
    ):
        raise ValueError("compiled render intent does not bind current inputs; successor required")
    completion, completion_sha256 = read_canonical_render_json(
        root / "qa" / "renderer_completion_receipt.json", COMPLETION_KEYS,
        "renderer completion receipt", canonical_episode_root=root,
    )
    renderer_receipt = completion.get("renderer_receipt")
    final_video = root / "renders" / "final.mp4"
    if (
        completion.get("schema") != "compiled-renderer-completion-v1"
        or completion.get("intent_sha256") != intent_sha256
        or completion.get("render_contract_sha256") != contract_sha256
        or not isinstance(renderer_receipt, dict)
        or completion.get("renderer_receipt_sha256")
        != hashlib.sha256(canonical_render_json(renderer_receipt)).hexdigest()
        or not isinstance(completion.get("output_sha256"), str)
        or len(completion["output_sha256"]) != 64
        or renderer_receipt.get("output_sha256") != completion["output_sha256"]
        or _regular_digest(final_video, root, "canonical final video") != completion["output_sha256"]
        or receipt.get("completion_sha256") != completion_sha256
        or receipt.get("renderer_receipt") != renderer_receipt
    ):
        raise ValueError("renderer completion receipt is not bound to current final bytes")
    return receipt, final_video
