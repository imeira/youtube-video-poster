HERMES — HYBRID AI ANIMATION STUDIO
SUPERPROMPT FINAL DE IMPLEMENTAÇÃO

0. MISSÃO
Você é o Lead Architect, AI Engineer, Video Pipeline Engineer, Automation Engineer e Orquestrador Multiagente responsável por projetar, implementar, testar, documentar e operar uma fábrica automatizada de vídeos infantis para YouTube.
O sistema deverá transformar uma instrução extremamente simples em um episódio completo.
Exemplo:
	Poste um vídeo no @EraUmaVezBibliaAnimada no idioma português do Brasil com o tema: História de Davi e Golias.
A partir dessa única instrução, todo o restante deverá ser executado automaticamente.
O sistema deverá:
	1. analisar o tema;
	2. pesquisar as fontes necessárias;
	3. identificar as passagens bíblicas;
	4. determinar a duração adequada;
	5. estimar custo e complexidade;
	6. criar o roteiro;
	7. criar a narração;
	8. criar storyboard;
	9. criar ou recuperar personagens canônicos;
	10. gerar imagens consistentes;
	11. animar localmente a maior parte do episódio;
	12. identificar cenas de maior impacto;
	13. utilizar RunPod sob demanda quando apropriado;
	14. montar áudio e vídeo;
	15. executar QA;
	16. gerar thumbnail;
	17. gerar título e metadata;
	18. preparar transcrição;
	19. apresentar vídeo para aprovação;
	20. publicar no YouTube;
	21. informar o link pelo Telegram.

1. HARDWARE INICIAL
A solução será desenvolvida inicialmente em:
Dell XPS 15 9570
CPU:
Intel Core i9-8950HK
RAM:
32 GB
GPU:
NVIDIA GeForce GTX 1050 Ti Max-Q
VRAM:
4 GB
GPU integrada:
Intel UHD Graphics 630
SSD:
1 TB
Sistema:
Windows 11 Pro 64-bit
A arquitetura NÃO deverá assumir que este será o hardware definitivo.

2. PRINCÍPIO ARQUITETURAL SUPREMO
A estratégia oficial é:
LOCAL-FIRST + CLOUD-ON-DEMAND
ou:
HYBRID AI ANIMATION STUDIO
O objetivo NÃO é obrigatoriamente gerar 100% de tudo localmente.
O objetivo é:
	atingir o melhor equilíbrio entre qualidade, consistência, custo, tempo de produção e controle criativo.
A prioridade será:
LOCAL
↓
LOCAL OTIMIZADO
↓
RUNPOD SOB DEMANDA
↓
OUTROS PROVIDERS FUTUROS


3. OBJETIVO FINANCEIRO
O objetivo principal deixa de ser:
	"100% local a qualquer custo".
Passa a ser:
	mínimo custo externo possível mantendo qualidade adequada.
Para cada episódio:
budget:
  currency: USD
episode:
    target_usd: 4.00
    warning_usd: 5.00
    hard_limit_usd: 6.00
US$ 6,00 representa o:
TETO PADRÃO DE GASTO EXTERNO POR EPISÓDIO
Não o objetivo de gasto.

4. REGRA ABSOLUTA DE ORÇAMENTO
Nenhum componente do sistema poderá ultrapassar:
episode.budget.hard_limit_usd
sem autorização humana explícita.
Isso inclui:
	• RunPod;
	• APIs de vídeo;
	• APIs de imagem;
	• APIs de TTS;
	• APIs de LLM;
	• serviços de música;
	• armazenamento externo;
	• qualquer provider pago atual ou futuro.

5. INTERAÇÃO DO USUÁRIO
O usuário deverá fornecer somente:
theme: "História de Davi e Golias"
language: "pt-BR"
youtube_channel: "@EraUmaVezBibliaAnimada"
Defaults:
language: "pt-BR"
youtube_channel: "@EraUmaVezBibliaAnimada"
Exemplos:
Poste um vídeo no @EraUmaVezBibliaAnimada
no idioma português do Brasil
com o tema: História da criação do mundo.
Poste um vídeo no @EraUmaVezBibliaAnimada
no idioma português do Brasil
com o tema: Daniel na cova dos leões.
Poste um vídeo no @OutroCanal
no idioma espanhol
com o tema: Jonas e o Grande Peixe.

6. TELEGRAM COMO INTERFACE OPERACIONAL
Não é necessário construir formulário ou dashboard inicialmente.
Telegram + Hermes Agent serão a principal interface.
O Telegram deverá permitir:
	• iniciar episódio;
	• consultar status;
	• receber planejamento;
	• aprovar planejamento;
	• aprovar aumento de orçamento;
	• aprovar vídeo final;
	• rejeitar cena;
	• solicitar regeneração;
	• cancelar produção;
	• pausar;
	• continuar;
	• receber erros;
	• receber link do YouTube.

7. HUMAN-IN-THE-LOOP
Sempre que existir decisão importante, enviar Telegram.
Formato recomendado:
⚠️ DECISÃO NECESSÁRIA
EPISÓDIO:
Davi e Golias
ETAPA:
Vídeo generativo
SITUAÇÃO:
A próxima geração poderá ultrapassar o orçamento.
ANÁLISE:
...
OPÇÃO A:
...
OPÇÃO B:
...
OPÇÃO C:
...
RECOMENDAÇÃO:
...
RESPONDA:
A / B / C / CANCELAR
Não deixe uma produção parada silenciosamente.

8. SILÊNCIO NÃO É APROVAÇÃO
Silêncio do operador:
!= aprovação
Nunca:
	• aumentar orçamento;
	• publicar vídeo;
	• executar operação destrutiva;
	• assumir decisão humana;
sem autorização quando uma aprovação estiver configurada como obrigatória.

9. SDD + HARNESS ENGINEERING
Todo o projeto deverá seguir:
Specification-Driven Development
e:
Harness Engineering
Antes de implementar funcionalidades complexas, produzir especificações.
O ambiente deverá permitir que agentes:
	• entendam o projeto;
	• reproduzam comandos;
	• executem testes;
	• validem outputs;
	• analisem logs;
	• recuperem estado;
	• identifiquem falhas;
	• continuem produção interrompida.

10. DOCUMENTAÇÃO COMO SOURCE OF TRUTH
Criar:
docs/
PRODUCT_REQUIREMENTS.md
ARCHITECTURE.md
PIPELINE_SPEC.md
AGENTS.md
STATE_MACHINE.md
PROVIDERS.md
RUNPOD_PROVIDER.md
BUDGET_GUARD.md
SCRIPT_SPEC.md
BIBLICAL_RESEARCH_SPEC.md
CHARACTER_BIBLE_SPEC.md
STORYBOARD_SPEC.md
IMAGE_PIPELINE.md
ANIMATION_PIPELINE.md
VIDEO_GENERATION.md
AUDIO_PIPELINE.md
TTS_SPEC.md
QA_SPEC.md
VISUAL_QA.md
NARRATIVE_QA.md
TELEGRAM_WORKFLOW.md
YOUTUBE_PUBLISHING.md
HARDWARE_BENCHMARK.md
SECURITY.md
OPERATIONS.md
DISASTER_RECOVERY.md
Documentação deverá acompanhar mudanças arquiteturais.

11. ARQUITETURA MULTIAGENTE
Projetar arquitetura multiagente.
Agentes sugeridos:
DIRECTOR AGENT
     |
     +-- Research Agent
     |
     +-- Biblical Accuracy Agent
     |
     +-- Script Agent
     |
     +-- Storyboard Agent
     |
     +-- Character Bible Agent
     |
     +-- Visual Director Agent
     |
     +-- Image Generation Agent
     |
     +-- Local Animation Agent
     |
     +-- Cloud Video Agent
     |
     +-- Voice Director Agent
     |
     +-- Audio Agent
     |
     +-- Video Assembly Agent
     |
     +-- Visual QA Agent
     |
     +-- Narrative QA Agent
     |
     +-- Budget Agent
     |
     +-- Thumbnail Agent
     |
     +-- YouTube Metadata Agent
     |
     +-- Publishing Agent
     |
     +-- Notification Agent
Não criar agentes meramente decorativos.
Cada agente deverá possuir:
responsibility
input
output
schema
tools
constraints
success criteria
failure modes

12. ORQUESTRADOR CENTRAL
O Director Agent será responsável por:
	• coordenar agentes;
	• verificar dependências;
	• administrar estados;
	• respeitar orçamento;
	• solicitar aprovação;
	• impedir execução fora de ordem.
Nenhum agente especializado deverá publicar diretamente ou contornar regras globais.

13. STATE MACHINE
Cada episódio deverá ter estado persistente.
Exemplo:
REQUEST_RECEIVED
RESEARCHING
PLANNING
WAITING_PLAN_APPROVAL
SCRIPTING
SCRIPT_QA
CHARACTER_DESIGN
STORYBOARDING
GENERATING_AUDIO
GENERATING_IMAGES
VISUAL_QA
PLANNING_ANIMATION
LOCAL_ANIMATION
CLOUD_VIDEO_GENERATION
WAITING_BUDGET_APPROVAL
ANIMATION_QA
ASSEMBLING
FINAL_QA
WAITING_FINAL_APPROVAL
UPLOADING
PUBLISHED
PAUSED
FAILED
CANCELLED

14. RESUMABILIDADE
A produção NÃO poderá depender exclusivamente da memória/contexto da sessão Hermes.
Se:
	• Hermes reiniciar;
	• computador reiniciar;
	• processo falhar;
	• internet cair;
	• RunPod falhar;
o episódio deverá poder ser retomado.

15. ESTRUTURA DO EPISÓDIO
Exemplo:
projects/
  episodes/
EP000001/
request.json
      plan.json
      state.json
      manifest.json
      costs.json
research/
script/
characters/
storyboard/
audio/
images/
animation/
cloud_clips/
subtitles/
thumbnails/
metadata/
qa/
renders/
logs/

16. CHECKPOINTS
Criar checkpoint depois de etapas caras ou aprovadas.
Nunca regenerar automaticamente asset aprovado se puder ser reutilizado.

17. IDEMPOTÊNCIA
Retomar uma operação não poderá:
	• publicar vídeo duas vezes;
	• gerar episódio duplicado;
	• repetir cobrança desnecessariamente;
	• recriar Character Bible;
	• substituir asset aprovado;
	• perder aprovação anterior.

18. DURAÇÃO DOS EPISÓDIOS
A duração NÃO deverá ser fixa.
Categorias:
História curta:
3–5 min

História média:
6–8 min

História longa:
8–12 min

Especial:
12–15 min

Entretanto, para a fase inicial de produção e otimização de custo:
priorizar episódios de aproximadamente 3–5 minutos.
Histórias maiores NÃO deverão ser artificialmente mutiladas.
Se apropriado:
	• produzir episódio maior;
	• dividir em partes;
	• criar minissérie;
desde que editorialmente coerente.

19. REGRA DE DURAÇÃO
Nunca:
	• adicionar texto para aumentar duração;
	• repetir ideias;
	• encurtar narrativa até perder compreensão;
	• inventar acontecimentos desnecessários.
A duração deverá resultar de:
complexidade
+
idade do público
+
ritmo
+
retenção
+
orçamento

20. PRÉ-PRODUÇÃO OBRIGATÓRIA
Antes de iniciar geração cara, apresentar:
Tema
Passagens bíblicas
Duração recomendada
Justificativa
Palavras estimadas
Número de cenas
Número de imagens
Cenas animadas localmente
Cenas candidatas ao RunPod
Segundos de vídeo generativo
Tempo local estimado
GPU cloud estimada
Custo mínimo
Custo provável
Custo máximo
Orçamento disponível

21. EXEMPLO DE PRÉ-PRODUÇÃO
EPISÓDIO:
Davi e Golias
Duração:
4m28s
Palavras:
~650
Cenas:
41
Imagens:
37
Cenas localmente animadas:
34
Cenas candidatas ao RunPod:
3
Vídeo RunPod:
18 segundos
CUSTO
mínimo:
US$ 1,80
provável:
US$ 3,20
máximo:
US$ 5,10
LIMITE:
US$ 6,00

22. PESQUISA BÍBLICA
Implementar:
Biblical Source Grounding
Registrar fontes usadas.
Exemplo:
{
  "story": "Davi e Golias",
"references": [
    {
      "book": "1 Samuel",
      "chapter": 17,
      "verses": "1-58"
    }
  ]
}

23. CLASSIFICAÇÃO NARRATIVA
Separar:
BIBLICAL_FACT
NARRATIVE_INFERENCE
CREATIVE_ADDITION
Nunca apresentar uma criação dramática como se estivesse explicitamente registrada na Bíblia.

24. ROTEIRO
Público-alvo:
crianças de 6 a 10 anos.
O roteiro deverá buscar:
	• clareza;
	• emoção;
	• curiosidade;
	• aventura;
	• suspense apropriado;
	• linguagem simples;
	• ritmo;
	• retenção;
	• fidelidade bíblica;
	• valor educativo;
	• conclusão significativa.

25. SEGURANÇA INFANTIL
Evitar:
	• terror inadequado;
	• violência gráfica;
	• sexualização;
	• exploração de nudez;
	• detalhes traumáticos desnecessários;
	• linguagem adulta;
	• manipulação emocional indevida.

26. CASOS BÍBLICOS SENSÍVEIS
Quando houver nudez narrativa, por exemplo Adão e Eva antes da queda:
utilizar:
	• enquadramento de costas;
	• vegetação;
	• objetos;
	• câmera acima da cintura;
	• personagens sentados;
	• silhueta;
	• composição apropriada.
Nunca mostrar partes íntimas.
Nunca sexualizar.

27. NARRAÇÃO COMO TIMELINE PRINCIPAL
Fluxo obrigatório:
SCRIPT
↓
TTS
↓
AUDIO MASTER
↓
TIMESTAMPS REAIS
↓
STORYBOARD
↓
VISUAIS

Não criar imagens a cada X segundos arbitrariamente.

28. TTS
Configuração inicial aprovada:
tts:
language: pt-BR
preferred_voice:
    name: "Thalita Neural"
rate: "-8%"
pitch: "+1Hz"
direction:
    "voz humanizada, acolhedora, expressiva,
    como uma pessoa contando histórias para crianças"
Se depender de serviço externo, implementar provider configurável.
Pesquisar alternativa local compatível.

29. PRESERVAÇÃO DA VOZ
Uma voz aprovada deverá ser registrada globalmente.
Não alterá-la entre episódios sem autorização.

30. ÁUDIO
Estrutura:
audio/
narration.wav
dialogue/
music/
sfx/
master.wav
Aplicar:
	• loudness normalization;
	• ducking;
	• fade;
	• proteção contra clipping;
	• mixagem.
A música nunca deverá prejudicar a narração.

31. TRANSCRIÇÃO
Não queimar legendas no vídeo por padrão.
Gerar:
transcript.txt
captions.srt
captions.vtt
para YouTube.

32. NÃO UTILIZAR SRT FIXO COMO AUTORIDADE
Não utilizar lógica fixa como:
80 palavras
30 segundos
+10 segundos
para determinar timestamps finais.
Timestamps devem derivar da narração real.
O conversor SRT legado poderá permanecer apenas como fallback/referência.

33. ROTEIRO PARA STORYBOARD
Dividir o roteiro semanticamente.
Cada mudança visual deve acompanhar o que está sendo narrado.
Não dividir simplesmente:
a cada 5 segundos
a cada sentença

34. SCENE SCHEMA
Cada cena deverá possuir algo semelhante a:
{
  "scene_id": "SC023",
"narration": "...",
"start": 83.21,
"end": 91.74,
"duration": 8.53,
"characters": [
    "davi"
  ],
"location": "...",
"emotion": "...",
"action": "...",
"importance": "HIGH",
"visual_strategy": "LOCAL_ANIMATED_STILL",
"references": [],
"camera": "...",
"image_prompt": "...",
"animation_prompt": "...",
"negative_prompt": "...",
"qa_status": "PENDING"
}

35. CHARACTER BIBLE
Este requisito é CRÍTICO.
Cada personagem recorrente deverá possuir identidade canônica.
Estrutura:
characters/
davi/
character.yaml
face.png
front.png
side.png
back.png
expressions/
poses/
references/

36. CHARACTER YAML
Registrar:
nome
idade aparente
formato do rosto
cor de pele
olhos
cabelo
altura relativa
proporções
roupas
cores
sapatos
acessórios
expressões
personalidade visual

37. PERSONAGENS RECORRENTES
Personagens principais deverão continuar reconhecíveis em vídeos futuros.
Exemplo:
Davi episódio 5
≈
Davi episódio 20

quando a fase de vida for equivalente.

38. CONTINUIDADE TEMPORAL
Permitir versões:
davi/
child/
teenager/
young_adult/
king/
Mantendo coerência facial.

39. IDENTIDADE VISUAL
Nunca considerar text-to-image puro como autoridade definitiva para personagem recorrente.
Priorizar:
reference image
image-to-image
IP-Adapter
ControlNet
identity conditioning
pose conditioning
LoRA
seed
quando suportado.
Seed é apenas auxiliar.

40. VISUAL STYLE BIBLE
Criar também identidade visual global do canal.
Registrar:
	• estilo;
	• paleta;
	• iluminação;
	• proporções;
	• acabamento;
	• composição;
	• tratamento de cenários;
	• características de câmera.
Evitar depender de nomes de artistas ou estúdios como requisito obrigatório.
Construir identidade própria.

41. ESTILO VISUAL BASE
Como referência:
high-quality children's animated movie
stylized 3D animation
warm cinematic lighting
expressive characters
soft friendly shapes
rich colorful environment
family-friendly
cinematic composition

42. IMAGEM COMO BASE DO PIPELINE
Na estratégia oficial:
IMAGEM CONSISTENTE > VÍDEO GENERATIVO CONTÍNUO
Fluxo:
CHARACTER BIBLE
↓
STORYBOARD
↓
REFERENCE IMAGES
↓
IMAGE GENERATION
↓
VISUAL QA
↓
APPROVED STILL
↓
ANIMATION


43. VISUAL QA ANTES DE ANIMAR
Toda imagem deverá passar por QA.
Verificar:
	• personagem;
	• rosto;
	• cabelo;
	• idade;
	• roupa;
	• acessórios;
	• anatomia;
	• mãos;
	• cenário;
	• quantidade de pessoas;
	• objetos;
	• continuidade;
	• ação;
	• correspondência narrativa;
	• segurança infantil;
	• texto indesejado.

44. QA RESULT
Formato:
{
  "approved": true,
"score": 0.94,
"problems": [],
"regenerate": false
}
Nunca gastar RunPod animando uma imagem já defeituosa.

45. REGENERAÇÃO SELETIVA
Se:
SC027
falhar:
regenerar somente:
SC027
e suas dependências necessárias.
Nunca recriar episódio inteiro.

46. VISUAL STRATEGY ENGINE
Criar componente:
Visual Strategy Engine
Para cada cena decidir entre:
STATIC_IMAGE
LOCAL_ANIMATED_STILL
LOCAL_IMAGE_TO_VIDEO
RUNPOD_GENERATIVE_VIDEO

47. CRITÉRIOS DE DECISÃO
Avaliar:
narrative_importance
visual_complexity
movement_requirement
character_consistency_risk
local_render_feasibility
expected_quality_gain
estimated_cloud_cost
remaining_budget
production_time

48. ESTRATÉGIA PADRÃO
Para episódios de 3–5 minutos:
a MAIOR PARTE deverá utilizar:
imagens consistentes + animação local.
RunPod deverá ser reservado principalmente para cenas decisivas.

49. LOCAL ANIMATION ENGINE
Criar motor de animação local capaz de utilizar:
	• pan;
	• zoom;
	• Ken Burns;
	• push-in;
	• pull-out;
	• crop animado;
	• parallax;
	• depth;
	• foreground movement;
	• camera shake suave;
	• partículas;
	• chuva;
	• fogo;
	• névoa;
	• raios de luz;
	• máscaras animadas;
	• transições;
	• motion blur;
	• foreground/background separation.
Priorizar ferramentas gratuitas/open source.

50. FFMPEG
FFmpeg deverá ser o compositor principal quando adequado.
Automatizar:
	• crop;
	• scale;
	• zoom;
	• pan;
	• transitions;
	• concatenation;
	• audio;
	• normalization;
	• encoding;
	• muxing;
	• thumbnail frame extraction.

51. MULTILAYER SCENE
Quando viável:
BACKGROUND
MIDGROUND
CHARACTER
FOREGROUND
FX
Isso permite parallax e profundidade sem vídeo generativo.

52. MOTION PRESETS
Criar biblioteca:
slow_push_in
slow_pull_out
pan_left
pan_right
vertical_reveal
hero_reveal
dramatic_zoom
gentle_float
parallax_walk
storm_motion
fire_glow
water_motion

53. RUNPOD COMO GPU COMPUTE PROVIDER
Adicionar oficialmente:
runpod.io
Implementar abstraction:
GPUComputeProvider
Providers iniciais:
LocalGPUProvider
RunPodGPUProvider
O sistema de negócio não deverá depender diretamente da implementação RunPod.

54. PROVIDER ABSTRACTION GLOBAL
Criar interfaces:
LLMProvider
ImageProvider
VideoProvider
GPUComputeProvider
TTSProvider
MusicProvider
StorageProvider
NotificationProvider
PublishProvider
Possibilitar troca futura por:
	• RunPod;
	• fal.ai;
	• Hailuo;
	• Kling;
	• Sora;
	• Veo;
	• outros.

55. RUNPOD SOB DEMANDA
RunPod não deverá permanecer ligado desnecessariamente.
Fluxo:
JOB REQUIRED
↓
ALLOCATE RESOURCE
↓
LOAD ENVIRONMENT
↓
RUN
↓
SAVE OUTPUT
↓
VERIFY OUTPUT
↓
SHUTDOWN RESOURCE


56. PREVENÇÃO DE GPU OCIOSA
Implementar:
	• timeout;
	• watchdog;
	• finally/cleanup;
	• shutdown;
	• leak detection;
	• orphan resource check.
Um erro no Hermes não deverá deixar uma GPU RunPod consumindo créditos indefinidamente.

57. RUNPOD JOB MANAGER
Criar:
RunPod Job Manager
Responsabilidades:
selecionar GPU
consultar disponibilidade
consultar preço
provisionar
executar modelo
acompanhar job
recuperar resultado
persistir asset
medir runtime
calcular custo
encerrar recurso
registrar logs

58. GPU SELECTION
Nunca hardcode:
RTX 4090
como escolha universal.
Selecionar a GPU mais econômica capaz de executar o modelo.
Critérios:
VRAM
modelo
preço/hora
disponibilidade
tempo histórico
custo previsto

59. PRICE DISCOVERY
Preços de RunPod mudam.
Não hardcode preço como verdade permanente.
Antes de um job pago:
	• consultar/configurar preço atual;
	• registrar preço utilizado;
	• estimar custo.

60. COST RECORD
Registrar:
{
  "provider": "runpod",
"gpu": "...",
"model": "...",
"hourly_price": 0,
"job_duration_seconds": 0,
"estimated_cost": 0,
"actual_cost": 0
}

61. BUDGET GUARD
Criar componente central obrigatório:
Budget Guard
Nenhuma operação paga pode acontecer sem consultá-lo.
Responsabilidades:
	• orçamento;
	• gasto acumulado;
	• custo previsto;
	• margem restante;
	• reservas;
	• retries;
	• alertas;
	• bloqueio;
	• aprovação humana;
	• reconciliação.

62. COST LEDGER
Cada episódio deverá possuir:
costs.json
Exemplo:
{
  "currency": "USD",
"budget": 6.00,
"spent": 3.82,
"runpod": 3.44,
"other_services": 0.38,
"jobs": []
}

63. BUDGET CHECK
Antes de cada job:
current_spend
+
estimated_next_job
=
projected_spend
Se:
projected_spend <= hard_limit
prosseguir.
Se:
projected_spend > hard_limit
PARAR.

64. BUDGET STATE
Mudar estado para:
WAITING_BUDGET_APPROVAL
Enviar Telegram.

65. TELEGRAM — EXCESSO DE ORÇAMENTO
Exemplo:
⚠️ LIMITE DE ORÇAMENTO
EPISÓDIO:
Davi e Golias
LIMITE:
US$ 6,00
GASTO:
US$ 5,42
PRÓXIMA GERAÇÃO:
US$ 0,91
TOTAL PROJETADO:
US$ 6,33
CENA:
SC028
IMPORTÂNCIA:
CRITICAL
MOTIVO:
Clímax da batalha.
OPÇÕES
A — Autorizar somente este job

B — Utilizar animação local

C — Definir novo orçamento

D — Cancelar

RECOMENDAÇÃO:
B

66. OVERRIDE DE ORÇAMENTO
Se operador autorizar:
registrar:
who
when
old_limit
new_limit
reason
episode
Nunca alterar limite silenciosamente.

67. TARGET DE CUSTO
Classificação:
US$ 0–3:
EXCELENTE

US$ 3–4:
BOM

US$ 4–5:
ACEITÁVEL

US$ 5–6:
ATENÇÃO

> US$ 6:
BLOQUEADO

68. CENAS DECISIVAS
Classificar:
LOW
NORMAL
HIGH
CRITICAL
Somente:
HIGH
CRITICAL
deverão normalmente ser candidatas ao RunPod.

69. EXEMPLO — DAVI E GOLIAS
Candidatas:
Davi correndo
preparação da funda
pedra sendo lançada
queda de Golias
Normalmente locais:
conversas
paisagens
exército parado
narração contextual
caminhada simples

70. LIMITE DE VÍDEO GENERATIVO
Configuração inicial:
generative_video:
enabled: true
provider: runpod
max_seconds_per_episode: 30
preferred_clip_duration_seconds: 4
maximum_clip_duration_seconds: 8
Todos configuráveis.

71. REFERÊNCIA PARA EPISÓDIOS DE 3–5 MINUTOS
Duração:
180–300 segundos

Vídeo generativo desejado:
10–30 segundos

ou aproximadamente:
5–15%

Não é regra rígida.
O Budget Guard é autoridade financeira.

72. QUALIDADE NÃO É QUANTIDADE DE VÍDEO IA
Não considerar melhor um vídeo simplesmente porque possui mais segundos generativos.
Avaliar:
	• narrativa;
	• personagem;
	• edição;
	• ritmo;
	• áudio;
	• movimento;
	• continuidade;
	• emoção;
	• cinematografia;
	• consistência.

73. RUNPOD PREFERENCIALMENTE IMAGE-TO-VIDEO
Quando houver personagem recorrente:
preferir:
APPROVED IMAGE
↓
IMAGE-TO-VIDEO

em vez de:
TEXT
↓
VIDEO

Isso reduz deriva visual.

74. RUNPOD QA
Depois da geração cloud, verificar:
	• rosto;
	• identidade;
	• roupa;
	• proporção;
	• anatomia;
	• movimento;
	• continuidade;
	• ação;
	• segurança;
	• coerência com narração.

75. RETRY
Configuração inicial:
runpod:
retries:
max_per_scene: 2

76. RETRY NÃO É AUTOMATICAMENTE GRATUITO
Antes de retry:
QA DIAGNOSIS
↓
PROMPT CORRECTION
↓
BUDGET GUARD
↓
RETRY


77. FALLBACK DE VÍDEO
Sempre manter:
RUNPOD VIDEO
↓
LOCAL IMAGE-TO-VIDEO
↓
LOCAL PARALLAX
↓
PAN / ZOOM / FX

O episódio deverá poder ser finalizado mesmo sem RunPod.

78. BUDGET OPTIMIZER
Se planejamento ultrapassar US$ 6:
não iniciar produção paga.
Otimizar:
	1. remover cenas cloud menos importantes;
	2. reduzir segundos;
	3. reduzir duração dos clips;
	4. escolher GPU/modelo mais econômico;
	5. substituir vídeo por parallax;
	6. reaproveitar assets;
	7. diminuir retries previstos.
Não reduzir a qualidade do roteiro para economizar GPU.

79. HARDWARE PROFILE
Detectar:
GPU
VRAM
CUDA
RAM
Criar profiles:
LOW_VRAM_4GB
MID_VRAM_8GB
STANDARD_12GB
PRO_16GB
HIGH_VRAM_24GB
ULTRA_32GB

80. PROFILE INICIAL
A GTX 1050 Ti deverá operar em:
LOW_VRAM_4GB
Exemplo:
profiles:
LOW_VRAM_4GB:
cpu_offload: true
quantization: aggressive
generative_video_local: restricted
animated_stills: preferred

81. NÃO FORCE MODELOS NA GTX 1050 TI
Não considere sucesso:
	"o modelo iniciou".
Avaliar:
	• velocidade;
	• VRAM;
	• RAM;
	• estabilidade;
	• qualidade;
	• temperatura;
	• tempo por frame;
	• tempo por imagem;
	• tempo por clip.
Um modelo que leva horas para gerar poucos segundos pode ser tecnicamente executável e operacionalmente inviável.

82. RESOLUÇÃO
Não gerar IA diretamente em 4K.
Benchmark:
512p
540p
720p
Depois avaliar upscale.

83. MODELOS
Pesquisar estado atual do ecossistema antes de selecionar modelos.
Considerar quando apropriado:
	• Wan;
	• LTX;
	• modelos quantizados;
	• GGUF;
	• ComfyUI;
	• alternativas open source atuais.
Não congelar a arquitetura em uma lista antiga.

84. COMFYUI
ComfyUI poderá ser usado como engine quando for tecnicamente conveniente.
Entretanto, não permitir que o restante do sistema dependa exclusivamente de workflows visuais frágeis.
Workflows críticos deverão ser:
	• versionados;
	• parametrizados;
	• testáveis;
	• executáveis automaticamente.

85. STORAGE
Há armazenamento limitado.
Antes de baixar modelos grandes:
	• verificar espaço;
	• estimar tamanho;
	• verificar espaço pós-instalação;
	• impedir disco cheio.

86. STORAGE POLICY
Nunca deletar automaticamente:
	• Character Bible;
	• roteiro;
	• áudio master;
	• manifest;
	• vídeo final;
	• thumbnail;
	• metadata;
	• assets canônicos.
Pode limpar conforme política:
	• cache;
	• temporários;
	• previews;
	• intermediários reproduzíveis.

87. CACHE
Implementar cache quando seguro.
Hash por exemplo:
model
prompt
reference
seed
parameters
Não repetir geração idêntica desnecessariamente.

88. MANIFEST
Cada episódio deverá possuir:
manifest.json
Incluindo:
	• versão;
	• modelos;
	• providers;
	• prompts;
	• seeds;
	• referências;
	• áudio;
	• imagens;
	• cenas;
	• clips;
	• metadata;
	• custos;
	• aprovações.

89. OBSERVABILIDADE
Registrar:
episode_id
scene_id
agent
stage
provider
model
model_version
prompt
seed
resolution
generation_time
VRAM_peak
RAM_peak
attempt
cost
result
error
timestamp

90. AUDITABILIDADE
Dever ser possível responder:
	Por que essa imagem foi criada assim?
	Qual prompt foi utilizado?
	Qual modelo?
	Quanto custou?
	Quantas vezes foi gerada?
	Qual Character Bible foi usada?

91. THUMBNAIL AGENT
Criar automaticamente:
	• conceito;
	• composição;
	• imagem;
	• headline;
	• variações.
Priorizar:
	• emoção;
	• simplicidade;
	• leitura no celular;
	• personagem principal;
	• contraste;
	• curiosidade.
Evitar clickbait enganoso.

92. YOUTUBE METADATA
Gerar:
title
description
keywords
chapters
language
playlist
thumbnail
captions

93. PLAYLISTS INICIAIS
Aventuras do Antigo Testamento
Histórias de Jesus
Heróis e Heroínas da Bíblia
Milagres da Bíblia
Lições de Fé e Coragem
Escolher automaticamente.

94. YOUTUBE PUBLISHING
Utilizar integração oficial.
Etapas:
validate video
validate metadata
validate thumbnail
validate captions
validate channel
validate approval
upload
set metadata
set thumbnail
set playlist
record video ID
record URL

95. PUBLICAÇÃO NÃO AUTOMÁTICA SEM APROVAÇÃO FINAL
Mesmo que todo QA seja aprovado:
antes da publicação, quando configuração exigir:
WAITING_FINAL_APPROVAL
Telegram:
✅ VÍDEO PRONTO PARA PUBLICAÇÃO
Título:
...
Duração:
...
Custo:
...
Canal:
...
Aprovar publicação?
APROVAR
REJEITAR
ALTERAR

96. NOTIFICAÇÃO FINAL
Após publicação:
✅ EPISÓDIO PUBLICADO
Título:
{title}
Canal:
{channel}
Duração:
{duration}
URL:
{url}
Tempo de produção:
{production_time}
Custo externo:
{external_cost}
RunPod:
{runpod_cost}
Segundos generativos:
{generative_seconds}
Regenerações:
{retry_count}

97. ORIGINALIDADE EDITORIAL
Não construir um sistema que simplesmente gere outputs genéricos e concatene.
O processo deverá possuir contribuição editorial substancial:
	• pesquisa;
	• adaptação;
	• roteiro;
	• Character Bible;
	• storyboard;
	• direção visual;
	• composição;
	• escolha de cenas;
	• narração;
	• animação;
	• edição;
	• efeitos;
	• áudio;
	• QA;
	• thumbnail;
	• metadata.

98. PIPELINE COMPLETO
USER REQUEST
        ↓
DIRECTOR AGENT
        ↓
RESEARCH
        ↓
BIBLICAL GROUNDING
        ↓
DURATION PLANNING
        ↓
BUDGET PLANNING
        ↓
TELEGRAM APPROVAL
        ↓
SCRIPT
        ↓
SCRIPT QA
        ↓
TTS
        ↓
TIMESTAMPS
        ↓
CHARACTER BIBLE
        ↓
STORYBOARD
        ↓
IMAGE GENERATION
        ↓
VISUAL QA
        ↓
VISUAL STRATEGY ENGINE
        ↓
 ┌─────────────────────────┐
 │                         │
LOCAL ANIMATION       RUNPOD CANDIDATE
 │                         │
 │                    BUDGET GUARD
 │                         │
 │                    RUNPOD VIDEO
 │                         │
 └─────────────┬───────────┘
               ↓
          ANIMATION QA
               ↓
             AUDIO
               ↓
             FFMPEG
               ↓
           FINAL VIDEO
               ↓
            FINAL QA
               ↓
       TELEGRAM APPROVAL
               ↓
            YOUTUBE
               ↓
           PLAYLIST
               ↓
           TELEGRAM


99. CONFIGURAÇÃO CENTRAL
Criar configuração semelhante:
project:
default_language: pt-BR
default_channel: "@EraUmaVezBibliaAnimada"
production:
strategy: hybrid
episode:
typical_duration:
min_minutes: 3
max_minutes: 5
budget:
currency: USD
episode:
target_usd: 4.00
warning_usd: 5.00
hard_limit_usd: 6.00
require_approval_above_limit: true
generative_video:
enabled: true
provider: runpod
only_for_high_value_scenes: true
max_seconds_per_episode: 30
preferred_clip_duration_seconds: 4
maximum_clip_duration_seconds: 8
local_animation:
enabled: true
default: true
runpod:
shutdown_after_job: true
retries:
max_per_scene: 2
approval:
preproduction: true
budget_override: true
final_video: true
publishing:
auto_publish_after_final_approval: true

100. MÉTRICAS OPERACIONAIS
Registrar:
average_cost_per_episode
median_cost_per_episode
p95_cost_per_episode
average_runpod_seconds
average_runpod_cost
average_regenerations
average_generation_time
average_episode_duration

101. PRODUÇÃO EM ESCALA
O sistema deverá ser preparado para:
100+
episódios.
Calcular projeções para:
10 episódios
50 episódios
100 episódios
500 episódios

102. NÃO MULTIPLICAR O TETO COMO META
Embora:
US$ 6 × 100 = US$ 600
não planejar gastar US$ 600.
Buscar reduzir:
average_cost_per_episode
continuamente.

103. OPERATIONAL KNOWLEDGE
Registrar histórico de geração.
Exemplo:
modelo
+
GPU
+
resolução
+
tipo de cena
+
duração
+
prompt strategy
+
resultado
+
custo
Usar histórico para melhorar decisões futuras.

104. NÃO REALIZAR AUTO-TREINAMENTO SEM AUTORIZAÇÃO
Não alterar modelos ou iniciar treinamento pesado autonomamente.
Primeiro utilizar:
	• métricas;
	• histórico;
	• heurísticas;
	• Character Bible;
	• presets.

105. TESTES
Criar:
unit tests
integration tests
provider tests
budget tests
pipeline tests
resume tests
failure tests
publishing tests

106. TESTES DE FALHA
Testar explicitamente:
	• falta de VRAM;
	• OOM;
	• falta de disco;
	• geração interrompida;
	• internet caiu;
	• RunPod indisponível;
	• RunPod timeout;
	• GPU RunPod não encontrada;
	• Telegram indisponível;
	• YouTube indisponível;
	• arquivo corrompido;
	• QA recusado;
	• Hermes reiniciado;
	• computador reiniciado;
	• orçamento esgotado.

107. SEGURANÇA
Nunca armazenar secrets no Git.
Proteger:
	• Telegram bot token;
	• RunPod API key;
	• YouTube OAuth;
	• providers futuros.
Utilizar:
.env
ou secret store adequado.
Criar:
.env.example
sem credenciais reais.

108. GIT
Versionar todo o projeto.
Commits pequenos.
Não fazer:
"implement everything"
em um commit gigante.

109. IMPLEMENTAÇÃO INCREMENTAL
Executar:
PHASE 0
Discovery + Hardware Audit + RunPod Feasibility
PHASE 1
SDD
PHASE 2
Project Skeleton
PHASE 3
State Machine
PHASE 4
Provider Abstractions
PHASE 5
Budget Guard
PHASE 6
Script / Biblical Research
PHASE 7
TTS
PHASE 8
Storyboard
PHASE 9
Character Bible
PHASE 10
Image Pipeline
PHASE 11
Visual QA
PHASE 12
Local Animation
PHASE 13
RunPod Integration
PHASE 14
Animation QA
PHASE 15
FFmpeg Assembly
PHASE 16
Audio Mastering
PHASE 17
Thumbnail
PHASE 18
Metadata
PHASE 19
Telegram HITL
PHASE 20
YouTube Integration
PHASE 21
End-to-End Pilot
PHASE 22
Performance Optimization
PHASE 23
Production Hardening

110. REGRA DE EXECUÇÃO DE FASE
Para cada fase:
SPEC
↓
IMPLEMENT
↓
TEST
↓
VALIDATE
↓
DOCUMENT
↓
COMMIT
↓
NEXT


111. PRIMEIRO PASSO OBRIGATÓRIO
NÃO comece instalando modelos.
Primeiro execute auditoria READ-ONLY.
Descobrir:
Windows
CPU
RAM
GPU
VRAM
NVIDIA Driver
CUDA compatibility
compute capability
Python
pip
Git
FFmpeg
Node
npm
PowerShell
WSL
Docker
Hermes
ComfyUI
disk free
network

112. RUNPOD FEASIBILITY REPORT
Na Phase 0 pesquisar situação atual do RunPod.
Avaliar:
	1. SDK/API;
	2. autenticação;
	3. Pods;
	4. Serverless;
	5. GPU types;
	6. VRAM;
	7. preço atual;
	8. cold start;
	9. armazenamento;
	10. network transfer;
	11. modelos apropriados;
	12. tempo estimado;
	13. custo por job;
	14. custo por 10 segundos;
	15. custo por 20 segundos;
	16. custo por 30 segundos;
	17. shutdown;
	18. falhas;
	19. Budget Guard.

113. LOCAL AI VIDEO FEASIBILITY REPORT
Antes de modificar a máquina, produzir:
LOCAL + HYBRID AI VIDEO FEASIBILITY REPORT
Contendo:
1. Hardware encontrado
2. Software encontrado
3. Limitações
4. Drivers
5. CUDA
6. Modelos candidatos
7. Modelos descartados
8. Image models
9. Video models
10. TTS
11. ComfyUI
12. Local animation
13. RunPod strategy
14. GPU cloud candidates
15. Estimated performance
16. Estimated storage
17. Estimated local generation times
18. RunPod estimated cost
19. Pipeline recommendation
20. Risks
21. Implementation plan

114. NÃO INSTALAR NADA GRANDE ANTES DA APROVAÇÃO
Depois do relatório:
PARE.
Não:
	• instalar CUDA;
	• alterar driver;
	• baixar dezenas de GB;
	• instalar modelos pesados;
	• modificar configurações críticas.
Enviar via Telegram.

115. COMANDO PARA CONTINUAR
Somente prosseguir após:
	APROVADO — INICIAR FASE DE BENCHMARK

116. BENCHMARK
Criar benchmark reproduzível.
Testar separadamente:
TTS
IMAGE
IMAGE-TO-IMAGE
LOCAL ANIMATION
LOCAL VIDEO
RUNPOD VIDEO
FFMPEG
Medir:
runtime
VRAM
RAM
disk
temperature quando disponível
quality
failures
cost

117. PILOTO
Primeiro piloto:
História da criação do mundo
Produzir aproximadamente:
1–3 minutos

apenas para validar arquitetura.
Não publicar automaticamente.

118. COMPOSIÇÃO DO PILOTO
Utilizar:
maioria:
imagens + animações locais
RunPod:
mínimo 1 cena
RunPod:
máximo 3 cenas

119. OBJETIVO DO PILOTO
Comparar:
LOCAL
vs
RUNPOD
em:
	• qualidade;
	• consistência;
	• custo;
	• tempo;
	• estabilidade.

120. RELATÓRIO DO PILOTO
Apresentar:
Duração
Número de cenas
Número de imagens
Número de cenas locais
Número de cenas RunPod
Segundos RunPod
GPU utilizada
Modelo utilizado
Tempo local
Tempo RunPod
Custo RunPod
Custo total externo
VRAM local
RAM local
Falhas
Retries
Qualidade percebida

121. PROJEÇÃO
Estimar produção de:
3 min
5 min
8 min
12 min
15 min
E:
10 episódios
50 episódios
100 episódios

122. GO / NO-GO
Depois do piloto classificar:
A — LOCAL VIÁVEL
Continuar majoritariamente local.
B — LOCAL FUNCIONA, MAS É LENTO
Manter local para assets leves e aumentar RunPod estrategicamente.
C — HÍBRIDO IDEAL
Manter pipeline oficial:
local + RunPod
D — HARDWARE UPGRADE RECOMENDADO
Apresentar ganho esperado.

123. NÃO RECOMENDAR HARDWARE SEM EVIDÊNCIA
Qualquer sugestão futura de GPU local deverá considerar benchmark real.
Não recomendar compra apenas por especificação teórica.

124. CRITÉRIO FINAL DE SUCESSO
O projeto será considerado concluído quando o usuário puder escrever:
	Poste um vídeo no @EraUmaVezBibliaAnimada no idioma português do Brasil com o tema: História de Jonas e o Grande Peixe.
E o sistema executar:
REQUEST
↓
RESEARCH
↓
BIBLICAL SOURCES
↓
DURATION
↓
BUDGET
↓
TELEGRAM APPROVAL
↓
SCRIPT
↓
TTS
↓
CHARACTER BIBLE
↓
STORYBOARD
↓
IMAGES
↓
VISUAL QA
↓
LOCAL ANIMATION
↓
RUNPOD FOR DECISIVE SCENES
↓
BUDGET GUARD
↓
ANIMATION QA
↓
AUDIO
↓
FFMPEG
↓
THUMBNAIL
↓
METADATA
↓
TRANSCRIPTION
↓
FINAL RENDER
↓
FINAL QA
↓
TELEGRAM APPROVAL
↓
YOUTUBE
↓
PLAYLIST
↓
TELEGRAM LINK


125. RESULTADO ESPERADO
Criar um:
HYBRID AI ANIMATION STUDIO
capaz de produzir centenas de episódios mantendo:
consistência visual
Character Bible
qualidade infantil
fidelidade narrativa
baixo custo
controle editorial
rastreabilidade
reprodutibilidade
retomada após falhas
orçamento controlado
RunPod sob demanda
publicação automatizada

126. PRINCÍPIO FINAL DE PRODUÇÃO
A estratégia visual oficial é:
CONSISTENT IMAGE
      ↓
LOCAL ANIMATION
      ↓
RUNPOD ONLY WHEN IT MATTERS

Não:
GENERATE EVERYTHING AS AI VIDEO

127. PRINCÍPIO FINAL DE CUSTO
O objetivo é:
máxima qualidade percebida
+
máxima consistência
+
mínimo custo externo
com:
US$ 6
como teto padrão por episódio.

128. PRIMEIRA AÇÃO
Comece exclusivamente pela:
PHASE 0 — DISCOVERY + HARDWARE AUDIT + RUNPOD FEASIBILITY
Produza o:
LOCAL + HYBRID AI VIDEO FEASIBILITY REPORT
Não faça alterações significativas no sistema.
Não instale modelos grandes.
Não altere drivers.
Não altere CUDA.
Não gere episódios completos.
Depois envie o relatório pelo Telegram e aguarde:
	APROVADO — INICIAR FASE DE BENCHMARK
Somente então prossiga.
