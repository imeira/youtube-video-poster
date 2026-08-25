# Memória de produção aprovada

**Versão:** 1.1
**Episódio de referência:** Episódio 1 — A Criação  
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
  - curta e objetiva: 3–5 min;
  - média: 6–8 min;
  - longa, com vários acontecimentos: 8–12 min;
  - especial ou extensa: 12–15 min.
- A decisão considera acontecimentos indispensáveis, compreensão de crianças de 6–10 anos, ritmo, retenção e orçamento.
- Nunca reduzir a passagem para caber numa duração curta nem inserir repetições para atingir uma duração maior.
- Antes de qualquer recurso pago, apresentar: duração e justificativa; palavras; cenas e imagens; quantidade, duração e segundos totais dos clipes generativos; custos mínimo, provável e máximo; referências bíblicas.
- Para episódios curtos, usar como ponto de partida — não como quota rígida — 390–750 palavras, 20–35 imagens e 3–5 clipes decisivos de 4–6 s.
- Os preços unitários são estimativas configuráveis e devem ser revistos quando o fornecedor mudar; o orçamento do episódio é o limite vinculante.

### Estratégia visual e orçamento

- Imagens consistentes animadas localmente com FFmpeg ou Remotion são a estratégia principal.
- Vídeo generativo é reservado a cenas HIGH/CRITICAL em que movimento real gere ganho narrativo; quantidade, duração, segundos totais e custo por clipe têm limites configuráveis.
- Se o custo máximo estimado ultrapassar o limite, enviar pelo Telegram as alternativas: reduzir clipes, usar 100% animação local, dividir a história ou aumentar o orçamento.
- Aprovar o plano narrativo não aprova estouro de orçamento. Somente autorização humana explícita da opção de aumento permite ultrapassar o limite; silêncio e escolhas de replanejamento não autorizam cobrança.

## 3. Voz e áudio

- Voz canônica: `pt-BR-ThalitaNeural`, rate `-8%`, pitch `+1Hz`.
- Preservar integralmente todo áudio aprovado; verificar por hash antes da montagem.
- Storyboard deriva do áudio aprovado e de timestamps reais.
- Nunca usar intervalos uniformes para trocar imagens.

## 4. Storyboard e sincronização

- Uma cena corresponde a uma frase semântica ou mudança de ação visual concreta.
- Se o TTS dividir uma ação em duas sentenças, agrupar os limites no mesmo quadro semântico.
- Cada registro contém narração, ação visual, início, fim e duração fracionária real.
- Auditar total de cenas, imagens, clipes, duração da timeline, áudio e vídeo antes da entrega.

## 5. Geração de imagens

- Provedor aprovado do episódio 1: OpenAI/Codex `gpt-image-2-medium`.
- Geração final somente por image-to-image/edição.
- Texto puro pode criar rascunho, mas nunca ativo definitivo.
- Usar a imagem anterior/rejeitada apenas como base de edição; o prompt deve descrever a frase ativa exata.
- Para personagens recorrentes, passar sempre as mesmas referências canônicas adicionais.
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
10. ausência de textos e marcas-d'água.

Score mínimo: 0,85 e `approved=true`. Regenerar somente cenas reprovadas e reauditar.

## 8. Movimento, transições, áudio masterizado e montagem

- Preservar duração fracionária; nunca converter timestamps para inteiro.
- Movimento cinematográfico suave: push-in, pull-out, pan e float alternados.
- Transições curtas aprovadas: 0,25 s.
- Compensar o tempo de sobreposição para que xfade não encurte a timeline.
- Vídeo padrão: 1920×1080, 30 fps, H.264, yuv420p e AAC.
- Diferença máxima entre áudio e vídeo: 0,5 s.
- Preservar o TTS original por hash e gerar uma cópia derivada para masterização.
- Antes da aprovação, medir com EBU R128 e normalizar a cópia derivada para **−16 LUFS ±1 LU**, com true peak máximo de **−1 dBTP**.
- Nunca normalizar ou substituir retroativamente um áudio já aprovado sem nova autorização.

## 9. Aprovação e publicação

- Entregar o vídeo no Telegram somente para aprovação.
- Nunca publicar automaticamente.
- Publicação exige instrução explícita e separada após aprovação do vídeo.
- Vídeo rejeitado deve ser excluído quando o usuário solicitar e a exclusão deve ser verificada.

## 10. Evidência do episódio aprovado

- 52 cenas, 52 imagens e 52 clipes.
- Duração do áudio: 241,440 s.
- Duração do vídeo: 241,333 s.
- Delta de sincronização: 0,107 s.
- Transições: 0,25 s.
- Imagens finais: `C:/HermesStudio/episodes/EP1_CREATION_REMAKE/images/`.
- QA final: `C:/HermesStudio/episodes/EP1_CREATION_REMAKE/qa/verified/`.
- Vídeo original: `C:/HermesStudio/episodes/EP1_CREATION_REMAKE/renders/final_approval.mp4`.
