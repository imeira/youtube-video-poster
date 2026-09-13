# Arquitetura híbrida rápida

## Duração adaptativa e encerramento obrigatório

A duração não é fixa: a pré-produção bíblica escolhe de **180 a 900 segundos**
(3--15 min) e registra acontecimentos indispensáveis, palavras da narração,
cenas e custo antes de qualquer job. O plano reserva obrigatoriamente **3--5 s**
para uma lição ou resolução emocional/teológica infantil; não é permitido acabar
em um corte de ação.

O planejador offline aceita essa decisão sem fazer chamadas de provedor:

```bash
python -m src.hybrid --duration-seconds 480 --essential-events 9 \
  --narration-words 1040 --scene-count 39 --closing-seconds 4 --images 2.024
```

Com 39 cenas, os 12 hero clips de 5 s usam 60 s; as 27 janelas restantes são
compostas/localmente animadas por FFmpeg. A reserva de 15 s de retry permanece
condicional e requer preflight, autorização unitária e orçamento disponível.

Implementação opt-in em `src/hybrid/`, sem imports de SDK, acesso a credenciais,
geração durante planejamento, acoplamento a R027 ou episódios específicos.
O comando legado `studio` não foi alterado para iniciar esta arquitetura sozinho.

## Contrato e uso

`config.yaml:hybrid_fast` e `Config.load()` definem capacidade **máxima**, não fila:
39 primeiras versões + 39 alternativas + 10 correções + 4 thumbnails.
O planner sempre retorna `requests: []` e `execution_authorized: false`.
Alternativas são pedidos explícitos; correções exigem QA reprovada do predecessor.
Uma primeira versão alterada não pode disfarçar uma correção.

```powershell
$env:PYTHONPATH=''
& C:/Users/meira/hermes-studio-venv/Scripts/python.exe -m src.hybrid --local-only
& C:/Users/meira/hermes-studio-venv/Scripts/python.exe -m src.hybrid --committed 7.98 --images 1.36
```

O segundo comando apresenta US$ 12,46 e `WAITING_BUDGET_APPROVAL` pelo BudgetGuard
existente. O cálculo conservador inclui US$ 0,78 de retries, totalizando US$ 13,24.
`committed` representa compromissos adicionais ao gasto já informado no ledger;
não se deve informar o mesmo gasto nos dois lugares. Local-only custa zero API;
master e rerender também custam zero API. Um orçamento de US$ 10 não autoriza
US$ 7,98 + US$ 1,36 + US$ 3,12.

`--candidates arquivo.json` aceita uma lista de objetos `Hero` com `scene_id`,
`action`, `impact`, `movement` e `start`. A seleção usa impacto vezes necessidade
de movimento, desempata deterministicamente e reordena os selecionados na timeline.
Não há seleção por intervalos uniformes. São até 12 slots hero de 5 s, 720p, com
pool compartilhado de até 3 retries condicionais à QA. O planner mostra a reserva
integral de 60 s mesmo quando há menos candidatos; não preenche slots por geração.

O planejamento adaptativo de 3–15 minutos em `src/agents/duration_planner.py`
e `src/content/roadmap.py` foi preservado sem alterações. O planejamento híbrido
consome cenas sem impor duração ao roteiro e sem adicionar narração de preenchimento.

## Preços e providers

Preços e esquemas foram conferidos em documentação oficial em 2026-09-09. Eles
continuam sendo estimativas configuradas: cada execução LIVE exige evidência de
preço vigente, vinculada ao pedido, imediatamente antes da reserva.

- FLUX: `fal-ai/flux-2/klein/9b/edit`, US$ 0,011 por MP de entrada **e** saída.
  A página oficial informa que inputs são redimensionados para 1 MP; por isso cada
  input e a saída têm piso conservador de 1 MP no estimador. Um input e uma saída
  de até 1 MP dão US$ 0,022. Fonte:
  <https://fal.ai/models/fal-ai/flux-2/klein/9b/edit>.
- RunPod: `seedance-v1-5-pro-i2v`, 5 s, 720p, US$ 0,052/s,
  `generate_audio: false`. Doze clips: US$ 3,12; três retries: US$ 0,78. A
  documentação oficial também confirma o endpoint `runsync`, 4–12 s, 480p/720p
  e US$ 0,26 por clip 720p de 5 s:
  <https://docs.runpod.io/public-endpoints/models/seedance-1-5-pro>.
- GPT Image 2.5 Flare existe na API oficial como `gpt-image-2.5-flare`, inclusive
  para edição. Nesta implementação continua sendo escolha explícita, com endpoint
  e tabela de preços vazios por padrão; não há fallback nem preço presumido.
  Fonte: <https://developers.openai.com/api/docs/models/gpt-image-2.5-flare>.

`image_job()` e `video_job()` em `providers.py` constroem pedidos explícitos.
Referências são caminhos de arquivos congelados, não URLs inventadas. O adapter
implantado deve transportar exatamente esses bytes e converter os handles locais
para inputs aceitos pelo endpoint, incluindo referências e metadados de geometria.

`execution.Provider` é a interface assíncrona de deployment: `mode`, `submit()` e
`recover()`. Não há transporte de rede embutido. Um adapter LIVE deve registrar o
ID remoto imediatamente via `checkpoint(provider_id=...)` e cada resultado parcial
via `checkpoint(partial=...)`; `recover` só consulta/recupera resultados, jamais
submete novamente. Credenciais devem ser injetadas no deployment, fora dos manifests.

Para executar LIVE, o chamador deve fornecer `Authorization` de um revisor humano
e `Price` com evidência oficial vigente: ambos ligados ao `request_id` exato,
endpoint, valor e validade. A autorização é revalidada depois da espera na fila.
Esses objetos são fronteira confiável de integração: a UI/serviço de aprovação
deve autenticar o revisor e a evidência; não se aceita conteúdo de modelo como
autorização humana. O executor não verifica autenticidade de documentos online.

**Estado de integração LIVE:** os nomes, payloads e preços configurados foram
comparados com as fontes oficiais acima, e o executor possui gates/recovery
testados com providers injetados. Porém, este branch **não contém adapters HTTP,
upload/download ou autenticação reais de fal, RunPod ou OpenAI**, e nenhum smoke
test credenciado foi executado para evitar cobrança. Portanto, LIVE ponta a ponta
permanece não validado e não deve ser descrito como pronto para produção. Antes de
uso real ainda é necessário implementar os adapters, testar staging dos bytes,
checkpoint/recovery remoto, download verificado e reconciliar custo retornado —
sempre atrás de autorização humana e evidência de preço frescas.

## Aprovação, persistência e recuperação

1. `FrozenAsset.approve(path, reviewer, mode)` congela cada referência por SHA-256.
2. `contact_sheet(assets, output)` produz uma imagem real e binding ordenado dos
   hashes. Sua geração não é aprovação.
3. Após revisão humana individual e do conjunto, `Manifest.freeze()` vincula
   referências, contact sheet, reviewer e TEST/LIVE. `save/load` preservam checksum.
4. O pedido explícito contém manifest, cena, endpoint, payload, custo máximo e
   predecessor. O SHA-256 canônico desses dados é seu ID estável. Alterações
   invalidam autorizações anteriores. O executor faz snapshot do pedido antes de await.
5. SQLite com `synchronous=FULL`, WAL e transações `BEGIN IMMEDIATE` persiste
   reserva e intent **antes** da submissão. Valores Decimal evitam saldo negativo
   por arredondamento; o BudgetGuard existente é consultado dentro da reserva.
6. Semáforo e locks de bytes do SO limitam concorrência inclusive entre executores
   usando o mesmo banco. Locks se soltam com a morte do processo. O orçamento e o
   limite de concorrência ficam fixados no banco; mudança silenciosa é rejeitada.
7. Receipts guardam ID, modo, manifest, request, ID remoto, resultado/hash, custo
   efetivo e evidências LIVE. Reservas pendentes não desaparecem após crash.
8. Resultado completo é reutilizado depois de verificar seu hash. ID remoto ou
   parcial existente usa apenas `recover`. Intent sem ID/parcial bloqueia por
   reconciliação: não há retry de timeout nem resubmissão cega.
9. `Executor.qa(id, result_sha256, False, reviewer)` vincula rejeição ao resultado
   exato. Um predecessor só admite um sucessor e deve ser da mesma cena/categoria
   compatível. O pool é cumulativo, persistente e compartilhado. Overrun real é
   registrado e bloqueia trabalho posterior; custo acima da reserva não é ocultado.

Use um banco por produção/conta e encaminhe todos os seus pedidos por ele. Dois
bancos diferentes não compartilham orçamento. TEST/LIVE têm IDs e contabilidade
separados e não podem compartilhar assets/aprovações entre modos.

## Renderização local

`LocalRenderer.render(scenes, manifest, audio, srt, output, hold=4)` não recebe
provider nem orçamento de API. Recebe somente inputs aprovados e saída nova.
`Scene(image, seconds)` produz zoom/pan real com FFmpeg. `Scene(image, 5, clip=...)`
usa um hero aprovado real, verificado por ffprobe; LIVE exige 1280×720.

Cada cena, exceto a última, recebe 0,25 s extras. Os offsets dos xfades são limites
cumulativos quantizados ao frame mais próximo, evitando acumular arredondamento por
cena: a duração da narração não diminui. A timeline deve coincidir com o áudio
aprovado, com tolerância de um frame. O final recebe 3–5 s por `tpad` clonando o
frame final; o filtro de legendas roda depois do `tpad`, portanto texto encerrado
com a narração não é congelado no hold.

Quando existe SRT aprovado, as legendas são queimadas pelo filtro `subtitles` a
partir de um snapshot byte a byte e validado. `srt=None` omite legendas em vez de
inferi-las ou fabricá-las. O áudio tem snapshot
com hash conferido e é muxado usando `-c:a copy`, sem filtro, normalização,
padding, `-shortest` ou reescrita do arquivo-fonte. Após a narração, o vídeo
permanece no hold; não se acrescentam amostras de silêncio ao áudio aprovado.

O container recomendado é MKV para áudio PCM/WAV imutável. MP4 exige áudio já
aprovado em codec compatível (por exemplo AAC); o renderer falha se o mux não
suportar o codec, sem transcodificá-lo implicitamente. A saída e seus metadados
são verificados com ffprobe e recebe receipt com hashes e `api_cost: 0`.
Rerender usa outra saída e reutiliza os mesmos assets sem chamadas externas.

## Reuso seletivo e limites da validação

Foram lidos somente trechos pertinentes das referências:

- `hybrid-adaptive-budget/src/pipeline/hybrid_planner.py`: separação capacidade/pedido,
  geometria, candidatos semânticos; taxas/endpoints antigos não foram copiados.
- `hybrid-adaptive-budget/src/providers/video/runpod_seedance_provider.py`: contrato
  de payload 720p/5s/sem áudio; transporte HTTP não foi copiado.
- `production-throughput-controller/src/throughput/controller.py`: intent durável,
  hashing canônico e gravação com flush/fsync. Não foram copiados discovery,
  infraestrutura de controlador, leases/PIDs ou acoplamentos a episódios.

O diretório protegido de episódios não foi modificado por esta entrega. `.env` e
segredos não foram lidos. O runner usa um diretório temporário para os testes
legados de episódios.

A primeira execução em um sandbox restrito não conseguiu resolver FFmpeg/ffprobe.
Na validação final, os executáveis reais ficaram disponíveis e os quatro testes do
módulo de mídia executaram. Não existem bytes MP4 fabricados nos testes novos: eles
geram imagens com Pillow e áudio/vídeo com FFmpeg, conferem duração, pixels das
legendas, ausência de legenda no hold e igualdade do PCM decodificado.

Na primeira execução ampla, dois testes legados tentaram leitura de `.env` e foram
barrados pelo audit hook; SD tentou obter modelo e TTS chegou à resolução DNS via
c-ares, terminando em timeout, sem resultado de serviço. Isso revelou que o audit
hook de sockets Python sozinho não cobre DNS nativo. O runner foi corrigido para
bloquear HTTP no aiohttp antes do DNS, ativar HF/Transformers offline e marcar os
dois testes de integração externos como skips explícitos. Não houve geração paga.
Não se afirma que essa primeira tentativa legada de DNS não ocorreu.

## Registro TDD e comandos

Todos os testes abaixo foram lançados com:

```powershell
$env:PYTHONPATH=''
& C:/Users/meira/hermes-studio-venv/Scripts/python.exe scripts/run_offline_tests.py <alvo> -q
```

| Ciclo | Alvo/comando | RED observado antes do código | Resultado após implementação |
|---|---|---|---|
| 1 | `tests/unit/test_hybrid_plan.py` | collection error: `No module named src.hybrid` | 4 passed |
| 2 | `tests/unit/test_hybrid_execution.py` | collection error: `No module named src.hybrid.artifacts` | 8 passed |
| 3 | `tests/unit/test_hybrid_render.py` | collection error: `No module named src.hybrid.render` | 2 skipped: FFmpeg/ffprobe indisponíveis; não é GREEN de mídia |
| 4 | `tests/unit/test_hybrid_execution.py` | 2 failed, 10 passed: alteração de first pass não bloqueava; concorrência 4 em vez de 2 | 12 passed |
| 5 | `tests/unit/test_hybrid_providers.py` | collection error: `No module named src.hybrid.providers` | 2 passed |
| 6 | `tests/unit/test_hybrid_plan.py` | 2 failed, 4 passed: guard ausente retornava None; CLI sem `__main__` | 6 passed |
| 7 | `tests/unit/test_hybrid_contracts.py` | collection error: `cannot import name timing` | 2 passed |
| 8 | `test_real_local_render_without_unapproved_subtitles` | `AttributeError` ao usar `srt=None` | 1 passed; render real sem legenda e áudio idêntico |

Incidente intermediário do ciclo 2: bloqueio de `socket.bind` impediu a criação
do pipe interno de wakeup do asyncio no Windows (1 passed, 7 errors). O runner
passou a permitir somente o `socketpair()` interno, mantendo bloqueio externo.
Nova execução: 8 passed.

Primeira suíte ampla: `tests/unit -q`, **4 failed, 228 passed, 2 skipped, 9 errors**
(40,67 s): dependências externas, segredo bloqueado e FFmpeg inacessível.
Após isolamento explícito: `tests/unit -q -rs`, **231 passed, 14 skipped** (14,42 s).
Os skips não foram convertidos em sucessos; o fixture-free teste de custo de
thumbnail foi liberado depois para evitar skip excessivo.

Ruff global não estava no PATH e `python -m ruff --version` também não o encontrou.
Foi localizado executável existente no cache local, sem instalação/download:
`C:/Users/meira/AppData/Local/uv/cache/archive-v0/OgjvXqDXkkyMjYxn/ruff-0.16.6.data/scripts/ruff.exe`.
Primeiro `ruff check`: 14 avisos (imports, Decimal inteiro e check explícito de
subprocess). Após ajustes de subprocess, `ruff check --fix src/hybrid
scripts/run_offline_tests.py tests/unit/test_hybrid_*.py`: 13 corrigidos, 0 restantes.

### Validação final

```powershell
$env:PYTHONPATH=''
& C:/Users/meira/hermes-studio-venv/Scripts/python.exe scripts/run_offline_tests.py tests/unit -q -rs
& C:/Users/meira/hermes-studio-venv/Scripts/python.exe -m compileall -q src tests/unit scripts/run_offline_tests.py
& C:/Users/meira/AppData/Local/uv/cache/archive-v0/OgjvXqDXkkyMjYxn/ruff-0.16.6.data/scripts/ruff.exe check src/hybrid scripts/run_offline_tests.py tests/unit/test_hybrid_*.py
```

- Suíte unitária completa com política offline após a integração: **253 passed,
  2 skipped**, 25,95 s, nenhum failure/error. FFmpeg/ffprobe reais foram encontrados
  e os quatro testes do módulo de mídia executaram; os dois skips são integrações legadas externas
  (TTS e download de modelo).
- `compileall`: exit 0, sem erros.
- Ruff 0.16.6: `All checks passed!`, exit 0. Foi usado o binário já presente no
  cache local porque não havia comando global no PATH. Não houve instalação.
- `ruff format --check`: 15 arquivos formatados, sem pendências, antes da suíte final.
- Nenhuma alteração em `duration_planner.py` ou `roadmap.py`; seus testes estão
  incluídos na suíte completa. Preço LIVE permanece obrigatoriamente sem autorização
  automática.

### Integração de planejamento EP8

`docs/EP8_HYBRID_HERO_CANDIDATES.json` registra 13 candidatos semânticos derivados
das ações e tempos do storyboard V10 real. O planner selecionou 12 por impacto e
movimento em `docs/EP8_HYBRID_BUDGET_PLAN.json`, mas deixou `requests: []`,
`execution_authorized: false` e `WAITING_BUDGET_APPROVAL`: US$ 8,08 comprometidos +
US$ 1,36 projetados + US$ 3,12 de heróis = US$ 12,56; com reserva de três retries,
US$ 13,34. `docs/EP8_HYBRID_LOCAL_ONLY_PLAN.json` comprova a rota sem API em US$ 9,44,
também sem autorização automática. Nenhum clip Seedance foi submetido.
