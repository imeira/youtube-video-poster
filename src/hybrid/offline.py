"""Offline finishing coordinator. No provider, notification or publishing adapters.

SQLite commits the active pair and append-only human receipts together. Render
failures leave only unreferenced files in a fresh revision directory, never an
approved output or a partially advanced gate.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.agents.thumbnail import ThumbnailAgent, ThumbnailContract
from src.hybrid.artifacts import Manifest, digest, sha256
from src.hybrid.compiled import CompiledEpisode
from src.hybrid.render import LocalRenderer, Scene


class OfflineCoordinator:
    def __init__(self, workspace):
        self.root = Path(workspace).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "delivery.sqlite3"
        with self._transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS immutable_receipt_update BEFORE UPDATE ON receipts
                BEGIN SELECT RAISE(ABORT, 'immutable receipt'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_receipt_delete BEFORE DELETE ON receipts
                BEGIN SELECT RAISE(ABORT, 'immutable receipt'); END;
            """)

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.database, timeout=30)
        try:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _read_state(db):
        row = db.execute("SELECT body FROM state WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {"status": "WAITING_PLAN_APPROVAL", "active": None}

    @staticmethod
    def _save(db, state):
        db.execute("INSERT OR REPLACE INTO state VALUES (1, ?)", (json.dumps(state),))

    @staticmethod
    def _receipt(db, body):
        body = {**body, "recorded_at": datetime.now(UTC).isoformat()}
        receipt_id = digest(body)
        db.execute("INSERT INTO receipts VALUES (?, ?)", (receipt_id, json.dumps(body)))
        return receipt_id

    @staticmethod
    def _binding(episode, manifest, contract, hold):
        if episode.source_binding is not None:
            from src.hybrid.ep8_offline import verify_source
            verify_source(episode, manifest, contract, hold)
        episode.audio.verify(episode.audio.mode)
        manifest.verify(episode.audio.mode)
        if len(episode.frames) != len(manifest.assets):
            raise ValueError("one approved manifest image per compiled frame required")
        end = 0
        for frame in episode.frames:
            if frame.hero or abs(frame.start - end) > 1e-6:
                raise ValueError("offline still timeline must be contiguous and contain no hero jobs")
            end = frame.end
        if not 3 <= hold <= 5:
            raise ValueError("closing hold must be 3 to 5 seconds")
        if episode.episode_id == "EP8" and (
            contract.title != "A promessa de um filho para Abraão e Sara"
            or contract.book_subtitle != "— Gênesis 15–18"
        ):
            raise ValueError("EP8 requires its exact title and biblical subtitle")
        binding = {"episode_id": episode.episode_id, "compilation": episode.checksum,
                   "manifest": manifest.checksum, "copy": asdict(contract), "hold": hold}
        if episode.source_binding is not None:
            binding["source"] = episode.source_binding
        return binding

    def approve_plan(self, episode, manifest, contract, *, reviewer, hold=4):
        if not reviewer.strip():
            raise ValueError("human reviewer required")
        binding = self._binding(episode, manifest, contract, hold)
        with self._transaction() as db:
            return self._receipt(db, {"kind": "plan", "binding": binding, "reviewer": reviewer})

    @staticmethod
    def _verify_pair(pair):
        if pair["binding"].get("source") is not None:
            from src.hybrid.ep8_offline import Ep8OfflineAdapter
            Ep8OfflineAdapter.verify_files(pair["binding"]["source"]["bindings"])
        for kind in ("video", "thumbnail"):
            asset = pair[kind]
            if sha256(asset["path"]) != asset["sha256"]:
                raise ValueError(f"{kind} hash mismatch")

    def prepare(self, episode, manifest, contract, *, plan_receipt, hold=4, renderer=None, video_suffix=".mkv"):
        if video_suffix not in {".mkv", ".mp4"}:
            raise ValueError("unsupported delivery video container")
        binding = self._binding(episode, manifest, contract, hold)
        with self._transaction() as db:
            row = db.execute("SELECT body FROM receipts WHERE id=?", (plan_receipt,)).fetchone()
            receipt = json.loads(row[0]) if row else {}
            if receipt.get("kind") != "plan" or receipt.get("binding") != binding:
                raise ValueError("persisted hash-bound plan receipt required")
            state = self._read_state(db)
            if state.get("episode_id", episode.episode_id) != episode.episode_id:
                raise ValueError("workspace belongs to another episode")
            if state["active"]:
                if state["active"]["binding"] != binding:
                    raise ValueError("reject active delivery before replacing it")
                self._verify_pair(state["active"])
                return state
            revision = uuid4().hex
            directory = self.root / "revisions" / revision
            directory.mkdir(parents=True)
            scenes = [Scene(asset, frame.end - frame.start)
                      for frame, asset in zip(episode.frames, manifest.assets)]
            video = directory / ("video" + video_suffix)
            render_receipt = (renderer or LocalRenderer()).render(
                scenes, manifest, episode.audio, None, video, hold=hold)
            thumb = Path(ThumbnailAgent()._compose(
                str(manifest.assets[0].path), *contract.layers, directory))
            pair = {kind: {"path": str(path), "sha256": sha256(path)}
                    for kind, path in (("video", video), ("thumbnail", thumb))}
            previous = state.get("superseded")
            if previous and any(pair[k]["sha256"] == previous[k]["sha256"]
                                or pair[k]["path"] == previous[k]["path"]
                                for k in ("video", "thumbnail")):
                raise ValueError("regenerated video and thumbnail must each have distinct hashes and paths")
            self._binding(episode, manifest, contract, hold)
            pair.update(revision=revision, binding=binding, approvals={}, plan_receipt=plan_receipt)
            pair["render_receipt"] = self._receipt(db, {
                "kind": "render", "pair": pair, "local_render": render_receipt,
                "plan_receipt": plan_receipt, "predecessor": previous,
                "feedback": state.get("feedback", ""),
            })
            state.update(episode_id=episode.episode_id, active=pair, status="WAITING_THUMBNAIL_APPROVAL")
            self._save(db, state)
            return state

    def decide(self, kind, revision, artifact_sha256, *, approved, reviewer, feedback=""):
        if kind not in {"thumbnail", "video"} or type(approved) is not bool or not reviewer.strip():
            raise ValueError("explicit human decision required")
        if not approved and not feedback.strip():
            raise ValueError("rejection feedback required")
        with self._transaction() as db:
            state = self._read_state(db)
            pair = state["active"]
            if not pair or pair["revision"] != revision or pair[kind]["sha256"] != artifact_sha256:
                raise ValueError("decision does not bind active revision")
            self._verify_pair(pair)
            if approved and kind == "video" and "thumbnail" not in pair["approvals"]:
                raise ValueError("thumbnail approval required first")
            if approved and kind in pair["approvals"]:
                return state
            receipt_id = self._receipt(db, {
                "kind": kind, "revision": revision, "artifact": pair[kind],
                "approved": approved, "reviewer": reviewer, "feedback": feedback,
                "render_receipt": pair["render_receipt"],
            })
            if not approved:
                # One commit retires both active artifacts and their eligibility;
                # the original approval receipts remain byte-for-byte unchanged.
                state.update(active=None, superseded=pair, feedback=feedback,
                             rejection_receipt=receipt_id, status="REGENERATION_REQUIRED")
            else:
                pair["approvals"][kind] = receipt_id
                state["status"] = "READY_FOR_PUBLICATION" if kind == "video" else "WAITING_VIDEO_APPROVAL"
            self._save(db, state)
            return state

    def status(self):
        with self._transaction() as db:
            state = self._read_state(db)
            if state["active"]:
                self._verify_pair(state["active"])
            return state


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("import-ep8", "approve-plan", "prepare", "approve", "reject", "status"))
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--source-revision")
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--compiled", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--copy", type=Path, help="ThumbnailContract JSON")
    parser.add_argument("--hold", type=float, default=4)
    parser.add_argument("--approve-plan", help="Existing receipt ID; never creates an approval")
    parser.add_argument("--reviewer", default="")
    parser.add_argument("--kind", choices=("thumbnail", "video"))
    parser.add_argument("--revision")
    parser.add_argument("--sha256")
    parser.add_argument("--feedback", default="")
    args = parser.parse_args(argv)
    try:
        if args.action == "import-ep8":
            from src.hybrid.ep8_offline import Ep8OfflineAdapter
            if args.source_root is None:
                parser.error("--source-root required")
            result = Ep8OfflineAdapter(args.source_root).build(args.workspace,
                dry_run=args.dry_run, expected_revision=args.source_revision)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.source_root is not None or args.dry_run or args.source_revision is not None:
            parser.error("source options and --dry-run require import-ep8")
        coordinator = OfflineCoordinator(args.workspace)
        if args.action in {"approve-plan", "prepare"}:
            if not all((args.compiled, args.manifest, args.copy)):
                parser.error("compiled, manifest and copy files required")
            inputs = (CompiledEpisode.load(args.compiled), Manifest.load(args.manifest),
                      ThumbnailContract(**json.loads(args.copy.read_text(encoding="utf-8"))))
            if args.action == "approve-plan":
                result = coordinator.approve_plan(*inputs, reviewer=args.reviewer, hold=args.hold)
            else:
                result = coordinator.prepare(*inputs, plan_receipt=args.approve_plan, hold=args.hold)
        elif args.action == "status":
            result = coordinator.status()
        else:
            result = coordinator.decide(args.kind, args.revision, args.sha256,
                approved=args.action == "approve", reviewer=args.reviewer, feedback=args.feedback)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
