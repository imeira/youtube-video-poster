"""TEST-only CLI integrated with AutonomousTwoGateRunner."""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from PIL import Image, ImageDraw

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.autonomous import (
    AutonomousPolicy,
    AutonomousTwoGateRunner,
    _atomic_json,
)
from src.hybrid.execution import Executor, Job, ProviderResult
from src.hybrid.locks import reject_reparse_path as _reject_reparse_path
from src.hybrid.planner import Config
from src.hybrid.standing_budget import StandingBudgetLedger

COMPILED_PACKET_FILES = ("compiled.json", "manifest.json", "policy.json")
TEST_COMPILED_PROVIDER = "test-local-fixture"
TEST_COMPILED_COST = Decimal("0.01")


def parse_prompt(prompt):
    lines, fields = prompt.splitlines(), {}
    for line in lines[1:]:
        k, s, v = line.partition(":")
        if s:
            fields[k.strip().casefold()] = v.strip()
    if (
        not lines
        or not lines[0].startswith("Poste um video no ")
        or not all(fields.get(k) for k in ("idioma", "playlist", "tema"))
    ):
        raise ValueError(
            "literal Portuguese prompt requires channel, Idioma, Playlist and Tema"
        )
    return {
        "channel": lines[0].removeprefix("Poste um video no "),
        "language": fields["idioma"],
        "playlist": fields["playlist"],
        "theme": fields["tema"],
    }


def policy():
    return AutonomousPolicy.from_dict(
        {
            "schema": 1,
            "version": "TEST-fixture-v1",
            "mode": "TEST",
            "episode_cap_usd": "1.00",
            "month_cap_usd": "1.00",
            "experiment_cap_usd": "1.00",
            "allowed_providers": ["test-local-fixture"],
            "publication": {
                "enabled": True,
                "channel": "TEST-local-only",
                "visibility": "private",
            },
        }
    )


def compiled_policy_document():
    """Internal allowlisted TEST authority; packet policy remains evidence only."""
    return {
        "schema": 1,
        "version": "compiled-test-internal-v1",
        "mode": "TEST",
        "episode_cap_usd": "1.00",
        "month_cap_usd": "1.00",
        "experiment_cap_usd": "1.00",
        "allowed_providers": [TEST_COMPILED_PROVIDER],
        "publication": {"enabled": False},
    }


def compiled_policy():
    """The only authority for compiled TEST execution; packet policy is evidence only."""
    return AutonomousPolicy.from_dict(compiled_policy_document())


def manifest(root):
    ref, sheet = root / "fixture-reference.png", root / "fixture-sheet.png"
    if not ref.exists():
        Image.new("RGB", (64, 64), "#14213d").save(ref)
    asset = FrozenAsset.approve(ref, "test-fixture", "TEST")
    if not sheet.exists():
        contact_sheet((asset,), sheet)
    return Manifest.freeze(
        (asset,),
        FrozenAsset.approve(sheet, "test-fixture", "TEST"),
        "test-fixture",
        "TEST",
    )


class FixtureProvider:
    mode = "TEST"

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    async def submit(self, job, rid, checkpoint):
        self.root.mkdir(parents=True, exist_ok=True)
        checkpoint(provider_id="TEST-" + rid[:12])
        if job.category in {"hero", "hero_retry"}:
            out = self.root / f"{rid}.TEST.mp4"
            out.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(
                subprocess.run,
                [
                    "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
                    "color=c=navy:s=1280x720:d=5", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=stereo", "-shortest", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(out),
                ],
                check=True,
            )
            return ProviderResult(out, job.cost)
        out = self.root / f"{rid}.TEST.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        image = Image.new("RGB", (1280, 720), "#14213d")
        ImageDraw.Draw(image).text(
            (20, 20), "TEST FIXTURE - NOT EPISODE MEDIA", fill="white"
        )
        image.save(out)
        image.save(self.root / "fixture-source.png")
        return ProviderResult(out, job.cost)

    async def recover(self, *args):
        raise RuntimeError("TEST fixture cannot remotely recover")


def runner(root, rid):
    parsed = policy()
    ledger = StandingBudgetLedger(
        root.parent.parent / "TEST-budget.sqlite3",
        policy_sha256=parsed.digest,
        episode_limit_usd=parsed.episode_cap_usd,
        month_limit_usd=parsed.month_cap_usd,
        experiment_limit_usd=parsed.experiment_cap_usd,
        mode="TEST",
    )
    return AutonomousTwoGateRunner(
        root,
        "TEST-" + rid[:20],
        parsed,
        Executor(root / "jobs.db", Config(limit=Decimal("1.00"))),
        ledger,
    )


def packet(raw, req):
    return {"mode": "TEST", "prompt_sha256": sha256(raw).hexdigest(), "request": req}


def render(root):
    thumb, video = root / "thumbnail.TEST.png", root / "video.TEST.mp4"
    if not thumb.exists():
        image = Image.open(root / "fixture-source.png").convert("RGB")
        ImageDraw.Draw(image).text(
            (20, 320), "TEST ONLY - NOT AN EPISODE DELIVERY", fill="yellow"
        )
        image.save(thumb)
    if not video.exists():
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-y",
                "-loop",
                "1",
                "-i",
                str(thumb),
                "-f",
                "lavfi",
                "-i",
                "anullsrc=r=48000:cl=mono",
                "-t",
                "1",
                "-shortest",
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                str(video),
            ],
            check=True,
        )
    return thumb, video


def public(root, rid, doc):
    return {
        "mode": "TEST",
        "request_id": rid,
        "prompt_path": str(root / "prompt.txt"),
        "prompt_sha256": json.loads((root / "harness.json").read_text())[
            "prompt_sha256"
        ],
        "state": doc["state"],
        "artifacts": {
            k: {"path": v["path"], "sha256": v["sha256"]}
            for k, v in doc.get("media", {}).items()
        },
        "publication": doc.get("publication"),
        "note": "Artifacts are local TEST fixtures, not human-approved real media and not an episode delivery.",
    }


def start(workspace, prompt_file):
    raw = Path(prompt_file).read_bytes()
    req = parse_prompt(raw.decode("utf-8"))
    rid = sha256(b"autonomous-two-gate-test-v1\0" + raw).hexdigest()
    root = Path(workspace).resolve() / "TEST" / rid
    if root.exists():
        raise ValueError("request namespace already exists; never overwrite")
    root.mkdir(parents=True)
    (root / "prompt.txt").write_bytes(raw)
    job = Job(
        "TEST-S001",
        "first",
        "TEST",
        "test-local-fixture",
        {"fixture": "local-only"},
        manifest(root),
        Decimal("0.01"),
    )
    run = runner(root, rid)
    run.start(packet(raw, req), [job])

    def qa(r):
        return {
            "approved": True,
            "reviewer": "independent-test-qa",
            "reviewer_identity": {
                "subject": "test-independent-qa",
                "issuer": "TEST assertion, not cryptographic authentication",
            },
            "request_id": r["request_id"],
            "result_sha256": r["result_sha256"],
            "codes": ["TEST_FIXTURE_PASS"],
        }

    asyncio.run(run.resume(FixtureProvider(root), qa))
    thumb, video = render(root)
    doc = run.stage_delivery(
        thumb,
        video,
        qa={
            "approved": True,
            "mode": "TEST",
            "subject": "test-technical-qa",
            "issuer": "TEST assertion",
            "codes": ["PIL_PASS", "FFPROBE_PASS"],
        },
    )
    _atomic_json(
        root / "harness.json",
        {"request_id": rid, "prompt_sha256": sha256(raw).hexdigest(), "request": req},
    )
    return public(root, rid, doc)


def load(workspace, rid):
    if len(rid) != 64 or any(c not in "0123456789abcdef" for c in rid):
        raise ValueError("request-id must be lowercase sha256")
    root = Path(workspace).resolve() / "TEST" / rid
    meta = json.loads((root / "harness.json").read_text())
    raw = (root / "prompt.txt").read_bytes()
    if meta["prompt_sha256"] != sha256(raw).hexdigest():
        raise ValueError("stored prompt bytes hash mismatch")
    run = runner(root, rid)
    run.assert_packet(packet(raw, meta["request"]))
    return root, run, run._load()


def _under(path: Path, root: Path) -> Path:
    path = _reject_reparse_path(path).resolve()
    root = _reject_reparse_path(root).resolve()
    if path != root and root not in path.parents:
        raise ValueError("compiled TEST input path escapes its local packet")
    return path


def _packet_policy(path: Path):
    """Validate untrusted packet metadata without granting it execution authority."""
    path = _reject_reparse_path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    parsed = AutonomousPolicy.from_dict(data)
    if (
        parsed.mode != "TEST"
        or parsed.publication_enabled
        or parsed.allowed_providers != {TEST_COMPILED_PROVIDER}
        or parsed.episode_cap_usd < TEST_COMPILED_COST
    ):
        raise ValueError("compiled CLI accepts only declared local TEST policy with publication disabled")
    return data, compiled_policy()


def _snapshot_compiled_input(source: Path, destination: Path) -> tuple[dict, str]:
    """Copy a closed local TEST packet, then rebind all input paths to that copy."""
    from src.hybrid.compiled import CompiledEpisode

    source = _reject_reparse_path(Path(source))
    if not source.is_dir():
        raise ValueError("compiled input must be a local non-symlink directory")
    for item in source.rglob("*"):
        _reject_reparse_path(item)
    files = {name: _reject_reparse_path(source / name) for name in COMPILED_PACKET_FILES}
    if not all(path.is_file() for path in files.values()):
        raise ValueError("compiled input requires compiled.json, manifest.json and policy.json")
    policy_data, parsed = _packet_policy(files["policy.json"])
    episode = CompiledEpisode.load(files["compiled.json"])
    manifest = Manifest.load(files["manifest.json"])
    if episode.audio.mode != "TEST" or manifest.mode != "TEST":
        raise ValueError("compiled CLI accepts TEST episode and manifest only")
    for asset in (episode.audio, *manifest.assets, manifest.sheet):
        _under(asset.path, source)
    if destination.exists():
        raise ValueError("compiled input snapshot already exists")
    shutil.copytree(source, destination)

    def rebound(asset):
        return FrozenAsset.approve(destination / _under(asset.path, source).relative_to(source), asset.reviewer, "TEST")

    rebound_episode = CompiledEpisode.compile(
        episode.episode_id, rebound(episode.audio), episode.frames
    )
    if episode.source_binding is not None:
        rebound_episode = CompiledEpisode(
            rebound_episode.episode_id, rebound_episode.audio, rebound_episode.frames, episode.source_binding
        )
    rebound_manifest = Manifest.freeze(
        tuple(rebound(asset) for asset in manifest.assets), rebound(manifest.sheet), manifest.reviewer, "TEST"
    )
    rebound_episode.save(destination / "compiled.json")
    rebound_manifest.save(destination / "manifest.json")
    # Policy bytes are copied, parsed and pinned; no config value selects an adapter/provider.
    _atomic_json(destination / "policy.json", policy_data)
    hashes = {name: sha256((destination / name).read_bytes()).hexdigest() for name in COMPILED_PACKET_FILES}
    return {"input_sha256": hashes, "policy_sha256": parsed.digest}, rebound_episode.checksum


def _compiled_root(workspace: Path, rid: str) -> Path:
    # Keep the durable namespace short enough for Windows' legacy path limit.
    return Path(workspace).resolve() / "TC" / rid[:16]


def _compiled_public(root: Path, adapter, document: dict) -> dict:
    media = document.get("media", {})
    return {
        "kind": "compiled-test",
        "mode": "TEST",
        "request_id": json.loads((root / "compiled-harness.json").read_text(encoding="utf-8"))["request_id"],
        "state": document["state"],
        "compiled_sha256": adapter.pipeline.episode.checksum,
        "source_manifest_sha256": adapter.pipeline.run.source_manifest.checksum,
        "artifacts": {key: {"path": value["path"], "sha256": value["sha256"]} for key, value in media.items()},
        "note": "Local TEST compiled evidence only; TEST assertions and gates are not human approval or a production delivery.",
    }


def _load_compiled(workspace: Path, rid: str):
    from src.hybrid.autonomous import (
        AutonomousCompiledTestAdapter,
        AutonomousTwoGateRunner,
    )
    from src.hybrid.compiled import CompiledEpisode, OperationalPipeline

    if len(rid) != 64 or any(char not in "0123456789abcdef" for char in rid):
        raise ValueError("request-id must be lowercase sha256")
    root = _compiled_root(workspace, rid)
    meta_path, inputs = root / "compiled-harness.json", root / "inputs"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if meta.get("kind") != "compiled-test" or meta.get("mode") != "TEST" or meta.get("request_id") != rid:
        raise ValueError("not a compiled TEST request")
    observed = {name: sha256((inputs / name).read_bytes()).hexdigest() for name in COMPILED_PACKET_FILES}
    if observed != meta.get("input_sha256"):
        raise ValueError("compiled input snapshot hash mismatch")
    _policy_data, parsed = _packet_policy(inputs / "policy.json")
    if parsed.digest != meta.get("policy_sha256"):
        raise ValueError("compiled policy hash mismatch")
    episode, manifest = CompiledEpisode.load(inputs / "compiled.json"), Manifest.load(inputs / "manifest.json")
    if episode.checksum != meta.get("compiled_sha256") or manifest.checksum != meta.get("source_manifest_sha256"):
        raise ValueError("compiled input binding mismatch")
    for asset in (episode.audio, *manifest.assets, manifest.sheet):
        _under(asset.path, inputs)
    executor = Executor(root / "executor.sqlite3", Config(limit=parsed.episode_cap_usd))
    pipeline = OperationalPipeline(episode, executor, manifest, workspace=root / "compiled", endpoint=TEST_COMPILED_PROVIDER, image_cost=TEST_COMPILED_COST)
    ledger = StandingBudgetLedger(root / "standing-budget.sqlite3", policy_sha256=parsed.digest, episode_limit_usd=parsed.episode_cap_usd, month_limit_usd=parsed.month_cap_usd, experiment_limit_usd=parsed.experiment_cap_usd, mode="TEST")
    adapter = AutonomousCompiledTestAdapter(AutonomousTwoGateRunner(root / "runner", episode.episode_id, parsed, executor, ledger), pipeline)
    return root, adapter, adapter.runner._load() if adapter.runner.path.is_file() else None


def _compiled_image_qa(reject_scene: str | None = None):
    def verdict(receipt):
        return {"approved": receipt["request"]["scene"] != reject_scene, "reviewer": "TEST local image assertion (not human)", "reviewer_identity": {"subject": "TEST-local-image-qa", "issuer": "TEST assertion, not human approval"}, "request_id": receipt["request_id"], "result_sha256": receipt["result_sha256"], "codes": ["TEST_LOCAL_ASSERTION"]}
    return verdict


def _compiled_media_qa(evidence):
    return {"approved": True, "mode": "TEST", "reviewer": "TEST local media assertion (not human)", "reviewer_identity": {"subject": "TEST-local-media-qa", "issuer": "TEST assertion, not human approval"}, "manifest_sha256": evidence["manifest_sha256"], "thumbnail_sha256": evidence["thumbnail_sha256"], "video_sha256": evidence["video_sha256"], "sidecars_sha256": {kind: item["sha256"] for kind, item in evidence["sidecars"].items()}}


def _stage_compiled(adapter, document):
    if document["state"] == "MEDIA_PENDING" and adapter.pipeline.render_ready():
        from src.hybrid.render import LocalRenderer
        return adapter.stage_local_delivery(LocalRenderer(width=320, height=180), _compiled_media_qa, hold=3)
    return document


def start_compiled(workspace: Path, packet: Path, reject_scene: str | None):
    packet = _reject_reparse_path(Path(packet))
    packet_files = tuple(_reject_reparse_path(packet / name) for name in COMPILED_PACKET_FILES)
    seed = b"autonomous-compiled-test-v1\0" + b"".join(path.read_bytes() for path in packet_files)
    rid, root = sha256(seed).hexdigest(), _compiled_root(workspace, sha256(seed).hexdigest())
    if root.exists():
        raise ValueError("request namespace already exists; never overwrite")
    root.mkdir(parents=True)
    snapshot, compiled_sha = _snapshot_compiled_input(packet, root / "inputs")
    _atomic_json(root / "compiled-harness.json", {"kind": "compiled-test", "mode": "TEST", "request_id": rid, "input_sha256": snapshot["input_sha256"], "policy_sha256": snapshot["policy_sha256"], "compiled_sha256": compiled_sha, "source_manifest_sha256": Manifest.load(root / "inputs" / "manifest.json").checksum})
    # Reload only after metadata exists: this proves the fresh-process reconstruction contract.
    _, adapter, _ = _load_compiled(workspace, rid)
    adapter.start()
    document = asyncio.run(adapter.resume(FixtureProvider(root.parent / "provider"), _compiled_image_qa(reject_scene)))
    return _compiled_public(root, adapter, _stage_compiled(adapter, document))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("start", "preproduce", "advance", "compile-preproduction", "status", "resume", "remediate", "heroes", "approve", "publish"))
    p.add_argument("--workspace", required=True, type=Path)
    p.add_argument("--prompt-file", type=Path)
    p.add_argument("--source-file", type=Path)
    p.add_argument("--timeline-file", type=Path)
    p.add_argument("--audio-file", type=Path)
    p.add_argument("--manifest-file", type=Path)
    p.add_argument("--mode", choices=("TEST", "PREPRODUCTION"))
    p.add_argument("--compiled-input", type=Path)
    p.add_argument("--request-id")
    p.add_argument("--kind", choices=("thumbnail", "video"))
    p.add_argument("--test-approval", action="store_true")
    p.add_argument("--test-publish", action="store_true")
    p.add_argument("--test-reject-scene")
    p.add_argument("--correction", action="append", default=[])
    a = p.parse_args(argv)
    try:
        if a.action == "preproduce":
            if a.prompt_file is None or a.compiled_input is not None:
                p.error("preproduce requires --prompt-file and does not accept --compiled-input")
            from src.hybrid.preproduction import start_preproduction

            result = start_preproduction(a.workspace, a.prompt_file.read_bytes())
        elif a.action == "advance":
            if not a.request_id or a.source_file is None or a.mode is None:
                p.error("advance requires --request-id --source-file and explicit --mode")
            if a.compiled_input is not None or a.prompt_file is not None:
                p.error("advance accepts only local source/timeline evidence")
            from src.hybrid.preproduction import advance_preproduction

            result = advance_preproduction(a.workspace, a.request_id, a.source_file, mode=a.mode,
                                           timeline_path=a.timeline_file, audio_path=a.audio_file)
        elif a.action == "compile-preproduction":
            if not a.request_id or a.manifest_file is None:
                p.error("compile-preproduction requires --request-id and --manifest-file")
            if any(value is not None for value in (a.prompt_file, a.source_file, a.timeline_file, a.audio_file, a.compiled_input)):
                p.error("compile-preproduction accepts only a local TEST manifest")
            from src.hybrid.preproduction import compile_preproduction

            result = compile_preproduction(a.workspace, a.request_id, a.manifest_file)
        elif a.action == "start":
            if a.compiled_input is not None:
                if a.prompt_file is not None:
                    p.error("compiled TEST start does not accept --prompt-file")
                result = start_compiled(a.workspace, a.compiled_input, a.test_reject_scene)
            else:
                if a.prompt_file is None:
                    p.error("--prompt-file required for start")
                result = start(a.workspace, a.prompt_file)
        else:
            if not a.request_id:
                p.error("--request-id required")
            preproduction_root = Path(a.workspace).absolute() / "PREPRODUCTION" / a.request_id
            if preproduction_root.exists():
                if a.action not in {"status", "resume"}:
                    raise ValueError("offline preproduction has no execution, approval, or publication operation")
                from src.hybrid.preproduction import load_preproduction

                result = load_preproduction(a.workspace, a.request_id)
            elif _compiled_root(a.workspace, a.request_id).exists():
                root, adapter, doc = _load_compiled(a.workspace, a.request_id)
                run = adapter.runner
                if a.action == "status":
                    pass
                elif a.action == "resume":
                    doc = asyncio.run(adapter.resume(FixtureProvider(root.parent / "provider"), _compiled_image_qa()))
                    doc = _stage_compiled(adapter, doc)
                elif a.action == "remediate":
                    corrections = {}
                    for value in a.correction:
                        scene, marker, prompt = value.partition("=")
                        if not marker or not scene or not prompt:
                            raise ValueError("--correction must be SCENE=compiled prompt")
                        corrections[scene] = prompt
                    if not corrections:
                        raise ValueError("compiled remediation requires --correction SCENE=prompt")
                    doc = asyncio.run(adapter.remediate(corrections, FixtureProvider(root.parent / "provider"), _compiled_image_qa()))
                    doc = _stage_compiled(adapter, doc)
                elif a.action == "heroes":
                    doc = asyncio.run(adapter.run_heroes(TEST_COMPILED_COST, TEST_COMPILED_PROVIDER, FixtureProvider(root.parent / "provider"), _compiled_image_qa()))
                    doc = _stage_compiled(adapter, doc)
                elif a.action == "approve":
                    if not a.test_approval or not a.kind:
                        raise ValueError("only explicit --test-approval can label a TEST compiled gate decision")
                    doc = run.record_human_gate(a.kind, Path(doc["media"][a.kind]["path"]), approved=True, reviewer="TEST gate assertion (not human approval)")
                elif a.action == "publish":
                    raise ValueError("compiled TEST policy disables publication; no publication path exists")
                result = _compiled_public(root, adapter, doc)
            else:
                root, run, doc = load(a.workspace, a.request_id)
                if a.action == "status":
                    pass
                elif a.action == "resume":
                    doc = asyncio.run(run.resume(FixtureProvider(root), lambda _: (_ for _ in ()).throw(AssertionError("QA must not rerun"))))
                elif a.action == "approve":
                    if not a.test_approval or not a.kind:
                        raise ValueError("only explicit --test-approval can approve TEST fixture media")
                    doc = run.record_human_gate(a.kind, Path(doc["media"][a.kind]["path"]), approved=True, reviewer="TEST local assertion")
                elif a.action == "publish":
                    if not a.test_publish:
                        raise ValueError("only explicit --test-publish can publish TEST fixture media")
                    doc = run.publish(lambda x: {"mode": "TEST", "video_id": "TEST-" + x["video_sha256"][:16], "readback": {"video_id": "TEST-" + x["video_sha256"][:16], "mode": "TEST", "local_only": True}})
                else:
                    raise ValueError("fixture harness has no compiled remediation or hero operation")
                result = public(root, a.request_id, doc)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as e:
        print(json.dumps({"status": "BLOCKED", "mode": "TEST", "error": str(e)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
