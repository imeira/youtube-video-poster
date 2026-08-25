# STATE MACHINE
## Hybrid AI Animation Studio

**Version:** 1.0
**Date:** 2026-08-19
**Reference:** §13, §14 (resumability), §17 (idempotency)

---

## 1. States

```
REQUEST_RECEIVED          ← User sends theme via Telegram
    ↓
RESEARCHING               ← Research Agent: biblical sources
    ↓
PLANNING                  ← Duration + budget + scene estimation
    ↓
WAITING_PLAN_APPROVAL     ← Telegram: pre-production plan (§95)
    ↓ (approved)                ↘ (rejected → CANCELLED)
SCRIPTING                 ← Script Agent: narration text
    ↓
SCRIPT_QA                 ← Biblical fidelity check (§23)
    ↓
CHARACTER_DESIGN          ← Character Bible Agent: canonical identity
    ↓
STORYBOARDING             ← Storyboard Agent: scenes with timestamps
    ↓
GENERATING_AUDIO          ← TTS (ThalitaNeural) + timestamps
    ↓
GENERATING_IMAGES         ← SD1.5+LCM or IP-Adapter
    ↓
VISUAL_QA                 ← Per-image QA (§43-44)
    ↓                     ↘ (rejected → regenerate specific scene)
PLANNING_ANIMATION        ← Visual Strategy Engine (§46-48)
    ↓
LOCAL_ANIMATION           ← ffmpeg: Ken Burns, parallax, transitions
    ↓
    ┌─────────────────────┴──────────────────────┐
    ↓                                            ↓
CLOUD_VIDEO_GENERATION     (skip if no RunPod scenes)
    ↓                                            ↓
WAITING_BUDGET_APPROVAL    ← if projected > hard_limit (§63-65)
    ↓ (approved)                               ↘ (rejected → fallback to local)
ANIMATION_QA               ← Verify cloud clips (§74)
    ↓
    └─────────────────────┬──────────────────────┘
                           ↓
ASSEMBLING                 ← ffmpeg: concat + mux audio
    ↓
FINAL_QA                   ← Full episode quality check
    ↓
WAITING_FINAL_APPROVAL     ← Telegram: video ready (§95)
    ↓ (approved)                ↘ (rejected → rework)
UPLOADING                 ← YouTube Data API v3
    ↓
PUBLISHED                  ← Record URL, add to playlist
    ↓
(notify Telegram with link)
```

**Non-linear states:**
```
PAUSED    ← User can pause at any state (§6)
FAILED    ← Any state can transition to FAILED on error
CANCELLED ← User can cancel at any state
```

## 2. State Transitions

### 2.1 Normal Flow

| From | To | Trigger | Agent |
|---|---|---|---|
| REQUEST_RECEIVED | RESEARCHING | Director starts pipeline | Director |
| RESEARCHING | PLANNING | Research complete | Research |
| PLANNING | WAITING_PLAN_APPROVAL | Plan generated | Director |
| WAITING_PLAN_APPROVAL | SCRIPTING | User approves | Director |
| WAITING_PLAN_APPROVAL | CANCELLED | User rejects | Director |
| SCRIPTING | SCRIPT_QA | Script complete | Script |
| SCRIPT_QA | CHARACTER_DESIGN | QA passes | Biblical Accuracy |
| CHARACTER_DESIGN | STORYBOARDING | Characters created | Character Bible |
| STORYBOARDING | GENERATING_AUDIO | Scenes defined | Storyboard |
| GENERATING_AUDIO | GENERATING_IMAGES | Audio + timestamps ready | Voice Director |
| GENERATING_IMAGES | VISUAL_QA | Images generated | Image Gen |
| VISUAL_QA | PLANNING_ANIMATION | All images approved | Visual QA |
| VISUAL_QA | GENERATING_IMAGES | Scene rejected (regen) | Visual QA |
| PLANNING_ANIMATION | LOCAL_ANIMATION | Strategy decided | Visual Strategy |
| LOCAL_ANIMATION | CLOUD_VIDEO_GENERATION | Has RunPod candidates | Local Animation |
| LOCAL_ANIMATION | ANIMATION_QA | No RunPod scenes | Local Animation |
| CLOUD_VIDEO_GENERATION | WAITING_BUDGET_APPROVAL | Over budget | Budget Guard |
| CLOUD_VIDEO_GENERATION | ANIMATION_QA | Within budget | Cloud Video |
| WAITING_BUDGET_APPROVAL | CLOUD_VIDEO_GENERATION | User approves | Budget Guard |
| WAITING_BUDGET_APPROVAL | LOCAL_ANIMATION | User chooses local (B) | Budget Guard |
| ANIMATION_QA | ASSEMBLING | All clips pass | Video Assembly |
| ASSEMBLING | FINAL_QA | Video assembled | Video Assembly |
| FINAL_QA | WAITING_FINAL_APPROVAL | QA passes | Narrative QA |
| WAITING_FINAL_APPROVAL | UPLOADING | User approves | Publishing |
| WAITING_FINAL_APPROVAL | ASSEMBLING | User requests changes | Publishing |
| UPLOADING | PUBLISHED | Upload complete | Publishing |
| PUBLISHED | (terminal) | Telegram notification sent | Notification |

### 2.2 Special Transitions

| From | To | Trigger |
|---|---|---|
| Any | PAUSED | User sends /pause |
| PAUSED | (previous) | User sends /continue |
| Any | FAILED | Unrecoverable error |
| Any | CANCELLED | User sends /cancel |
| FAILED | (previous) | User sends /retry after fix |

## 3. State Persistence

### 3.1 state.json

```json
{
  "episode_id": "EP000001",
  "current_state": "GENERATING_IMAGES",
  "previous_state": "VISUAL_QA",
  "state_history": [
    {"state": "REQUEST_RECEIVED", "timestamp": "2026-08-19T10:00:00Z", "agent": "Director"},
    {"state": "RESEARCHING", "timestamp": "2026-08-19T10:00:05Z", "agent": "Research"},
    {"state": "PLANNING", "timestamp": "2026-08-19T10:01:00Z", "agent": "Director"},
    {"state": "WAITING_PLAN_APPROVAL", "timestamp": "2026-08-19T10:01:30Z", "agent": "Director"},
    {"state": "SCRIPTING", "timestamp": "2026-08-19T10:05:00Z", "agent": "Script", "note": "approved by user"},
    {"state": "GENERATING_IMAGES", "timestamp": "2026-08-19T10:12:00Z", "agent": "Image Gen"}
  ],
  "checkpoint": {
    "last_completed_scene": "SC015",
    "approved_assets": ["SC001", "SC002", "...", "SC015"],
    "pending_regeneration": ["SC016"]
  },
  "updated_at": "2026-08-19T10:15:00Z"
}
```

### 3.2 Resumability Rules (§14, §17)

1. **On restart:** Director Agent reads `state.json` and resumes from `current_state`
2. **Idempotency:** Never re-execute completed steps (check `checkpoint`)
3. **Asset preservation:** Never regenerate approved assets (check `approved_assets`)
4. **Orphan cleanup:** On startup, check for orphaned RunPod pods and terminate (§56)

## 4. Checkpoints (§16)

Checkpoint after expensive or approved stages:

| Checkpoint | After State | Saved Data |
|---|---|---|
| CP1 | WAITING_PLAN_APPROVAL | plan.json (immutable after approval) |
| CP2 | GENERATING_AUDIO | narration.wav + timestamps |
| CP3 | CHARACTER_DESIGN | character.yaml + reference PNGs |
| CP4 | VISUAL_QA (per scene) | approved image per scene_id |
| CP5 | LOCAL_ANIMATION | animation clips per scene |
| CP6 | CLOUD_VIDEO_GENERATION | cloud clips per scene |
| CP7 | ASSEMBLING | final.mp4 |
| CP8 | FINAL_QA | qa_report.json |
| CP9 | PUBLISHED | youtube_video_id + URL |

**Rule:** Never auto-regenerate an asset that has been checkpointed as approved.
