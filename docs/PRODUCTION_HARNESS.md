# Harness de produção auditável

## Escopo

Este documento define o caminho executável para um episódio e a recuperação segura de falhas. O fluxo não autoriza publicação automática nem substitui os gates humanos de thumbnail, vídeo e publicação.

## Pré-requisitos verificáveis

1. `request.json`, pesquisa e plano existem e o plano possui aprovação humana persistida.
2. O roteiro estruturado em `script/script.json` passou `ScriptQAAgent`.
3. O áudio aprovado, WordBoundary e storyboard com timestamps semânticos existem.
4. Character Bible, Visual Bible e referências image-to-image estão congelados antes da ativação compilada.
5. Para LIVE, cada job possui preço atual e `Authorization` exatamente vinculados ao request ID. Credenciais nunca entram em arquivos de episódio.

## Caminho compilado

1. `DirectorAgent.activate_compiled_production()` somente aceita `GENERATING_IMAGES` e os ativos congelados.
2. `DirectorAgent.dispatch_compiled_baselines()` encaminha provider, preços e autoridades ao `Executor`; no modo LIVE o executor bloqueia qualquer job sem ambos.
3. O pipeline prepara pacotes técnicos e exige QA visual independente para cada hash de candidato.
4. `DirectorAgent.record_compiled_visual_qa()` promove exclusivamente ativos aprovados. Uma reprovação preserva o FAIL e exige sucessor de remediação.
5. `DirectorAgent.complete_compiled_final_qa()` executa, nesta ordem: render compilado sem legenda embutida, captions/thumbnail/metadata derivados do pacote congelado, auditoria de evidência e QA final independente.
6. Somente após PASS a máquina entra em `WAITING_THUMBNAIL_APPROVAL`.
7. `deliver_for_approval()` envia thumbnail e vídeo em operações separadas, com recibos hash-bound. Timeout ou falha não é sucesso e não permite retry cego.
8. `confirm_delivered_artifact()` exige comando humano exato: thumbnail aprovada abre o gate do vídeo; vídeo aprovado abre o gate final.
9. `publish_after_explicit_instruction()` exige um comando humano posterior, recibos atuais de thumbnail/vídeo, hash atual de metadata e readback remoto antes de `PUBLISHED`.

## Recuperação

- `Executor` recupera pedido com ID durável exclusivamente por `recover()`. `INTENT` sem ID ou parcial é ambíguo e bloqueia nova submissão.
- R029, R030 e R031 consumidos não podem ser reenviados. Qualquer correção inicia um sucessor com novo preflight, orçamento e autoridade.
- Se thumbnail ou vídeo forem rejeitados, `reject_delivered_artifact()` marca o artefato e dependentes como `SUPERSEDED`, preserva bytes/hashes e reabre `ASSEMBLING`; uma aprovação futura exige sucessor ativo.
- Não sobrescreva mídia, áudio, manifestos, QA ou recibos aprovados/rejeitados.

## Comandos de verificação

```bash
uv run --extra dev pytest tests/unit -m "not slow" -q
uvx ruff check $(git diff --name-only origin/master...HEAD -- '*.py')
git diff --check
```

A CI deve estar concluída com sucesso no commit remoto do PR antes de merge. Uma suíte local verde não é autorização de publicação.
