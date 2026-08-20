"""Batch SDXL img2img generation for the Creation episode on RunPod."""

from __future__ import annotations

import base64
import json
import time
import urllib.request
from pathlib import Path

from scripts.generate_creation_reference_frames import EPISODE, ROOT, build_timeline, env_key, gemini_qa
from src.pipeline.reference_generation import select_reference

ENDPOINT_ID = "zsu2gb03282vs5"
BATCH_SIZE = 2


def old_scene_source(index: int) -> Path:
    old_index = index if index <= 50 else index + 1
    return ROOT / f"episodes/EP8SOLPLAN/images/SC{old_index:03d}.png"


def build_batch(items: list[tuple[int, dict, Path]]) -> tuple[dict, list[dict], dict[str, dict]]:
    workflow: dict[str, dict] = {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "_meta": {"title": "Shared SDXL Base"},
            "inputs": {"ckpt_name": "sd_xl_base_1.0.safetensors"},
        }
    }
    images = []
    meta: dict[str, dict] = {}
    for slot, (index, scene, source) in enumerate(items, 1):
        base = 1000 + slot * 20
        ids = {"load": str(base + 1), "sampler": str(base + 3), "pos": str(base + 6), "neg": str(base + 7), "decode": str(base + 8), "save": str(base + 9), "encode": str(base + 12)}
        source_name = f"{scene['scene_id']}_reference.png"
        prompt = str(scene.get("visual_prompt_en", "")) + ", polished high quality 3D children's animated film, smooth rounded forms, cinematic 16:9 composition"
        reference_kind = "scene_draft"
        if "canonical/creation" in str(source).replace("\\", "/"):
            reference_kind = "canonical_actor"
            prompt += ", preserve the exact supplied canonical adult face, age, hair, skin and proportions; no clothing or fabric on Adam or Eve; use foliage, hair, foreground objects or camera crop for child-safe coverage"
        workflow[ids["load"]] = {"class_type": "LoadImage", "inputs": {"image": source_name}}
        workflow[ids["sampler"]] = {"class_type": "KSampler", "inputs": {"seed": 880000 + index * 97, "steps": 30, "cfg": 6.5, "sampler_name": "dpmpp_2m", "scheduler": "karras", "denoise": 0.68 if reference_kind == "canonical_actor" else 0.48, "model": ["4", 0], "positive": [ids["pos"], 0], "negative": [ids["neg"], 0], "latent_image": [ids["encode"], 0]}}
        workflow[ids["pos"]] = {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}}
        workflow[ids["neg"]] = {"class_type": "CLIPTextEncode", "inputs": {"text": "text, watermark, photorealistic, live action, horror, deformed anatomy, extra limbs, duplicate people, modern objects, identity drift, different face, clothing on Adam, clothing on Eve, explicit anatomy, sexualized pose", "clip": ["4", 1]}}
        workflow[ids["encode"]] = {"class_type": "VAEEncode", "inputs": {"pixels": [ids["load"], 0], "vae": ["4", 2]}}
        workflow[ids["decode"]] = {"class_type": "VAEDecode", "inputs": {"samples": [ids["sampler"], 0], "vae": ["4", 2]}}
        workflow[ids["save"]] = {"class_type": "SaveImage", "inputs": {"filename_prefix": scene["scene_id"], "images": [ids["decode"], 0]}}
        images.append({"name": source_name, "image": base64.b64encode(source.read_bytes()).decode("ascii")})
        meta[scene["scene_id"]] = {"scene": scene, "source": source, "reference_kind": reference_kind}
    return workflow, images, meta


def submit(workflow: dict, images: list[dict]) -> dict:
    request = urllib.request.Request(
        f"https://api.runpod.ai/v2/{ENDPOINT_ID}/runsync",
        data=json.dumps({"input": {"workflow": workflow, "images": images}}).encode("utf-8"),
        headers={"Authorization": "Bearer " + env_key("RUNPOD_API_KEY"), "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=1200) as response:
        data = json.loads(response.read())
    job_id = data.get("id")
    if data.get("status") in {"IN_QUEUE", "IN_PROGRESS"} and job_id:
        deadline = time.time() + 1200
        while time.time() < deadline:
            time.sleep(5)
            status_request = urllib.request.Request(
                f"https://api.runpod.ai/v2/{ENDPOINT_ID}/status/{job_id}",
                headers={"Authorization": "Bearer " + env_key("RUNPOD_API_KEY")},
            )
            with urllib.request.urlopen(status_request, timeout=60) as response:
                data = json.loads(response.read())
            if data.get("status") in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}:
                break
    if data.get("status") != "COMPLETED":
        raise RuntimeError(f"RunPod batch failed: {data.get('status')} {data.get('error')}")
    return data


def main() -> int:
    images_dir = EPISODE / "images"
    qa_dir = EPISODE / "qa"
    images_dir.mkdir(parents=True, exist_ok=True)
    qa_dir.mkdir(parents=True, exist_ok=True)
    refs = {
        "adam": str(ROOT / "canonical/creation/adam/adam_reference_16x9.png"),
        "eve": str(ROOT / "canonical/creation/eve/eve_reference_16x9.png"),
        "adam_eve": str(ROOT / "canonical/creation/adam_eve_reference_16x9.png"),
    }
    scenes = build_timeline()
    pending = []
    for index, scene in enumerate(scenes, 1):
        final_path = images_dir / f"{scene['scene_id']}.png"
        qa_path = qa_dir / f"{scene['scene_id']}_visual.json"
        if final_path.exists() and qa_path.exists():
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            if qa.get("approved") and float(qa.get("score", 0)) >= 0.85:
                print(f"{scene['scene_id']}: cached approved", flush=True)
                continue
        chars = [str(x).lower() for x in scene.get("characters", [])]
        ref_value = select_reference(scene["narration"], chars, refs)
        source = Path(ref_value) if ref_value else old_scene_source(index)
        pending.append((index, scene, source))

    for batch_number, start in enumerate(range(0, len(pending), BATCH_SIZE), 1):
        batch = pending[start:start + BATCH_SIZE]
        workflow, inputs, meta = build_batch(batch)
        print(f"batch {batch_number}: {[x[1]['scene_id'] for x in batch]}", flush=True)
        result = submit(workflow, inputs)
        outputs = result.get("output", {}).get("images", [])
        by_scene = {}
        for item in outputs:
            filename = item.get("filename", "")
            scene_id = next((sid for sid in meta if filename.startswith(sid)), None)
            if scene_id:
                value = item.get("data", "")
                if value.startswith("data:"):
                    value = value.split(",", 1)[1]
                by_scene[scene_id] = base64.b64decode(value)
        if len(by_scene) != len(batch):
            raise RuntimeError(f"Batch output mismatch: expected {len(batch)}, got {len(by_scene)}")
        for index, scene, source in batch:
            scene_id = scene["scene_id"]
            final_path = images_dir / f"{scene_id}.png"
            final_path.write_bytes(by_scene[scene_id])
            actor_ref = source if meta[scene_id]["reference_kind"] == "canonical_actor" else None
            qa = gemini_qa(final_path, scene, actor_ref)
            qa.update({"scene_id": scene_id, "narration": scene["narration"], "start": scene["start"], "end": scene["end"], "duration": scene["duration"], "reference": str(source), "reference_kind": meta[scene_id]["reference_kind"], "generation_method": "runpod_sdxl_img2img_batch", "runpod_job_id": result.get("id")})
            (qa_dir / f"{scene_id}_visual.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"{scene_id}: score={qa.get('score')} approved={qa.get('approved')}", flush=True)

    reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(qa_dir.glob("SC*_visual.json"))]
    approved = [r for r in reports if r.get("approved") and float(r.get("score", 0)) >= 0.85]
    audit = {"scene_count": len(scenes), "image_count": len(list(images_dir.glob("SC[0-9][0-9][0-9].png"))), "qa_count": len(reports), "approved_count": len(approved), "all_approved": len(approved) == len(scenes)}
    (qa_dir / "image_timeline_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit), flush=True)
    return 0 if audit["all_approved"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
