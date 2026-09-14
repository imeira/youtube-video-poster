# Control plane de produção

`src.hybrid.control.ControlPlane` é o caminho único de agendamento para novas
execuções. Ele elimina a proliferação de scripts e artefatos por microetapa sem
relaxar as fronteiras transacionais.

## Fonte de verdade

Cada episódio usa um SQLite exclusivo, com `synchronous=FULL`, ligado a um
`EpisodeSpec` checksummed: identificador, modo, quadros ativos, storyboard,
áudio e teto de custo. O banco recusa a abertura com um spec diferente; uma
mudança exige migração explícita.

`state.json` e `costs.json` são exportações/readbacks de compatibilidade,
nunca fontes concorrentes de autoridade.

## Estados por quadro

```text
BASELINE elegível
  → candidate(hash, producer)
  → QA independente e imutável(hash, verdict, reviewer)
  → APPROVED | REMEDIATE elegível
```

O controller emite somente ações elegíveis e determinísticas. O consumo é
idempotente por `action_id`, derivado de spec, tipo, quadro e hash predecessor.

- `BASELINE`: somente sem candidato;
- `REMEDIATE`: somente para QA reprovada e vinculada ao hash do candidato;
- `RENDER`: somente quando todos os quadros ativos têm QA aprovada.

O controller não gera mídia nem acessa rede. A chamada paga segue no
`Executor.run()`, que mantém intenção write-ahead, locks, request ID e
recuperação sem resubmissão.

## QA e promoção

A QA registra hash do candidato e identidade do produtor/revisor. O mesmo
identificador não pode produzir e revisar. A atestação não pode ser alterada.
A próxima evolução integra essa atestação ao manifesto de readiness exigido
pelo renderer; durante a migração, o pipeline legado continua congelado.

## Migração

1. Não alterar artefatos históricos nem executores já consumidos.
2. Criar um `EpisodeSpec` para o próximo episódio ou para um quadro ainda não
   submetido.
3. Executar em shadow mode e comparar ações elegíveis com os manifestos
   existentes.
4. Ativar o controller como único escritor apenas depois do readback.

R031 do EP8 permanece fora dessa migração: seu request e recuperação já foram
consumidos e nunca devem receber novo submit/retry.
