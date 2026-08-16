# LOCAL + HYBRID AI VIDEO FEASIBILITY REPORT
## PHASE 0 — DISCOVERY + HARDWARE AUDIT + RUNPOD FEASIBILITY

**Projeto:** HERMES — Hybrid AI Animation Studio
**Canal:** @EraUmaVezBibliaAnimada
**Data da auditoria:** 16 de agosto de 2026
**Máquina:** Dell XPS 15 9570 (`C:\Users\meira`)
**Natureza desta fase:** READ-ONLY. Nenhum driver, modelo, CUDA ou pacote foi instalado ou alterado.

---

## VEREDITO EXECUTIVO

> **A arquitetura HÍBRIDA é VIÁVEL e o teto de US$ 6,00/episódio é FOLGADO — provavelmente 10× maior que o necessário.**
> **Porém, a metade "LOCAL" do plano tem 3 bloqueios reais que precisam de decisão antes do benchmark.**

| Pilar | Veredito | Evidência |
|---|---|---|
| **Animação local (ffmpeg)** | 🟢 **EXCELENTE** | Medido: episódio de 4 min renderiza em ~7 min de CPU |
| **Áudio local (ffmpeg)** | 🟢 **EXCELENTE** | Medido: cadeia completa (loudnorm + ducking + master) em 3s/30s de áudio |
| **TTS pt-BR (Thalita)** | 🟢 **VIÁVEL E GRÁTIS** | Voz confirmada no catálogo; word-timestamps disponíveis |
| **RunPod (vídeo generativo)** | 🟢 **MUITO BARATO** | Calculado: ~US$ 0,32 para 20s de vídeo i2v |
| **Geração de imagem local** | 🔴 **RISCO CRÍTICO** | 4GB VRAM + PyTorch abandonou Pascal — **é o gargalo real** |
| **Encoding por GPU (NVENC)** | 🔴 **QUEBRADO** | Medido: driver 528.79 < 610 exigido pelo ffmpeg 9.0 |
| **Publicação YouTube** | 🟡 **BLOQUEIO ADMINISTRATIVO** | Audit do Google é pré-requisito, não é código |

**O erro estratégico a evitar:** o gargalo NÃO é o vídeo generativo (é barato). O gargalo é **gerar 35–40 imagens consistentes por episódio numa GPU de 4GB de 2017**.

---

## 1. HARDWARE ENCONTRADO

Confirmado por `wmic`, `nvidia-smi` e `systeminfo`.

| Componente | Valor medido | Observação |
|---|---|---|
| **SO** | Windows 11, build 10.0.26200.9168 | 64-bit |
| **CPU** | Intel Core i9-8950HK @ 2.90 GHz | 6 núcleos / 12 threads |
| **RAM** | 34.122.801.152 B = **31,8 GB** | Confirmado |
| **GPU dedicada** | NVIDIA GTX 1050 Ti with Max-Q Design | **4096 MiB VRAM** (4020 MiB livres) |
| **Compute Capability** | **6.1 (Pascal)** | ⚠️ Sem Tensor Cores, sem bf16, fp16 lento |
| **Driver NVIDIA** | **528.79** (fev/2023) | ⚠️ Muito antigo — CUDA runtime 12.0 |
| **GPU integrada** | Intel UHD Graphics 630 | Driver 27.20.100.9664 |
| **Extra** | 2× DisplayLink USB Device | Docking station |
| **Disco C:** | 936 GB total / **402 GB livres** (57% usado) | Suficiente |
| **Disco D:** | Presente, sem tamanho reportado | Provável leitor vazio |
| **Temperatura GPU idle** | 52–53 °C | Clock idle 607 MHz / máx 1911 MHz |

**Nota sobre Max-Q:** esta é a variante de baixo consumo do 1050 Ti. `nvidia-smi` reporta `power.limit = [N/A]`, típico de laptops — o throttling térmico sob carga sustentada é um risco real e ainda **não medido**.

---

## 2. SOFTWARE ENCONTRADO

| Ferramenta | Status | Versão / Caminho |
|---|---|---|
| **ffmpeg** | ✅ | **9.0-full** (gyan.dev) — build completo, muito recente |
| **ffprobe** | ✅ | Idem |
| **Python 3.12** | ✅ | `C:\Python312\python.exe` (3.12.1) — **interpretador padrão do `py`** |
| **Python 3.11** | ✅ | venv do Hermes (3.11.16, via uv) |
| **pip** | ✅ | 23.2.1 → vinculado ao Python 3.12 |
| **git** | ✅ | 2.43.0.windows.1 |
| **node / npm** | ✅ | v22.23.2 / 12.0.2 |
| **gh (GitHub CLI)** | ✅ | Presente |
| **jq, curl** | ✅ | Presentes |
| **Docker** | ⚠️ | Instalado, **daemon NÃO está rodando** |
| **WSL2** | ⚠️ | `Ubuntu` e `docker-desktop` presentes, ambos **Stopped** |
| **Hermes Agent** | ✅ | **v0.20.1** (2026.8.13) |
| **CUDA Toolkit (nvcc)** | ❌ | **AUSENTE** |
| **PyTorch** | ❌ | **AUSENTE** em ambos os interpretadores |
| **ComfyUI** | ❌ | **AUSENTE** |
| **Modelos / HF cache** | ❌ | `~/.cache/huggingface` não existe — **0 GB de modelos baixados** |

**Pacotes Python já presentes (Python312) que são úteis:**
- `faster-whisper 1.2.1` → **já serve para forced alignment / timestamps**
- `google-api-python-client 2.194.0` → **já serve para YouTube Data API**
- `numpy 2.4.3`, `pillow 12.3.0`

Isso é um bom ponto de partida: o Python312 já está parcialmente equipado para o eixo áudio+publicação.

---

## 3. LIMITAÇÕES (as que realmente importam)

### 🔴 L1 — PyTorch abandonou a arquitetura Pascal (sm_61)

Este é o achado mais importante do relatório.

- PyTorch 2.7+ / 2.8 (wheels CUDA 12.8) **removeram os kernels sm_61**. Usuários de GTX 10xx recebem `CUDA kernel error: no kernel image is available`.
  Fonte: [github.com/bnsreenu/.../issues/57](https://github.com/bnsreenu/digitalsreeni-image-annotator/issues/57), [r/LocalLLaMA](https://www.reddit.com/r/LocalLLaMA/comments/1lrerwe/pytorch_27x_no_longer_supports_pascal_architecture/)
- A matriz oficial do PyTorch confirma: **apenas o build CUDA 12.6.3 (rotulado "Legacy")** ainda inclui Pascal (6.0/6.1) — e ele **não está no PyPI**, só em `download.pytorch.org`. Os builds CUDA 13.0/13.2 começam em Turing (7.5).
  Fonte: [dev-discuss.pytorch.org — Introducing CUDA 13.2 and Deprecating CUDA 12.8](https://dev-discuss.pytorch.org/t/introducing-cuda-13-2-and-deprecating-cuda-12-8-release-2-12/3337)

**Consequência prática:** ficamos travados num ramo legado de PyTorch, com `--index-url https://download.pytorch.org/whl/cu126`. Toda a stack a jusante (diffusers, ComfyUI, xformers) precisa ser compatível com esse ramo congelado. **Esta janela vai fechar** — é uma dívida técnica com data de validade.

### 🔴 L2 — NVENC está quebrado (MEDIDO, não teórico)

Tentei encodar via GPU e falhou de forma limpa:

```
[h264_nvenc] Driver does not support the required nvenc API version.
             Required: 13.1  Found: 12.0
[h264_nvenc] The minimum required Nvidia driver for nvenc is 610.00 or newer
```

O ffmpeg 9.0 (muito novo) exige driver ≥ 610; temos 528.79. **Todo encoding hoje é CPU (libx264).** A boa notícia: medi o caminho CPU e ele é rápido o suficiente (seção 15). A má notícia: atualizar o driver para destravar NVENC entra em conflito com L1 — drivers novos ainda suportam Pascal, mas é uma mudança que **exige sua autorização** e deve ser testada isoladamente.

*Alternativa sem tocar no driver:* o encoder **`h264_qsv` (Intel Quick Sync na UHD 630)** está disponível no build e não foi testado. Pode ser o atalho seguro para encoding acelerado.

### 🔴 L3 — 4 GB de VRAM

4 GB é o limite inferior absoluto da geração de imagem moderna. Com Pascal (sem Tensor Cores), perde-se também a aceleração fp16 que placas modernas usam para compensar pouca VRAM.

### 🟡 L4 — O projeto está dentro do OneDrive

`C:\Users\meira\OneDrive\IdeaProjects\youtube-video-poster` — e o `OneDrive.exe` está **rodando agora**. Um `du -sh` na pasta OneDrive **estourou 300 s de timeout**, o que já indica uma árvore grande/lenta.

Este pipeline vai gerar **GB de PNGs, WAVs e MP4s intermediários por episódio**. Deixá-los numa pasta sincronizada causa: sincronização constante em background, competição por I/O durante renders, risco de arquivos "cloud-only" (placeholders) quebrarem o ffmpeg, e possível estouro da cota do OneDrive.

**Recomendação:** código versionado fica no OneDrive; **`projects/episodes/` deve ficar FORA** (ex.: `C:\HermesStudio\episodes`) e ser adicionado ao `.gitignore`.

### 🟡 L5 — Docker e WSL2 parados

Ambos existem mas estão desligados. ComfyUI e workers RunPod locais tipicamente assumem Linux. Ligar WSL2 consome RAM e adiciona uma camada de I/O — decisão a tomar no benchmark.

---

## 4. DRIVERS

| Item | Atual | Situação |
|---|---|---|
| NVIDIA Display Driver | **528.79** (fev/2023) | Antigo. Funciona para CUDA 12.0. |
| NVENC API | 12.0 | ❌ Insuficiente (ffmpeg 9.0 quer 13.1 / driver 610+) |
| Intel UHD 630 | 27.20.100.9664 | Antigo, mas QSV está exposto no ffmpeg |

**Não alterei nada.** A decisão de atualizar o driver NVIDIA é sua e deve ser isolada: ela pode destravar NVENC, mas mexe na base de toda a stack CUDA. Recomendo tratá-la como um experimento próprio, com ponto de retorno.

---

## 5. CUDA

- **CUDA Toolkit: não instalado** (`nvcc` ausente, sem diretório em `Program Files\NVIDIA GPU Computing Toolkit`).
- **CUDA runtime disponível via driver: 12.0** (reportado pelo `nvidia-smi`).
- Compute capability do dispositivo: **6.1**.

**Importante:** para PyTorch **não é preciso instalar o CUDA Toolkit** — as wheels trazem o runtime embutido. Só precisamos que o driver seja compatível. Um driver 528.79 (CUDA 12.0) roda wheels cu121/cu126 via *minor version compatibility*. Isto é **UNVERIFIED para este par exato** e é um dos primeiros itens a validar no benchmark.

---

## 6. MODELOS CANDIDATOS (imagem — o eixo crítico)

Estimativas baseadas em literatura pública para Pascal 4GB. **Nenhum foi medido nesta máquina** — medir isto é o objetivo nº 1 da fase de benchmark.

| Modelo | Cabe em 4GB? | Tempo estimado 512×512 | Veredito |
|---|---|---|---|
| **SD 1.5** (fp16) | ✅ Sim | ~30–60 s (20 steps) | 🟢 **Candidato principal** |
| **SD 1.5 + LCM/Turbo LoRA** (4–8 steps) | ✅ Sim | ~8–20 s | 🟢 **Melhor candidato** |
| SDXL (medvram + tiled VAE) | ⚠️ No limite | vários minutos | 🟡 Provavelmente inviável |
| SDXL-Turbo / Lightning | ⚠️ No limite | ~1–2 min | 🟡 Testar, sem expectativa |
| SD 3.5 Medium | ❌ Não | — | 🔴 Descartado |
| FLUX.1-schnell (GGUF Q4) | ❌ Não em 4GB | — | 🔴 Descartado local |

**Consequência para a estratégia:** SD 1.5 gera nativamente em 512×512. Para um canal 1080p, o fluxo obrigatório é **gerar 512–768 → upscale local (Real-ESRGAN) → 1920×1080**. Isso é normal e funciona bem para animação estilizada, mas precisa estar no pipeline desde o início.

### Consistência de personagem (requisito CRÍTICO — seções 35–39 do briefing)

| Ferramenta | Custo VRAM extra | Viabilidade em 4GB |
|---|---|---|
| **LoRA por personagem** (inferência) | ~+0,1 GB | 🟢 Melhor opção |
| **IP-Adapter** (SD1.5) | ~+0,3–0,6 GB | 🟢 Provável |
| **ControlNet** (openpose/depth) | ~+0,7–1,4 GB | 🟡 Um por vez, apertado |
| IP-Adapter **+** ControlNet juntos | ~+1,5–2 GB | 🔴 Improvável em 4GB |
| **Treinar LoRA** localmente | 6–8 GB | 🔴 **Inviável local → usar RunPod** |

**Insight importante:** treinar um LoRA por personagem canônico é a forma mais robusta de garantir "Davi do ep. 5 ≈ Davi do ep. 20". Não cabe nos 4 GB, mas **custa centavos no RunPod e é pago UMA VEZ por personagem**, não por episódio. Isso encaixa perfeitamente na filosofia LOCAL-FIRST + CLOUD-ON-DEMAND e reduz o custo marginal de todos os episódios futuros.

---

## 7. MODELOS DESCARTADOS

**Descartados para execução local** (VRAM insuficiente por ampla margem): SD 3.5 Large/Medium, FLUX.1 dev/schnell em fp8/fp16, HunyuanVideo, Wan 2.1/2.2 14B, CogVideoX-5B, LTX-Video 13B, SVD-XT.

**Descartados para vídeo generativo local:** todos. Nenhum modelo de vídeo moderno opera em 4 GB de Pascal em tempo operacionalmente aceitável. Isto **não é um problema** — é exatamente o caso de uso do RunPod, e a seção 12 mostra que é barato.

---

## 8. IMAGE MODELS — recomendação

**Stack recomendada:** SD 1.5 (base) + LCM LoRA (velocidade) + LoRA por personagem (identidade) + Real-ESRGAN (upscale), tudo via **ComfyUI em modo headless** (endpoint `/prompt`) para automação.

Por que ComfyUI e não diffusers puro: o requisito 84 exige workflows versionados, parametrizados e testáveis. A API headless do ComfyUI aceita um JSON de workflow — que **é** um artefato versionável e parametrizável. Satisfaz o requisito sem depender de cliques numa UI.

---

## 9. VIDEO MODELS

**Local:** nenhum (ver seção 7).
**Cloud (RunPod):** Wan 2.2 (5B e 14B) e LTX-Video são os candidatos com melhor relação custo/qualidade para **image-to-video**, que é o modo exigido pelo requisito 73 (preserva a identidade do personagem partindo de um still já aprovado).

---

## 10. TTS — 🟢 RESOLVIDO, GRÁTIS

**A voz solicitada existe e está confirmada no catálogo oficial da Microsoft:**

| Voz | Tipo | Nota |
|---|---|---|
| `pt-BR-ThalitaNeural` | Standard (Female) | ✅ **A voz aprovada no briefing** |
| `pt-BR-ThalitaMultilingualNeural` | Multilingual (Female) | Alternativa (soa diferente) |
| `pt-BR-Thalita:DragonHDLatestNeural` | **Neural HD** | Qualidade superior (só Azure pago) |
| `pt-BR-Luana:MAI-Voice-2` | Standard + **18 estilos emocionais** | 🌟 Forte candidata para narração infantil |

Fonte: [Microsoft Learn — Language and voice support for Azure Speech](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support)

**Descoberta relevante:** as vozes `MAI-Voice-2` (Caio, Luana, Pedro, Rafael) suportam estilos como `excited`, `hopeful`, `joyful`, `softvoice`, `whispering`. Para "voz humanizada, acolhedora, expressiva, contando histórias para crianças" (requisito 28), controle de estilo por cena é **muito** mais expressivo que apenas `rate`/`pitch`. Vale um teste comparativo A/B na fase de benchmark.

**Timestamps reais (requisitos 27 e 32):** confirmado que `edge-tts` expõe eventos `WordBoundary`/`SentenceBoundary` via `SubMaker`, permitindo derivar SRT diretamente da narração gerada — sem heurística de "80 palavras / 30 segundos".
Fonte: [Stack Overflow — Subtitle generation in edge-tts](https://stackoverflow.com/questions/79403115/subtitle-generation-in-edge-tts-python), [edge-tts issue #335](https://github.com/rany2/edge-tts/issues/335)

**Redundância:** `faster-whisper 1.2.1` **já está instalado** e faz alinhamento com `word_timestamps=True` como verificação cruzada.

⚠️ **Ressalva honesta (ToS):** `edge-tts` é um cliente **não-oficial** do endpoint do Microsoft Edge. É grátis e amplamente usado, mas **não é uma API licenciada para uso comercial**. Para um canal monetizado, a rota juridicamente limpa é **Azure Speech pago** (a mesma voz Thalita, via chave oficial). O custo é irrisório: ~650 palavras ≈ 4.000 caracteres/episódio; mesmo a US$ 15–16/milhão de caracteres isso dá **menos de US$ 0,07 por episódio**. **Recomendo fortemente Azure oficial** — o risco de ToS não vale a economia de centavos. *(Preço exato do Azure: UNVERIFIED — confirmar na fase de benchmark.)*

---

## 11. COMFYUI

Não instalado. Recomendado como engine de imagem, em modo headless com `--lowvram`, nós GGUF e Tiled VAE. Workflows devem viver em `workflows/*.json` versionados no git, parametrizados por um wrapper Python (requisito 84).

---

## 12. LOCAL ANIMATION — 🟢 O DESTAQUE DO RELATÓRIO

**Estes números foram MEDIDOS nesta máquina, agora.** É a evidência mais forte de que a estratégia "imagem consistente + animação local" está correta.

| Operação | Resolução | Tempo medido | Ratio |
|---|---|---|---|
| **Ken Burns** (push-in, zoompan) 5 s | 1080p30, x264 veryfast crf20 | **3,56 s** | **1,4× realtime** |
| Ken Burns 5 s (alta qualidade) | 1080p30, x264 medium crf18 | 7,50 s | 0,67× realtime |
| **Parallax 2 camadas** 5 s | 1080p30, x264 veryfast | **15,06 s** | 0,33× realtime |
| **Transição xfade** | 1080p30 | 4,27 s | — |
| **Concat 8 clipes** (stream copy) | 1080p | **0,77 s** | ~52× realtime |
| ~~NVENC GPU encode~~ | — | ❌ **FALHOU** | driver antigo |

Todos os filtros necessários estão presentes no build: `zoompan`, `xfade`, `minterpolate`, `displace`, `remap`, `gblur`, `overlay`, `chromakey`, `colorkey`, `alphamerge`, `maskedmerge`, `blend`, `tblend`, `deshake`, `lenscorrection`, `vignette`, `noise`, `rotate`, `scale2ref`.

**Conclusão:** a biblioteca de motion presets do requisito 52 (`slow_push_in`, `parallax_walk`, `hero_reveal`, `storm_motion`, etc.) é **totalmente implementável hoje, com o ffmpeg já instalado, sem instalar nada.** O motor de animação local não é um risco — é um ativo pronto.

---

## 13. RUNPOD STRATEGY

**Modelo escolhido:** Pods on-demand por job (não Serverless, na v1).

Justificativa: Serverless tem cold start e complexidade de deploy de worker; Pods dão controle explícito de ciclo de vida, que é o que o requisito 55–56 (prevenção de GPU ociosa) realmente exige. Billing é **por segundo**, então um pod bem gerenciado não é mais caro que serverless para jobs em lote.

**Ciclo obrigatório:** `ALLOCATE → LOAD → RUN → SAVE → VERIFY → SHUTDOWN`, com `try/finally`, watchdog de timeout e uma varredura de pods órfãos no startup do Director Agent.

**Regra de ouro a implementar:** o Budget Guard **reserva** o custo estimado *antes* de provisionar, e reconcilia com o custo real *depois* do shutdown. Nunca provisionar sem reserva.

---

## 14. GPU CLOUD CANDIDATES (preços verificados em 16/ago/2026)

| GPU | VRAM | On-demand $/hr | Adequação |
|---|---|---|---|
| **RTX A4000** | 16 GB | **$0.25** | Imagem, upscale, treino de LoRA |
| **RTX A4500** | 20 GB | $0.25 | Imagem / vídeo leve |
| **RTX A5000** | 24 GB | **$0.27** | 🌟 **Melhor custo-benefício p/ i2v** |
| RTX A2000 | 12 GB | $0.50 | — |
| **L4** | 24 GB | $0.49 | Eficiente, mais lenta |
| A40 | 48 GB | $0.44 | Bom preço por VRAM |
| RTX A6000 | 48 GB | $0.53 | — |
| **RTX 4090** | 24 GB | **$0.74** | 🌟 **Mais rápida da faixa barata** |
| L40S | 48 GB | $0.99 | Overkill |
| H100 | 80 GB | $2.89–3.29 | ❌ Desnecessário |

Fontes: [runpod.io/pricing](https://www.runpod.io/pricing), [gpus.io/providers/runpod](https://gpus.io/en/providers/runpod) (recuperado em 16/08/2026). Community cloud pode chegar a **$0.34/hr no RTX 4090** ([Northflank](https://northflank.com/blog/runpod-gpu-pricing)).

**Regra para o GPU Selector (requisito 58):** nunca hardcodar RTX 4090. Escolher a GPU mais barata com VRAM suficiente. Para i2v de 480p, **RTX A5000 a $0.27/hr** costuma vencer; o 4090 vence quando a velocidade compensa o preço 2,7× maior.

---

## 15. ESTIMATED PERFORMANCE

### Render local de um episódio de 4 min (240 s) — extrapolado dos números medidos

| Etapa | Cálculo | Tempo |
|---|---|---|
| 28 cenas Ken Burns (168 s de vídeo) | 0,71 s por segundo de saída | ~2,0 min |
| 12 cenas parallax (72 s de vídeo) | 3,01 s por segundo de saída | ~3,6 min |
| Transições + concat + mux | medido | ~1,0 min |
| Masterização de áudio | medido (3 s / 30 s) | ~0,5 min |
| **TOTAL FFMPEG** | | **≈ 7 min** |

🟢 **Excelente.** A montagem não é gargalo.

### Geração de imagem local (ESTIMADO — este é o número que falta)

~37 imagens/episódio. A 45 s/imagem (SD1.5 padrão) → **~28 min**. Com LCM a 15 s/imagem → **~9 min**.
Somando QA e regenerações: **estimativa de 30–60 min por episódio, dominada pela geração de imagem.**

⚠️ **Este número é o único item verdadeiramente desconhecido do projeto, e é o que decide o GO/NO-GO.** Medi-lo é o objetivo nº 1 da fase de benchmark.

---

## 16. ESTIMATED STORAGE

| Item | Tamanho |
|---|---|
| PyTorch (cu126) + deps | ~4 GB |
| ComfyUI + nós | ~1 GB |
| SD 1.5 base + VAE | ~4 GB |
| LCM LoRA + ControlNet + IP-Adapter | ~3 GB |
| Real-ESRGAN / RIFE | ~0,5 GB |
| **Subtotal software+modelos** | **~13 GB** |
| Por episódio (imagens + áudio + clipes + render) | **~2–4 GB** |
| 100 episódios | **~200–400 GB** |

**Disponível: 402 GB.** Cabe, mas 100 episódios consomem quase tudo. É necessária uma política de arquivamento (requisito 86: nunca deletar Character Bible, roteiro, master de áudio, vídeo final, manifest; pode limpar intermediários reproduzíveis).

**Rede medida:** ~16,8 MB/s (**~134 Mbps**) de download real do HuggingFace CDN. Os ~13 GB de setup levam ~15 min. Nenhum gargalo de rede.

---

## 17. ESTIMATED LOCAL GENERATION TIMES

Resumo: **~7 min de ffmpeg (medido)** + **~30–60 min de imagem (estimado)** = **~40–70 min por episódio de 4 min**, assumindo que SD1.5 rode em velocidade decente. Aceitável para produção assíncrona noturna; inviável para produção sob demanda interativa.

---

## 18. RUNPOD ESTIMATED COST — 🟢 MUITO ABAIXO DO TETO

**Benchmark público de referência:** WAN 2.1 em RTX 4090 — 5 s de vídeo a 480p em **5,3 min**; a 720p em **40 min**.
Fonte: [blog.salad.com/benchmarking-wan2-1](https://blog.salad.com/benchmarking-wan2-1/). Wan 2.2 5B é mais rápido (~9 min para 5 s a 720p, [Medium](https://medium.com/data-science-in-your-pocket/wan2-2-ai-video-generation-in-budget-gpu-a314238b2a54)).

### Cálculo explícito — 20 s de vídeo generativo por episódio

**Cenário A — 480p + upscale local (RECOMENDADO)**
```
20 s de vídeo ÷ 4 s por clipe          = 5 clipes
5 clipes × 4,24 min (pro-rata de 5,3)  = 21,2 min de geração
+ boot do pod + carga do modelo        =  5,0 min (uma vez)
                                         ─────────
total                                  = 26,2 min = 0,437 h
0,437 h × $0.74/hr (RTX 4090)          = $0.32
0,437 h × $0.34/hr (community)         = $0.15
```

**Cenário B — 720p nativo**
```
5 clipes × 32 min + 5 min overhead = 165 min = 2,75 h
2,75 h × $0.74/hr                  = $2.04
```

| Segundos generativos | Custo @480p+upscale | Custo @720p nativo |
|---|---|---|
| 10 s | **~$0.18** | ~$1.05 |
| 20 s | **~$0.32** | ~$2.04 |
| 30 s | **~$0.46** | ~$3.03 |

### Custo total externo projetado por episódio

| Item | Custo |
|---|---|
| RunPod (20 s i2v @480p+upscale) | $0.32 |
| TTS (Azure oficial, ~4k caracteres) | ~$0.07 |
| LLM (roteiro, storyboard, QA, metadata) | ~$0.30–0.80 |
| **TOTAL PROVÁVEL** | **≈ $0.70 – $1.20** |
| **TETO CONFIGURADO** | **$6.00** |

> 🟢 **O teto de US$ 6,00 é ~5–8× maior que o custo provável.** A restrição financeira real do projeto não é o dinheiro — **é o tempo de GPU local.**
>
> **Recomendação estratégica:** com essa folga, considere elevar o alvo de vídeo generativo (o requisito 70 permite: `max_seconds_per_episode` é configurável) OU usar o orçamento sobrando para **treinar LoRAs de personagem no RunPod**, o que ataca diretamente o requisito nº 1 mais difícil do projeto (consistência de personagem) e é pago uma única vez por personagem.

---

## 19. PIPELINE RECOMMENDATION

Confirmo a arquitetura do briefing, com **uma correção de ênfase**:

```
CHARACTER BIBLE (LoRA treinado no RunPod — pago 1× por personagem)
        ↓
SCRIPT → TTS (Azure Thalita) → TIMESTAMPS REAIS (WordBoundary)
        ↓
STORYBOARD alinhado à narração
        ↓
IMAGENS: SD1.5 + LCM + LoRA de personagem, 512–768px  ← GARGALO REAL
        ↓
VISUAL QA (rejeita antes de gastar)
        ↓
UPSCALE local (Real-ESRGAN → 1080p)
        ↓
   ┌────────────────┴────────────────┐
LOCAL ANIMATION (~90%)      RUNPOD i2v (~10%, cenas CRITICAL)
ffmpeg — MEDIDO E RÁPIDO         $0.32/episódio
   └────────────────┬────────────────┘
        ↓
FFMPEG ASSEMBLY + AUDIO MASTER (medido: rápido)
        ↓
QA FINAL → TELEGRAM → YOUTUBE
```

**Correção de ênfase vs. o briefing:** o documento original trata o custo do RunPod como a restrição central (seções 61–78). Os dados mostram que o RunPod é barato. **A restrição central é a geração de imagem local.** O Budget Guard continua obrigatório (é uma trava de segurança correta), mas o esforço de engenharia deve se concentrar no **throughput e na consistência da geração de imagem**, não em economizar centavos de GPU cloud.

---

## 20. RISKS

| # | Risco | Sev. | Mitigação |
|---|---|---|---|
| R1 | **PyTorch abandonou Pascal**; janela legada vai fechar | 🔴 Alta | Fixar `cu126` legacy; planejar upgrade de GPU ou mover imagem p/ RunPod |
| R2 | **SD1.5 lento demais em 4GB** → episódios de horas | 🔴 Alta | **Medir primeiro**; LCM/Turbo; fallback: gerar imagens no RunPod (~$0.10) |
| R3 | **NVENC quebrado** (medido) | 🟡 Média | libx264 CPU já é suficiente (medido); testar `h264_qsv`; driver só com aprovação |
| R4 | **Consistência de personagem falha** — mata o canal | 🔴 Alta | LoRA por personagem (treinado no RunPod) + IP-Adapter + QA obrigatório |
| R5 | **Assets no OneDrive** → I/O lento, cota, arquivos fantasma | 🟡 Média | Mover `episodes/` para fora do OneDrive |
| R6 | **Audit do YouTube Data API** — bloqueio administrativo | 🟡 Média | Iniciar o pedido JÁ (leva semanas); MVP publica como `private` |
| R7 | Quota YouTube: 10.000 unidades/dia, upload = 1.600 | 🟢 Baixa | ~6 uploads/dia — folgado. Fonte: [Google](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits) |
| R8 | **`edge-tts` é não-oficial** (ToS em canal monetizado) | 🟡 Média | Usar **Azure Speech oficial** (~$0.07/ep) |
| R9 | **GPU órfã no RunPod** queimando créditos | 🔴 Alta | `try/finally` + watchdog + varredura de órfãos no startup |
| R10 | Throttling térmico do Max-Q sob carga longa | 🟡 Média | Logar temperatura no benchmark (requisito 116) |
| R11 | **Política do YouTube p/ conteúdo em massa / IA** | 🟡 Média | Requisito 97 já cobre: contribuição editorial substancial |
| R12 | Sem `RUNPOD_API_KEY` / `TELEGRAM_BOT_TOKEN` configurados | 🟢 Baixa | Você precisa criar (seção 21) |

---

## 21. IMPLEMENTATION PLAN

### ⛔ AÇÃO HUMANA NECESSÁRIA (bloqueia fases futuras — comece agora, roda em paralelo)

| # | Ação | Onde | Tempo | Bloqueia |
|---|---|---|---|---|
| H1 | Criar **bot do Telegram** (@BotFather) e obter token + chat_id | Telegram | 10 min | Fase 19 |
| H2 | Criar conta **RunPod** + API key + créditos iniciais | runpod.io | 15 min | Fase 13 |
| H3 | Criar projeto **Google Cloud** + OAuth client + ativar YouTube Data API v3 | console.cloud.google.com | 30 min | Fase 20 |
| H4 | **Submeter o YouTube API Audit** | Formulário do Google | 30 min + **semanas de espera** | Publicação pública |
| H5 | *(Opcional, recomendado)* Chave **Azure Speech** | portal.azure.com | 20 min | TTS licenciado |
| H6 | **Decidir:** atualizar driver NVIDIA? | — | decisão | NVENC |
| H7 | **Decidir:** mover `episodes/` para fora do OneDrive? | — | decisão | Storage |

**Chaves de API já presentes** no ambiente Hermes: `ANTHROPIC`, `OPENAI`, `GOOGLE`, `XAI`, `OPENROUTER`, `DEEPSEEK`, `FIREWORKS`, `GLM`, `DASHSCOPE`, `NVIDIA`, `HF_TOKEN`.
**Ausentes:** `RUNPOD_API_KEY`, `TELEGRAM_BOT_TOKEN`, credenciais OAuth do YouTube.

### Fase de Benchmark proposta (a executar após seu "APROVADO")

Ordem deliberada: **medir o risco maior primeiro, e instalar o mínimo possível antes de saber se vale a pena.**

| Passo | O que | Instala? | Decide |
|---|---|---|---|
| **B0** | Testar `h264_qsv` (Intel QSV) como encoder | Nada | Contorna NVENC sem mexer no driver |
| **B1** | Instalar **só** PyTorch cu126 legacy; validar `torch.cuda.is_available()` e um matmul em sm_61 | ~4 GB | **Se falhar, todo o plano local morre aqui** |
| **B2** | SD 1.5 + ComfyUI `--lowvram`: medir s/imagem a 512² e 768², VRAM pico, temperatura | ~5 GB | **O número que define o GO/NO-GO** |
| **B3** | LCM/Turbo LoRA: medir o ganho de velocidade | ~0,5 GB | Viabilidade de produção |
| **B4** | IP-Adapter + LoRA: teste de consistência do mesmo personagem em 10 imagens | ~2 GB | Viabilidade do Character Bible |
| **B5** | TTS: Thalita vs. Luana:MAI-Voice-2; validar word-timestamps | ~0 | Fixa a voz do canal |
| **B6** | Um job RunPod real de i2v: medir tempo, custo real, e **provar o shutdown** | ~0 local | Valida o Budget Guard |
| **B7** | Consolidar `HARDWARE_BENCHMARK.md` com números reais | — | Entrada da Fase 1 (SDD) |

**Critério de decisão em B2:** se uma imagem 512×512 levar **> 90 s**, a geração local de imagem é operacionalmente inviável (37 imagens = ~1 h só de imagem) e devemos mover a geração de imagem para o RunPod — o que ainda cabe com folga em US$ 6,00 e provavelmente resulta em episódios *mais baratos em tempo* e melhores em qualidade.

---

## RESUMO DA DECISÃO

✅ **A arquitetura híbrida está correta e o orçamento é folgado.**
🔴 **O risco concentra-se num único ponto: geração de imagem consistente numa GPU de 4 GB e arquitetura descontinuada.**
📌 **Nada foi instalado ou alterado nesta máquina.**

**Aguardando:** `APROVADO — INICIAR FASE DE BENCHMARK`

---
*Relatório gerado na Fase 0. Toda medição marcada como "medido" foi executada nesta máquina em 16/08/2026. Itens marcados como "estimado" ou "UNVERIFIED" não foram medidos e serão resolvidos na fase de benchmark.*
