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

Um relatório PASS só abre `WAITING_THUMBNAIL_APPROVAL` após o QA técnico de render também passar. O agente que produziu o artefato não escreve esse veredito. Além de `ProductionEvidenceQA`, `PostProductionNarrativeQA` persiste uma revisão separada de segmentos bíblicos, faixa 6–10, CTA infantil e formato VTT. Ambos os recibos PASS são pré-requisitos de `FinalRenderQA`.

## Recuperação

Todo FAIL é preservado e bloqueia promoção. Correção requer sucessor hash-bound; não há alteração in-place de roteiro, manifest, áudio, thumbnail ou recibo.

## Testes de aceitação

- pacote válido com inventário vazio: PASS;
- qualquer input ausente: FAIL fechado;
- hash publicado igual: FAIL;
- CTA proibido, referência ausente ou licença ausente: FAIL;
- manifest/captions/metadata mutados após a coleta: FAIL;
- Director não avança de `FINAL_QA` sem relatório PASS persistido.

## Encoded final-media contract (fresh EP8 revision)

`FinalRenderQA` probes the delivered container and runs a decode-only FFmpeg
loudnorm analysis on its encoded AAC track. Parsed integrated loudness must be
-16 LUFS within 1 LU; measured true peak must be at most -1 dBTP. The render
receipt persists the measurements, and QA measures again rather than trusting
target settings. Corrupt media, silence/nonfinite readings, mismatched receipts,
subtitle streams, and subtitle/drawtext burn filters fail closed. Captions remain
separate sidecars. This supplements H.264/AAC, geometry, duration and closing-hold
checks without a second encode.

The fresh revision route delivers only the thumbnail after QA. Exact thumbnail
approval enters READY_VIDEO_DELIVERY; a later run/resume delivers the frozen
video and opens WAITING_VIDEO_APPROVAL. Exact video approval stops at
WAITING_FINAL_APPROVAL. No publication operation is exposed by this route.
Executable media negatives live in tests/unit/test_final_render_qa.py; public
CLI and crash recovery coverage lives in tests/unit/test_new_ep8_revision.py.
