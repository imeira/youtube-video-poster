"""Fail-closed, byte-bound José editorial request validation.

This module consumes supplied bytes only. It cannot open production paths,
construct providers, resolve credentials, or create a dispatch capability.

Closed fields are identifiers, hashes, counts, scope, timing, and cross-file
references. Narrative descriptions remain free-form non-empty strings: their
text is not treated as an authorization enum.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from itertools import pairwise
from pathlib import Path
from typing import Any

_DIGEST = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
_GIT_SHA = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
_SRT_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2}):(\d{2}):(\d{2}),(\d{3})\Z")
_VTT_TIME = re.compile(r"(\d{2}):(\d{2}):(\d{2})\.(\d{3}) --> (\d{2}):(\d{2}):(\d{2})\.(\d{3})\Z")
_DURATION = Decimal("283.104")
_TIMELINE_TOLERANCE = Decimal("0.001")
_MAX_NATURAL_CAPTION_GAP = Decimal("0.100")
_EXPECTED_SCOPE = {
    "channel": "@EraUmaVezBibliaAnimada",
    "playlist": "Aventuras do Antigo Testamento",
    "locale": "pt-BR",
    "title": "José e sua túnica especial",
}


class OperationRequestError(ValueError):
    """Supplied bytes are malformed, altered, or semantically incompatible."""


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key}")
        result[key] = value
    return result


def _strict_bytes_json(raw: object, label: str) -> dict[str, Any]:
    if not isinstance(raw, bytes):
        raise OperationRequestError(f"{label} must be UTF-8 JSON bytes")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys, parse_constant=_reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise OperationRequestError(f"{label} must be strict JSON") from error
    if type(value) is not dict:
        raise OperationRequestError(f"{label} must be a JSON object")
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _raw_digest(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _closed(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise OperationRequestError(f"{label} closed schema is invalid")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise OperationRequestError(f"{label} must be a string")
    return value


def _positive_count(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise OperationRequestError(f"{label} must be a positive integer")
    return value


def _nonempty_string(value: object, label: str) -> str:
    value = _string(value, label)
    if not value.strip():
        raise OperationRequestError(f"{label} must be non-empty")
    return value


def _closed_positive_counts(value: object, keys: set[str], label: str) -> dict[str, int]:
    value = _closed(value, keys, label)
    return {key: _positive_count(value[key], f"{label} {key}") for key in keys}


def _closed_bindings(value: object, keys: set[str], label: str) -> dict[str, str]:
    value = _closed(value, keys, label)
    bindings = {key: _nonempty_string(value[key], f"{label} {key}") for key in keys}
    for key, item in bindings.items():
        if key.endswith("_sha256") or key == "approved_input_sha256":
            _digest(item, f"{label} {key}")
    return bindings


def _digest(value: object, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST.fullmatch(value):
        raise OperationRequestError(f"{label} must be a lowercase SHA-256")
    return value


def _decimal(value: object, label: str) -> Decimal:
    if type(value) not in {int, float, str}:
        raise OperationRequestError(f"{label} must be a finite decimal")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise OperationRequestError(f"{label} must be a finite decimal") from error
    if not result.is_finite():
        raise OperationRequestError(f"{label} must be a finite decimal")
    return result


def _unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise OperationRequestError(f"{label} must be unique")


def _srt_timestamp(parts: tuple[str, ...], offset: int) -> Decimal:
    hour, minute, second, millisecond = (int(part) for part in parts[offset:offset + 4])
    if minute >= 60 or second >= 60:
        raise OperationRequestError("SRT timestamp is invalid")
    return Decimal(hour * 3600 + minute * 60 + second) + Decimal(millisecond) / 1000


def _parse_srt(raw: bytes, expected_count: int, duration: Decimal) -> tuple[dict[str, object], list[tuple[int, Decimal, Decimal, str]]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise OperationRequestError("SRT must be UTF-8") from error
    blocks = re.split(r"\r?\n\r?\n", text.strip())
    cues: list[tuple[int, Decimal, Decimal, str]] = []
    for expected_index, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].isdigit():
            raise OperationRequestError("SRT cue structure is invalid")
        index = int(lines[0])
        if index != expected_index:
            raise OperationRequestError("SRT cue indices must be unique and contiguous")
        match = _SRT_TIME.fullmatch(lines[1])
        if not match:
            raise OperationRequestError("SRT timestamp is invalid")
        start, end = _srt_timestamp(match.groups(), 0), _srt_timestamp(match.groups(), 4)
        if start < 0 or start >= end or end > duration:
            raise OperationRequestError("SRT timestamp is outside the original audio duration")
        caption = "\n".join(lines[2:]).strip()
        if not caption:
            raise OperationRequestError("SRT cue text is required")
        cues.append((index, start, end, caption))
    if len(cues) != expected_count:
        raise OperationRequestError("SRT cue count does not match approved source coverage")
    gaps: list[Decimal] = []
    for previous, current in pairwise(cues):
        if current[1] < previous[2]:
            raise OperationRequestError("SRT cues must be sorted with no overlap")
        gap = current[1] - previous[2]
        if gap:
            if gap > _MAX_NATURAL_CAPTION_GAP:
                raise OperationRequestError("SRT gap exceeds the bounded natural-pause policy")
            gaps.append(gap)
    trailing = duration - cues[-1][2]
    if trailing < 0 or trailing > _MAX_NATURAL_CAPTION_GAP:
        raise OperationRequestError("SRT trailing gap exceeds the bounded natural-pause policy")
    return ({"inter_cue_gaps_seconds": [format(gap, "f") for gap in gaps], "trailing_gap_seconds": format(trailing, "f"), "policy": "bounded natural/provider pause; captions are not stretched"}, cues)


def _parse_vtt(raw: bytes, expected_cues: list[tuple[int, Decimal, Decimal, str]]) -> None:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise OperationRequestError("VTT must be UTF-8") from error
    blocks = re.split(r"\r?\n\r?\n", text.strip())
    if not blocks or blocks[0] != "WEBVTT":
        raise OperationRequestError("VTT header is invalid")
    cues: list[tuple[Decimal, Decimal, str]] = []
    for block in blocks[1:]:
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip():
            raise OperationRequestError("VTT cue structure is invalid")
        match = _VTT_TIME.fullmatch(lines[1])
        if not match:
            raise OperationRequestError("VTT timestamp is invalid")
        start, end = _srt_timestamp(match.groups(), 0), _srt_timestamp(match.groups(), 4)
        caption = "\n".join(lines[2:]).strip()
        if start >= end or not caption:
            raise OperationRequestError("VTT cue values are invalid")
        cues.append((start, end, caption))
    expected = [(start, end, caption) for _, start, end, caption in expected_cues]
    if cues != expected:
        raise OperationRequestError("VTT cue times and text must match validated SRT exactly")


@dataclass(frozen=True)
class CanonicalEditorialBundle:
    """Validated actual editorial bytes and manifest-derived publication metadata."""

    audio_duration_seconds: str
    caption_cue_count: int
    beat_count: int
    shot_count: int
    narration_input_sha256: str
    timeline_sha256: str
    asset_requirements_sha256: str
    subtitles_sha256: str
    vtt_sha256: str
    parent_manifest_sha256: str
    channel: str
    playlist: str
    language: str
    title: str
    caption_gap_report: dict[str, object]

    def artifact_hashes(self) -> dict[str, str]:
        return {"timeline.json": self.timeline_sha256, "asset_requirements.json": self.asset_requirements_sha256, "subtitles.srt": self.subtitles_sha256, "subtitles.vtt": self.vtt_sha256, "parent_manifest.json": self.parent_manifest_sha256}


def parse_canonical_editorial_bundle(*, timeline_bytes: bytes, asset_requirements_bytes: bytes, subtitles_bytes: bytes, vtt_bytes: bytes, parent_manifest_bytes: bytes, request_manifest_bytes: bytes) -> CanonicalEditorialBundle:
    """Validate closed schemas and actual relations; every input is caller-supplied bytes."""
    timeline = _strict_bytes_json(timeline_bytes, "timeline")
    assets = _strict_bytes_json(asset_requirements_bytes, "asset_requirements")
    parent = _strict_bytes_json(parent_manifest_bytes, "parent_manifest")
    manifest = _strict_bytes_json(request_manifest_bytes, "request_manifest")
    _closed(timeline, {"schema", "status", "duration_seconds", "audio_scope", "audio_bindings", "timing_method", "counts", "token_reconciliation", "asr_limitations", "beats", "publication_state", "prohibitions"}, "timeline")
    _closed(assets, {"schema", "status", "identity_requirements", "asset_requirements", "reuse_policy", "missing_frame_policy", "asset_gate"}, "asset_requirements")
    _closed(parent, {"schema", "source_root", "immutable_source_policy", "outputs", "output_hashes", "source_hashes", "counts", "qa_counts_note"}, "parent_manifest")
    if timeline["schema"] != "jose_postproduction_timeline/v1" or assets["schema"] != "jose_asset_requirements/v1" or parent["schema"] != "jose_postproduction_parent_manifest/v1":
        raise OperationRequestError("canonical schema identifier is invalid")
    scope = _closed(manifest.get("scope"), {"channel", "locale", "playlist", "title", "passage", "roadmap_episode", "audience"}, "request scope")
    for field in ("channel", "locale", "playlist", "title", "passage", "audience"):
        _nonempty_string(scope[field], f"request scope {field}")
    _positive_count(scope["roadmap_episode"], "request scope roadmap_episode")
    for key, expected in _EXPECTED_SCOPE.items():
        if scope[key] != expected:
            raise OperationRequestError(f"request manifest {key} metadata is required")
    binding_keys = {"approved_source", "approved_input_sha256", "sentence_boundary_events", "sentence_boundary_events_sha256", "audio_qa", "audio_qa_sha256", "wav", "wav_sha256", "mp3", "mp3_sha256"}
    count_keys = {"provider_sentence_boundaries", "captions", "semantic_beats", "shot_needs", "asset_requirements_missing", "declared_narrator_words", "whitespace_split_tokens", "local_unicode_internal_hyphen_apostrophe_tokens", "immutable_audio_qa_normalized_tokens"}
    output_keys = {"timeline.json", "subtitles.srt", "subtitles.vtt", "asset_requirements.json"}
    output_hashes = _closed_bindings(parent["output_hashes"], output_keys, "parent manifest output_hashes")
    for name, raw in (("timeline.json", timeline_bytes), ("asset_requirements.json", asset_requirements_bytes), ("subtitles.srt", subtitles_bytes), ("subtitles.vtt", vtt_bytes)):
        if output_hashes[name] != _raw_digest(raw):
            raise OperationRequestError(f"{name} hash does not match parent manifest")
    counts = _closed_positive_counts(timeline["counts"], count_keys, "timeline counts")
    parent_counts = _closed_positive_counts(parent["counts"], count_keys, "parent manifest counts")
    audio = _closed_bindings(timeline["audio_bindings"], binding_keys, "timeline audio_bindings")
    source_hashes = _closed_bindings(parent["source_hashes"], binding_keys, "parent manifest source_hashes")
    if source_hashes != audio:
        raise OperationRequestError("parent manifest source hashes do not match timeline audio bindings")
    captions = _positive_count(counts.get("captions"), "caption count")
    beats_count = _positive_count(counts.get("semantic_beats"), "beat count")
    shots_count = _positive_count(counts.get("shot_needs"), "shot count")
    duration = _decimal(timeline["duration_seconds"], "duration_seconds")
    if duration != _DURATION:
        raise OperationRequestError("duration_seconds must preserve the original 283.104 timing")
    gap_report, srt_cues = _parse_srt(subtitles_bytes, captions, duration)
    _parse_vtt(vtt_bytes, srt_cues)
    beats, requirements = timeline["beats"], assets["asset_requirements"]
    if type(beats) is not list or type(requirements) is not list or len(beats) != beats_count:
        raise OperationRequestError("beat count does not match timeline beats")
    beat_ids: list[str] = []
    shot_ids: list[str] = []
    referenced_assets: list[str] = []
    previous_end: Decimal | None = None
    for beat in beats:
        beat = _closed(beat, {"id", "source_verse", "meaning", "sentence_range", "start_seconds", "end_seconds", "coverage_rule", "shots"}, "timeline beat")
        beat_id = _nonempty_string(beat["id"], "beat id")
        beat_ids.append(beat_id)
        for field in ("source_verse", "meaning", "coverage_rule"):
            _nonempty_string(beat[field], f"timeline beat {field}")
        sentence_range = beat["sentence_range"]
        if (type(sentence_range) is not list or len(sentence_range) != 2
                or any(type(item) is not int for item in sentence_range)
                or not 1 <= sentence_range[0] <= sentence_range[1] <= captions):
            raise OperationRequestError("timeline beat sentence_range is invalid")
        start, end = _decimal(beat["start_seconds"], "beat start"), _decimal(beat["end_seconds"], "beat end")
        if start < 0 or start >= end or end > duration:
            raise OperationRequestError("timeline beat timing is invalid")
        if previous_end is None and start != 0:
            raise OperationRequestError("timeline beats must begin at zero")
        if previous_end is not None and abs(start - previous_end) > _TIMELINE_TOLERANCE:
            raise OperationRequestError("timeline beats must continuously cover original audio")
        previous_end = end
        if type(beat["shots"]) is not list:
            raise OperationRequestError("timeline beat shots are required")
        for shot in beat["shots"]:
            shot = _closed(shot, {"id", "focus", "asset_requirement_id", "asset_status", "use_rule"}, "timeline shot")
            for field in ("id", "focus", "asset_requirement_id", "asset_status", "use_rule"):
                _nonempty_string(shot[field], f"timeline shot {field}")
            shot_ids.append(shot["id"])
            referenced_assets.append(shot["asset_requirement_id"])
    if previous_end is None or abs(previous_end - duration) > _TIMELINE_TOLERANCE:
        raise OperationRequestError("timeline beats must end at original audio duration")
    _unique(beat_ids, "beat IDs")
    _unique(shot_ids, "shot IDs")
    if len(shot_ids) != shots_count:
        raise OperationRequestError("shot count does not match timeline shots")
    asset_ids: list[str] = []
    asset_beats: dict[str, str] = {}
    for requirement in requirements:
        requirement = _closed(requirement, {"id", "beat_id", "status", "mandatory_visual_need", "allowed_characters", "safety"}, "asset requirement")
        for field in ("id", "beat_id", "status", "mandatory_visual_need", "allowed_characters", "safety"):
            _nonempty_string(requirement[field], f"asset requirement {field}")
        identifier = requirement["id"]
        asset_ids.append(identifier)
        asset_beats[identifier] = requirement["beat_id"]
    _unique(asset_ids, "asset IDs")
    if len(asset_ids) != shots_count or set(referenced_assets) != set(asset_ids) or len(referenced_assets) != len(asset_ids):
        raise OperationRequestError("shot-to-asset relationship is incomplete or foreign")
    for beat in beats:
        for shot in beat["shots"]:
            if asset_beats[shot["asset_requirement_id"]] != beat["id"]:
                raise OperationRequestError("shot-to-asset relationship crosses a foreign beat")
    if parent_counts != counts or counts["asset_requirements_missing"] != len(asset_ids):
        raise OperationRequestError("parent manifest counts do not match canonical timeline")
    narration_sha = _digest(audio.get("approved_input_sha256"), "timeline narration hash")
    if source_hashes.get("approved_input_sha256") != narration_sha:
        raise OperationRequestError("parent manifest narration hash does not match timeline")
    return CanonicalEditorialBundle(format(duration, "f"), captions, beats_count, shots_count, narration_sha, _raw_digest(timeline_bytes), _raw_digest(asset_requirements_bytes), _raw_digest(subtitles_bytes), _raw_digest(vtt_bytes), _raw_digest(parent_manifest_bytes), _EXPECTED_SCOPE["channel"], _EXPECTED_SCOPE["playlist"], _EXPECTED_SCOPE["locale"], _EXPECTED_SCOPE["title"], gap_report)


def build_v3_preflight_artifact(*, canonical_bundle: object, base_revision: object, code_revision: object) -> dict[str, object]:
    """Create a non-authorizing artifact after an external caller verifies Git HEAD."""
    if not isinstance(canonical_bundle, CanonicalEditorialBundle):
        raise OperationRequestError("validated canonical editorial bundle is required")
    if not isinstance(base_revision, str) or not _GIT_SHA.fullmatch(base_revision):
        raise OperationRequestError("base_revision must be a full lowercase Git SHA")
    if not isinstance(code_revision, str) or not _GIT_SHA.fullmatch(code_revision):
        raise OperationRequestError("code_revision must be a full lowercase Git SHA")
    request = {"scope": {"channel": canonical_bundle.channel, "playlist": canonical_bundle.playlist, "language": canonical_bundle.language, "title": canonical_bundle.title}, "base_revision": base_revision, "code_revision": code_revision, "editorial_artifact_hashes": canonical_bundle.artifact_hashes(), "editorial_inventory": {"audio_duration_seconds": canonical_bundle.audio_duration_seconds, "caption_cue_count": canonical_bundle.caption_cue_count, "beat_count": canonical_bundle.beat_count, "shot_count": canonical_bundle.shot_count}, "caption_gap_report": canonical_bundle.caption_gap_report, "paid_generation": False, "dispatch_permitted": False}
    return {"schema": "jose-operation-preflight/v3", "request": request, "request_sha256": _raw_digest(_canonical(request)), "parent_manifest_sha256": canonical_bundle.parent_manifest_sha256, "gate": "INDEPENDENT_QA_REQUIRED_NOT_HUMAN_APPROVAL", "status": "PREPRODUCTION_READY", "human_approval": False}


def _verified_checkout_revisions(checkout_root: str | Path) -> tuple[str, str]:
    """Read provenance from Git; byte parsing intentionally has no Git authority."""
    root = Path(checkout_root)
    try:
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
        base = subprocess.check_output(["git", "merge-base", "HEAD", "origin/master"], cwd=root, text=True).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise OperationRequestError("verified checkout Git revisions are required") from error
    if not _GIT_SHA.fullmatch(head) or not _GIT_SHA.fullmatch(base):
        raise OperationRequestError("verified checkout returned invalid Git revisions")
    return head, base


def generate_v4_preflight_artifact(*, canonical_bundle: object, checkout_root: str | Path) -> dict[str, object]:
    """Create the durable non-authorizing preflight from a verified checkout."""
    code_revision, base_revision = _verified_checkout_revisions(checkout_root)
    v3 = build_v3_preflight_artifact(
        canonical_bundle=canonical_bundle,
        base_revision=base_revision,
        code_revision=code_revision,
    )
    return {
        **v3,
        "schema": "jose-operation-preflight/v4",
        "supersedes": {
            "schema": "jose-operation-preflight/v3",
            "reason": "v3 accepted caller-supplied revision strings; v4 captures Git HEAD and merge-base origin/master from the verified checkout.",
        },
    }
