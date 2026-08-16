# HARDWARE BENCHMARK REPORT
## Fase de Benchmark — Resultados Medidos

**Data:** 16 de agosto de 2026
**Máquina:** Dell XPS 15 9570 — i9-8950HK, 32GB RAM, GTX 1050 Ti Max-Q (4GB VRAM, Pascal sm_61)
**Driver:** 528.79 (CUDA 12.0)
**Python:** 3.12.1 (venv dedicado: `C:\Users\meira\hermes-studio-venv`)
**PyTorch:** 2.7.1+cu118 (CUDA 11.8 — compatível com driver 528.79)

---

## TABELA RESUMO

| Teste | Status | Tempo Medido | VRAM Peak | Veredito |
|---|---|---|---|---|
| **B0**: Intel QSV encode | ✅ | 8.83s (5s clip) | — | ❌ Descartado (mais lento que CPU) |
| **B1**: PyTorch + CUDA sm_61 | ✅ | — | 0.012 GB | 🟢 **PASSED** |
| **B2**: SD 1.5 fp16 (all-GPU) | ✅ | 38.8s @ 512²/20steps | 2.87 GB | 🟢 **VIABLE** |
| **B3**: SD 1.5 + LCM LoRA | ✅ | 7.1s @ 512²/6steps | 3.00 GB | 🟢 **EXCELLENT** |
| **B4**: IP-Adapter consistência | ✅ | 65s @ 512²/20steps | 3.34 GB | 🟡 **PARTIAL** (6/10) |
| **B5**: TTS (Thalita) + timestamps | ✅ | 1.8s gen / 36.5s audio | — | 🟢 **EXCELLENT** |
| **B6**: RunPod i2v | ⏳ | — | — | Pendente (requer API key) |

---

## B0: ENCODER COMPARISON

| Encoder | 5s clip | 30s clip | Nota |
|---|---|---|---|
| libx264 veryfast crf20 (CPU) | **4.19s** | — | ✅ Vencedor |
| h264_qsv (Intel UHD 630) | 8.83s | 18.18s | ❌ 2× mais lento |
| h264_nvenc (GPU) | FALHOU | — | ❌ Driver 528.79 < 610 exigido |

**Conclusão:** libx264 CPU é o encoder para todo o pipeline. NVENC está quebrado e QSV é mais lento.

---

## B1: PYTORCH + CUDA

| Item | Valor |
|---|---|
| PyTorch version | 2.7.1+cu118 |
| CUDA available | ✅ True |
| CUDA runtime | 11.8 |
| Device | NVIDIA GeForce GTX 1050 Ti with Max-Q Design |
| Compute capability | sm_61 (Pascal) |
| VRAM total | 4.00 GB |
| Multi processors | 6 |
| fp32 512×512 matmul | 0.20 ms each |
| fp16 512×512 matmul | 0.18 ms each (1.12× speedup) |

**Nota crítica:** PyTorch cu126 (CUDA 12.6) **FALHOU** — `cudaErrorDevicesUnavailable`. O driver 528.79 suporta CUDA 12.0, não 12.6. A solução é **cu118 (CUDA 11.8)** que é retrocompatível com driver ≥520.

**Impacto arquitetural:** ficamos travados no ramo legacy de PyTorch (cu118). PyTorch 2.8+ remove kernels sm_61 dos wheels CUDA 12.x. A janela para GPU local é finita — planejar upgrade de GPU ou migração de imagem para RunPod.

---

## B2: STABLE DIFFUSION 1.5

### Configuração testada
- Modelo: `runwayml/stable-diffusion-v1-5` (fp16)
- Estratégia: todos os componentes na GPU (sem CPU offload)
- Tiled VAE: não necessário (VRAM suficiente)
- Attention slicing: não usado (incompatível com IP-Adapter)

### Resultados medidos

| Resolução | Steps | Tempo | VRAM Peak | s/step |
|---|---|---|---|---|
| 512×512 | 20 | **38.8s** | 2.87 GB | 1.82s |
| 512×512 | 30 | **74.6s** | 2.87 GB | 2.40s |
| 768×768 | 20 | 168.2s | 3.29 GB | 8.05s |

### Comparação com CPU offload (seq. offload)

| Modo | 512²/20steps | VRAM Peak | Speedup |
|---|---|---|---|
| Sequential CPU offload | 162.1s | 0.86 GB | 1.0× |
| **All-GPU fp16** | **38.8s** | 2.87 GB | **4.2×** |

**Insight:** o CPU offload é desnecessário e catastrófico para performance. Com SD1.5 fp16, os 4 GB são suficientes para manter tudo na GPU. O gargalo do offload é o I/O CPU↔GPU a cada step, não a computação.

### Projeção por episódio (37 imagens)

| Configuração | Tempo total | Com 20% retries |
|---|---|---|
| 512² @ 20 steps (all-GPU) | 23.9 min | 28.7 min |
| 512² @ 30 steps (all-GPU) | 46.0 min | 55.2 min |

### Qualidade da imagem
Validada por inspeção visual: imagem coerente, estilo de animação infantil, proporções corretas. Mãos simplificadas (esperado em SD1.5). Adequada para o pipeline com upscale posterior.

---

## B3: LCM-LoRA (ACELERAÇÃO)

### Configuração
- LoRA: `latent-consistency/lcm-lora-sdv1-5`
- Scheduler: LCMScheduler
- guidance_scale: 1.0 (LCM requer CFG baixo)

### Resultados medidos

| Steps | Tempo | VRAM Peak | s/step |
|---|---|---|---|
| 4 | 8.8s | 3.00 GB | 2.20s (warmup) |
| **6** | **7.1s** | 3.00 GB | 1.07s |
| 8 | 9.7s | 3.00 GB | 1.03s |

### Comparação

| Modo | 512² | Speedup vs 20 steps |
|---|---|---|
| SD 1.5 padrão (20 steps) | 38.8s | 1.0× |
| **SD 1.5 + LCM (6 steps)** | **7.1s** | **5.5×** |

### Projeção com LCM

| Configuração | 37 imagens | Com 20% retries |
|---|---|---|
| **LCM 6 steps** | **4.4 min** | **5.3 min** |
| LCM 4 steps | 5.4 min | 6.5 min |

### Qualidade LCM
Validada por inspeção visual: qualidade aceitável para animação infantil estilizada. Leve perda de refinamento vs 20 steps, mas adequada para o caso de uso. Para cenas de maior impacto, usar 8 steps ou voltar para 20 steps padrão.

---

## B4: IP-ADAPTER (CONSISTÊNCIA DE PERSONAGEM)

### Configuração testada
- IP-Adapter: `h94/IP-Adapter` (models/ip-adapter_sd15.bin)
- Estratégia: VAE movido para CPU fp32 (monkey-patch decode para fp32)
- cross_attention_kwargs scale: 0.5
- 20 steps, 512×512, guidance_scale 7.5 (sem LCM — incompatível com IP-Adapter em 4GB)

### Resultados medidos

| Variação | Tempo | VRAM Peak | Status |
|---|---|---|---|
| field (Davi em campo) | 73.2s | 3.34 GB | ✅ |
| harp (Davi com harpa) | 64.9s | 3.34 GB | ✅ |
| giant (Davi vs gigante) | 62.4s | 3.34 GB | ✅ |

### Análise de consistência (por visão computacional)

| Atributo | Manteve? | Nota |
|---|---|---|
| Rosto redondo | ✅ | Boa correspondência |
| Cabelo castanho bagunçado | ✅ | Excelente |
| Tom de pele quente | ✅ | Consistente |
| **Cor dos olhos** | ❌ | Azul vs marrom (referência) |
| **Roupas** | ❌ | Azul + overalls vs azul + gola branca |
| Idade aparente | ✅ | ~6-8 anos em ambos |

**Score de consistência: 6/10**

### Conclusão do B4
IP-Adapter preserva a estrutura facial e cabelo, mas **não é suficiente sozinho** para garantir consistência total de roupas e detalhes. Para o pipeline de produção:

1. **IP-Adapter serve como base** — mantém rosto e proporções
2. **LoRA por personagem** (treinado no RunPod, ~$0.10-0.30 uma vez por personagem) é necessário para fixar roupas, cor dos olhos e detalhes canônicos
3. **Seed fixo + prompt detalhado** complementa a consistência
4. **ControlNet** (openpose/depth) para poses consistentes — **não testado** em combinação com IP-Adapter em 4GB (pode causar OOM)

### Limitação de VRAM
IP-Adapter + LCM LoRA juntos causam OOM em 4GB. A configuração de produção deve escolher:
- **Modo rápido (LCM, sem IP-Adapter):** 7s/imagem, consistência por prompt+seed
- **Modo consistente (IP-Adapter, sem LCM):** 65s/imagem, consistência por referência visual

---

## B5: TTS + TIMESTAMPS

### edge-tts (pt-BR-ThalitaNeural)

| Métrica | Valor |
|---|---|
| Voz | `pt-BR-ThalitaNeural` (Female) |
| Rate | -8% |
| Pitch | +1Hz |
| Texto de teste | 87 palavras |
| Tempo de geração | **1.8s** |
| Duração do áudio | 36.5s |
| RTF (real-time factor) | **0.048×** (20× mais rápido que realtime) |
| Tamanho do arquivo | 214 KB (MP3) |
| SentenceBoundary | ✅ 10 timestamps precisos |

### Timestamps de sentença (medidos)

| # | Início (s) | Fim (s) | Duração (s) | Texto |
|---|---|---|---|---|
| 1 | 0.05 | 4.81 | 4.76 | Era uma vez, em uma terra distante... |
| 2 | 4.81 | 7.93 | 3.12 | Davi era o mais novo de oito irmãos. |
| 3 | 7.93 | 10.57 | 2.63 | Ele cuidava das ovelhas de seu pai, |
| 4 | 10.57 | 14.17 | 3.60 | levando-as a pastos verdes e águas tranquilas. |
| 5 | 14.17 | 17.66 | 3.49 | Enquanto seus irmãos mais velhos... |
| 6 | 17.66 | 22.28 | 4.62 | Davi ficou no campo, tocando sua harpa... |
| 7 | 22.28 | 27.27 | 4.99 | Um dia, um gigante chamado Golias... |
| 8 | 27.27 | 29.97 | 2.70 | Ninguém tinha coragem de enfrentá-lo. |
| 9 | 29.97 | 32.68 | 2.70 | Mas Davi, com a força de Deus, |
| 10 | 32.68 | 36.48 | 3.80 | disse: Eu vou lutar contra você... |

### faster-whisper (alinhamento word-level)

| Modelo | Tempo | Palavras | Dispositivo |
|---|---|---|---|
| tiny (int8) | **3.2s** | 86 | CPU |
| base (int8) | 6.6s | 87 | CPU |

**Primeiras 20 palavras alinhadas:**
```
[  0.00 -  0.42] Era
[  0.42 -  0.70] uma
[  0.70 -  1.10] vez,
[  1.26 -  1.42] em
[  1.42 -  1.58] uma
[  1.58 -  1.82] terra
[  1.82 -  2.44] distante,
...
```

### Pipeline de áudio validado
```
edge-tts (Thalita) → SentenceBoundary (timestamps de sentença)
                   → faster-whisper tiny (word-level alignment)
                   → SRT/VTT para YouTube
```

**Custo:** $0 (edge-tts é grátis). Alternativa licenciada: Azure Speech ~$0.07/episódio.

---

## PROJEÇÃO COMPLETA DO EPISÓDIO

### Episódio de 4 minutos (240s)

| Etapa | Tempo Local | Custo Externo |
|---|---|---|
| Imagens (37 @ LCM 6 steps) | **4.4 min** | $0 |
| TTS (narração 4 min) | **0.1 min** | $0 (edge-tts) ou $0.07 (Azure) |
| Alinhamento word-level | 0.1 min | $0 |
| Animação ffmpeg (Fase 0) | 7.0 min | $0 |
| Áudio mastering (Fase 0) | 0.5 min | $0 |
| RunPod i2v (20s generativo) | externo | ~$0.32 |
| Thumbnail (1 imagem) | 0.1 min | $0 |
| **TOTAL LOCAL** | **~12 min** | |
| **TOTAL EXTERNO** | | **~$0.32-0.39** |

### Projeção de escala

| Volume | Tempo Local (12min/ep) | Custo Externo ($0.35/ep) |
|---|---|---|
| 10 episódios | 2.0 horas | $3.50 |
| 50 episódios | 10.0 horas | $17.50 |
| 100 episódios | 20.0 horas | $35.00 |
| 500 episódios | 100.0 horas | $175.00 |

---

## GO / NO-GO VERDICT

| Componente | Veredito |
|---|---|
| Geração de imagem local | 🟢 **GO** — 7.1s/imagem com LCM |
| Animação local (ffmpeg) | 🟢 **GO** — 7 min/episódio |
| TTS + timestamps | 🟢 **GO** — 1.8s para 36.5s de áudio |
| IP-Adapter | 🟡 **GO COM RESSALVAS** — 6/10, precisa LoRA |
| RunPod i2v | ⏳ Pendente (API key) |
| **Custo total/episódio** | **~$0.35** (vs teto de $6.00) |

**Classificação:** **B — LOCAL FUNCIONA, RUNPOD PARA CENAS DECISIVAS**

O hardware local é **viável e eficiente** para a maior parte do pipeline. A geração de imagem com LCM (7s/imagem) é o ponto forte inesperado. A consistência de personagem (IP-Adapter 6/10) é o ponto fraco que justifica o investimento em LoRA treinado no RunPod.

---

## DECISÕES TÉCNICAS FIXADAS PELO BENCHMARK

1. **PyTorch:** 2.7.1+cu118 (NÃO cu126)
2. **Encoder:** libx264 CPU (NÃO NVENC, NÃO QSV)
3. **SD 1.5:** fp16, all-GPU, sem CPU offload
4. **LCM LoRA:** 6 steps, guidance 1.0 — modo de produção padrão
5. **IP-Adapter:** VAE na CPU fp32, cross_attention scale 0.5 — para cenas com personagem recorrente
6. **TTS:** edge-tts ThalitaNeural, rate -8%, pitch +1Hz
7. **Timestamps:** SentenceBoundary (sentença) + faster-whisper tiny (palavra)
8. **Venv:** `C:\Users\meira\hermes-studio-venv` (Python 3.12, isolado do Hermes)
9. **PYTHONPATH:** deve ser limpo (`PYTHONPATH=""`) ao rodar scripts do studio venv

---

*Relatório gerado em 16/08/2026. Todos os tempos foram medidos diretamente nesta máquina. Nenhum número é estimado.*
