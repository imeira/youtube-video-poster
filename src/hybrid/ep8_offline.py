"""Read-only EP8 authority adapter; emits coordinator inputs, never media jobs."""

import json
import math
import shutil
from dataclasses import asdict
from pathlib import Path

from src.agents.thumbnail import ThumbnailContract
from src.hybrid.artifacts import FrozenAsset, Manifest, atomic_json, digest, sha256
from src.hybrid.compiled import CompiledEpisode, FrameSpec
from src.hybrid.ep8_bridge import Ep8ApprovedBridge


class Ep8OfflineAdapter:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def path(self, value):
        path = (self.root / value).resolve()
        if self.root not in path.parents or not path.is_file():
            raise ValueError("EP8 input must be a file inside source root")
        return path

    def inspect(self):
        bindings = {}

        def read(name):
            path = self.path(name)
            bindings[str(path)] = sha256(path)
            return json.loads(path.read_text(encoding="utf-8"))

        state = read("state.json")
        revision = state["checkpoint"]["revision_v2"]
        ids = [f"R{i:03d}" for i in range(1, 40)]
        if (revision["approved_frame_count"] != 39 or revision["required_frame_count"] != 39
                or revision["approved_assets"] != ids or revision["pending_assets"]
                or state.get("publication_authorized") is not False):
            raise ValueError("complete current 39-frame approval required")
        # Check containment before invoking the existing bridge.
        board = read(revision["storyboard_path"])
        for item in revision["manifest_bindings"]:
            read(item["manifest_path"])
        imports = Ep8ApprovedBridge(self.root).validate()
        if [f["frame_id"] for f in board["frames"]] != ids:
            raise ValueError("storyboard must contain exactly 39 ordered frames")
        if [item.sequence_index for item in imports] != list(range(1, 40)):
            raise ValueError("frame sequence mismatch")
        freeze = read("compiled/EP8_visual_freeze_v2.json")
        if (freeze["frame_manifest_bindings"] != revision["manifest_bindings"]
                or freeze["frame_count"] != 39 or freeze["visual_freeze_pass"] is not True
                or freeze["render_authorized"] is not True
                or freeze["publication_authorized"] is not False
                or freeze["episode_id"] != state["episode_id"]
                or board["episode_id"] != state["episode_id"]):
            raise ValueError("stale or unapproved visual freeze")
        sheet = self.path(freeze["contact_sheet"]["path"])
        bindings[str(sheet)] = freeze["contact_sheet"]["sha256"]
        audio = read("audio/narration_v1_manifest.json")
        audio_path = self.path(audio["audio_path"])
        if (audio["status"] != "PASS" or audio["episode_id"] != state["episode_id"]
                or audio["ffprobe_decode_verified"] is not True
                or audio_path != self.path(board["audio_contract"]["source"])
                or audio["duration_s"] != board["audio_contract"]["duration_s"]):
            raise ValueError("approved narration binding mismatch")
        bindings[str(audio_path)] = audio["audio_sha256"]
        bindings[str(self.path(audio["script_path"]))] = audio["script_sha256"]
        assets = tuple(FrozenAsset(self.path(i.asset_path), i.asset_sha256,
                                  "EP8 state-bound approval", "LIVE") for i in imports)
        bindings.update({str(a.path): a.sha256 for a in assets})
        frames = tuple(FrameSpec(f["frame_id"], float(f["start_s"]), float(f["end_s"]),
                                 f["prompt_en"], f["action_visual_pt"]) for f in board["frames"])
        end = 0
        for frame in frames:
            if (not math.isfinite(frame.start) or not math.isfinite(frame.end)
                    or abs(frame.start - end) > 1e-6 or frame.end - frame.start <= .25):
                raise ValueError("invalid contiguous EP8 timeline")
            end = frame.end
        if abs(end - audio["duration_s"]) > 1e-6:
            raise ValueError("timeline does not match approved audio")
        copy = board["thumbnail_plan"]
        contract = ThumbnailContract(copy["headline"], copy["required_title"], copy["required_subtitle"])
        if (contract.title != "A promessa de um filho para Abra\u00e3o e Sara"
                or contract.book_subtitle != "\u2014 G\u00eanesis 15\u201318"):
            raise ValueError("EP8 requires its exact title and biblical subtitle")
        hold = board["video_plan"]["ending_tail_s"]
        if not math.isfinite(hold) or not 3 <= hold <= 5:
            raise ValueError("invalid approved closing hold")
        self.verify_files(bindings)
        source = {"root": str(self.root), "bindings": bindings, "revision": digest(bindings)}
        episode = CompiledEpisode("EP8", FrozenAsset(audio_path, audio["audio_sha256"],
                                   "EP8 narration manifest", "LIVE"), frames, source)
        return episode, assets, sheet, contract, hold

    @staticmethod
    def verify_files(bindings):
        for path, expected in bindings.items():
            if sha256(path) != expected:
                raise ValueError("stale EP8 source binding: " + path)

    def build(self, workspace, *, dry_run=False, expected_revision=None):
        workspace = Path(workspace).resolve()
        if workspace == self.root or self.root in workspace.parents or workspace in self.root.parents:
            raise ValueError("output workspace must be separate from EP8 source")
        episode, assets, sheet, contract, hold = self.inspect()
        revision = episode.source_binding["revision"]
        if expected_revision is not None and expected_revision != revision:
            raise ValueError("stale EP8 revision")
        result = {"status": "VERIFIED", "source_revision": revision, "frame_count": 39,
                  "hold": hold, "generation_allowed": False, "publication_authorized": False}
        if dry_run:
            return result
        if expected_revision is None:
            raise ValueError("expected source revision required; run --dry-run first")
        destination = (workspace / "ep8-inputs" / revision).resolve()
        if workspace not in destination.parents or self.root in destination.parents:
            raise ValueError("input packet must remain inside separate workspace")
        if destination.exists():
            raise ValueError("input packet already exists; use existing packet or a new workspace")
        destination.mkdir(parents=True)
        target = destination / "sheet.png"
        shutil.copyfile(sheet, target)
        if sha256(target) != episode.source_binding["bindings"][str(sheet)]:
            raise ValueError("contact sheet changed during copy")
        atomic_json(target.with_suffix(".binding.json"),
                    {"assets": [a.sha256 for a in assets], "sheet": sha256(target)})
        manifest = Manifest.freeze(assets, FrozenAsset(target, sha256(target),
                                   "EP8 visual freeze", "LIVE"), "EP8 visual freeze", "LIVE")
        self.verify_files(episode.source_binding["bindings"])
        episode.save(destination / "compiled.json")
        manifest.save(destination / "manifest.json")
        atomic_json(destination / "copy.json", asdict(contract))
        result.update(status="WAITING_PLAN_APPROVAL", packet=str(destination))
        return result


def verify_source(episode, manifest, contract, hold):
    """Revalidate authority and derived input, including after local rendering."""
    if episode.source_binding is None:
        return
    current, assets, sheet, expected_copy, expected_hold = Ep8OfflineAdapter(episode.source_binding["root"]).inspect()
    if current != episode:
        raise ValueError("stale EP8 revision or compiled input")
    if (manifest.assets != assets or manifest.sheet.sha256 != sha256(sheet)
            or contract != expected_copy or hold != expected_hold):
        raise ValueError("EP8 coordinator inputs differ from current approved artifacts")
