# SDD: fábrica pública `studio next-video` (TEST/offline)

## Escopo e limite

Este slice é uma única fachada pública, genérica e somente `TEST`, para operar o próximo episódio compilado: `start`, `resume`, `status` e `worker`. Não cria rota LIVE, provider externo, Azure, segredo, TTS, cobrança, nem publicação. `publication` permanece desabilitada pela política TEST e não há comando de publicar nesta fachada.

A fachada somente compõe o `Director`/pacote compilado existente, `AutonomousCompiledTestAdapter`, `EpisodeTaskQueue` e `DurableEpisodeWorker`. Ela não cria um segundo runner, executor ou kernel de submit: o `Executor` já ligado ao pipeline é o único kernel.

## Entradas imutáveis e identidade

- `--compiled-input`: diretório local com `compiled.json`, `manifest.json` e `policy.json`, copiado e hash-bound pelo adaptador existente;
- política fechada `TEST`, com publicação desabilitada e provider de fixture local já allowlisted;
- identidade de episódio do pacote compilado e request ID SHA-256 derivado dos bytes copiados;
- manifesto, áudio, timeline e receipts reabertos/re-hashados pelos componentes compilados existentes;
- `concurrency`: inteiro persistido, padrão `1`, intervalo fechado `1..32`.

`start` rejeita namespace existente em vez de sobrescrever. Ele cria o runner compilado através do Director, persiste a configuração da fachada, e enfileira a identidade do episódio. `resume`, `status` e `worker` recebem o request ID literal SHA-256; nunca procuram um episódio por texto, tema ou nome de personagem.

## Estados fechados e transições

A fila durável é a única projeção de trabalho:

`PENDING/RESUME -> RUNNING -> WAITING/HUMAN_THUMBNAIL -> PENDING/RESUME -> WAITING/HUMAN_VIDEO -> PENDING/RESUME -> DONE/TERMINAL`.

O runner associado usa seus estados fechados já existentes, incluindo `WAITING_THUMBNAIL_APPROVAL`, `WAITING_VIDEO_APPROVAL`, `READY_TO_PUBLISH`, `REJECTED` e `UNKNOWN_REQUIRES_RECONCILIATION`. Os únicos waits humanos são thumbnail, depois vídeo; a aprovação e a publicação não são parte desta fachada.

Antes de cada mutação da fila, o writer publica `WRITER_IN_PROGRESS`; após commit, publica snapshot `CURRENT`. O resultado de cada submit, QA e render permanece nos receipts hash-bound do runner/pipeline. `status` lê somente o snapshot JSON da fila e a projeção compilada de arquivos; não abre SQLite, WAL ou cria estado.

Antes de qualquer `resolve`, abertura, criação de diretório, fila ou pacote, a fachada normaliza a grafia nativa Windows e nega `C:\\HermesStudio\\production` e **todo descendente**, sem diferença de maiúsculas/minúsculas e aceitando barras normais ou invertidas. Em seguida ela resolve uma única vez para detectar alias/junction: qualquer alias que resolva para essa raiz ou descendente também é negado antes de traversal ou open. Testes usam somente strings de policy ou um alias mockado para scratch; nunca acessam a árvore real de produção.

`status` não lê `runner.json`, nem abre SQLite/WAL. Ele reutiliza exclusivamente o snapshot JSON fechado do writer da fila: valida hash canônico, freshness, identidade do episódio/tipo compiled, generation e fencing token. A projeção inclui `identity_sha256`, `snapshot_sha256`, generation e fence apenas quando o snapshot é `CURRENT`; snapshot ausente, stale ou inválido retorna `runner={"status":"UNVERIFIED","snapshot":"STALE"}` e não inclui estado caller-controlled. `publication` é sempre `DISABLED`.

## Ownership, recuperação e ambiguidade

`DurableEpisodeWorker` conserva a ordem `episode lease -> runner ownership -> request ownership`. Resume usa o mesmo queue task e o mesmo adapter. Em processo novo, o handler reabre a cópia de pacote, runner, executor, receipts e QA; bytes ou hash divergentes falham antes de reutilização. Um receipt completo não é submetido novamente. Queda após COMPLETE executa somente a continuação elegível; queda após QA não repete QA/provider.

Perda, expiração ou cancelamento de lease deixa `UNKNOWN_REQUIRES_RECONCILIATION`; a fachada não faz reset cego nem redispatch automático. Dois resumes concorrentes permanecem fenced pelo lease/ownership existente. O `resume` rejeita tentativa de trocar a concorrência persistida antes de abrir a fila ou dispatch.

## Interface pública

`studio next-video start --workspace ROOT --compiled-input PACKET [--mode TEST] [--concurrency 1..32]`

`studio next-video resume --workspace ROOT --request-id SHA256 [--concurrency N]`

`studio next-video status --workspace ROOT --request-id SHA256`

`studio next-video worker --workspace ROOT --request-id SHA256 [--once] [--concurrency N]`

`worker --once` é o exercício determinístico para testes; sem `--once`, hospeda o `DurableEpisodeWorker` existente com a concorrência persistida. Nenhum comando aceita LIVE. `--mode LIVE`, policy não TEST, publicação habilitada, assets fora do pacote, ou concorrência diferente no resume/worker falham antes de criar queue, reserva ou submit.

## Aceitação e negativos

1. Um pacote sintético genérico percorre a entrada pública `start -> worker` até `WAITING_THUMBNAIL_APPROVAL`, via Director/compiled adapter/worker/QA/render, sem rede ou provider pago.
2. Processo novo `resume` mantém receipts e submit count; não duplica submit concluído.
3. `status` durante/apos worker retorna a projeção `CURRENT` sem mutar arquivos da fila.
4. `concurrency` padrão é 1; 2 e 32 são aceitos e persistidos; 0, 33 ou alteração no resume/worker são negados.
5. `--mode LIVE`, endpoint externo/política LIVE e tentativa de publicação são negados antes de state write, reserva ou submit.
6. Benchmark continua explicitamente TEST/local; reporta latência, concorrência, cache e submits e não é prova LIVE.
