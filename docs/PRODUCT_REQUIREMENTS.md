# PRODUCT REQUIREMENTS DOCUMENT (PRD)
## Hybrid AI Animation Studio

**Version:** 1.0
**Date:** 2026-08-19
**Channel:** @EraUmaVezBibliaAnimada
**Status:** Approved — Phase 1 (SDD)

---

## 1. Vision

An automated factory that transforms a single instruction into a complete children's Bible story YouTube video:

> **Input:** "Poste um vídeo no @EraUmaVezBibliaAnimada no idioma português do Brasil com o tema: História de Davi e Golias."
>
> **Output:** A published YouTube video with consistent characters, narration, animation, captions, thumbnail, and metadata — delivered via Telegram with the video URL.

## 2. Target Audience

- **Primary:** Children ages 6–10
- **Language:** Brazilian Portuguese (pt-BR)
- **Content:** Biblical stories, adapted for children

## 3. Core Principles

| # | Principle | Source |
|---|---|---|
| 1 | LOCAL-FIRST + CLOUD-ON-DEMAND | §2 |
| 2 | CONSISTENT IMAGE > GENERATIVE VIDEO | §42, §127 |
| 3 | Minimum external cost, not "100% local at any cost" | §3 |
| 4 | Silence is NOT approval | §8 |
| 5 | Editorial originality — not generic output concatenation | §97 |
| 6 | Biblical fidelity — never present creative addition as biblical fact | §23 |
| 7 | Child safety — no graphic violence, sexualization, or trauma | §25 |

## 4. Functional Requirements

### 4.1 Episode Lifecycle (§98)
The system shall execute 21 steps automatically from request to YouTube link:

1. Analyze theme
2. Research sources
3. Identify biblical passages
4. Determine appropriate duration
5. Estimate cost and complexity
6. Create script
7. Create narration (TTS)
8. Create storyboard
9. Create/retrieve canonical characters
10. Generate consistent images
11. Animate locally (majority of episode)
12. Identify high-impact scenes
13. Use RunPod on-demand when appropriate
14. Assemble audio and video
15. Execute QA
16. Generate thumbnail
17. Generate title and metadata
18. Prepare transcription
19. Present video for approval
20. Publish to YouTube
21. Notify via Telegram with link

### 4.2 User Input (§5)
```
theme: "História de Davi e Golias"
language: "pt-BR"  # default
youtube_channel: "@EraUmaVezBibliaAnimada"  # default
```

### 4.3 Telegram Interface (§6)
The system shall support via Telegram:
- Start episode
- Query status
- Receive planning for approval
- Approve planning
- Approve budget increase
- Approve final video
- Reject scene
- Request regeneration
- Cancel production
- Pause / Continue
- Receive errors
- Receive YouTube link

### 4.4 Budget Control (§3, §4, §61–67)
- Target: US$ 4.00/episode
- Warning: US$ 5.00
- **Hard limit: US$ 6.00**
- No component may exceed hard limit without explicit human authorization
- Budget Guard must be consulted before every paid operation

### 4.5 Resumability (§14)
The system must survive:
- Hermes restart
- Computer restart
- Process crash
- Internet outage
- RunPod failure

### 4.6 Idempotency (§17)
Resuming must NOT:
- Publish video twice
- Generate duplicate episode
- Repeat unnecessary charges
- Recreate Character Bible
- Substitute approved asset
- Lose prior approval

## 5. Non-Functional Requirements

### 5.1 Performance (from benchmark)
- Image generation: ≤ 7.1s/image (LCM 6 steps) — **MET**
- Episode assembly (ffmpeg): ≤ 7 min for 4 min episode — **MET**
- TTS generation: ≤ 2s for 36s audio — **MET**
- Total local production: ~12 min/episode — **ACCEPTABLE**

### 5.2 Cost (from benchmark + RunPod API)
- External cost per episode: ~US$ 0.35 — **17× below hard limit**
- 100 episodes projected: ~US$ 35 external

### 5.3 Character Consistency (§35–39)
- Canonical identity per recurring character
- Character Bible with YAML + reference images
- IP-Adapter for reference-based generation (6/10, needs LoRA improvement)
- LoRA per character (trained on RunPod, one-time cost)
- Temporal versions: child / teenager / young_adult / king

### 5.4 Visual QA (§43–44)
Every image must pass QA before animation:
- Character identity, face, hair, age, clothing
- Anatomy, hands, scene, object count
- Continuity, action match, child safety
- No unwanted text
- **Never spend RunPod animating a defective image**

### 5.5 Observability (§89)
Log per operation:
`episode_id, scene_id, agent, stage, provider, model, model_version, prompt, seed, resolution, generation_time, VRAM_peak, RAM_peak, attempt, cost, result, error, timestamp`

### 5.6 Auditability (§90)
Must answer: "Why was this image created this way? What prompt? What model? How much? How many times? Which Character Bible?"

## 6. Constraints

| # | Constraint | Rationale |
|---|---|---|
| C1 | 4GB VRAM (GTX 1050 Ti Pascal) | Hardware limit — B1-B4 |
| C2 | PyTorch cu118 only (cu126 fails) | Driver 528.79 — B1 |
| C3 | libx264 CPU encoder (NVENC broken) | B0 |
| C4 | No auto-training without authorization | §104 |
| C5 | No secrets in git | §107 |
| C6 | YouTube API audit required for public publishing | §H4 |
| C7 | episodes/ outside OneDrive | B0 storage finding |

## 7. Success Criteria (§124)

The project is complete when the user can write:

> "Poste um vídeo no @EraUmaVezBibliaAnimada no idioma português do Brasil com o tema: História de Jonas e o Grande Peixe."

And the system executes the full pipeline (§98) and delivers the YouTube link via Telegram.

## 8. Pilot (§117–120)

First pilot: "História da criação do mundo"
- Duration: 1–3 minutes
- Majority: local images + local animation
- RunPod: minimum 1 scene, maximum 3 scenes
- Goal: compare LOCAL vs RUNPOD on quality, consistency, cost, time, stability
- **Do not auto-publish**

## 9. Out of Scope (Phase 1)

- Web dashboard (Telegram is the interface)
- Multi-language support beyond pt-BR
- Video generation models other than Wan 2.2 / LTX
- Auto-training of LoRA models (requires authorization per §104)
