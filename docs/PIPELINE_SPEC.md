# PIPELINE SPECIFICATION
## Hybrid AI Animation Studio

**Version:** 1.0
**Date:** 2026-08-19
**Reference:** §98 (full pipeline), §27 (narration as timeline)

---

## 1. Pipeline Overview (§98)

```
USER REQUEST
    ↓
DIRECTOR AGENT
    ↓
RESEARCH (Biblical Source Grounding, §22)
    ↓
BIBLICAL GROUNDING + NARRATIVE CLASSIFICATION (§23)
    ↓
DURATION PLANNING (§18-19)
    ↓
BUDGET PLANNING (§20-21, §61-67)
    ↓
TELEGRAM: PRE-PRODUCTION APPROVAL (§95)
    ↓
SCRIPT (§24, audience 6-10)
    ↓
SCRIPT QA (Biblical fidelity, §23)
    ↓
TTS (ThalitaNeural, §28)
    ↓
TIMESTAMPS (SentenceBoundary + faster-whisper, §27/32)
    ↓
CHARACTER BIBLE (§35-39)
    ↓
STORYBOARD (semantic, aligned to narration, §33-34)
    ↓
IMAGE GENERATION (SD1.5+LCM, §42)
    ↓
VISUAL QA (§43-44) → REJECT → REGENERATE (§45)
    ↓
VISUAL STRATEGY ENGINE (§46-48)
    ↓
    ┌───────────────────────┴───────────────────┐
    ↓                                           ↓
LOCAL ANIMATION (§49-52)              RUNPOD CANDIDATE (§68-69)
    ↓                                           ↓
    ↓                                   BUDGET GUARD CHECK
    ↓                                           ↓
    ↓                                   RUNPOD i2v (§73)
    ↓                                           ↓
    └───────────────────┬───────────────────────┘
                        ↓
                ANIMATION QA (§74)
                        ↓
                    AUDIO (§30)
                        ↓
                    FFMPEG (§50)
                        ↓
                    FINAL VIDEO
                        ↓
                    FINAL QA
                        ↓
            TELEGRAM: FINAL APPROVAL (§95)
                        ↓
                    YOUTUBE (§94)
                        ↓
                    PLAYLIST (§93)
                        ↓
                    TELEGRAM: LINK (§96)
```

## 2. Stage Specifications

### 2.1 Research & Biblical Grounding (§22)

**Input:** `theme` string
**Output:** `research/sources.json`
```json
{
  "story": "Davi e Golias",
  "references": [
    {"book": "1 Samuel", "chapter": 17, "verses": "1-58"}
  ],
  "narrative_classification": {
    "BIBLICAL_FACT": ["Davi era pastor", "Golias era gigante filisteu"],
    "NARRATIVE_INFERENCE": ["Davi era jovem e corajoso"],
    "CREATIVE_ADDITION": ["descrição visual do campo"]
  }
}
```
**Rule:** Never present a creative addition as biblical fact (§23).

### 2.2 Duration Planning (§18-19)

**Input:** story complexity, audience age, research
**Output:** `plan.json` with:
- recommended duration (3-5 min for initial phase)
- estimated word count (~150 words/min of narration)
- scene count estimate
- image count estimate
- local vs cloud scene split
- cost estimate (min/probable/max)

**Rules:**
- Never pad with text to increase duration
- Never truncate narrative to lose comprehension
- Duration = complexity + audience_age + pace + retention + budget

### 2.3 Budget Planning (§20-21, §61-67)

**Pre-production plan must include:**
```
Tema, Passagens bíblicas, Duração recomendada, Justificativa,
Palavras estimadas, Nº de cenas, Nº de imagens,
Cenas animadas localmente, Cenas candidatas ao RunPod,
Segundos de vídeo generativo, Tempo local estimado,
GPU cloud estimada, Custo mínimo, Custo provável,
Custo máximo, Orçamento disponível
```

### 2.4 Script (§24)

**Input:** research, duration plan
**Output:** `script/narration.txt`, `script/scenes.json`

**Target:** children 6-10, seeking: clareza, emoção, curiosidade, aventura, suspense apropriado, linguagem simples, ritmo, retenção, fidelidade bíblica, valor educativo, conclusão significativa.

### 2.5 TTS + Timestamps (§27-28, §31-32)

**Flow (narration as timeline):**
```
SCRIPT → TTS (edge-tts ThalitaNeural) → AUDIO MASTER → REAL TIMESTAMPS → STORYBOARD → VISUAIS
```

**Timestamps:**
1. `edge-tts` SentenceBoundary → sentence-level (precise)
2. `faster-whisper` tiny CPU → word-level (verification/refinement)
3. **Never** use fixed "80 words / 30 seconds" heuristic (§32)

**Output:** `audio/narration.wav`, `audio/narration.srt`

### 2.6 Character Bible (§35-39)

**Input:** script character list
**Output:** `characters/<name>/character.yaml` + reference PNGs

```yaml
# characters/davi/character.yaml
name: "Davi"
apparent_age: 10
life_stage: "child"  # child | teenager | young_adult | king
face_shape: "round"
skin_tone: "warm peachy"
eyes: { color: "brown", shape: "large, expressive" }
hair: { color: "dark brown", style: "tousled" }
height: "short for age"
clothing:
  tunic: { color: "cream", style: "simple" }
  sandals: { color: "brown leather" }
accessories: ["staff", "sling"]
expressions: ["friendly smile", "determined", "curious"]
visual_personality: "courageous, innocent, faithful"
```

**Identity preservation:** IP-Adapter (base 6/10) + LoRA per character (target 9/10). LoRA trained on RunPod, one-time cost per character.

### 2.7 Storyboard (§33-34)

**Input:** script + real timestamps from TTS
**Output:** `storyboard/scenes.json`

**Scene schema (§34):**
```json
{
  "scene_id": "SC023",
  "narration": "...",
  "start": 83.21,
  "end": 91.74,
  "duration": 8.53,
  "characters": ["davi"],
  "location": "Valle de Ela",
  "emotion": "determination",
  "action": "Davi approaches Golias",
  "importance": "HIGH",
  "visual_strategy": "LOCAL_ANIMATED_STILL",
  "references": ["davi/character.yaml"],
  "camera": "slow push-in",
  "image_prompt": "...",
  "animation_prompt": "...",
  "negative_prompt": "...",
  "qa_status": "PENDING"
}
```

### 2.8 Image Generation (§42)

**Strategy:** CONSISTENT IMAGE > VIDEO

**Modes:**
1. **Fast mode (LCM 6 steps):** 7.1s/image — for most scenes
2. **Quality mode (20 steps):** 38.8s/image — for CRITICAL scenes
3. **IP-Adapter mode:** 65s/image — for character consistency (VAE on CPU)

**Post-generation:** upscale with Real-ESRGAN to 1080p (512² → 1920×1080)

### 2.9 Visual QA (§43-44)

**Before animating, verify:**
- Character identity, face, hair, age, clothing, accessories
- Anatomy, hands, scene, object count
- Continuity, action match, child safety
- No unwanted text

**Result format:**
```json
{"approved": true, "score": 0.94, "problems": [], "regenerate": false}
```

**Rule:** Never spend RunPod animating a defective image (§44).

### 2.10 Visual Strategy Engine (§46-48)

**Decision per scene:**
```
IF importance IN [HIGH, CRITICAL] AND movement_requirement = high AND budget_allows:
    → RUNPOD_GENERATIVE_VIDEO
ELIF movement_requirement = medium:
    → LOCAL_ANIMATED_STILL (parallax, Ken Burns)
ELSE:
    → STATIC_IMAGE or LOCAL_ANIMATED_STILL
```

### 2.11 Local Animation (§49-52)

**Engine:** ffmpeg 9.0 (measured: 7 min for 4 min episode)

**Techniques:** zoompan (Ken Burns), xfade, parallax (2-layer), minterpolate, overlay, chromakey

**Motion presets (§52):** slow_push_in, slow_pull_out, pan_left, pan_right, vertical_reveal, hero_reveal, dramatic_zoom, gentle_float, parallax_walk, storm_motion, fire_glow, water_motion

### 2.12 RunPod i2v (§73)

**Preferred:** image-to-video from approved still (preserves character identity)

**Lifecycle (§55-56):**
```
JOB REQUIRED → ALLOCATE (SECURE cloud) → LOAD ENVIRONMENT → RUN → SAVE OUTPUT → VERIFY → SHUTDOWN
```

**Fallback chain (§77):** RunPod → local i2v → local parallax → pan/zoom/FX

### 2.13 Audio Mastering (§30)

**Chain (measured: 3s for 30s audio):**
1. EBU R128 loudnorm to -14 LUFS
2. Sidechain ducking (music under narration)
3. True-peak limiting (-1.0 dB)
4. Fades

### 2.14 Final QA + Publishing (§94-96)

**Pre-publish checklist:**
- [ ] Video validated
- [ ] Metadata validated
- [ ] Thumbnail validated
- [ ] Captions validated
- [ ] Channel validated
- [ ] Final approval received (Telegram)

**After publish:** record video ID + URL, notify Telegram (§96 format)
