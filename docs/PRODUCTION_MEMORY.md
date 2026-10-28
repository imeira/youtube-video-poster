# Memória de produção aprovada

**Versão:** 3.0
**Episódios de referência:** Episódios 1 e 4; EP11 — Jacó e Esaú fazem as pazes
**Status:** padrão obrigatório para episódios futuros

## 1. Idioma e público

- Comunicação, roteiros, relatórios e metadados em português do Brasil.
- Público principal: crianças de 6 a 10 anos.
- Estilo visual: filme de animação 3D infantil, formas arredondadas, cores acolhedoras, composição cinematográfica e segurança infantil.

## 2. Fundamentação bíblica

- Ler capítulos e versículos do tema antes de escrever o roteiro.
- Adaptar a linguagem para crianças sem inventar acontecimentos como se fossem fatos bíblicos.
- Classificar fatos, inferências e adições criativas.
- A duração não é fixa: deve ser recomendada pela complexidade da passagem, sem cortes artificiais ou preenchimento.

### Duração adaptativa e planejamento obrigatório

- Faixa normal global: **3 a 15 minutos**. Três a cinco minutos é somente a categoria curta, nunca um teto geral.
- Recomendações iniciais, sem rigidez:

  | Tipo | Duração sugerida | Exemplos |
  | --- | --- | --- |
  | Curta e objetiva | 3–5 min | A moeda perdida; Jesus acalma a tempestade |
  | Média | 6–8 min | Davi e Golias; Daniel na cova dos leões |
  | Longa, com vários acontecimentos | 8–12 min | Noé e a arca; José do Egito |
  | Especial ou extensa | 12–15 min | Nascimento, vida e ressurreição de Jesus |

- Antes de recomendar a duração, identificar os acontecimentos indispensáveis, a compreensão esperada das crianças, a quantidade de palavras, o ritmo, a retenção, as cenas necessárias, os momentos que justificariam movimento generativo e o orçamento disponível.
- Nunca reduzir a passagem para caber numa duração curta nem inserir repetições para atingir uma duração maior.
- Antes de qualquer recurso pago, apresentar e aguardar o gate de pré-produção com: duração e justificativa; quantidade aproximada de palavras; cenas e imagens; quantidade, duração unitária e segundos totais dos clipes generativos; custos mínimo, provável e máximo; referências bíblicas; riscos e alternativas.
- Para episódios curtos, usar como ponto de partida — não como quota rígida — 390–750 palavras, 20–35 imagens e até 3–5 clipes decisivos de 4–6 s. Escalar os recursos pela semântica em histórias mais longas; zero clipe generativo é válido quando nenhum momento justificar o custo.
- Os preços unitários são estimativas configuráveis e devem ser revistos quando o fornecedor mudar; o orçamento do episódio é o limite vinculante.

### Estratégia visual e orçamento

- Imagens consistentes animadas localmente com FFmpeg ou Remotion são a estratégia principal.
- Vídeo generativo é reservado a cenas HIGH/CRITICAL em que movimento real gere ganho narrativo; quantidade, duração, segundos totais e custo por clipe têm limites configuráveis.
- Montagem, legendas, música, efeitos sonoros e movimentos de câmera são locais por padrão.
- Referência econômica histórica para um episódio curto — nunca cotação vinculante —: aproximadamente 30 imagens por US$ 0,45, cinco clipes de cinco segundos por US$ 2,50 e total provável de US$ 3–6 com repetições. Descobrir preços atuais antes de usar esses números.
- Orçamento padrão do estúdio: **US$ 4 alvo, US$ 5 alerta e US$ 6 limite rígido**, salvo configuração explícita diferente para o episódio.
- Se o custo máximo estimado ultrapassar o limite, enviar pelo Telegram as alternativas: reduzir clipes, usar 100% animação local, dividir a história ou aumentar o orçamento.
- Aprovar o plano narrativo não aprova estouro de orçamento. Somente autorização humana explícita da opção de aumento permite ultrapassar o limite; silêncio e escolhas de replanejamento não autorizam cobrança.

### Contrato operacional do pipeline híbrido

- O Diretor produz uma recomendação explícita — e não uma duração fixa — baseada na complexidade bíblica, no número de acontecimentos indispensáveis, na idade de 6–10 anos, na retenção esperada e no orçamento.
- O gate pré-pago declara três cenários: custo mínimo, provável e máximo. Deve informar também palavras estimadas, cenas, imagens, clipes generativos, duração por clipe, segundos generativos totais, fontes bíblicas e justificativa da duração.
- A referência curta de 3–5 min opera normalmente com 20–35 imagens, 390–750 palavras e, quando houver ganho narrativo claro, 3–5 clipes de 4–6 s. Esses números são referências econômicas; nunca metas que obriguem cortes, repetição ou geração desnecessária.
- A prioridade é imagem consistente aprovada + animação local com FFmpeg ou Remotion. Vídeo por API entra somente em cenas decisivas e sob limites configuráveis de quantidade, duração e custo.
- Ao prever ultrapassagem do orçamento, interromper antes da cobrança e entregar no Telegram opções comparáveis: reduzir clipes, usar apenas animação local, dividir a narrativa em episódios ou aumentar o teto. Só a aprovação humana explícita de uma alternativa de aumento autoriza gasto acima do limite.

## 3. Voz e áudio

- Voz canônica: `pt-BR-ThalitaNeural`, rate `-8%`, pitch `+1Hz`.
- Gerar com `edge-tts` e `boundary="WordBoundary"`; os mesmos eventos de palavra alimentam o storyboard e o SRT.
- Manter master de narração sem perdas em WAV, com no mínimo 24 kHz mono ou 44,1 kHz estéreo; versões comprimidas são derivadas de entrega, não fontes de montagem.
- Validar pico, loudness, DC offset, silêncios editoriais e inteligibilidade. Não aceitar clipping, eco, ruído de fundo, distorção ou compressão excessiva.
- A fala deve soar natural para crianças; qualquer ajuste de velocidade é definido antes da aprovação, nunca por alongamento ou compressão posterior do áudio aprovado.
- Preservar integralmente todo áudio aprovado; verificar por hash antes da montagem.
- Storyboard deriva do áudio aprovado e de timestamps reais.
- Nunca usar intervalos uniformes para trocar imagens.

## 4. Storyboard e sincronização

- Uma cena corresponde a uma frase semântica ou mudança de ação visual concreta.
- Se o TTS dividir uma ação em duas sentenças, agrupar os limites no mesmo quadro semântico.
- Cada registro contém narração, ação visual, início, fim e duração fracionária real.
- Mapear cada quadro à palavra/frase/ação que realmente ocorre naquele trecho; não distribuir imagens pela duração total por divisão uniforme.
- Mudanças de estado narrativo — antes/depois da chuva, chegada/partida, chão molhado/seco, presença/ausência de objetos — precisam aparecer somente depois do timestamp correspondente.
- Auditar total de cenas, imagens, clipes, duração da timeline, áudio e vídeo antes da entrega.

## 5. Geração de imagens

- Provedor aprovado do episódio 1: OpenAI/Codex `gpt-image-2-medium`.
- Geração final somente por image-to-image/edição.
- Texto puro pode criar rascunho, mas nunca ativo definitivo.
- Usar a imagem anterior/rejeitada apenas como base de edição; o prompt deve descrever a frase ativa exata.
- Para personagens recorrentes, passar sempre as mesmas referências canônicas adicionais.
- Objetos recorrentes também recebem ficha/referência canônica. Preservar forma, material, proporção, quantidade de níveis/partes, abertura, acessórios e escala; nunca deixar um objeto virar casa, prédio ou outro objeto entre cenas.
- Todo prompt final explicita a ação, o estado do ambiente naquele timestamp, as identidades presentes, a referência estrutural e o que não pode aparecer.
- Seeds são auxiliares e nunca definem identidade.

## 6. Identidade de personagens

Bloquear em ficha YAML: rosto, idade, pele, olhos, cabelo, barba, corpo, proporções, roupas, cores e acessórios.

### Adão

- Ficha: `assets/characters/creation/adam/character_v1.yaml`
- Referência: `assets/characters/creation/adam/face_v1.png`
- A identidade deve ser reutilizada em toda história futura que citar Adão.

### Eva

- Ficha: `assets/characters/creation/eve/character_v1.yaml`
- Referência: `assets/characters/creation/eve/face_v1.png`
- A identidade deve ser reutilizada em toda história futura que citar Eva.

### Regras especiais de Gênesis

- Antes da formação de Adão: nenhum humano, criança, rosto, corpo, sombra ou silhueta humana.
- Deus nunca aparece como pessoa; somente luz, vento, águas ou transformação da natureza.
- Antes da queda, Adão e Eva não usam roupas ou tecidos. Usar cabelo, plantas, flores, troncos, objetos em primeiro plano, distância e enquadramento para cobertura infantil não sexualizada.

## 7. Auditoria visual

Cada imagem deve ser verificada individualmente contra:

1. frase ativa da narração;
2. ação visual planejada;
3. fichas canônicas;
4. idade, rosto, cabelo, corpo, roupa e acessórios;
5. anatomia e mãos;
6. continuidade;
7. ausência de personagens/objetos extras;
8. estilo de animação infantil;
9. segurança infantil;
10. ausência de textos e marcas-d'água;
11. estrutura e contagem exatas de níveis, partes, personagens e objetos;
12. estado correto do ambiente para o timestamp narrativo.

Score mínimo: 0,85 e `approved=true`. Regenerar somente cenas reprovadas e reauditar.

- Quando o usuário indicar um timestamp, extrair e auditar o frame real do master naquele ponto antes de corrigir.
- Depois da montagem, auditar início, meio e fim das cenas críticas no vídeo final, incluindo os extremos de Ken Burns, pans, fades e transições. Um PNG aprovado não garante um master aprovado.
- Persistir manifesto e relatório por quadro; alterações posteriores invalidam somente os dependentes e nunca autorizam sobrescrever um ativo aprovado.

## 8. Thumbnail

- Toda thumbnail final contém **três camadas textuais distintas e legíveis**:
  1. título/headline principal;
  2. gancho infantil verdadeiro, sem clickbait enganoso;
  3. referência bíblica obrigatória, por exemplo `GÊNESIS 6–9`.
- A referência bíblica é um campo obrigatório (`book_subtitle`), não parte opcional da descrição nem texto implícito na arte.
- Usar personagens e objetos canônicos, contraste forte, leitura em tamanho reduzido e área segura; impedir cortes, spoilers, texto sobre rostos e elementos não presentes no episódio.
- A thumbnail possui gate independente do vídeo. Rejeição de um artefato não aprova nem invalida silenciosamente o outro.

## 9. Movimento, transições, áudio masterizado e montagem

- Preservar duração fracionária; nunca converter timestamps para inteiro.
- Movimento cinematográfico suave: push-in, pull-out, pan e float alternados.
- Transições curtas aprovadas: 0,25 s.
- Compensar o tempo de sobreposição para que xfade não encurte a timeline.
- Vídeo padrão: 1920×1080, 30 fps, H.264, yuv420p e AAC.
- Diferença máxima entre áudio e vídeo: 0,5 s.
- Preservar o TTS original por hash e gerar uma cópia derivada para masterização.
- Antes da aprovação, medir com EBU R128 e normalizar a cópia derivada para **−16 LUFS ±1 LU**, com true peak máximo de **−1 dBTP**.
- Nunca normalizar ou substituir retroativamente um áudio já aprovado sem nova autorização.
- Decodificar integralmente cada clipe antes da montagem e o master final depois da montagem; tamanho de arquivo e exit code de encode não provam integridade.
- Vincular o master aos hashes de roteiro revisado, narração, storyboard e manifesto de imagens. Mudança upstream torna a entrega anterior `SUPERSEDED`.

## 10. Aprovação, publicação e readback

- Entregar thumbnail e vídeo separadamente no Telegram, com revisão, hashes e comandos de aprovação inequívocos.
- Silêncio nunca é aprovação. Feedback de rejeição gera nova revisão e invalidação explícita do pacote anterior.
- Nunca publicar automaticamente.
- Publicação exige thumbnail aprovada, vídeo aprovado e uma instrução explícita e separada posterior. Aprovação de pré-produção ou vídeo nunca implica autorização de publicação.
- Antes do upload, reconciliar caminhos, revisões e hashes dos arquivos aprovados para impedir publicação de um master superseded.
- Metadados devem ser atraentes e verdadeiros para crianças de 6–10 anos: título claro, descrição com gancho e resumo fiel, referência bíblica, lição, conversa em família, capítulos reais, tags relevantes e playlist canônica.
- No YouTube, definir explicitamente: canal correto, visibilidade solicitada, conteúdo para crianças, idioma/áudio pt-BR, categoria Educação, legenda pt-BR, thumbnail aprovada, declaração de conteúdo gerado/alterado por IA e playlist.
- Depois da publicação, ler de volta e registrar: ID/URL públicos, canal, visibilidade, thumbnail, título/descrição/capítulos, legenda/transcrição, SD+HD processados e associação à playlist pela página pública ou feed oficial. Um clique bem-sucedido não encerra a publicação.
- Persistir `publication_receipt`, atualizar o estado para `PUBLISHED` e liberar o lease somente após o readback.
- Doze horas depois de uma publicação real, enviar um único check-in pelo Telegram perguntando se o próximo episódio canônico pode começar. Sem resposta afirmativa, não iniciar produção.
- Vídeo rejeitado deve ser excluído quando o usuário solicitar e a exclusão deve ser verificada.

## 11. Concorrência, custo e rastreabilidade

- Exatamente um escritor/orquestrador pode alterar um episódio. Adquirir lease atômico com run ID, início e heartbeat antes de gerar, montar, entregar ou publicar.
- Reconciliar `state.json`, `costs.json`, manifestos e hashes após esperas assíncronas; se outro processo avançou o estado, parar em vez de forçar transição regressiva.
- Registrar cada gasto no ledger imediatamente após a resposta do provedor e comparar total observado com estimativa mínima/provável/máxima.
- Regenerar somente ativos reprovados; preservar e reutilizar todos os aprovados por hash.
- Toda entrega nova recebe número de revisão. Mensagem, arquivo ou recibo antigo é marcado `SUPERSEDED`, nunca reutilizado por conveniência.

## 12. Evidência histórica — não usar como quota

- 52 cenas, 52 imagens e 52 clipes.
- Duração do áudio: 241,440 s.
- Duração do vídeo: 241,333 s.
- Delta de sincronização: 0,107 s.
- Transições: 0,25 s.
- Imagens finais: `C:/HermesStudio/episodes/EP1_CREATION_REMAKE/images/`.
- QA final: `C:/HermesStudio/episodes/EP1_CREATION_REMAKE/qa/verified/`.
- Vídeo original: `C:/HermesStudio/episodes/EP1_CREATION_REMAKE/renders/final_approval.mp4`.

O Episódio 4 demonstrou que uma narrativa mais complexa pode exigir cerca de 6 min 37 s, 64 cenas narradas e zero clipe generativo, mantendo clareza e custo sob controle. Esses números provam a duração adaptativa e o pipeline híbrido; não devem ser copiados como metas para outros episódios.

## 13. Padrão reforçado pelo EP11 — identidade, cronologia e vídeo final

Estas regras são obrigatórias para qualquer novo episódio que gere imagens ou vídeo.

### Planejamento e presença histórica

- Antes de escrever prompts, construir uma matriz por cena com: referência bíblica, evento, personagens obrigatórios, personagens proibidos, estado do ambiente e fato visualmente observável. Uma ausência narrativa é uma restrição positiva de QA, não apenas uma nota no roteiro.
- Usar apenas nomes e estados temporais apropriados ao trecho. Se houver título editorial diferente do nome usado na passagem, registrar que ele é editorial; roteiro, prompts, storyboard e QA usam o nome canônico da passagem.
- Todo fato verbal que dependa de imagem deve ter requisito verificável no frame e no master: por exemplo, uma peça de roupa, uma direção de luz, a posição do sol, a condição de uma mão ou a ausência de uma pessoa.
- Não inserir motivo, morte, viagem, parentesco, reação ou presença não estabelecidos no texto. Classificar adaptação infantil separadamente de fato bíblico.

### Linha canônica e geração de imagens

- Criar e obter aprovação humana de uma linha canônica antes de gerar as cenas quando o episódio tiver personagens nomeados recorrentes. A linha deve diferenciar cada pessoa por rosto, idade, cabelo, barba, corpo, vestimenta, cor, véu/acessório e papel narrativo.
- Toda cena usa um cartão de elenco positivo derivado dessa linha: descrever somente os personagens permitidos, suas características distintivas e os coadjuvantes autorizados. Não citar nos prompts nomes de personagens proibidos, nem em negações.
- A identidade deve ser comparada entre frames adjacentes: rosto, cabelo, barba, idade, roupa, manto, acessório e atribuição de papel. Personagens com arquétipos parecidos — patriarcas idosos, mulheres veladas ou irmãos — exigem distinções reforçadas.
- Toda geração final é imutável. Um candidato reprovado, aprovado ou entregue nunca é sobrescrito; correções criam candidato, receipt, manifesto e diretório sucessores vinculados por hash.
- Antes de uma chamada paga, persistir autorização de operação única vinculada ao episódio, cena, rota, referências, hashes, preço atual, limite de chamadas, ledger e caminho de receipt. Toda resposta de provedor é saneada antes de ser persistida ou retornada.

### QA visual e congelamento

- Inspecionar individualmente, em resolução integral, todos os frames com personagem nomeado, restrição de presença, interação física, objeto narrativo ou condição temporal. Contact sheet reduzida complementa, mas não substitui, essa inspeção.
- Para poses de contato entre duas pessoas, auditar separadamente braços, mãos, ombros e cotovelos de ambas. Reprovar membro ausente, fundido ou desconectado; oclusão natural só pode ser promovida após decisão humana explícita, registrada contra o hash exato.
- Validar por cena: elenco obrigatório, ausência de figuras indevidas, ação, anatomia, segurança infantil, cronologia, continuidade, texto/marcas-d'água e o fato visual exigido pela narração.
- Congelar somente um conjunto completo de revisão: manifestos individuais, linhagem dos sucessores, manifesto total, contact sheet rotulada e hashes de todos eles. Revisar o conjunto completo e obter aprovação humana no Telegram antes de animação, renderização ou I2V.
- Uma alteração de frame, narração, storyboard, composição ou personagem invalida os dependentes. Construir um sucessor completo em ordem narrativa e repetir QA do conjunto, inclusive dos frames retidos.

### Composição, Lorena e aprovação final

- Derivar a duração e as fronteiras de cena dos timestamps reais da narração. Não usar distribuição uniforme nem encurtar uma cena para caber em clipe curto.
- Para cada frame transformado ou reutilizado, revisar no master os extremos de movimento e amostras de início, meio e fim. Para clipe generativo e fala da Lorena, verificar no master mudança real de pixels, boca em estados aberto/fechado, sincronismo, ausência de ghosting, seams, freeze, blur e deformação.
- Lorena aparece somente no encerramento, até 10 segundos, com a voz canônica `LORENA_V9`; sua fala não altera a narração oficial. A aprovação da imagem estática não substitui QA de lipsync no master.
- Antes do gate de vídeo, executar QA fail-closed do master: `ffprobe` (1080p H.264/yuv420p, AAC 48 kHz), duração, decodificação completa, loudness, intervalos pretos, transcript/SRT, frame freezes e contact sheet de composição. Vincular hashes de roteiro, áudio, storyboard, manifesto de frames, plano de composição, master, revisão e evidências de QA.
- Thumbnail, vídeo e publicação têm gates humanos independentes e sequenciais: primeiro thumbnail, depois vídeo, e publicação somente mediante comando explícito posterior. Enviar cada gate com anexo, hash, receipt e readback de entrega Telegram; silêncio não aprova.
- Não versionar credenciais, `.env`, tokens, cookies, chaves, caches, saídas provisórias ou mídia pesada sem política explícita de LFS/artefatos. Receipts compartilháveis usam somente valores saneados ou `[REDACTED]`.
