# Arquitetura de produção em até uma hora

## Objetivo

Entregar um master **pronto para aprovação de vídeo** em no máximo 60 minutos
contados do início da execução autorizada. O prazo não inclui a decisão humana
de aprovação, a aprovação independente da thumbnail ou publicação no YouTube.

A meta é uma fronteira operacional: se uma dependência externa ameaça o prazo,
a produção degrada para a rota local já aprovada. Ela nunca transforma espera de
fila, nova tentativa ou revisão administrativa em extensão silenciosa do prazo.

## O que mudou

O fluxo anterior tratava cada microetapa de um quadro como uma nova cadeia de
scripts, autoridade, revisão e recibo. Isso conservava rastreabilidade, mas
serializava trabalho independente e multiplicava contexto operacional.

A rota rápida usa um **manifesto de execução por episódio** e eventos por
quadro dentro dele:

- um preflight e um orçamento por execução;
- ondas concorrentes para baselines independentes;
- QA individual por hash, consolidada em uma rodada e contact sheets;
- no máximo uma onda de remediação limitada pelo plano;
- um render FFmpeg local e uma QA final do master;
- recibos de geração e QA individuais continuam vinculados ao hash, mas não
  criam um executor Python, uma cadeia de planejamento e uma conversa própria
  por quadro.

Recuperações com ID remoto persistido continuam idempotentes. Uma submissão
ambígua continua bloqueada para reconciliação; o SLA não autoriza reenvio.

## Orçamento de tempo padrão

| Etapa | Limite | Regra de paralelismo |
| --- | ---: | --- |
| Congelar manifesto/preflight | 15 s | uma vez por execução |
| Gerar baselines | 225 s | até 8 simultâneos; 39 quadros em 5 ondas |
| QA de baselines | 150 s | até 8 simultâneos; cada hash é auditado |
| Remediação seletiva | 90 s | até 4 quadros reprovados, em uma onda |
| Render local | 600 s | FFmpeg com orçamento explícito de threads |
| QA final do master | 180 s | amostragem temporal + cenas críticas |
| Reserva | 120 s | absorve I/O e variação local |
| **Total previsto** | **1.380 s (23 min)** | teto operacional: 3.600 s |

O excedente entre a previsão e o teto é margem, não trabalho permitido. Não se
adicionam gerações ou tentativas apenas porque existe folga.

## Cortes de prazo e fallback

1. Antes de cada chamada paga, o planejador confirma que a onda cabe no saldo de
   tempo, orçamento e concorrência.
2. Se a fila/resultado não retorna dentro do orçamento da etapa, o job recebe
   checkpoint e entra em recuperação idempotente; a montagem segue usando uma
   imagem já aprovada quando a semântica permitir.
3. Cenas hero que não concluírem no prazo caem para movimento local sobre o
   baseline aprovado. Não há nova chamada para “salvar” o prazo.
4. Se uma remediação obrigatória não passa dentro de sua onda, a execução falha
   claramente como `SLA_BLOCKED_REQUIRED_FRAME_UNAPPROVED`; não é renderizado
   um vídeo com quadro reprovado.
5. Nunca há fallback de conteúdo, relaxamento de identidade, pulo de QA,
   redimensionamento sem revisão ou publicação automática.

## Invariantes preservados

- Narração e timestamps aprovados continuam imutáveis e hash-verificados.
- Cada quadro final continua aprovado individualmente contra sua frase,
  personagens, continuidade, anatomia e segurança infantil.
- Preços, orçamento e autorização continuam exigidos antes de chamadas pagas.
- Resultados remotos mantêm ID, WAL e recuperação sem resubmissão cega.
- Thumbnail e vídeo têm gates separados; publicar requer uma instrução humana
  posterior e explícita.

## Interface offline

O planejador expõe o SLA sem executar rede, mídia ou cobranças:

```bash
PYTHONPATH='' python -m src.hybrid \
  --one-hour-sla --scene-count 39 --provider-concurrency 8
```

O resultado contém as ondas, paralelismo, tempo previsto, limite de 3.600 s e
`provider_calls_authorized: 0`. A execução LIVE precisa de um adaptador real,
autorizações e preços vinculados ao request exato; esta interface não é uma
permissão de geração.
