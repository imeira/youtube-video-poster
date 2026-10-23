# SDD — runner autônomo de dois gates

## Política e autorização
**Entradas imutáveis:** política JSON versionada, hash da política, limites de episódio/mês/experimento, providers permitidos e configuração de publicação.
**Saídas fechadas:** `VALID`, `BLOCKED_POLICY` ou `BLOCKED_BUDGET`; política ausente/inválida nunca autoriza trabalho pago ou publicação. `publication.enabled: false` bloqueia somente publicação, não a preparação já autorizada.
**Recuperação:** reler o mesmo arquivo e comparar hash; o runner não altera teto, saldo ou política.
**Negativos:** política ausente, custo desconhecido, provider não permitido, teto excedido e publicação sem configuração bloqueiam.
**Integração:** `AutonomousTwoGateRunner` é criado pelo `DirectorAgent` e usa exclusivamente `Executor.run` para cada `Job`.

## Runner durável, QA e gates
**Entradas imutáveis:** pacote hash-bound, jobs hash-bound, recibos do executor e decisões QA independentes.
**Estados:** `READY -> RUNNING -> WAITING_THUMBNAIL_APPROVAL -> WAITING_VIDEO_APPROVAL -> READY_TO_PUBLISH -> PUBLISHED`; `PAUSED_RESOURCE_LIMIT` é terminal operacional. Só os dois estados `WAITING_*` representam gate humano.
**Recuperação:** `resume` relê `runner.json`, aceita `RUNNING` abandonado, exige os mesmos `Job` hash-bound após processo novo e reaproveita só recibos `COMPLETE` cujos bytes ainda conferem. Pedidos ambíguos não são reenviados.
**Negativos:** identidade de QA ausente, QA FAIL, pacote/job/hash alterado, gate fora de ordem, mídia não-decodificável, mutação pós-gate e publish sem os dois hashes exatos falham. `PUBLISH_INTENT` é write-ahead e bloqueia replay após crash até reconciliação remota.
**Integração demonstrada:** `python -m src.hybrid.autonomous_cli start|status|resume` gera somente mídia válida marcada `TEST` com FFmpeg/Pillow; não chama provider, Telegram ou YouTube e não declara aprovação humana. O runner bloqueia LIVE; a integração LIVE end-to-end continua no caminho de produção existente e não foi qualificada por este SDD.

## Observabilidade, cache e diagnóstico
**Entradas imutáveis:** usage do provider/modelo, latência, custos reconciliados/pendentes/desconhecidos e hashes de entrada+configuração.
**Saídas:** métricas persistidas; tokens/cache/raciocínio separados de dinheiro e quota. Cache de TTS/render só retorna bytes com chave compatível. QA FAIL registra códigos concretos e constraints de correção.
**Recuperação:** o cache persiste hash de saída e rejeita corrupção; métricas JSON atuais são uma projeção local, não evidência de cobrança do provider. O ledger autoritativo de custos permanece `Executor.sync_cost_ledger`/`CostLedger`; reconciliação mensal e de experimentos entre workers ainda é uma lacuna.
**Negativos:** quota não vira custo, custo desconhecido não vira zero, cache incompatível não é reutilizado e FAIL genérico é recusado.

## Lacunas explícitas
- Não há política financeira permanente/configuração de publicação para esta execução; nenhum gasto ou publicação é autorizado.
- O harness TEST não substitui roteiro, áudio, frames, render, QA e entrega de um episódio LIVE real; ele só prova o contrato de CLI, preservação do prompt, namespace e mídia tecnicamente decodificável.
- `DeliveryController`/`PublicationController` permanecem os únicos controllers para entrega/publicação LIVE; a ligação completa ao novo runner requer credenciais, política e readback real, portanto fica fail-closed.
