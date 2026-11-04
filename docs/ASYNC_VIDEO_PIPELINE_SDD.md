# SDD: fluxo assíncrono de produção de vídeo

## Objetivo

Reduzir a latência entre geração, QA e composição sem enfraquecer idempotência,
orçamento, identidade canônica, QA individual, congelamento de frames ou os gates
humanos independentes de thumbnail, vídeo e publicação.

Este slice qualifica somente execução `TEST`/offline e a orquestração local. Ele
não cria autoridade financeira LIVE, não executa chamadas pagas e não autoriza
publicação.

## Entradas imutáveis

- pacote compilado do episódio e seu SHA-256;
- áudio aprovado, duração decodificada e timeline semântica;
- manifesto de referências canônicas e hashes dos ativos;
- jobs com `request_id`, endpoint, payload, custo e predecessor imutáveis;
- política, teto de concorrência, prefetch e orçamento de CPU do render;
- para retomada, receipts do Executor, QA e artefatos revalidados contra os bytes
  atuais.

## Controle assíncrono

1. O DAG inteiro é validado antes de `RUNNING`: IDs únicos, predecessores
   existentes, ausência de ciclos e jobs em modo permitido.
2. Geração usa uma janela rolante durável limitada a `workers + prefetch`; apenas
   `workers` jobs podem estar em execução.
3. Cada resultado é persistido como `COMPLETE` antes do callback de conclusão.
4. A conclusão de uma cena libera imediatamente o pacote técnico e a QA daquela
   cena, sem aguardar o job mais lento do lote.
5. Geração e QA compartilham a mesma janela de jobs: um slot só é liberado após
   receipt durável e QA daquela cena. PASS/FAIL é hash-bound e write-once; FAIL
   exige sucessor explícito.
6. Hero ou correção depende apenas do predecessor exato da mesma cena. Render
   continua bloqueado até todos os ativos ativos e heroes exigidos passarem.
7. Nenhuma coroutine é criada para todo o backlog: admissão permanece limitada à
   janela rolante.

## Estados e persistência

Estados por job: `READY`, `RUNNING`, `COMPLETE`, `QA_PENDING`, `PASSED`, `FAILED`
ou `UNKNOWN_REQUIRES_RECONCILIATION`.

Estados agregados existentes permanecem válidos. Um callback só pode observar um
receipt já durável. Reinício reutiliza apenas receipt, contrato, artefato e QA que
forem revalidados; nunca reenvia um request com ID remoto ou submissão ambígua.

A ordem de ownership é:

`episode lease -> runner ownership -> request ownership`.

O slice atual mantém o lock de ownership do runner durante `resume`; a ativação
LIVE de fencing/lease permanece bloqueada até existir autoridade externa
qualificada.

## Render local

- FFmpeg roda fora do event loop por offload explícito.
- `scene_workers`, `encoder_threads` e `filter_complex_threads` formam um orçamento
  finito persistido no receipt.
- O contrato de render liga áudio, manifesto aprovado, cenas ordenadas, hashes,
  geometria, FPS, hold e configuração de threads.
- Cancelamento não libera ownership enquanto o processo de render ainda estiver
  em execução.
- O master final continua sujeito a ffprobe, loudness, QA visual consolidada e
  aos gates humanos existentes.

## Recuperação

- Crash após resultado de provider e antes da QA: retomar da receipt COMPLETE e
  executar somente QA.
- Crash após QA: revalidar artefato e QA; não repetir provider nem QA.
- Callback falho não transforma um resultado COMPLETE em FAILED e não autoriza
  um novo POST.
- Output de render sem intent/receipt compatível é ambíguo e não é promovido.
- Jobs ambíguos ficam bloqueados para reconciliação; não retornam automaticamente
  à fila de submissão.

## Critérios de aceitação

1. Com uma cena rápida e uma lenta, a QA da rápida começa antes da lenta terminar.
2. Pico de provider e QA nunca excede seus limites configurados.
3. Retomada em runner novo mantém o total de submits inalterado.
4. Callback recebe somente receipts já persistidos como COMPLETE.
5. Ciclo, predecessor ausente ou job adulterado falha antes de state write,
   reserva ou provider call.
6. O entrypoint público TEST percorre Director/compiled runner/QA/render sem rede,
   sem segredo e sem chamada paga.
7. Benchmark determinístico registra pico in-flight, submits, cache e restart; a
   otimização só é aceita com contratos de mídia idênticos, concorrência maior que
   um e zero novos submits após reconstruir o runner.

## Casos negativos obrigatórios

- callback que falha após COMPLETE;
- cancelamento com workers ativos;
- receipt COMPLETE cujo arquivo ou hash mudou;
- DAG com ciclo de dois ou três nós;
- predecessor desconhecido;
- dois resumes concorrentes do mesmo runner;
- render solicitado antes de QA completa;
- alteração de qualquer campo do contrato de render durante recuperação;
- tentativa LIVE sem autoridade financeira e trust root externos.

## Integração

O ponto de entrada é o fluxo público existente. `Executor.run()` permanece o
único kernel de submissão paga. `ProductionRun` usa a fila durável de throughput;
o runner TEST aplica a mesma semântica de janela rolante com release por DAG e
revalidação de receipts. O Director mantém os gates agregados e a fachada de
render. A publicação permanece uma operação posterior, explícita e separada.
