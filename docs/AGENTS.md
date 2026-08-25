# AGENTS SPECIFICATION
## Hybrid AI Animation Studio

**Version:** 1.1
**Date:** 2026-08-19
**Reference:** §11-12 (multiagent architecture)

---

## 1. Architecture

```
DIRECTOR AGENT (orchestrator)
    │
    ├── Research Agent
    ├── Biblical Accuracy Agent
    ├── Script Agent
    ├── Character Bible Agent
    ├── Storyboard Agent
    ├── Visual Director Agent
    ├── Image Generation Agent
    ├── Local Animation Agent
    ├── Cloud Video Agent
    ├── Voice Director Agent
    ├── Audio Agent
    ├── Video Assembly Agent
    ├── Visual QA Agent
    ├── Narrative QA Agent
    ├── Budget Agent
    ├── Thumbnail Agent
    ├── YouTube Metadata Agent
    ├── Publishing Agent
    └── Notification Agent
```

## 2. Director Agent (§12)

**Role:** Central orchestrator

**Responsibilities:**
- Coordinate all agents
- Verify dependencies between stages
- Manage episode state machine
- Enforce budget rules (via Budget Guard)
- Request human approval when required
- Prevent out-of-order execution
- Clean up orphaned RunPod pods on startup

**Rule:** No specialized agent may publish directly or bypass global rules.

### 2.1 Global visual production rules (mandatory)

- Final generation for recurring characters MUST use canonical reference images and image-to-image. Text-only generation is a draft and can never be marked final.
- Episode 1 is **A Criação**, grounded exactly in Gênesis 1:1-31 and Gênesis 2:1-25. Davi, Golias, Saul and Jessé do not belong to this episode.
- Episode 1 canonical recurring characters are Adão and Eva. Their fixed cards define face, adult age, hair, body proportions and physical identity. They have no clothing before the fall; use child-safe, non-sexual framing with vegetation covering intimate areas.
- Before Adão is formed, no human, child, human face, body, shadow or humanoid silhouette may appear.
- Deus is never depicted as a person. Represent divine action through light, wind, water and visible transformations in creation.
- Every scene featuring a recurring character MUST receive the same approved canonical references. Seeds are only auxiliary and never establish identity.
- Every image MUST map to the exact active narration phrase and real approved-audio timestamps. Uniform time slicing is forbidden.
- Every frame MUST pass individual visual QA against narration, action, identity, costume, anatomy, continuity and child safety before animation.
- The Director MUST audit generated-image count against narration phrases and audio duration before rendering.
- Approved audio assets and the approved Thalita voice MUST be preserved; overwrite is forbidden.
- Animation uses Comfy Cloud Wan image-to-video as primary and Hunyuan Video image-to-video as fallback, with gentle cinematic motion and short transitions.
- Episode 1 final still images use OpenAI/Codex `gpt-image-2-medium` editing with the rejected frame as the primary image and approved Adão/Eva canonical portraits as additional references. Every result remains subject to the same per-frame visual QA gate.
- New videos are delivered only for approval. Publishing requires a later, separate and explicit user instruction.

## 3. Agent Specifications

### 3.1 Research Agent

| Field | Value |
|---|---|
| **Responsibility** | Research biblical story, identify sources |
| **Input** | `theme` string |
| **Output** | `research/sources.json` with references |
| **Schema** | `{story, references: [{book, chapter, verses}], summary}` |
| **Tools** | web_search, web_extract, LLM |
| **Constraints** | Must cite biblical passages (§22); classify facts vs inferences vs additions (§23) |
| **Success criteria** | Sources identified and grounded |
| **Failure modes** | Theme ambiguous → ask Director for clarification; no sources found → FAILED |

### 3.2 Biblical Accuracy Agent

| Field | Value |
|---|---|
| **Responsibility** | Verify script fidelity to biblical text |
| **Input** | `script/narration.txt`, `research/sources.json` |
| **Output** | `qa/script_qa.json` with classification per claim |
| **Schema** | `{claims: [{text, type: BIBLICAL_FACT\|NARRATIVE_INFERENCE\|CREATIVE_ADDITION, source, verified}]}` |
| **Constraints** | Never present creative addition as biblical fact (§23); child-safe (§25) |
| **Success criteria** | All BIBLICAL_FACT claims verified; creative additions flagged |
| **Failure modes** | Claim not found in source → flag for Script Agent revision |

### 3.3 Script Agent

| Field | Value |
|---|---|
| **Responsibility** | Write narration script for children 6-10 |
| **Input** | research, duration plan |
| **Output** | `script/narration.txt`, `script/word_count` |
| **Constraints** | Clarity, emotion, curiosity, adventure, appropriate suspense, simple language, rhythm, retention, biblical fidelity, educational value, meaningful conclusion (§24) |
| **Success criteria** | Word count matches duration (~150 words/min); passes Biblical Accuracy QA |
| **Failure modes** | Too short/long → revise; QA rejection → revise |

### 3.4 Character Bible Agent

| Field | Value |
|---|---|
| **Responsibility** | Create/retrieve canonical character identity |
| **Input** | Script character list |
| **Output** | `characters/<name>/character.yaml` + `face.png`, `front.png`, `expressions/`, `poses/` |
| **Constraints** | Must be reusable across episodes (§37); temporal versions (§38); visual identity from reference images, not pure text-to-image (§39) |
| **Success criteria** | Character is recognizable in future episodes |
| **Failure modes** | Character not consistent → IP-Adapter + LoRA training on RunPod |

### 3.5 Storyboard Agent

| Field | Value |
|---|---|
| **Responsibility** | Divide script into scenes with real timestamps |
| **Input** | Script + TTS timestamps |
| **Output** | `storyboard/scenes.json` (scene schema §34) |
| **Constraints** | Semantic division (not fixed 5s/sentence) (§33); each visual change aligned to narration; timestamps from real audio (§32) |
| **Success criteria** | All scenes have valid start/end/duration; narrative flow preserved |
| **Failure modes** | Timestamp mismatch → re-align with faster-whisper |

### 3.6 Visual Director Agent

| Field | Value |
|---|---|
| **Responsibility** | Generate image prompts from scenes |
| **Input** | `storyboard/scenes.json`, character Bibles, visual style bible |
| **Output** | `image_prompt`, `negative_prompt` per scene |
| **Constraints** | Follow visual style bible (§40-41); child safety (§25-26); sensitive cases (§26) |
| **Success criteria** | Prompts produce images that pass Visual QA |

### 3.7 Image Generation Agent

| Field | Value |
|---|---|
| **Responsibility** | Generate consistent images |
| **Input** | Image prompts, character references |
| **Output** | `images/SC<id>.png` |
| **Tools** | SD1.5+LCM (fast, 7.1s), SD1.5+IP-Adapter (consistent, 65s) |
| **Constraints** | 4GB VRAM (B2-B4); fp16 all-GPU for LCM; VAE on CPU for IP-Adapter; upscale to 1080p after |
| **Success criteria** | Image passes Visual QA |
| **Failure modes** | OOM → switch to CPU offload mode; QA reject → regenerate with corrections (§45) |

### 3.8 Local Animation Agent

| Field | Value |
|---|---|
| **Responsibility** | Animate stills with ffmpeg |
| **Input** | Approved images, motion preset assignments |
| **Output** | `animation/SC<id>.mp4` |
| **Tools** | ffmpeg 9.0 (zoompan, xfade, parallax, chromakey) |
| **Constraints** | libx264 CPU only (B0); motion presets (§52); multilayer support (§51) |
| **Success criteria** | Clip duration matches scene duration; smooth motion |
| **Failure modes** | ffmpeg error → retry with fallback preset |

### 3.9 Cloud Video Agent

| Field | Value |
|---|---|
| **Responsibility** | Run image-to-video on RunPod for decisive scenes |
| **Input** | Approved image, animation prompt, GPU selection |
| **Output** | `cloud_clips/SC<id>.mp4` |
| **Tools** | RunPod SDK, Wan 2.2 i2v |
| **Constraints** | SECURE cloud (B6); image-to-video preferred (§73); max 8s/clip; Budget Guard approval; shutdown after job (§55); max 2 retries (§75) |
| **Success criteria** | Clip passes Animation QA |
| **Failure modes** | OOM/community failure → fallback to local (§77); budget exceeded → WAITING_BUDGET_APPROVAL |

### 3.10 Voice Director Agent

| Field | Value |
|---|---|
| **Responsibility** | Generate TTS narration + timestamps |
| **Input** | Script text |
| **Output** | `audio/narration.wav`, `audio/narration.srt` |
| **Tools** | edge-tts (ThalitaNeural), faster-whisper (word alignment) |
| **Constraints** | Preserve voice across episodes (§29); rate -8%, pitch +1Hz (§28) |
| **Success criteria** | Audio duration matches plan; timestamps aligned |
| **Failure modes** | edge-tts unavailable → fallback to Azure |

### 3.11 Audio Agent

| Field | Value |
|---|---|
| **Responsibility** | Master audio (narration + music + SFX) |
| **Input** | narration.wav, music files, SFX files |
| **Output** | `audio/master.wav` |
| **Tools** | ffmpeg (loudnorm, sidechaincompress, alimiter) |
| **Constraints** | -14 LUFS (YouTube); ducking; true-peak -1.0 dB (§30) |
| **Success criteria** | Loudness compliant; narration audible above music |

### 3.12 Video Assembly Agent

| Field | Value |
|---|---|
| **Responsibility** | Assemble final video |
| **Input** | Animation clips, cloud clips, master audio |
| **Output** | `renders/final.mp4` |
| **Tools** | ffmpeg (concat, xfade, mux) |
| **Constraints** | 1080p30; libx264; CRF 20 |
| **Success criteria** | Duration matches plan; audio synced; no artifacts |

### 3.13 Visual QA Agent

| Field | Value |
|---|---|
| **Responsibility** | Verify each image before animation |
| **Input** | Generated image, scene context, character Bible |
| **Output** | `qa/SC<id>_visual.json` |
| **Schema** | `{approved: bool, score: float, problems: [], regenerate: bool}` (§44) |
| **Constraints** | Check: character, face, hair, age, clothing, anatomy, hands, scene, objects, continuity, action, child safety, unwanted text (§43) |
| **Success criteria** | Score ≥ 0.8 and approved=true |
| **Failure modes** | Score < 0.8 → regenerate (only that scene, §45) |

### 3.14 Narrative QA Agent

| Field | Value |
|---|---|
| **Responsibility** | Final episode quality check |
| **Input** | final.mp4, script, storyboard |
| **Output** | `qa/final_report.json` |
| **Constraints** | Narrative flow, character consistency, audio quality, visual quality, child safety |
| **Success criteria** | All criteria pass; ready for approval |

### 3.15 Budget Agent

| Field | Value |
|---|---|
| **Responsibility** | Enforce budget rules, track costs |
| **Input** | Cost estimates, actual costs |
| **Output** | `costs.json` updates, Telegram alerts |
| **Constraints** | Hard limit $6 (§4); check before each job (§63); override logging (§66); silence ≠ approval (§8) |

### 3.16 Thumbnail Agent

| Field | Value |
|---|---|
| **Responsibility** | Create YouTube thumbnail |
| **Output** | `thumbnails/thumb_v1.png`, variations |
| **Constraints** | Emotion, simplicity, mobile readability, main character, contrast, curiosity (§91); no deceptive clickbait |

### 3.17 YouTube Metadata Agent

| Field | Value |
|---|---|
| **Responsibility** | Generate title, description, keywords, chapters |
| **Output** | `metadata/youtube.json` |
| **Constraints** | Auto-select playlist (§93); pt-BR language tags; chapters from scene timestamps |

### 3.18 Publishing Agent

| Field | Value |
|---|---|
| **Responsibility** | Upload to YouTube |
| **Input** | final.mp4, metadata, thumbnail, captions |
| **Output** | `youtube_video_id`, `youtube_url` |
| **Constraints** | Requires final approval (§95); validate all assets before upload (§94); record video ID |
| **Success criteria** | Video published, URL recorded, added to playlist |

### 3.19 Notification Agent

| Field | Value |
|---|---|
| **Responsibility** | Send Telegram notifications |
| **Output** | Telegram messages (approvals, alerts, links) |
| **Constraints** | HITL format (§7); inline keyboards for approvals; silence ≠ approval (§8) |
| **Success criteria** | User receives and responds to all required notifications |
