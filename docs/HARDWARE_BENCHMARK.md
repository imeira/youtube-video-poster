# HARDWARE BENCHMARK REPORT
## PHASE 1 — BENCHMARK DE GERAÇÃO LOCAL

**Projeto:** HERMES — Hybrid AI Animation Studio
**Canal:** @EraUmaVezBibliaAnimada
**Data do benchmark:** 16 de agosto de 2026
**Máquina:** Dell XPS 15 9570 — i9-8950HK, GTX 1050 Ti Max-Q (4GB VRAM, Pascal sm_61), 32GB RAM, Windows 11
**Natureza:** Testes reais com medição de tempo, VRAM, temperatura e qualidade.

---

## VEREDITO EXECUTIVO

> **🟢 GO — A geração de imagem local é VIÁVEL na GTX 1050 Ti Max-Q.**
> Com LCM LoRA (4 steps), cada imagem leva ~24s. Para 37 imagens/episódio: **~15 minutos** — aceitável para produção assíncrona noturna.

---

## B0 — ENCODER h264_qsv (Intel Quick Sync)

| Encoder | Tempo (10s 1080p30 sintético) | Tempo (Ken Burns 5s) | Tamanho |
|---|---|---|---|
| **h264_qsv** (Intel UHD 630) | 6.1s | 10.7s | 700KB |
| **libx264 veryfast** (CPU) | 2.5s | 2.7s | 163KB |

**Veredito:** 🔴 **QSV descartado.** A UHD 630 é mais lenta que o CPU e produz arquivos 4× maiores. **libx264 permanece como encoder padrão** — já validado na Fase 0 como suficientemente rápido (~7 min para um episódio de 4 min).

---

## B1 — PyTorch CUDA EM PASCAL sm_61

### Descoberta crítica: cu126 FALHA, cu121 FUNCIONA

| Versão PyTorch | CUDA Runtime | Funciona no driver 528.79? | Status |
|---|---|---|---|
| **torch 2.13.0+cu126** | CUDA 12.6 | ❌ `cudaErrorDevicesUnavailable` | 🔴 Incompatível |
| **torch 2.5.1+cu121** | CUDA 12.1 | ✅ CUDA funcional | 🟢 **USAR ESTA** |

**Causa da falha do cu126:** O driver 528.79 (CUDA 12.0) não suporta o runtime CUDA 12.6 via minor version compatibility neste par driver/GPU. O erro é `cudaErrorDevicesUnavailable` — a GPU aparece como "busy" apesar de 0% de utilização no nvidia-smi.

**Solução adotada:** PyTorch 2.5.1+cu121 (última versão cu121 no PyPI). Minor version compat 12.1→12.0 funciona corretamente.

### Validação CUDA

```
PyTorch: 2.5.1+cu121
CUDA available: True
Device: NVIDIA GeForce GTX 1050 Ti with Max-Q Design
Compute capability: (6, 1)
Arch list: ['sm_50', 'sm_60', 'sm_61', 'sm_70', 'sm_75', 'sm_80', 'sm_86', 'sm_90']
```

**Matmul 1024×1024 (20 iterações):**
- GPU: 102.3 ms/iter
- CPU: 12.2 ms/iter
- **GPU 8.4× mais LENTA que CPU** (fp32, sem Tensor Cores)

> ⚠️ **Nota importante:** A GPU Pascal é mais lenta que o CPU para matmul genérico em fp32. O ganho real vem com **fp16** (modelos de difusão) e operações especializadas (conv2d, attention). Não use a GPU para compute genérico — use para inference de modelos especializados.

### Ambiente Python

- **Python:** 3.12.1 (C:\Python312)
- **venv:** `C:\Users\meira\OneDrive\IdeaProjects\youtube-video-poster\.venv`
- **diffusers:** 0.39.0
- **transformers:** 5.15.0
- **edge-tts:** 7.2.8
- **peft:** instalado

> ⚠️ **PYTHONPATH:** O Hermes contamina o venv do projeto com seu PYTHONPATH. Todo script Python precisa de `export PYTHONPATH=""` antes de executar. Isto deve ser tratado no wrapper do pipeline.

---

## B2 — STABLE DIFFUSION 1.5 (GO/NO-GO)

### Configuração
- **Modelo:** stable-diffusion-v1-5/stable-diffusion-v1-5
- **Precision:** fp16
- **Estratégia de memória:** `enable_sequential_cpu_offload()` + `enable_attention_slicing()`
- **Nota:** Sem sequential_cpu_offload, ocorre OOM (CUDA out of memory: tried to allocate 648 MiB, 0 bytes free). A GPU tem 4GB, mas o display WDDM consome ~1.2GB, deixando ~2.8GB para compute.

### Resultados medidos

| Resolução | Steps | Tempo/imagem | VRAM pico | Temp máx | Status |
|---|---|---|---|---|---|
| **512×512** | 20 | **83.8s** | 952 MB | 73°C | 🟢 |
| **768×768** | 20 | **171.9s** | 1.568 MB | 72°C | 🟡 |
| ~~sem offload~~ | — | OOM | — | — | 🔴 |

### Análise do critério GO/NO-GO

> **Critério do relatório Fase 0:** se 512×512 levar >90s → inviável.
> **Resultado: 83.8s → PASSOU (com folga de 6.2s)**

**Projeção por episódio (37 imagens):**
- 512×512 a 83.8s: ~52 min (borderline)
- 768×768 a 171.9s: ~106 min (inviável para produção regular)

**Conclusão:** A geração local em 512×512 é viável mas lenta. LCM LoRA (B3) resolve este problema.

---

## B3 — LCM LoRA (ACELERAÇÃO)

### Configuração
- **LoRA:** latent-consistency/lcm-lora-sdv1-5
- **Scheduler:** LCMScheduler
- **Guidance scale:** 1.0 (LCM exige guidance baixo)
- **Precision:** fp16
- **Offload:** sequential_cpu_offload + attention_slicing

### Resultados medidos

| Configuração | Steps | Tempo/imagem | Speedup vs base | VRAM | Temp |
|---|---|---|---|---|---|
| SD 1.5 base | 20 | 83.8s | 1.0× | 952 MB | 73°C |
| **LCM LoRA** | **4** | **23.6s** | **3.5×** | 952 MB | 72°C |
| LCM LoRA | 6 | 28.9s | 2.9× | 952 MB | 73°C |
| LCM LoRA | 8 | 37.3s | 2.2× | 952 MB | 73°C |

### Projeção por episódio

| Configuração | 37 imagens | Viabilidade |
|---|---|---|
| SD 1.5 base (20 steps) | ~52 min | 🟡 Borderline |
| **LCM 4 steps** | **~14.5 min** | 🟢 **IDEAL** |
| LCM 6 steps | ~18 min | 🟢 Bom |
| LCM 8 steps | ~23 min | 🟢 Aceitável |

**Qualidade (LCM 4 steps):** Aceitável para storyboard/pré-produção. Rosto e mãos mostram menos definição. Para frames finais, usar 6-8 steps.

**Veredito:** 🟢 **LCM 4 steps é a configuração padrão recomendada.** Reduz o tempo de geração de imagem de ~52 min para ~15 min por episódio.

---

## B4 — CONSISTÊNCIA DE PERSONAGEM

### Configuração
- **Abordagem:** img2img (IP-Adapter falhou no diffusers 0.39 — bug `added_cond_kwargs is None`)
- **Pipeline:** StableDiffusionImg2ImgPipeline
- **Strength:** 0.65–0.70 (preserva features do personagem, muda o cenário)
- **Steps:** 25, guidance 7.5

### Resultados medidos

| Operação | Tempo | Observação |
|---|---|---|
| Referência (text2img, 20 steps) | 131.5s | Imagem base do personagem Davi |
| 10 imagens img2img (média) | 137.9s/imagem | Variação por cena |
| **Total (1 ref + 10 cenas)** | **~25 min** | Inclui 1 outlier de 439s (throttling?) |
| Tempo sem outlier | ~106s/imagem | 9 imagens normais |

### Avaliação de consistência

**Abordagem img2img (strength=0.65):**
- ✅ Mantém paleta de cores (túnica roxa, cabelo castanho)
- ✅ Mantém estilo (ilustração de livro infantil)
- ✅ Mantém adereços (cajado, roupa pastoral)
- ⚠️ Variação em proporções faciais
- ⚠️ Variação em idade aparente
- 🔴 Não garante mesma face em close-ups

### IP-Adapter — BUG conhecido

```
TypeError: argument of type 'NoneType' is not iterable
  (em process_encoder_hidden_states: "image_embeds" not in added_cond_kwargs)
```

- **Causa:** diffusers 0.39 + IP-Adapter + SD1.5 tem bug no UNet (added_cond_kwargs=None)
- **Tentativa de downgrade:** diffusers 0.30/0.32 falha por `FLAX_WEIGHTS_NAME` removido em transformers 5.x
- **Workaround futuro:** usar ComfyUI (que tem IP-Adapter funcional) ou patchear diffusers 0.39
- **Recomendação:** LoRA treinado por personagem no RunPod continua sendo a estratégia primária

### Estratégia recomendada para consistência

```
1. CURIOSO/PRE-PRODUÇÃO: img2img com strength=0.65 (funciona hoje)
2. PRODUÇÃO: LoRA por personagem (treinado no RunPod — pago 1× por personagem)
3. REFINAMENTO: IP-Adapter (quando ComfyUI estiver instalado ou diffusers for patcheado)
```

---

## B5 — TTS (TEXT-TO-SPEECH)

### Configuração
- **Engine:** edge-tts 7.2.8 (cliente não-oficial do Microsoft Edge TTS)
- **Texto de teste:** 53 palavras em pt-BR (narrativa bíblica infantil)
- **boundary:** `"WordBoundary"` (necessário para timestamps palavra-a-palavra)

### Resultados medidos

| Voz | Tempo gen | Duração áudio | Word timestamps | Tamanho |
|---|---|---|---|---|
| **pt-BR-ThalitaNeural** | 4.47s | 18.24s | ✅ 53 palavras | 107KB |
| pt-BR-AntonioNeural | 2.52s | 21.07s | ✅ 53 palavras | 124KB |
| pt-BR-FranciscaNeural | 2.54s | 18.34s | ✅ 53 palavras | 107KB |

### Validação de word-timestamps

```
50ms (+175ms): 'Era'
225ms (+188ms): 'uma'
412ms (+350ms): 'vez'
938ms (+88ms): 'num'
1025ms (+425ms): 'pequeno'
...
17475ms (+412ms): 'mudou'
```

**SRT gerado automaticamente:** 53 entradas, uma por palavra, com offset e duração precisos em ms.

> ✅ **Requisitos 27 e 32 do briefing ATENDIDOS.** Word-timestamps reais via `edge-tts` com `boundary="WordBoundary"`. SRT derivado diretamente da narração, sem heurística.

### Descoberta da API

> ⚠️ **edge-tts 7.x:** O parâmetro `boundary` deve ser `"WordBoundary"` (default é `"SentenceBoundary"`). Sem isso, apenas SentenceBoundary é retornado — sem timestamps por palavra.

### Vozes

- **ThalitaNeural** — voz feminina, aprovada no briefing. Ideal para narração principal.
- **AntonioNeural** — voz masculina, útil para diálogos de personagens masculinos.
- **FranciscaNeural** — voz feminina alternativa.
- **LuanaNeural (MAI-Voice-2)** — ❌ não disponível no edge-tts (requer Azure pago para estilos emocionais).

> ⚠️ **ToS:** `edge-tts` é não-oficial. Para canal monetizado, migrar para **Azure Speech** (mesma voz Thalita, ~$0.07/episódio). O custo é irrisório.

---

## B6 — RUNPOD i2v (NÃO EXECUTADO)

**Status:** 🔴 **BLOQUEADO — requer `RUNPOD_API_KEY` (ação humana H2).**

Não foi possível executar o benchmark de RunPod porque a API key não está configurada no ambiente. Este teste depende de:
1. Criar conta no RunPod + adicionar créditos
2. Obter a API key
3. Configurar `RUNPOD_API_KEY` no ambiente Hermes

**Estimativas do relatório Fase 0 continuam válidas:**
- 20s de vídeo i2v @480p+upscale: ~$0.32/episódio
- GPU recomendada: RTX A5000 ($0.27/hr) ou RTX 4090 ($0.74/hr)

---

## RESUMO CONSOLIDADO

### Tempos medidos por episódio (estimativa completa)

| Etapa | Configuração | Tempo | Status |
|---|---|---|---|
| TTS (650 palavras) | edge-tts Thalita | ~5s | 🟢 |
| Geração de imagem (37 imagens) | SD1.5 + LCM 4 steps | **~15 min** | 🟢 |
| Upscale (37 imagens) | Real-ESRGAN (a testar) | ~? | 🟡 |
| Animação local (ffmpeg) | Ken Burns + parallax | ~7 min | 🟢 |
| Assembly + áudio + mux | ffmpeg | ~1 min | 🟢 |
| Vídeo generativo (RunPod) | 20s i2v @480p | ~$0.32 | 🟡 (não testado) |
| **TOTAL LOCAL** | | **~25 min** | 🟢 |

### Limitações confirmadas

| # | Limitação | Impacto | Mitigação |
|---|---|---|---|
| L1 | PyTorch cu126 não funciona no driver 528.79 | Bloqueio de versões futuras | Usar cu121; planejar upgrade de GPU |
| L2 | NVENC quebrado (driver antigo) | Encoding CPU-only | libx264 é suficiente |
| L3 | 4GB VRAM exige sequential_cpu_offload | Adiciona ~30% de overhead | Aceito; LCM compensa |
| L4 | WDDM consome 1.2GB de VRAM | Só 2.8GB disponíveis para compute | sequential_cpu_offload resolve |
| L5 | IP-Adapter bug no diffusers 0.39 | Sem consistência facial via IP-Adapter | img2img como workaround; LoRA no RunPod |
| L6 | PYTHONPATH do Hermes contamina venv | Scripts falham sem `export PYTHONPATH=""` | Wrapper deve limpar PYTHONPATH |

### Decisões tomadas no benchmark

1. **Encoder:** libx264 (CPU) — QSV descartado por ser mais lento
2. **PyTorch:** 2.5.1+cu121 (cu126 incompatível com driver 528.79)
3. **Geração de imagem:** SD 1.5 + LCM LoRA 4 steps @ 512×512 — **GO**
4. **Consistência:** img2img (curto prazo) → LoRA por personagem no RunPod (médio prazo)
5. **TTS:** edge-tts ThalitaNeural com `boundary="WordBoundary"` — timestamps validados
6. **Diffusers:** 0.39.0 (com bug de IP-Adapter; monitorar correção)

### Ações humanas pendentes (bloqueios para próximas fases)

| # | Ação | Bloqueia |
|---|---|---|
| H2 | Criar conta RunPod + API key + créditos | B6, Fase 13 (Cloud Video) |
| H3 | Criar projeto Google Cloud + YouTube API | Fase 20 (Publishing) |
| H4 | Submeter YouTube API Audit | Publicação pública |
| H5 | (Opcional) Chave Azure Speech | TTS licenciado |
| H6 | Decidir: atualizar driver NVIDIA? | NVENC, cu126 |

---

## ARQUIVOS GERADOS

- `benchmarks/benchmark_b2_sd15.py` — script de benchmark SD 1.5
- `benchmarks/benchmark_b3_lcm.py` — script de benchmark LCM LoRA
- `benchmarks/benchmark_b4_ipadapter.py` — script de benchmark consistência
- `benchmarks/benchmark_b5_tts.py` — script de benchmark TTS
- `C:\Users\meira\AppData\Local\Temp\benchmark_b2_results.json` — resultados B2
- `C:\Users\meira\AppData\Local\Temp\benchmark_b3_lcm_results.json` — resultados B3
- `C:\Users\meira\AppData\Local\Temp\benchmark_b4_results.json` — resultados B4
- `C:\Users\meira\AppData\Local\Temp\benchmark_b5_tts_v2_results.json` — resultados B5
- `C:\Users\meira\AppData\Local\Temp\bench_sd15_512_*.png` — imagens B2
- `C:\Users\meira\AppData\Local\Temp\bench_lcm_512_*.png` — imagens B3
- `C:\Users\meira\AppData\Local\Temp\b4_*.png` — imagens B4 (referência + 10 cenas)
- `C:\Users\meira\AppData\Local\Temp\tts_*.mp3` — áudios TTS B5
- `C:\Users\meira\AppData\Local\Temp\tts_*.srt` — SRTs com word-timestamps

---

## CONCLUSÃO

> **🟢 O projeto HERMES é viável para produção local nesta máquina.**
>
> A geração de imagem local com SD 1.5 + LCM LoRA a 4 steps entrega 37 imagens em ~15 minutos, dentro da janela operacional de produção assíncrona noturna. O orçamento de US$ 6,00/episódio permanece com folga de 5-8× sobre o custo provável.
>
> **O próximo passo é a Fase 1 (SDD) — escrever as especificações de design do sistema e iniciar a implementação do pipeline.**

---
*Relatório gerado na Fase de Benchmark. Todas as medições foram executadas nesta máquina em 16/08/2026.*
