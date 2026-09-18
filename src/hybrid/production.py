"""Source-pinned EP8 local production. No provider or publication entry points.

The append-only ledger is authoritative; files alone never prove completion.
An interrupted encode is deliberately not retried in the same workspace.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sqlite3
import time
import tempfile
from dataclasses import asdict
from pathlib import Path

from src.agents.captions import CaptionsAgent
from src.agents.research import ResearchAgent
from src.agents.script_qa import ScriptQAAgent
from src.agents.thumbnail import ThumbnailContract
from src.hybrid.artifacts import FrozenAsset, Manifest, atomic_json, digest, sha256
from src.hybrid.compiled import CompiledEpisode
from src.hybrid.ep8_offline import Ep8OfflineAdapter, verify_source
from src.hybrid.locks import try_lock
from src.hybrid.offline import OfflineCoordinator
from src.hybrid.render import command, probe, measure_loudness

TITLE = "A promessa de um filho para Abraão e Sara"
THEME = TITLE + " — Gênesis 15–18"
ROUTE = "ep8-local-ffmpeg-v1"


class AssetsRequired(ValueError):
    pass


class TTSRequired(ValueError):
    pass


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def hashes(paths):
    return {str(Path(p).resolve()): sha256(p) for p in paths}


class ProductionHarness:
    def __init__(self, workspace, *, tts_factory=None):
        self.root = Path(workspace).resolve()
        self.db = None
        self.tts_factory = tts_factory

    def __enter__(self):
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = try_lock(self.root / "production.lock")
        if self.lock is None:
            raise ValueError("production workspace busy")
        self.db = sqlite3.connect(self.root / "production.sqlite3")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY, operation_key TEXT NOT NULL,
                stage TEXT NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL,
                UNIQUE(operation_key, status));
            CREATE TRIGGER IF NOT EXISTS no_update BEFORE UPDATE ON events
            BEGIN SELECT RAISE(ABORT, 'append-only state'); END;
            CREATE TRIGGER IF NOT EXISTS no_delete BEFORE DELETE ON events
            BEGIN SELECT RAISE(ABORT, 'append-only state'); END;
        """)
        return self

    def __exit__(self, *args):
        self.db.close()
        os.close(self.lock)

    def event(self, stage, status, body, revision=""):
        key = digest({"revision": revision, "stage": stage, "route": ROUTE})
        old = self.db.execute("SELECT body FROM events WHERE operation_key=? AND status=?", (key, status)).fetchone()
        if old:
            if json.loads(old[0]) != body:
                raise ValueError("operation key collision")
            return
        self.db.execute("INSERT INTO events(operation_key,stage,status,body) VALUES(?,?,?,?)",
                        (key, stage, status, json.dumps(body)))
        self.db.commit()

    def latest(self, stage, status=None):
        rows = self.db.execute("SELECT status,body FROM events WHERE stage=? ORDER BY sequence DESC", (stage,))
        return next((json.loads(body) for state, body in rows if status is None or state == status), None)

    def validate(self):
        for (body,) in self.db.execute("SELECT body FROM events ORDER BY sequence"):
            Ep8OfflineAdapter.verify_files(json.loads(body).get("outputs", {}))
        plan = self.latest("plan", "WAITING_PLAN_APPROVAL")
        if not plan:
            raise ValueError("production plan required")
        packet = Path(plan["packet"])
        inputs = (CompiledEpisode.load(packet / "compiled.json"), Manifest.load(packet / "manifest.json"),
                  ThumbnailContract(**read(packet / "copy.json")))
        verify_source(*inputs, plan["hold"])
        completed = self.latest('revoice', 'REVOICE_COMPLETED')
        if completed:
            if completed['plan_hash'] != plan['plan_hash']:
                raise ValueError('revoice plan hash mismatch')
            packet = Path(completed['packet'])
            inputs = (CompiledEpisode.load(packet / 'compiled.json'), Manifest.load(packet / 'manifest.json'),
                      ThumbnailContract(**read(packet / 'copy.json')))
            verify_source(*inputs, plan['hold'])
        return plan, inputs

    def plan(self, source, episode_id, theme, language, channel):
        source = Path(source).resolve()
        if source == self.root or source in self.root.parents or self.root in source.parents:
            raise ValueError("output workspace must be separate from EP8 source")
        request = dict(episode_id=episode_id, theme=theme, language=language, channel=channel,
                       source_root=str(source), workspace=str(self.root))
        if episode_id != "EP8" or theme != THEME or language != "pt-BR":
            raise ValueError("this route requires the EP8 pt-BR editorial contract")
        if self.latest("plan", "WAITING_PLAN_APPROVAL"):
            plan, _ = self.validate()
            if plan["request"] != request:
                raise ValueError("immutable request differs; new workspace required")
            return plan
        prior = self.latest("request")
        if prior and prior != request:
            raise ValueError("immutable request differs; new workspace required")
        self.event("request", "REQUESTED", request, digest(request))
        adapter = Ep8OfflineAdapter(source)
        # Explicit absence is different from stale or invalid approval authority.
        state = read(source / "state.json") if (source / "state.json").is_file() else None
        if state is None:
            raise AssetsRequired("approved EP8 state, narration, semantic storyboard and visual freeze required")
        revision = state["checkpoint"]["revision_v2"]
        if revision["pending_assets"] or revision["approved_frame_count"] != 39:
            raise AssetsRequired("39 approved local stills required; local generation is unavailable")
        for item in revision["manifest_bindings"]:
            manifest = read(adapter.path(item["manifest_path"]))
            candidate = (source / manifest.get("asset_path", manifest.get("final_path", ""))).resolve()
            if source not in candidate.parents:
                raise ValueError("asset outside source")
            if not candidate.is_file():
                raise AssetsRequired("approved local still missing: " + str(candidate))
        if not (source / "audio/narration_v1_manifest.json").is_file():
            raise AssetsRequired("approved audio and timing required; local TTS unavailable")
        audio = read(source / "audio/narration_v1_manifest.json")
        if not (source / audio["audio_path"]).is_file():
            raise AssetsRequired("approved audio missing; local TTS unavailable")
        episode, assets, sheet, contract, hold = adapter.inspect()
        editorial = self.editorial(source, state, audio, hold)
        source_revision = episode.source_binding["revision"]
        result = adapter.build(self.root, expected_revision=source_revision)
        packet = Path(result["packet"])
        research = asyncio.run(ResearchAgent().run("EP8", THEME, str(packet / "research")))
        if not research.success:
            raise ValueError("local EP8 research failed")
        atomic_json(packet / "editorial.json", editorial)
        plan = {**result, "request": request, "route": ROUTE, "cost_usd": 0,
                "revoice_required": bool(editorial['revision_findings']),
                "revoice_policy": "split-only-v1; Thalita pt-BR; exact WordBoundary; source preserved",
                "outputs": hashes(p for p in packet.rglob("*") if p.is_file())}
        plan["plan_hash"] = digest(plan)
        self.event("plan", "PLANNED", plan, plan["plan_hash"])
        self.event("plan", "WAITING_PLAN_APPROVAL", plan, plan["plan_hash"])
        return plan

    @staticmethod
    def editorial(source, state, audio, hold):
        board = read(source / state["checkpoint"]["revision_v2"]["storyboard_path"])
        cues, segments = [], []
        def in_scope(value: str | None) -> bool:
            if not value:
                return False
            chapter, span = value.removeprefix("Gênesis ").split(":", 1)
            start, _, finish = span.partition("-")
            first, last = int(start), int(finish or start)
            return (
                chapter == "15" and 1 <= first <= last <= 6
            ) or (
                chapter == "17" and (1 <= first <= last <= 9 or 15 <= first <= last <= 21)
            ) or (
                chapter == "18" and 1 <= first <= last <= 15
            )

        for frame in board["frames"]:
            text = frame.get("narration_text", frame.get("exact_active_narration", frame.get("narration_phrase", "")))
            raw_ref = str(frame.get("source_ref", frame.get("biblical_reference", "")))
            ref_match = re.search(r"G(?:ê|e)nesis\s+(15|17|18):(\d+(?:[–-]\d+)?)", raw_ref, flags=re.IGNORECASE)
            ref = f"Gênesis {ref_match.group(1)}:{ref_match.group(2).replace('–', '-')}" if ref_match else None
            declares_biblical_ref = "GENESIS" in ScriptQAAgent._fold(raw_ref)
            kind = "biblical_paraphrase" if ref or declares_biblical_ref else "family_reflection"
            if not text or (kind == "biblical_paraphrase" and not in_scope(ref)):
                raise AssetsRequired("source-bound narration phrase and allowed biblical source required per semantic frame")
            folded = ScriptQAAgent._fold(text)
            visual = ScriptQAAgent._fold(frame["action_visual_pt"])
            if re.search(r"\b(?:SEGURA|SEGURANDO|COM|HOLDING|WITH)\b.{0,32}\b(ISAAC|ISAQUE|BABY|BEBE|INFANT|NEWBORN)\b", visual):
                raise ValueError("EP8 forbids Isaac or an implied baby visually")
            if (ref or "").startswith("Gênesis 15:") and re.search(r"\b(ABRAAO|ABRAHAM|SARA|SARAH)\b", folded + " " + visual):
                raise ValueError("Abrão/Sarai names required before Genesis 17")
            if re.search(r"(?:ISAQUE|ISAAC).{0,20}(?:NASCEU|BORN)", folded):
                raise ValueError("Isaac is not born in EP8")
            cues.append(dict(start=frame["start_s"], end=frame["end_s"], text=text))
            segments.append(dict(id=frame["frame_id"], kind=kind, narration=text,
                                 source_refs=[ref] if ref else []))
        narration = "\n\n".join(s["narration"] for s in segments)
        approved_script = (source / audio["script_path"]).read_text(encoding="utf-8")
        # Markdown is an editorial container; only narrative paragraphs bind to TTS.
        approved_script = "\n".join(
            line for line in approved_script.splitlines()
            if line.strip() and not line.lstrip().startswith("#") and not line.lstrip().startswith("**")
        )
        if " ".join(approved_script.split()) != " ".join(narration.split()):
            raise ValueError("semantic narration differs from approved script")
        packet = dict(audience={"min_age": 6, "max_age": 10}, closing_duration_s=hold,
                      segments=segments, narration=narration)
        qa = ScriptQAAgent().review(packet)
        if any(not finding.endswith(':SENTENCE_TOO_LONG') for finding in qa.findings):
            raise ValueError("script QA failed: " + ", ".join(qa.findings))
        return dict(script=packet, cues=cues, revision_findings=list(qa.findings),
                    canonical_validation="source-bound per-frame approvals and visual freeze")

    def require_approval(self, plan):
        approval = self.latest('approval')
        if not approval or approval['plan_hash'] != plan['plan_hash']:
            raise ValueError('persisted hash-bound plan approval required')
        return approval

    def revoice(self):
        from src.hybrid.revoice import allocate, split_script
        plan, inputs = self.validate()
        approval = self.require_approval(plan)
        completed = self.latest('revoice', 'REVOICE_COMPLETED')
        if completed:
            return completed
        if not plan.get('revoice_required'):
            raise ValueError('no narration revision required')
        episode, manifest, contract = inputs
        editorial = read(Path(plan['packet']) / 'editorial.json')
        script = split_script(editorial['script'])
        # A unique unpublished directory is never a render input until ledger commit.
        directory = Path(tempfile.mkdtemp(prefix='revoice-', dir=self.root))
        try:
            if self.tts_factory is None:
                from src.providers.tts.edge_tts_provider import EdgeTTSProvider
                provider = EdgeTTSProvider()
            else:
                provider = self.tts_factory()
            target = directory / 'narration.mp3'
            result = asyncio.run(provider.synthesize(script['narration'], output_path=target,
                voice='pt-BR-ThalitaNeural', rate='-8%', pitch='+1Hz'))
            if not result.success or result.cost != 0 or Path(result.audio_path).resolve() != target:
                raise ValueError('free workspace TTS output required')
            info = probe(target)
            duration = float(info['format']['duration'])
            if len(info['streams']) != 1 or info['streams'][0]['codec_type'] != 'audio' or abs(duration-result.duration_seconds) > .033:
                raise ValueError('TTS audio verification failed')
            frames, cues = allocate(episode.frames, editorial['cues'], result.word_timestamps, duration)
            atomic_json(directory / 'script.json', script)
            atomic_json(directory / 'timeline.json', dict(words=result.word_timestamps, duration_s=duration))
            atomic_json(directory / 'editorial.json', dict(script=script, cues=cues, revision_findings=[]))
            lineage = dict(plan_hash=plan['plan_hash'], approval=approval,
                           parent_packet=plan['packet'], parent_outputs=plan['outputs'],
                           outputs=hashes(directory / name for name in ('script.json', 'timeline.json', 'editorial.json', 'narration.mp3')))
            binding = {**episode.source_binding, 'revoice': lineage}
            derived = CompiledEpisode('EP8', FrozenAsset(target, sha256(target), 'approved split-only revoice', 'LIVE'), frames, binding)
            derived.save(directory / 'compiled.json')
            manifest.save(directory / 'manifest.json')
            atomic_json(directory / 'copy.json', asdict(contract))
            verify_source(derived, manifest, contract, plan['hold'])
            # Receipt binds the deterministic result to the persisted human-approved policy.
            receipt = OfflineCoordinator(self.root / 'delivery').approve_plan(derived, manifest, contract,
                reviewer=approval['reviewer'], hold=plan['hold'])
            completed = dict(status='REVOICE_COMPLETED', plan_hash=plan['plan_hash'], packet=str(directory),
                             receipt=receipt, outputs=hashes(p for p in directory.iterdir() if p.is_file()))
            self.event('revoice', 'REVOICE_COMPLETED', completed, plan['plan_hash'])
            return completed
        except Exception as error:
            failure = dict(status='TTS_REQUIRED', error=str(error), plan_hash=plan['plan_hash'])
            self.event('tts_failure', 'TTS_REQUIRED', failure, digest(failure))
            raise TTSRequired('TTS_REQUIRED: ' + str(error)) from error

    def approve(self, plan_hash, reviewer):
        plan, inputs = self.validate()
        if plan_hash != plan["plan_hash"] or not reviewer.strip():
            raise ValueError("human reviewer and exact plan hash required")
        existing = self.latest("approval")
        if existing:
            return existing
        receipt = OfflineCoordinator(self.root / "delivery").approve_plan(*inputs, reviewer=reviewer, hold=plan["hold"])
        result = dict(status="SCRIPT_APPROVED", plan_hash=plan_hash, reviewer=reviewer, receipt=receipt)
        self.event("approval", "SCRIPT_APPROVED", result, plan_hash)
        return result

    def run(self):
        plan, inputs = self.validate()
        self.require_approval(plan)
        completed = self.latest('revoice', 'REVOICE_COMPLETED')
        if plan.get('revoice_required') and not completed:
            raise TTSRequired('TTS_REQUIRED: approved narration revision must complete revoice before run')
        previous = self.latest("render")
        if previous:
            if previous["status"] != "TECHNICAL_QA_PASSED":
                raise ValueError("interrupted render; unverified output requires a new workspace")
            return previous
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise AssetsRequired("local FFmpeg and ffprobe required")
        episode, manifest, contract = inputs
        revision = plan["plan_hash"]
        directory = self.root / "renders" / revision
        directory.mkdir(parents=True, exist_ok=False)
        self.event("assets", "ASSETS_READY", {"status": "ASSETS_READY"}, revision)
        self.event("render", "STARTED", {"status": "STARTED"}, revision)
        editorial = read(Path((completed or plan)["packet"]) / "editorial.json")
        captions = asyncio.run(CaptionsAgent().run("EP8", sentence_timestamps=editorial["cues"],
            narration=editorial["script"]["narration"], subtitles_dir=str(directory)))
        if not captions.success:
            raise ValueError(captions.error)
        atomic_json(directory / "metadata.json", dict(title=contract.title, language=plan["request"]["language"],
            channel=plan["request"]["channel"], description=THEME, publication_authorized=False,
            chapters=[dict(start=f.start, title=f.semantic_action) for f in episode.frames]))
        video = directory / "video.mp4"
        performance = render_once(episode, manifest, video, plan["hold"])
        self.validate()
        self.event("rendered", "RENDERED", {"outputs": hashes([video])}, revision)
        atomic_json(directory / "performance.json", performance)
        result = dict(status="TECHNICAL_QA_PASSED", video=str(video),
                      outputs=hashes(p for p in directory.iterdir() if p.is_file()), plan_hash=revision)
        self.event("render", "TECHNICAL_QA_PASSED", result, revision)
        return result

    def deliver(self):
        plan, inputs = self.validate()
        render = self.latest("render", "TECHNICAL_QA_PASSED")
        if not render:
            raise ValueError("technical QA required before delivery")
        c = OfflineCoordinator(self.root / "delivery")
        if c.status()["status"] == "REGENERATION_REQUIRED":
            raise ValueError("REJECTED: both artifacts retired; new source revision and workspace required")
        receipt = (self.latest('revoice', 'REVOICE_COMPLETED') or self.latest('approval'))['receipt']
        result = c.prepare(*inputs, plan_receipt=receipt, hold=plan["hold"],
                           renderer=VerifiedRender(Path(render["video"]), sha256(render["video"])), video_suffix=".mp4")
        self.event("delivery", result["status"], result, plan["plan_hash"])
        return result

    def status(self):
        if not self.latest("plan", "WAITING_PLAN_APPROVAL") and self.latest("missing"):
            return self.latest("missing")
        plan, _ = self.validate()
        delivery = self.latest("delivery")
        if delivery:
            result = OfflineCoordinator(self.root / "delivery").status()
            state = "REJECTED" if result["status"] == "REGENERATION_REQUIRED" else result["status"]
            self.event("gate", state, result, plan["plan_hash"])
            result = {**result, "status": state}
        else:
            result = self.latest("render") or self.latest('revoice', 'REVOICE_COMPLETED') or self.latest("approval") or plan
            if plan.get('revoice_required') and self.latest('approval') and not self.latest('revoice', 'REVOICE_COMPLETED'):
                result = dict(status='TTS_REQUIRED', plan_hash=plan['plan_hash'])
        action = {"WAITING_PLAN_APPROVAL": "approve-plan", "SCRIPT_APPROVED": "run",
                  "TECHNICAL_QA_PASSED": "deliver", "WAITING_THUMBNAIL_APPROVAL": "offline approve --kind thumbnail",
                  "WAITING_VIDEO_APPROVAL": "offline approve --kind video", "READY_FOR_PUBLICATION": "none; publication out of scope",
                  "TTS_REQUIRED": "revoice", "REVOICE_COMPLETED": "run",
                  "REJECTED": "new source revision and workspace", "STARTED": "inspect interrupted run; new workspace required"}
        return {**result, "next_action": action[result["status"]]}


class VerifiedRender:
    """Coordinator adapter: copy a QA-passed encode, never invoke an encoder."""
    def __init__(self, video, expected):
        self.video, self.expected = video, expected

    def render(self, scenes, manifest, audio, srt, output, *, hold):
        if sha256(self.video) != self.expected or output.exists():
            raise ValueError("verified production video changed or delivery output exists")
        with self.video.open("rb") as source, output.open("xb") as target:
            shutil.copyfileobj(source, target)
        if sha256(output) != self.expected:
            raise ValueError("delivery copy mismatch")
        return dict(api_cost=0, render_invocations=0, reused_sha256=self.expected)


def render_once(episode, manifest, output, hold, *, motion_plan=None, hero_clips=None):
    """One filtergraph, one bounded H.264 encode with derived AAC audio."""
    started = time.perf_counter()
    operations = {}
    if motion_plan is not None:
        scenes = motion_plan.get("scenes", []) if isinstance(motion_plan, dict) else []
        operations = {scene.get("scene_id"): scene.get("operation") for scene in scenes}
        if set(operations) != {frame.scene_id for frame in episode.frames}:
            raise ValueError("motion plan must bind every compiled scene exactly once")
    audio_info = probe(episode.audio.path)
    duration = episode.frames[-1].end
    if len([s for s in audio_info["streams"] if s["codec_type"] == "audio"]) != 1 or abs(float(audio_info["format"]["duration"]) - duration) > 1 / 30:
        raise ValueError("approved audio duration/streams do not match semantic timeline")
    args = ["ffmpeg", "-v", "error", "-nostdin", "-n", "-filter_complex_threads", "1"]
    filters, labels = [], []
    hero_clips = hero_clips or {}
    for i, (frame, asset) in enumerate(zip(episode.frames, manifest.assets)):
        clip = hero_clips.get(frame.scene_id)
        source = clip.path if clip is not None else asset.path
        args += ["-threads", "1", "-i", str(source)]
        frames = round(frame.end * 30) - round(frame.start * 30)
        if clip is not None:
            if sha256(clip.path) != clip.sha256:
                raise ValueError("hero clip hash mismatch before encode")
            filters.append(f"[{i}:v]trim=duration={frame.end - frame.start},setpts=PTS-STARTPTS,scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{i}]")
            labels.append(f"[v{i}]")
            continue
        operation = operations.get(frame.scene_id, "push_in")
        if operation == "push_in":
            effect = "z='min(1+on*0.0002,1.04)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
        elif operation == "pan_left":
            effect = f"z='1.04':x='(iw-iw/zoom)*(1-on/{max(frames - 1, 1)})':y='ih/2-ih/zoom/2'"
        elif operation == "pull_back":
            effect = "z='max(1.04-on*0.0002,1.0)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
        else:
            raise ValueError("unsupported motion operation")
        filters.append(f"[{i}:v]zoompan={effect}:d={frames}:s=1920x1080:fps=30,setsar=1,format=yuv420p[v{i}]")
        labels.append(f"[v{i}]")
    # concat exposes a variable frame rate. Establish 30fps before tpad so the
    # closing duration cannot be converted using an inferred, incorrect rate.
    filters.append("".join(labels) + f"concat=n={len(labels)}:v=1:a=0,fps=30,tpad=stop_mode=clone:stop_duration={hold}[v]")
    args += ["-i", str(episode.audio.path)]
    filters.append(f"[{len(labels)}:a]loudnorm=I=-16:TP=-2:LRA=11,apad=pad_dur={hold}[a]")
    graph = ";".join(filters)
    args += ["-filter_complex", graph, "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-threads", "1",
             "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-t", str(duration + hold), "-movflags", "+faststart", str(output)]
    command(args)
    info = probe(output)
    streams = info["streams"]
    if (len(streams) != 2 or streams[0]["codec_name"] != "h264" or streams[1]["codec_name"] != "aac"
            or (streams[0]["width"], streams[0]["height"]) != (1920, 1080)
            or abs(float(info["format"]["duration"]) - duration - hold) > .08
            or any(abs(float(s.get("duration", 0)) - duration - hold) > .08 for s in streams)):
        raise ValueError("technical output QA failed")
    return dict(render_invocations=1, max_concurrent_encodes=1, elapsed_seconds=time.perf_counter() - started,
                api_cost=0, filtergraph=graph, hold_seconds=hold, audio_operation="derived AAC; source unchanged",
                technical_qa=info, output_sha256=sha256(output), loudness=measure_loudness(output))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("plan", "approve-plan", "revoice", "run", "status", "deliver"))
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--episode-id", default="EP8")
    parser.add_argument("--theme", default=THEME)
    parser.add_argument("--language", default="pt-BR")
    parser.add_argument("--channel", default="@EraUmaVezBibliaAnimada")
    parser.add_argument("--plan-hash")
    parser.add_argument("--reviewer", default="")
    args = parser.parse_args(argv)
    code = 0
    try:
        # Reject source overlap before opening the ledger or lock file.
        if args.source_root:
            source, workspace = args.source_root.resolve(), args.workspace.resolve()
            if source == workspace or source in workspace.parents or workspace in source.parents:
                raise ValueError("output workspace must be separate from EP8 source")
        with ProductionHarness(args.workspace) as harness:
            try:
                if args.action == "plan":
                    if not args.source_root:
                        raise ValueError("--source-root required")
                    result = harness.plan(args.source_root, args.episode_id, args.theme, args.language, args.channel)
                elif args.action == "approve-plan":
                    result = harness.approve(args.plan_hash, args.reviewer)
                else:
                    result = getattr(harness, args.action)()
            except TTSRequired as error:
                result = dict(status='TTS_REQUIRED', error=str(error), next_action='revoice')
                code = 1
            except AssetsRequired as error:
                result = dict(status="ASSETS_REQUIRED", error=str(error), next_action="supply approved local assets; retry plan/run")
                harness.event("missing", "ASSETS_REQUIRED", result, digest(result))
                code = 1
    except (ValueError, OSError, KeyError, TypeError, RuntimeError) as error:
        result, code = dict(status="BLOCKED", error=str(error)), 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code
