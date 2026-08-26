"""Content roadmap — playlists and canonical episode order (§99 global config).

Single source of truth for the channel's content plan. The Director consumes
this to know which episode comes next, which playlist it belongs to, and how
to handle sensitive passages for children aged 6-10.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Episode:
    """One planned video in the channel roadmap."""

    number: int
    theme: str
    passage: str

    def prompt(self, playlist: str) -> str:
        return f"Playlist: {playlist}\nTema: {self.theme} — {self.passage}"


@dataclass(frozen=True)
class Playlist:
    """A YouTube playlist and its ordered episodes."""

    name: str
    episodes: tuple[Episode, ...]


ROADMAP: dict[str, Playlist] = {
    "Aventuras do Antigo Testamento": Playlist(
        name="Aventuras do Antigo Testamento",
        episodes=(
            Episode(1, "A criação do mundo", "Gênesis 1–2"),
            Episode(2, "Adão e Eva no Jardim do Éden", "Gênesis 2–3"),
            Episode(3, "Caim e Abel: aprendendo a controlar a raiva", "Gênesis 4"),
            Episode(4, "Noé e a grande arca", "Gênesis 6–9"),
            Episode(5, "A Torre de Babel", "Gênesis 11"),
            Episode(6, "O chamado de Abraão", "Gênesis 12"),
            Episode(7, "Abraão e Ló escolhem caminhos diferentes", "Gênesis 13"),
            Episode(8, "A promessa de um filho para Abraão e Sara", "Gênesis 15–18"),
            Episode(9, "O nascimento de Isaque", "Gênesis 21"),
            Episode(10, "Isaque e Rebeca", "Gênesis 24"),
            Episode(11, "Jacó e Esaú fazem as pazes", "Gênesis 27–33"),
            Episode(12, "O sonho de Jacó e a escada para o céu", "Gênesis 28"),
            Episode(13, "José e sua túnica especial", "Gênesis 37"),
            Episode(14, "José é levado para o Egito", "Gênesis 37–39"),
            Episode(15, "José interpreta os sonhos do faraó", "Gênesis 40–41"),
            Episode(16, "José salva o Egito da fome", "Gênesis 41"),
            Episode(17, "José perdoa seus irmãos", "Gênesis 42–50"),
            Episode(18, "O bebê Moisés no cesto", "Êxodo 1–2"),
            Episode(19, "Moisés e a sarça ardente", "Êxodo 3–4"),
            Episode(20, "Moisés enfrenta o faraó", "Êxodo 5–12"),
            Episode(21, "A primeira Páscoa", "Êxodo 12"),
            Episode(22, "A travessia do Mar Vermelho", "Êxodo 14"),
            Episode(23, "O maná que caiu do céu", "Êxodo 16"),
            Episode(24, "Água brotando da rocha", "Êxodo 17"),
            Episode(25, "Os Dez Mandamentos", "Êxodo 19–20"),
            Episode(26, "O bezerro de ouro", "Êxodo 32"),
            Episode(27, "Os doze exploradores de Canaã", "Números 13–14"),
            Episode(28, "A serpente de bronze", "Números 21"),
            Episode(29, "Balaão e sua jumenta", "Números 22"),
            Episode(30, "Josué atravessa o rio Jordão", "Josué 3–4"),
            Episode(31, "A queda das muralhas de Jericó", "Josué 6"),
            Episode(32, "Raabe ajuda os exploradores", "Josué 2 e 6"),
            Episode(33, "O dia em que o sol parou", "Josué 10"),
            Episode(34, "Gideão e seu pequeno exército", "Juízes 6–7"),
            Episode(35, "Sansão e sua força extraordinária", "Juízes 13–16"),
            Episode(36, "Rute e Noemi: uma amizade fiel", "Rute 1–4"),
            Episode(37, "O menino Samuel ouve a voz de Deus", "1 Samuel 3"),
            Episode(38, "A arca da aliança retorna a Israel", "1 Samuel 5–7"),
            Episode(39, "Davi e Golias", "1 Samuel 17"),
            Episode(40, "A amizade de Davi e Jônatas", "1 Samuel 18–20"),
            Episode(41, "Davi poupa a vida de Saul", "1 Samuel 24 e 26"),
            Episode(42, "Salomão pede sabedoria", "1 Reis 3"),
            Episode(43, "Salomão constrói o templo", "1 Reis 5–8"),
            Episode(44, "Elias e os corvos", "1 Reis 17"),
            Episode(45, "Elias e a viúva de Sarepta", "1 Reis 17"),
            Episode(46, "Elias no monte Carmelo", "1 Reis 18"),
            Episode(47, "Elias escuta a voz suave de Deus", "1 Reis 19"),
            Episode(48, "Elias é levado ao céu", "2 Reis 2"),
            Episode(49, "Naamã mergulha sete vezes no Jordão", "2 Reis 5"),
            Episode(50, "O machado que flutuou", "2 Reis 6"),
            Episode(51, "Jonas e o grande peixe", "Jonas 1–4"),
            Episode(52, "Daniel e seus amigos na Babilônia", "Daniel 1"),
            Episode(53, "A fornalha ardente", "Daniel 3"),
            Episode(54, "Daniel na cova dos leões", "Daniel 6"),
            Episode(55, "Ester salva seu povo", "Ester 1–9"),
            Episode(56, "Neemias reconstrói os muros", "Neemias 1–6"),
            Episode(57, "Esdras ensina a Palavra ao povo", "Esdras 7; Neemias 8"),
        ),
    ),
    "Histórias de Jesus": Playlist(
        name="Histórias de Jesus",
        episodes=(
            Episode(1, "O anjo anuncia o nascimento de Jesus", "Lucas 1"),
            Episode(2, "Maria visita Isabel", "Lucas 1"),
            Episode(3, "O nascimento de Jesus", "Lucas 2"),
            Episode(4, "Os pastores encontram o menino Jesus", "Lucas 2"),
            Episode(5, "Os sábios seguem a estrela", "Mateus 2"),
            Episode(6, "A fuga da família de Jesus para o Egito", "Mateus 2"),
            Episode(7, "Jesus ainda criança visita o templo", "Lucas 2"),
            Episode(8, "João Batista prepara o caminho", "Mateus 3; Lucas 3"),
            Episode(9, "O batismo de Jesus", "Mateus 3"),
            Episode(10, "Jesus vence as tentações", "Mateus 4"),
            Episode(11, "Jesus chama seus primeiros discípulos", "Mateus 4; Lucas 5"),
            Episode(12, "Jesus chama Mateus", "Mateus 9"),
            Episode(13, "Jesus escolhe os doze apóstolos", "Marcos 3; Lucas 6"),
            Episode(14, "Jesus conversa com Nicodemos", "João 3"),
            Episode(15, "Jesus e a mulher samaritana", "João 4"),
            Episode(16, "Jesus ensina as bem-aventuranças", "Mateus 5"),
            Episode(17, "Jesus ensina a amar os inimigos", "Mateus 5"),
            Episode(18, "Jesus ensina a oração do Pai Nosso", "Mateus 6"),
            Episode(19, "Jesus ensina sobre os tesouros verdadeiros", "Mateus 6"),
            Episode(20, "A casa construída sobre a rocha", "Mateus 7"),
            Episode(21, "Jesus acolhe as crianças", "Marcos 10"),
            Episode(22, "Jesus visita Marta e Maria", "Lucas 10"),
            Episode(23, "Jesus visita Zaqueu", "Lucas 19"),
            Episode(24, "O bom samaritano", "Lucas 10"),
            Episode(25, "A parábola do filho pródigo", "Lucas 15"),
            Episode(26, "A ovelha perdida", "Lucas 15"),
            Episode(27, "A moeda perdida", "Lucas 15"),
            Episode(28, "O semeador e os diferentes solos", "Mateus 13"),
            Episode(29, "O grão de mostarda", "Mateus 13"),
            Episode(30, "O tesouro escondido", "Mateus 13"),
            Episode(31, "A pérola de grande valor", "Mateus 13"),
            Episode(32, "O servo que não quis perdoar", "Mateus 18"),
            Episode(33, "Os trabalhadores da vinha", "Mateus 20"),
            Episode(34, "Os talentos confiados aos servos", "Mateus 25"),
            Episode(35, "O fariseu e o cobrador de impostos", "Lucas 18"),
            Episode(36, "O rico e Lázaro", "Lucas 16"),
            Episode(37, "O amigo que pediu ajuda durante a noite", "Lucas 11"),
            Episode(38, "A viúva perseverante", "Lucas 18"),
            Episode(39, "Jesus elogia a oferta da viúva", "Marcos 12"),
            Episode(40, "Pedro reconhece quem é Jesus", "Mateus 16"),
            Episode(41, "Jesus é transfigurado", "Mateus 17"),
            Episode(42, "Jesus ensina Pedro sobre o perdão", "Mateus 18"),
            Episode(43, "Jesus lava os pés dos discípulos", "João 13"),
            Episode(44, "A entrada de Jesus em Jerusalém", "Mateus 21"),
            Episode(45, "A última ceia", "Lucas 22"),
            Episode(46, "Jesus ora no Getsêmani", "Mateus 26"),
            Episode(47, "Pedro nega Jesus e aprende com seu erro", "Lucas 22; João 21"),
            Episode(48, "A ressurreição de Jesus", "Mateus 28; João 20"),
            Episode(49, "Jesus aparece aos discípulos", "Lucas 24; João 20"),
            Episode(50, "Tomé volta a acreditar", "João 20"),
            Episode(51, "Jesus prepara o café da manhã para os discípulos", "João 21"),
            Episode(52, "Jesus restaura Pedro", "João 21"),
            Episode(53, "A ascensão de Jesus", "Atos 1"),
            Episode(54, "A promessa do Espírito Santo", "João 14–16; Atos 1"),
            Episode(55, "O caminho de Emaús", "Lucas 24"),
        ),
    ),
    "Heróis e Heroínas da Bíblia": Playlist(
        name="Heróis e Heroínas da Bíblia",
        episodes=(
            Episode(1, "Noé: obedecer mesmo quando ninguém entende", ""),
            Episode(2, "Abraão: partir confiando na promessa", ""),
            Episode(3, "Sara: aprender a esperar", ""),
            Episode(4, "Rebeca: demonstrar generosidade", ""),
            Episode(5, "Jacó: reconhecer erros e mudar", ""),
            Episode(6, "José: permanecer fiel nos momentos difíceis", ""),
            Episode(7, "José: escolher perdoar", ""),
            Episode(8, "Moisés: vencer o medo de falar", ""),
            Episode(9, "Arão: ajudar em uma grande missão", ""),
            Episode(10, "Miriã: cuidar do pequeno Moisés", ""),
            Episode(11, "Josué: ser forte e corajoso", ""),
            Episode(12, "Calebe: acreditar quando os outros desistiram", ""),
            Episode(13, "Raabe: tomar uma decisão corajosa", ""),
            Episode(14, "Débora: liderar com sabedoria", "Juízes 4–5"),
            Episode(15, "Gideão: descobrir coragem mesmo sentindo medo", ""),
            Episode(16, "Sansão: aprender a usar bem seus dons", ""),
            Episode(17, "Rute: demonstrar amizade e lealdade", ""),
            Episode(18, "Noemi: reencontrar esperança", ""),
            Episode(19, "Boaz: agir com bondade e justiça", ""),
            Episode(20, "Ana: orar com confiança", "1 Samuel 1–2"),
            Episode(21, "Samuel: aprender a ouvir a voz de Deus", ""),
            Episode(22, "Davi: enfrentar um gigante", ""),
            Episode(23, "Davi: respeitar Saul mesmo sendo perseguido", ""),
            Episode(24, "Jônatas: ser um amigo verdadeiro", ""),
            Episode(25, "Abigail: impedir uma grande briga", "1 Samuel 25"),
            Episode(26, "Mefibosete: recebido com bondade pelo rei", "2 Samuel 9"),
            Episode(27, "Salomão: escolher sabedoria em vez de riqueza", ""),
            Episode(28, "Elias: confiar que Deus proverá", ""),
            Episode(29, "Eliseu: servir antes de liderar", ""),
            Episode(30, "A menina israelita que ajudou Naamã", "2 Reis 5"),
            Episode(31, "Josias: o jovem rei que reencontrou a Lei", "2 Reis 22–23"),
            Episode(32, "Ezequias: apresentar seus problemas a Deus", "2 Reis 18–20"),
            Episode(33, "Ester: coragem para defender seu povo", ""),
            Episode(34, "Mardoqueu: fazer o que é certo", ""),
            Episode(35, "Neemias: reconstruir com perseverança", ""),
            Episode(36, "Esdras: ensinar e praticar a Palavra", ""),
            Episode(37, "Jó: permanecer fiel na dificuldade", ""),
            Episode(38, "Daniel: continuar orando apesar do perigo", ""),
            Episode(39, "Sadraque, Mesaque e Abede-Nego: permanecer firmes", ""),
            Episode(40, "Jonas: receber uma segunda oportunidade", ""),
            Episode(41, "Maria: aceitar uma grande missão", ""),
            Episode(42, "José, pai terreno de Jesus: proteger e obedecer", ""),
            Episode(43, "Isabel: esperar com fé", ""),
            Episode(44, "João Batista: preparar o caminho", ""),
            Episode(45, "Pedro: aprender depois de seus erros", ""),
            Episode(46, "André: levar pessoas até Jesus", ""),
            Episode(47, "João: aprender sobre o amor", ""),
            Episode(48, "Mateus: deixar a antiga vida e seguir Jesus", ""),
            Episode(49, "Tomé: levar suas dúvidas a Jesus", ""),
            Episode(50, "Maria Madalena: anunciar a ressurreição", ""),
            Episode(51, "Marta: aprender a equilibrar serviço e atenção", ""),
            Episode(52, "Maria de Betânia: escolher ouvir Jesus", ""),
            Episode(53, "Zaqueu: corrigir os próprios erros", ""),
            Episode(54, "Barnabé: o amigo que encorajava", "Atos 4; 9; 11"),
            Episode(55, "Estêvão: permanecer fiel", "Atos 6–7"),
            Episode(56, "Filipe: explicar as Escrituras", "Atos 8"),
            Episode(57, "Paulo: uma vida completamente transformada", "Atos 9"),
            Episode(58, "Silas: cantar mesmo na prisão", "Atos 16"),
            Episode(59, "Lídia: abrir o coração e a casa", "Atos 16"),
            Episode(60, "Priscila e Áquila: trabalhar e ensinar juntos", "Atos 18"),
            Episode(61, "Timóteo: aprender desde criança", "2 Timóteo 1 e 3"),
            Episode(62, "Dorcas: usar seus talentos para ajudar", "Atos 9"),
            Episode(63, "O menino que compartilhou seus pães e peixes", "João 6"),
        ),
    ),
    "Milagres da Bíblia": Playlist(
        name="Milagres da Bíblia",
        episodes=(
            Episode(1, "A travessia do Mar Vermelho", "Êxodo 14"),
            Episode(2, "Água amarga se torna própria para beber", "Êxodo 15"),
            Episode(3, "O maná no deserto", "Êxodo 16"),
            Episode(4, "Água saindo da rocha", "Êxodo 17"),
            Episode(5, "A travessia do rio Jordão", "Josué 3"),
            Episode(6, "A queda das muralhas de Jericó", "Josué 6"),
            Episode(7, "O sol permanece no céu", "Josué 10"),
            Episode(8, "A farinha e o azeite que não acabaram", "1 Reis 17"),
            Episode(9, "O filho da viúva volta à vida", "1 Reis 17"),
            Episode(10, "Fogo desce no monte Carmelo", "1 Reis 18"),
            Episode(11, "Elias é levado ao céu", "2 Reis 2"),
            Episode(12, "As águas são purificadas", "2 Reis 2"),
            Episode(13, "O azeite da viúva se multiplica", "2 Reis 4"),
            Episode(14, "O filho da sunamita volta à vida", "2 Reis 4"),
            Episode(15, "Uma grande panela é purificada", "2 Reis 4"),
            Episode(16, "Pães são multiplicados por Eliseu", "2 Reis 4"),
            Episode(17, "Naamã é curado", "2 Reis 5"),
            Episode(18, "O machado que flutuou", "2 Reis 6"),
            Episode(19, "Daniel é protegido na cova dos leões", "Daniel 6"),
            Episode(20, "Os três amigos são protegidos na fornalha", "Daniel 3"),
            Episode(21, "Jonas sobrevive dentro do grande peixe", "Jonas 1–2"),
            Episode(22, "Água se transforma em vinho", "João 2"),
            Episode(23, "A pesca maravilhosa", "Lucas 5"),
            Episode(24, "Jesus cura um homem com lepra", "Marcos 1"),
            Episode(25, "Jesus cura o servo do centurião", "Mateus 8"),
            Episode(26, "Jesus cura a sogra de Pedro", "Marcos 1"),
            Episode(27, "O paralítico desce pelo telhado", "Marcos 2"),
            Episode(28, "Jesus cura o homem da mão ressequida", "Marcos 3"),
            Episode(29, "Jesus acalma a tempestade", "Marcos 4"),
            Episode(30, "Jesus liberta um homem atormentado", "Marcos 5"),
            Episode(31, "A filha de Jairo volta à vida", "Marcos 5"),
            Episode(32, "Uma mulher é curada ao tocar o manto de Jesus", "Marcos 5"),
            Episode(33, "Dois homens cegos recuperam a visão", "Mateus 9"),
            Episode(34, "A multiplicação dos pães e peixes", "João 6"),
            Episode(35, "Jesus caminha sobre as águas", "Mateus 14"),
            Episode(36, "Pedro caminha sobre as águas", "Mateus 14"),
            Episode(37, "Jesus cura a filha de uma mulher estrangeira", "Marcos 7"),
            Episode(38, "Jesus cura um homem surdo", "Marcos 7"),
            Episode(39, "A segunda multiplicação dos pães", "Marcos 8"),
            Episode(40, "Jesus cura um menino", "Marcos 9"),
            Episode(41, "A moeda encontrada na boca do peixe", "Mateus 17"),
            Episode(42, "Jesus cura dez homens com lepra", "Lucas 17"),
            Episode(43, "Jesus cura um homem cego de nascença", "João 9"),
            Episode(44, "Jesus cura uma mulher encurvada", "Lucas 13"),
            Episode(45, "Jesus cura um homem junto ao tanque de Betesda", "João 5"),
            Episode(46, "Jesus cura Bartimeu", "Marcos 10"),
            Episode(47, "Lázaro volta à vida", "João 11"),
            Episode(48, "A figueira seca", "Marcos 11"),
            Episode(49, "A orelha do servo é restaurada", "Lucas 22"),
            Episode(50, "A ressurreição de Jesus", "Mateus 28"),
            Episode(51, "A segunda pesca maravilhosa", "João 21"),
            Episode(52, "O Espírito Santo no Pentecostes", "Atos 2"),
            Episode(53, "Pedro cura um homem que não podia andar", "Atos 3"),
            Episode(54, "Pedro é libertado da prisão", "Atos 12"),
            Episode(55, "Paulo e Silas e o terremoto na prisão", "Atos 16"),
            Episode(56, "Êutico volta à vida", "Atos 20"),
            Episode(57, "Paulo sobrevive ao naufrágio", "Atos 27"),
            Episode(58, "Paulo é protegido da serpente", "Atos 28"),
        ),
    ),
    "Lições de Fé e Coragem": Playlist(
        name="Lições de Fé e Coragem",
        episodes=(
            Episode(1, "Confiar em Deus diante de um gigante — Davi", ""),
            Episode(2, "Fazer o que é certo mesmo sozinho — Noé", ""),
            Episode(3, "Obedecer mesmo sem conhecer todo o caminho — Abraão", ""),
            Episode(4, "Aprender a esperar com paciência — Abraão e Sara", ""),
            Episode(5, "Perdoar quem nos machucou — José e seus irmãos", ""),
            Episode(6, "Não desistir por causa da timidez — Moisés", ""),
            Episode(7, "Enfrentar mudanças com confiança — Josué", ""),
            Episode(8, "Ser corajoso mesmo sentindo medo — Gideão", ""),
            Episode(9, "Manter a palavra e ser leal — Rute", ""),
            Episode(10, "Ouvir com atenção — Samuel", ""),
            Episode(11, "Ser um amigo verdadeiro — Davi e Jônatas", ""),
            Episode(12, "Escolher a paz em vez da vingança — Davi e Saul", ""),
            Episode(13, "Usar palavras sábias para impedir uma briga — Abigail", ""),
            Episode(14, "Pedir sabedoria para tomar decisões — Salomão", ""),
            Episode(15, "Compartilhar mesmo quando temos pouco — a viúva de Sarepta", ""),
            Episode(16, "Reconhecer a voz suave de Deus — Elias", ""),
            Episode(17, "Ajudar alguém sem esperar recompensa — a menina e Naamã", ""),
            Episode(18, "Orar mesmo quando parece difícil — Daniel", ""),
            Episode(19, "Permanecer firme diante da pressão — os três amigos de Daniel", ""),
            Episode(20, "Defender outras pessoas com coragem — Ester", ""),
            Episode(21, "Reconstruir depois de uma dificuldade — Neemias", ""),
            Episode(22, "Aceitar uma segunda oportunidade — Jonas", ""),
            Episode(23, "Não fugir das responsabilidades", ""),
            Episode(24, "Reconhecer um erro e pedir perdão", ""),
            Episode(25, "Fazer as pazes com a família — Jacó e Esaú", ""),
            Episode(26, "Controlar a raiva antes de agir — Caim e Abel", ""),
            Episode(27, "Não ter inveja dos dons dos outros", ""),
            Episode(28, "Usar nossos talentos para fazer o bem", ""),
            Episode(29, "Não julgar uma pessoa pela aparência", "1 Samuel 16"),
            Episode(30, "Ter coragem para começar algo novo", ""),
            Episode(31, "Permanecer fiel nas pequenas tarefas", ""),
            Episode(32, "Ser honesto mesmo quando ninguém está olhando", ""),
            Episode(33, "Cuidar de quem está sozinho", ""),
            Episode(34, "Receber bem quem é diferente", ""),
            Episode(35, "Dividir o que temos — o menino dos pães e peixes", ""),
            Episode(36, "Amar o próximo — o bom samaritano", ""),
            Episode(37, "Voltar para casa e recomeçar — o filho pródigo", ""),
            Episode(38, "Procurar quem está perdido — a ovelha perdida", ""),
            Episode(39, "Construir a vida sobre bons ensinamentos — a casa na rocha", ""),
            Episode(40, "Tratar os outros como queremos ser tratados", ""),
            Episode(41, "Perdoar mais de uma vez", ""),
            Episode(42, "Demonstrar gratidão — o homem que voltou para agradecer", ""),
            Episode(43, "Não se preocupar excessivamente — as aves e os lírios", ""),
            Episode(44, "Escolher o que realmente importa — Marta e Maria", ""),
            Episode(45, "Corrigir os próprios erros — Zaqueu", ""),
            Episode(46, "Acolher as crianças — Jesus e os pequenos", ""),
            Episode(47, "Enfrentar uma tempestade sem perder a esperança", ""),
            Episode(48, "Levar nossas dúvidas a Jesus — Tomé", ""),
            Episode(49, "Recomeçar depois de falhar — Pedro", ""),
            Episode(50, "Servir com humildade — Jesus lava os pés", ""),
            Episode(51, "Permanecer unido nos momentos difíceis", ""),
            Episode(52, "Orar por quem nos trata mal", ""),
            Episode(53, "Ser luz por meio de boas atitudes", ""),
            Episode(54, "Não esconder os próprios talentos", ""),
            Episode(55, "Persistir na oração", ""),
            Episode(56, "Aprender a agradecer diariamente", ""),
            Episode(57, "Ajudar sem procurar aplausos", ""),
            Episode(58, "Dizer a verdade com bondade", ""),
            Episode(59, "Defender quem sofre injustiça", ""),
            Episode(60, "Ter esperança quando tudo parece perdido", ""),
            Episode(61, "Cuidar da criação de Deus", ""),
            Episode(62, "Respeitar pais, responsáveis e professores", ""),
            Episode(63, "Saber pedir ajuda", ""),
            Episode(64, "Reconhecer que não sabemos tudo", ""),
            Episode(65, "Compartilhar a fé sem desrespeitar outras pessoas", ""),
            Episode(66, "Escolher bons amigos", ""),
            Episode(67, "Incentivar alguém que está desanimado — Barnabé", ""),
            Episode(68, "Louvar a Deus em momentos difíceis — Paulo e Silas", ""),
            Episode(69, "Trabalhar em equipe — Priscila e Áquila", ""),
            Episode(70, "Usar nossas habilidades para ajudar — Dorcas", ""),
            Episode(71, "Aprender a ser generoso", ""),
            Episode(72, "Não responder ao mal com o mal", ""),
            Episode(73, "Ser paciente com pessoas diferentes", ""),
            Episode(74, "Demonstrar compaixão", ""),
            Episode(75, "Cumprir promessas", ""),
            Episode(76, "Vencer o medo de tentar novamente", ""),
            Episode(77, "Praticar justiça e misericórdia", ""),
            Episode(78, "Confiar em Deus durante mudanças", ""),
            Episode(79, "Encontrar coragem na oração", ""),
            Episode(80, "Transformar preocupação em confiança", ""),
        ),
    ),
}

# Connected series published back-to-back to encourage binge watching.
SERIES: tuple[tuple[str, int, int], ...] = (
    # (playlist, first_number, last_number)
    ("Aventuras do Antigo Testamento", 13, 17),  # José: sonhos, dificuldades e perdão
    ("Aventuras do Antigo Testamento", 18, 25),  # Moisés: do cesto ao Mar Vermelho
    ("Aventuras do Antigo Testamento", 52, 54),  # Daniel e seus amigos na Babilônia
)

# Themes that need child-safe adaptation (no graphic violence, ages 6-10).
SENSITIVE_THEMES: frozenset[str] = frozenset({
    "Adão e Eva no Jardim do Éden",
    "Caim e Abel: aprendendo a controlar a raiva",
    "Noé e a grande arca",
    "A fuga da família de Jesus para o Egito",
    "A última ceia",
    "Jesus ora no Getsêmani",
    "A ressurreição de Jesus",
})

EPISODES_PUBLISHED = 1  # EP1 "A criação do mundo" already produced/approved.


class ContentPlanError(ValueError):
    """Raised when the roadmap is inconsistent."""


def next_episode(published: int = EPISODES_PUBLISHED) -> tuple[Playlist, Episode]:
    """Return the next playlist+episode to produce given how many are published.

    Episodes are consumed in strict roadmap order across playlists:
    all of Aventuras do AT, then Histórias de Jesus, etc.
    """
    remaining = published
    for playlist in ROADMAP.values():
        if remaining < len(playlist.episodes):
            return playlist, playlist.episodes[remaining]
        remaining -= len(playlist.episodes)
    raise ContentPlanError("All roadmap episodes have been produced.")


def find_episode(playlist_name: str, number: int) -> tuple[Playlist, Episode]:
    playlist = ROADMAP.get(playlist_name)
    if playlist is None:
        raise ContentPlanError(f"Unknown playlist: {playlist_name}")
    for episode in playlist.episodes:
        if episode.number == number:
            return playlist, episode
    raise ContentPlanError(f"Episode {number} not found in playlist {playlist_name}")


def series_label(playlist_name: str, number: int) -> str | None:
    """Return the connected-series label if this episode belongs to one."""
    for name, first, last in SERIES:
        if name == playlist_name and first <= number <= last:
            _, ep_first = find_episode(name, first)
            span = last - first + 1
            base = ep_first.theme.split(":")[0].split("—")[0].strip()
            return f"{base}: série de {span} episódios"
    return None
