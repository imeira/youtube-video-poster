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

## Operação

`DirectorAgent.create_operational_pipeline()` é a única fachada de ativação. Ela
lê o storyboard já compilado e exige explicitamente áudio congelado, manifest de
referências image-to-image, banco SQLite, endpoint e custo máximo. O Diretor não
executa mais o loop serial de imagem/animação depois do storyboard.

`OperationalPipeline` persiste o pacote, recibos de QA, cópias hash-bound dos
quadros aprovados, contact sheet e manifesto dentro de `episodes/<id>/compiled/`.
O provider é injetado no despacho e toda chamada passa por `Executor.run()`.

Não migra nem reabre R029, R030 ou R031 do EP8. Requests já consumidos continuam
congelados; qualquer ponte futura deve importar somente manifestos aprovados e
iniciar novos trabalhos com IDs sucessores.

## Regras restantes

- A autoridade LIVE, preço atualizado e recibo continuam requisitos por chamada.
- A QA visual continua individual. Contact sheets agrupam apresentação, não
  substituem a decisão por quadro.
- `LocalRenderer.render_compiled()` exige que o control plane tenha QA completa
  antes de iniciar FFmpeg.
- Thumbnail, vídeo e publicação continuam gates humanos separados; nenhuma rota
  desta camada publica ou agenda YouTube.
