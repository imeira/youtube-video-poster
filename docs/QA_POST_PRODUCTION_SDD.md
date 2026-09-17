# SDD: QA independente pós-produção

## Objetivo

Definir o gate independente que bloqueia a abertura da aprovação humana quando evidência bíblica, segurança infantil, originalidade, licença ou fidelidade entre roteiro e mídia estiver ausente.

## Entradas imutáveis

- `script/script.json`: segmentos estruturados, audiência 6–10, referências e reflexão familiar.
- `compiled/manifest.json`: ativos visuais aprovados e contact sheet.
- `subtitles/captions.vtt`: transcrição temporizada separada do MP4.
- `metadata/metadata.json`: referências, thumbnail e configuração infantil.
- inventário de episódios publicados: hashes de roteiro, thumbnail, transcript e licenças declaradas.

## Veredito fechado

O relatório possui `approved`, `findings` e hashes de todos os inputs. Falha se qualquer entrada faltar, estiver mutada, ou se houver:

1. segmento bíblico sem referência;
2. audiência fora de 6–10, clickbait infantil proibido ou CTA de engajamento;
3. roteiro/thumbnail/transcript igual a item publicado;
4. licença obrigatória ausente ou não declarada;
5. asset/manifest sem vínculo com a cena e a narração;
6. ausência de captions VTT ou metadata com referência bíblica.

Um relatório PASS só abre `WAITING_THUMBNAIL_APPROVAL` após o QA técnico de render também passar. O agente que produziu o artefato não escreve esse veredito.

## Recuperação

Todo FAIL é preservado e bloqueia promoção. Correção requer sucessor hash-bound; não há alteração in-place de roteiro, manifest, áudio, thumbnail ou recibo.

## Testes de aceitação

- pacote válido com inventário vazio: PASS;
- qualquer input ausente: FAIL fechado;
- hash publicado igual: FAIL;
- CTA proibido, referência ausente ou licença ausente: FAIL;
- manifest/captions/metadata mutados após a coleta: FAIL;
- Director não avança de `FINAL_QA` sem relatório PASS persistido.
