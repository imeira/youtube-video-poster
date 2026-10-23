# SDD — runner autônomo de dois gates

## Política e autorização
**Entradas imutáveis:** política JSON versionada, hash da política, limites de episódio/mês/experimento, providers permitidos e configuração de publicação.
**Saídas fechadas:** `VALID`, `BLOCKED_POLICY` ou `BLOCKED_BUDGET`; política ausente/inválida nunca autoriza trabalho pago ou publicação.
**Recuperação:** reler o mesmo arquivo e comparar hash; o runner não altera teto, saldo ou política.
**Negativos:** política ausente, custo desconhecido, provider não permitido, teto excedido e publicação sem configuração bloqueiam.
**Integração:** `AutonomousTwoGateRunner` é criado pelo `DirectorAgent` e usa exclusivamente `Executor.run` para cada `Job`.

## Runner durável, QA e gates
**Entradas imutáveis:** pacote hash-bound, jobs hash-bound, recibos do executor e decisões QA independentes.
**Estados:** `READY -> RUNNING -> WAITING_THUMBNAIL_APPROVAL -> WAITING_VIDEO_APPROVAL -> READY_TO_PUBLISH -> PUBLISHED`; `PAUSED_RESOURCE_LIMIT` é terminal operacional. Só os dois estados `WAITING_*` representam gate humano.
**Recuperação:** `resume` relê `runner.json`; recibos `COMPLETE` são reaproveitados pelo executor e pedidos ambíguos não são reenviados.
**Negativos:** mesmo revisor do gerador, decisão com hash errado, gate fora de ordem, aprovação humana automática e publish sem os dois hashes exatos falham.
**Integração:** o Director expõe factory; CLI oferece `autonomous start/resume` sem acionar provider LIVE.

## Observabilidade, cache e diagnóstico
**Entradas imutáveis:** usage do provider/modelo, latência, custos reconciliados/pendentes/desconhecidos e hashes de entrada+configuração.
**Saídas:** métricas persistidas; tokens/cache/raciocínio separados de dinheiro e quota. Cache de TTS/render só retorna bytes com chave compatível. QA FAIL registra códigos concretos e constraints de correção.
**Recuperação:** métricas e cache são append/idempotentes por request/chave; hash divergente resulta em miss.
**Negativos:** quota não vira custo, custo desconhecido não vira zero, cache incompatível não é reutilizado e FAIL genérico é recusado.
