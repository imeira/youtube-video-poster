# BUDGET GUARD SPECIFICATION
## Hybrid AI Animation Studio

**Version:** 1.0
**Date:** 2026-08-19
**Reference:** §3-4, §61-67, §78

---

## 1. Mission

**No paid operation may proceed without Budget Guard approval.**

Budget Guard is the financial gatekeeper. It is consulted before every operation that costs money — RunPod, APIs, TTS, LLM, music, storage, any provider.

## 2. Budget Structure (§3, §61-67)

```yaml
budget:
  currency: USD
  episode:
    target_usd: 4.00      # Goal (not ceiling)
    warning_usd: 5.00      # Alert threshold
    hard_limit_usd: 6.00   # BLOCKING — cannot exceed without human approval
```

## 3. Cost Classification (§67)

| Range | Rating | Action |
|---|---|---|
| $0 – $3.00 | EXCELLENT | Proceed |
| $3.00 – $4.00 | GOOD | Proceed |
| $4.00 – $5.00 | ACCEPTABLE | Proceed |
| $5.00 – $6.00 | ATTENTION | Log warning, proceed |
| > $6.00 | BLOCKED | **STOP — require human approval** |

## 4. Budget Check Formula (§63)

```
BEFORE EACH PAID JOB:
  projected_spend = current_spend + estimated_next_job_cost

  IF projected_spend <= hard_limit:
      PROCEED
  ELIF projected_spend > hard_limit:
      → State: WAITING_BUDGET_APPROVAL
      → Send Telegram alert (§65)
      → WAIT for human response
```

## 5. Cost Ledger (§62)

Every episode has `costs.json`:

```json
{
  "currency": "USD",
  "budget": 6.00,
  "target": 4.00,
  "warning": 5.00,
  "hard_limit": 6.00,
  "spent": 0.35,
  "projected": 0.52,
  "runpod": 0.32,
  "other_services": 0.03,
  "jobs": [
    {
      "job_id": "JOB001",
      "provider": "runpod",
      "gpu": "NVIDIA GeForce RTX 4090",
      "model": "wan-2.2-5b-i2v",
      "hourly_price": 0.74,
      "job_duration_seconds": 156,
      "estimated_cost": 0.032,
      "actual_cost": 0.032,
      "scene_id": "SC027",
      "timestamp": "2026-08-19T12:00:00Z",
      "status": "completed"
    }
  ],
  "overrides": []
}
```

## 6. Telegram Budget Alert (§65)

```
⚠️ LIMITE DE ORÇAMENTO
EPISÓDIO: Davi e Golias
LIMITE: US$ 6,00
GASTO: US$ 5,42
PRÓXIMA GERAÇÃO: US$ 0,91
TOTAL PROJETADO: US$ 6,33
CENA: SC028
IMPORTÂNCIA: CRITICAL
MOTIVO: Clímax da batalha.

OPÇÕES
A — Autorizar somente este job
B — Utilizar animação local
C — Definir novo orçamento
D — Cancelar

RECOMENDAÇÃO: B
```

**Format:** Inline keyboard with callback_data: `budget_A`, `budget_B`, `budget_C`, `budget_D`

## 7. Override Logging (§66)

When operator authorizes budget override:

```json
{
  "who": "141718934",
  "when": "2026-08-19T12:05:00Z",
  "old_limit": 6.00,
  "new_limit": 7.00,
  "reason": "Cena crítica do clímax",
  "episode": "EP000001",
  "approval_method": "telegram_inline_keyboard",
  "callback_data": "budget_C"
}
```

**Rule:** Never alter limit silently.

## 8. RunPod Cost Calculation (B6 verified)

### GPU Prices (live from RunPod API, 2026-08-19)

| GPU | VRAM | Secure $/hr | Community $/hr |
|---|---|---|---|
| RTX A4000 | 16 GB | $0.25 | $0.17 |
| RTX A5000 | 24 GB | $0.27 | $0.16 |
| RTX 4000 Ada | 20 GB | $0.28 | $0.20 |
| RTX 3090 | 24 GB | $0.50 | $0.22 |
| L4 | 24 GB | $0.49 | $0.44 |
| **RTX 4090** | **24 GB** | **$0.74** | **$0.34** |
| A40 | 48 GB | $0.44 | $0.35 |

**Note:** Community cloud is unreliable (B6: containers don't start). Use **SECURE** for production.

### i2v Cost Math (20s of generative video per episode)

```
Wan 2.2 5B on RTX 4090 (SECURE):
  20s video ÷ 4s per clip = 5 clips
  5 clips × 5.3 min gen (480p) = 26.5 min
  + boot + model load = 5 min
  Total = 31.5 min = 0.525 hr
  Cost = 0.525 × $0.74 = $0.39

At 720p native:
  5 clips × 32 min = 160 min + 5 min = 165 min = 2.75 hr
  Cost = 2.75 × $0.74 = $2.04
```

## 9. Budget Optimizer (§78)

If planning exceeds $6.00, do NOT start paid production. Optimize in order:

1. Remove least-important cloud scenes
2. Reduce cloud clip seconds
3. Reduce clip duration
4. Choose cheaper GPU/model
5. Substitute video with parallax
6. Reuse assets
7. Reduce planned retries

**Rule:** Never reduce script quality to save GPU.

## 10. Operational Metrics (§100)

Budget Guard tracks and reports:
- `average_cost_per_episode`
- `median_cost_per_episode`
- `p95_cost_per_episode`
- `average_runpod_seconds`
- `average_runpod_cost`
- `average_regenerations`

## 11. Historical Knowledge (§103)

Budget Guard uses historical data to improve future decisions:
```
model + GPU + resolution + scene_type + duration + prompt_strategy + result + cost
→ optimize GPU selection and cost estimation for future episodes
```
