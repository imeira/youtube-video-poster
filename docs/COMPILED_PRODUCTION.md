# Produção compilada

## Objetivo

Reduzir latência operacional e consumo de contexto sem enfraquecer orçamento,
identidade canônica, QA individual, hashes ou gates humanos. A duração continua
adaptativa; este fluxo não tem SLA temporal bloqueante.

## Contrato

`src/hybrid/compiled.py` é uma camada sem clientes LLM, chat ou SDK de provider:

1. a pré-produção aprovada entrega áudio congelado, timestamps reais, ação
   semântica e prompt já compilado para cada quadro;
2. `CompiledEpisode.compile()` valida IDs únicos e janelas semânticas ordenadas,
   sem sobreposição;
3. `ProductionRun.dispatch_baselines()` despacha somente as baselines
   independentes, concorrentes até o limite do `Executor`;
4. QA só aceita o hash de um recibo completo e é imutável; uma revisão divergente
   exige uma remediação sucessora;
5. um hero só é elegível quando a baseline exata da mesma cena foi aprovada;
6. uma remediação só pode nascer de uma baseline com QA reprovado, vinculada ao
   hash do resultado;
7. render só fica elegível quando cada baseline ativa e cada hero liberado passou
   em QA.

Toda submissão permanece em `Executor.run()`: ele persiste `INTENT` antes de I/O,
usa request ID determinístico, lock exclusivo, orçamento atômico e recuperação sem
novo POST. Uma retomada recompila o mesmo request ID e não reemite um hero já
persistido.

## Limites deliberados

- Esta etapa não altera `DirectorAgent` nem executa mídia, provider, render,
  Telegram ou YouTube.
- Não migra nem reabre R029, R030 ou R031 do EP8. Requests já consumidos continuam
  congelados; qualquer ponte futura deve importar somente manifestos aprovados e
  iniciar novos trabalhos com IDs sucessores.
- A autoridade LIVE, preço atualizado e recibo continuam requisitos por chamada.
- A QA visual continua individual. Contact sheets agrupam apresentação, não
  substituem a decisão por quadro.

## Migração segura

1. Usar o compilador em shadow mode em um episódio novo ou numa cópia de metadados;
2. comparar IDs, custos, quadro a quadro, hashes e decisões contra a rota atual;
3. integrar o `DirectorAgent` somente como fachada que envia o pacote compilado ao
   control plane, sem writer paralelo;
4. depois de validação, migrar o renderer para exigir os recibos de QA do mesmo
   ledger antes de iniciar FFmpeg.
