"""Generate and visually audit Creation episode frames on RunPod SDXL.

Every final frame is image-to-image. Recurring actors use the same approved
canonical references. Non-character scenes use a text draft only as an input
to a required img2img final pass.
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import time
import urllib.request
from pathlib import Path

from src.pipeline.reference_generation import align_plan_to_timestamps, select_reference

ENDPOINT_ID = "zsu2gb03282vs5"
ROOT = Path("C:/HermesStudio")
EPISODE = ROOT / "episodes/EP1_CREATION_REMAKE"
REPO = Path("C:/Users/meira/OneDrive/IdeaProjects/youtube-video-poster")
WORKFLOWS = Path("C:/Users/meira/AppData/Local/hermes/skills/creative/comfyui/workflows")


def env_key(name: str) -> str:
    for line in (Path.home() / "AppData/Local/hermes/.env").read_text(encoding="utf-8").splitlines():
        if line.startswith(name + "="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"{name} missing")


def runpod(workflow: dict, images: list[dict] | None = None) -> tuple[bytes, dict]:
    payload = {"input": {"workflow": workflow}}
    if images:
        payload["input"]["images"] = images
    request = urllib.request.Request(
        f"https://api.runpod.ai/v2/{ENDPOINT_ID}/runsync",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": "Bearer " + env_key("RUNPOD_API_KEY"), "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=1200) as response:
        data = json.loads(response.read())
    job_id = data.get("id")
    if data.get("status") in {"IN_QUEUE", "IN_PROGRESS"} and job_id:
        status_url = f"https://api.runpod.ai/v2/{ENDPOINT_ID}/status/{job_id}"
        deadline = time.time() + 1200
        while time.time() < deadline:
            time.sleep(5)
            status_request = urllib.request.Request(
                status_url,
                headers={"Authorization": "Bearer " + env_key("RUNPOD_API_KEY")},
            )
            with urllib.request.urlopen(status_request, timeout=60) as response:
                data = json.loads(response.read())
            if data.get("status") in {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}:
                break
    if data.get("status") != "COMPLETED":
        raise RuntimeError(f"RunPod job failed: {data.get('status')} {data.get('error')}")
    items = data.get("output", {}).get("images", [])
    if not items:
        raise RuntimeError(f"RunPod returned no image: {str(data.get('output'))[:800]}")
    encoded = items[0].get("data") if isinstance(items[0], dict) else items[0]
    if encoded.startswith("data:"):
        encoded = encoded.split(",", 1)[1]
    return base64.b64decode(encoded), data


def t2i(prompt: str, seed: int) -> tuple[bytes, dict]:
    wf = json.loads((WORKFLOWS / "sdxl_txt2img.json").read_text(encoding="utf-8"))
    wf.pop("_comment", None)
    wf["3"]["inputs"].update({"seed": seed, "steps": 30, "cfg": 7.0, "denoise": 1.0})
    wf["5"]["inputs"].update({"width": 1024, "height": 576, "batch_size": 1})
    wf["6"]["inputs"]["text"] = prompt
    wf["7"]["inputs"]["text"] = "text, watermark, photorealistic, live action, scary, horror, deformed anatomy, extra limbs, duplicate characters, unrelated people, modern objects, buildings before civilization"
    return runpod(wf)


def img2img(prompt: str, source: Path, seed: int, denoise: float) -> tuple[bytes, dict]:
    wf = json.loads((WORKFLOWS / "sdxl_img2img.json").read_text(encoding="utf-8"))
    wf.pop("_comment", None)
    name = "reference.png"
    wf["1"]["inputs"]["image"] = name
    wf["3"]["inputs"].update({"seed": seed, "steps": 30, "cfg": 6.5, "denoise": denoise})
    wf["6"]["inputs"]["text"] = prompt
    wf["7"]["inputs"]["text"] = "text, watermark, photorealistic, live action, scary, horror, deformed anatomy, extra limbs, duplicate characters, identity drift, different face, clothing on Adam, clothing on Eve, explicit anatomy, sexualized pose"
    encoded = base64.b64encode(source.read_bytes()).decode("ascii")
    return runpod(wf, [{"name": name, "image": encoded}])


def gemini_qa(image_path: Path, scene: dict, reference: Path | None) -> dict:
    parts = [{"text": (
        "Responda somente JSON válido com approved (bool), score (0 a 1) e problems (lista). "
        "Audite este quadro de animação bíblica infantil contra a frase e ação exatas. "
        f"NARRAÇÃO: {scene['narration']}\nAÇÃO VISUAL: {scene.get('visual_action_pt','')}\n"
        f"CENA: {scene['scene_id']}. Antes de Adão: {int(scene['scene_id'][2:]) < 26}. "
        "Verifique correspondência literal da ação, ausência de humanos antes de Adão, Deus nunca como pessoa, "
        "estilo animação 3D infantil, anatomia, mãos, ausência de texto e objetos modernos. "
        "Quando Adão/Eva aparecerem, compare rigorosamente rosto, idade, cabelo e proporções com a referência; "
        "eles não podem usar roupa ou tecido e áreas íntimas devem estar cobertas apenas por vegetação/enquadramento infantil."
    )}, {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(image_path.read_bytes()).decode("ascii")}}]
    if reference:
        parts.extend([
            {"text": "IMAGEM DE REFERÊNCIA CANÔNICA OBRIGATÓRIA:"},
            {"inline_data": {"mime_type": "image/png", "data": base64.b64encode(reference.read_bytes()).decode("ascii")}},
        ])
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key=" + env_key("GEMINI_API_KEY")
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as response:
        data = json.loads(response.read())
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def build_timeline() -> list[dict]:
    plan = json.loads((ROOT / "sol_plans/creation_genesis_1_2_sol.json").read_text(encoding="utf-8"))["scenes"]
    old = json.loads((ROOT / "episodes/EP8SOLPLAN/storyboard/scenes.json").read_text(encoding="utf-8"))["scenes"]
    timestamps = [{"text": x["narration"], "start": x["start"], "end": x["end"]} for x in old]
    return align_plan_to_timestamps(plan, timestamps)


def main() -> int:
    images_dir = EPISODE / "images"
    qa_dir = EPISODE / "qa"
    storyboard_dir = EPISODE / "storyboard"
    audio_dir = EPISODE / "audio"
    for d in (images_dir, qa_dir, storyboard_dir, audio_dir):
        d.mkdir(parents=True, exist_ok=True)

    approved_audio = ROOT / "episodes/EP8SOLPLAN/audio/narration.mp3"
    preserved_audio = audio_dir / "narration_approved.mp3"
    if not preserved_audio.exists():
        shutil.copy2(approved_audio, preserved_audio)
    if hashlib.sha256(approved_audio.read_bytes()).hexdigest() != hashlib.sha256(preserved_audio.read_bytes()).hexdigest():
        raise RuntimeError("Approved audio hash changed")

    refs = {
        "adam": str(ROOT / "canonical/creation/adam/adam_reference_16x9.png"),
        "eve": str(ROOT / "canonical/creation/eve/eve_reference_16x9.png"),
        "adam_eve": str(ROOT / "canonical/creation/adam_eve_reference_16x9.png"),
    }
    scenes = build_timeline()
    (storyboard_dir / "scenes_aligned.json").write_text(json.dumps({"scenes": scenes}, ensure_ascii=False, indent=2), encoding="utf-8")

    manifest = []
    for index, scene in enumerate(scenes, 1):
        scene_id = scene["scene_id"]
        final_path = images_dir / f"{scene_id}.png"
        qa_path = qa_dir / f"{scene_id}_visual.json"
        if final_path.exists() and qa_path.exists():
            prior = json.loads(qa_path.read_text(encoding="utf-8"))
            if prior.get("approved") and float(prior.get("score", 0)) >= 0.85:
                manifest.append(prior)
                print(f"{scene_id}: cached approved", flush=True)
                continue
        chars = [str(x).lower() for x in scene.get("characters", [])]
        reference_value = select_reference(scene["narration"], chars, refs)
        reference = Path(reference_value) if reference_value else None
        base_prompt = str(scene.get("visual_prompt_en", ""))
        identity = ""
        if reference:
            identity = (" Use the supplied canonical actor reference exactly: identical adult face, age, hair, skin, body proportions. "
                        "Adam and Eve wear no clothing or fabric; use leaves, flowers, long hair, tree trunks, foreground objects or camera crop to cover intimate areas safely. ")
        prompt = base_prompt + identity + " polished high quality 3D children's animated film, fluid cinematic composition, 16:9"
        best = None
        for attempt in range(1, 3):
            seed = 700000 + index * 101 + attempt
            if reference:
                data, meta = img2img(prompt, reference, seed, 0.62 if attempt == 1 else 0.52)
            else:
                draft_data, draft_meta = t2i(prompt, seed)
                draft_path = images_dir / f"{scene_id}_draft.png"
                draft_path.write_bytes(draft_data)
                data, meta = img2img(prompt, draft_path, seed + 1, 0.28)
            final_path.write_bytes(data)
            qa = gemini_qa(final_path, scene, reference)
            qa.update({
                "scene_id": scene_id,
                "narration": scene["narration"],
                "start": scene["start"],
                "end": scene["end"],
                "duration": scene["duration"],
                "reference": str(reference) if reference else None,
                "generation_method": "img2img",
                "attempt": attempt,
                "runpod_job_id": meta.get("id"),
            })
            qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
            best = qa
            print(f"{scene_id}: attempt={attempt} score={qa.get('score')} approved={qa.get('approved')}", flush=True)
            if qa.get("approved") and float(qa.get("score", 0)) >= 0.85:
                break
            prompt += " Correct these QA failures: " + "; ".join(map(str, qa.get("problems", [])))
        manifest.append(best or {})

    approved = [x for x in manifest if x.get("approved") and float(x.get("score", 0)) >= 0.85]
    audit = {
        "scene_count": len(scenes),
        "image_count": len(list(images_dir.glob("SC[0-9][0-9][0-9].png"))),
        "qa_count": len(manifest),
        "approved_count": len(approved),
        "all_approved": len(approved) == len(scenes),
        "audio_sha256": hashlib.sha256(preserved_audio.read_bytes()).hexdigest(),
        "timeline_start": scenes[0]["start"],
        "timeline_end": scenes[-1]["end"],
        "timeline_duration": round(scenes[-1]["end"] - scenes[0]["start"], 3),
    }
    (qa_dir / "image_timeline_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False), flush=True)
    return 0 if audit["all_approved"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
