# Status formal do roadmap

**Atualizado em:** 2026-08-20  
**Referência aprovada:** Episódio 1 — A Criação  
**Fonte das fases:** `IDEA.md`, §109

## Regra de interpretação

Uma fase é marcada como **CONCLUÍDA** somente quando há implementação e evidência verificável. Fases exercitadas pelo piloto, mas sem o artefato formal previsto, permanecem **PARCIAIS**. Dependências humanas ou credenciais ausentes são marcadas como **BLOQUEADAS**.

| Fase | Escopo | Estado | Evidência / pendência |
|---:|---|---|---|
| 0 | Discovery + Hardware Audit + RunPod Feasibility | CONCLUÍDA | `docs/PHASE0_FEASIBILITY_REPORT.md` |
| 1 | SDD | CONCLUÍDA | `IDEA.md`, `docs/AGENTS.md` |
| 2 | Project Skeleton | CONCLUÍDA | `src/`, `tests/`, `config.yaml` |
| 3 | State Machine | CONCLUÍDA | implementação e testes unitários |
| 4 | Provider Abstractions | CONCLUÍDA | `src/providers/` |
| 5 | Budget Guard | CONCLUÍDA | `docs/BUDGET_GUARD.md` e testes |
| 6 | Script / Biblical Research | CONCLUÍDA | piloto aprovado, pesquisa e roteiro fundamentados |
| 7 | TTS | CONCLUÍDA | áudio Thalita aprovado com timestamps reais |
| 8 | Storyboard | CONCLUÍDA | 52 cenas alinhadas ao áudio aprovado |
| 9 | Character Bible | CONCLUÍDA | fichas e rostos canônicos de Adão/Eva; resolvedor YAML coberto por testes |
| 10 | Image Pipeline | CONCLUÍDA | 52 imagens finais image-to-image em `EP1_CREATION_REMAKE/images/` |
| 11 | Visual QA | CONCLUÍDA | 52/52 relatórios aprovados em `qa/verified/` |
| 12 | Local Animation | CONCLUÍDA | 52 clipes locais com movimento suave |
| 13 | RunPod Integration | BLOQUEADA | requer H2: conta, API key e créditos RunPod; nenhum segredo será inventado |
| 14 | Animation QA | CONCLUÍDA | 52/52 clipes aprovados em `qa/animation_qa.json` |
| 15 | FFmpeg Assembly | CONCLUÍDA | vídeo 1080p H.264/AAC; delta A/V 0,107 s |
| 16 | Audio Mastering | PARCIAL | auditoria formal concluída: −22,2 LUFS, LRA 2,2 LU, pico −5,9 dBFS; áudio aprovado preservado, normalização exigida nos próximos episódios |
| 17 | Thumbnail | CONCLUÍDA | thumbnail aprovada, versionada e aplicada no YouTube |
| 18 | Metadata | CONCLUÍDA | título, descrição, hashtags e público infantil verificados |
| 19 | Telegram HITL | CONCLUÍDA | vídeo e thumbnail entregues e aprovados no Telegram |
| 20 | YouTube Integration | CONCLUÍDA | vídeo público `GlWh69LF9LM`, thumbnail e playlist verificadas |
| 21 | End-to-End Pilot | CONCLUÍDA | episódio aprovado e publicado após autorização explícita |
| 22 | Performance Optimization | CONCLUÍDA | manifesto SHA-256; render inalterado reutilizado com segurança em 0,845 s |
| 23 | Production Hardening | PENDENTE | retomada idempotente, manifesto de episódio, testes de falha e recuperação |

## Evidências medidas do piloto

- 52 imagens finais; dimensões 1672×940/941 antes do enquadramento 1080p.
- 52 relatórios de QA visual, todos com `approved=true`.
- 52 clipes locais.
- Timeline: 241,335 s.
- Áudio: 241,440 s.
- Vídeo: 241,333 s.
- Delta A/V: 0,107 s.
- Transições: 0,25 s.
- 141 testes unitários aprovados em 2026-08-20.

## Próxima execução automática

1. Executar **Fase 23 — Production Hardening**.
2. Aplicar masterização mensurável na cópia derivada de áudio do próximo episódio antes da aprovação.
3. A **Fase 13** só pode ser encerrada após a ação humana H2.
