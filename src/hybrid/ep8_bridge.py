"""Read-only migration bridge for pre-existing approved EP8 visual assets."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from src.hybrid.artifacts import atomic_json, sha256


@dataclass(frozen=True)
class Ep8Import:
    frame_id: str
    scene_id: str
    sequence_index: int
    manifest_path: str
    manifest_sha256: str
    asset_path: str
    asset_sha256: str


class Ep8ApprovedBridge:
    """Imports only state-bound approved imagery; it never submits media work."""

    schema = "hybrid-approved-frame-import/1"
    consumed = {
        "R029": {
            "job_id": "ep8-r029-distinct-cdn-token-production-executor-v21",
            "wal_path": "approval/EP8_R029_distinct_cdn_token_production_executor_queue_consumed_wal_v21.json",
        },
        "R030": {
            "job_id": "ep8-r030-v2-transactional-one-call",
            "wal_path": "approval/EP8_R030_v2_queue_consumed_wal.json",
        },
        "R031": {
            "job_id": "ep8-r031-v3-transactional-one-call",
            "wal_path": "approval/EP8_R031_v3_queue_consumed_wal.json",
        },
    }

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.state_path = self.root / "state.json"
        self.costs_path = self.root / "costs.json"

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _read(self, path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))

    def _asset(self, manifest: dict) -> tuple[str, str]:
        pairs = [("final_path", "final_sha256"), ("asset_path", "sha256")]
        seen = [(manifest[p], manifest[h]) for p, h in pairs if p in manifest and h in manifest]
        if not seen or any(pair != seen[0] for pair in seen):
            raise ValueError("manifest asset locator is missing or inconsistent")
        candidate = Path(seen[0][0])
        path = candidate if candidate.is_absolute() else self.root / candidate
        path = path.resolve()
        if self.root not in path.parents or not path.is_file() or sha256(path) != seen[0][1]:
            raise ValueError("approved asset binding mismatch")
        with Image.open(path) as image:
            image.load()
            if image.format != "PNG" or image.mode != "RGB" or image.size != (1920, 1080):
                raise ValueError("approved asset must be 1920x1080 RGB PNG")
        return self._relative(path), seen[0][1]

    def validate(self) -> tuple[Ep8Import, ...]:
        state = self._read(self.state_path)
        checkpoint = state["checkpoint"]["revision_v2"]
        required_count = int(checkpoint["required_frame_count"])
        approved_count = int(checkpoint["approved_frame_count"])
        if required_count != 39 or approved_count < 1 or approved_count > required_count:
            raise ValueError("EP8 frame counts are invalid")
        storyboard_path = self.root / checkpoint["storyboard_path"]
        if sha256(storyboard_path) != checkpoint["storyboard_sha256"]:
            raise ValueError("storyboard binding mismatch")
        storyboard = self._read(storyboard_path)
        frames = {item["frame_id"]: item for item in storyboard["frames"]}
        bindings = checkpoint["manifest_bindings"]
        if len(bindings) != approved_count:
            raise ValueError("manifest binding count must equal approved frame count")
        result = []
        for binding in bindings:
            frame_id = binding["frame_id"]
            manifest_path = self.root / binding["manifest_path"]
            if sha256(manifest_path) != binding["manifest_sha256"]:
                raise ValueError("approved manifest binding mismatch")
            manifest = self._read(manifest_path)
            if manifest.get("frame_id") != frame_id or manifest.get("status") not in {
                "APPROVED",
                "APPROVED_FINAL_FRAME_V2",
            } or manifest.get("publication_authorized") is not False:
                raise ValueError("unapproved or publishable frame cannot be imported")
            if frame_id not in frames:
                raise ValueError("approved frame missing from storyboard")
            asset_path, asset_sha = self._asset(manifest)
            source = frames[frame_id]
            result.append(
                Ep8Import(
                    frame_id,
                    frame_id,
                    int(source["sequence_index"]),
                    binding["manifest_path"],
                    binding["manifest_sha256"],
                    asset_path,
                    asset_sha,
                )
            )
        ids = [item.frame_id for item in result]
        expected_ids = [f"R{index:03d}" for index in range(1, approved_count + 1)]
        if ids != expected_ids:
            raise ValueError("EP8 imports must be contiguous from R001 through the approved frame count")
        return tuple(result)

    def report(self) -> dict:
        imports = self.validate()
        state = self._read(self.state_path)
        checkpoint = state["checkpoint"]["revision_v2"]
        no_resubmit = []
        for frame_id, item in self.consumed.items():
            wal = self.root / item["wal_path"]
            if not wal.is_file():
                raise ValueError("consumed media WAL is missing")
            no_resubmit.append(
                {
                    "frame_id": frame_id,
                    **item,
                    "wal_sha256": sha256(wal),
                    "submit_allowed": False,
                    "resubmit_allowed": False,
                }
            )
        return {
            "schema": self.schema,
            "episode_id": state["episode_id"],
            "source_root": str(self.root),
            "state_binding": {"path": "state.json", "sha256": sha256(self.state_path)},
            "costs_binding": {"path": "costs.json", "sha256": sha256(self.costs_path)},
            "storyboard_binding": {
                "path": checkpoint["storyboard_path"],
                "sha256": checkpoint["storyboard_sha256"],
            },
            "expected_approved_frame_count": len(imports),
            "approved_frame_ids": [item.frame_id for item in imports],
            "imports": [
                {
                    **item.__dict__,
                    "decision": "IMPORTED_APPROVED",
                    "import_cost_usd": 0,
                    "generation_allowed": False,
                    "resubmission_allowed": False,
                }
                for item in imports
            ],
            "no_resubmit": no_resubmit,
            "dispatchable_frame_ids": [
                f"R{index:03d}" for index in range(len(imports) + 1, int(checkpoint["required_frame_count"]) + 1)
            ],
        }

    def materialize(self) -> Path:
        report = self.report()
        destination = self.root / "compiled" / "approved_frame_import_v1.json"
        if destination.exists():
            if self._read(destination) != report:
                raise ValueError("existing EP8 bridge differs; explicit migration required")
            return destination
        atomic_json(destination, report)
        return destination
