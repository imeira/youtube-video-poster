# Memória de produção aprovada

**Versão:** 2.0
**Episódios de referência:** Episódio 1 — A Criação e Episódio 4 — Noé e a Grande Arca
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
