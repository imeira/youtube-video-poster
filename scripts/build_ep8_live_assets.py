"""Maintain the reviewed-copy candidate and deployment schema; never uses APIs.

The deployment's human script authority must review and pin the resulting bytes.
Changing this copy retires existing approvals. This is not a runtime author.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src/hybrid/assets"

COPY = [
    ("15:1-6", "Abrão ainda não tinha o filho que tanto esperava. Numa visão, Deus falou com ele e disse que não tivesse medo. Deus seria sua proteção. Aquela conversa trouxe uma promessa para ouvir com atenção.", "Abram sits outside his tent at night, looking thoughtful; wide sky and warm lamplight", ["abraham"]),
    ("15:1-6", "Abrão contou a Deus sua preocupação. Sem um filho, quem receberia o que ele tinha? Ele pensava em alguém de sua casa. Abrão podia falar sobre aquilo que não entendia e apresentar sua pergunta.", "Abram opens his empty hands in a questioning gesture beside the tent; no other people", ["abraham"]),
    ("15:1-6", "A resposta de Deus foi clara: o herdeiro seria um filho do próprio Abrão. A promessa não era apenas sobre coisas que ele possuía. Era sobre uma família que ainda cresceria no futuro.", "Close view of Abram listening quietly, hands resting on his knees, hopeful eyes", ["abraham"]),
    ("15:1-6", "Então Deus levou Abrão para fora e mandou que olhasse para o céu. Havia estrelas para contar! Será que Abrão conseguiria contar todas? Assim seria o grande número de seus descendentes, anunciou Deus.", "Abram stands beyond his tent, head tilted up, lifting one hand toward a vast star-filled sky", ["abraham"]),
    ("15:1-6", "Descendentes são os filhos e as gerações que vêm depois deles. Abrão ainda não via aquela grande família. Mas ele creu em Deus. O texto conta que Deus considerou essa confiança como justiça.", "Abram lowers his hand to his heart under the stars; peaceful face; no imagined children", ["abraham"]),
    ("17:1-9", "Mais adiante, quando Abrão tinha noventa e nove anos, Deus voltou a falar com ele. Apresentou-se como o Deus Todo-Poderoso. Chamou Abrão a viver em sua presença e a fazer o que era certo.", "Elderly Abram pauses on a sunlit path near his tents, listening with a serious expression", ["abraham"]),
    ("17:1-9", "Deus falou de uma aliança. Uma aliança é um compromisso. Abrão se prostrou, com o rosto voltado para o chão, enquanto ouvia. Deus prometeu que dele viriam muitos povos, e também reis.", "Abram bows with his face toward the ground beside his tent; respectful side view", ["abraham"]),
    ("17:1-9", "Nessa conversa, Abrão recebeu um novo nome: Abraão. Deus explicou que ele seria pai de muitas nações. O nome novo apontava para a promessa. A família anunciada seria muito maior do que ele podia ver.", "Abraham rises slowly from his bow, looking across an open landscape; no crowns or future crowds", ["abraham"]),
    ("17:1-9", "Deus disse que sua aliança continuaria com os descendentes de Abraão, pelas gerações. Também falou da terra de Canaã. Abraão e sua família deveriam guardar essa aliança. Era um compromisso para levar a sério.", "Abraham stands at the edge of the camp surveying the hills of Canaan, one hand on a walking staff", ["abraham"]),
    ("17:15-21", "Sarai também recebeu um novo nome: Sara. Deus disse que a abençoaria e que ela teria um filho. Da família dela viriam povos e reis. Sara fazia parte daquela promessa de um modo muito especial.", "Sarah stands at her tent doorway, arranging a folded cloth; gentle dignified portrait of an elderly woman", ["sarah"]),
    ("17:15-21", "Abraão se prostrou e riu. Pensou em sua idade e na idade de Sara. Ele teria cem anos, e ela, noventa. Como poderiam ter um filho? Era uma notícia muito maior do que ele esperava.", "Abraham bows and smiles in astonishment, hand near his mouth; no mockery and no child", ["abraham"]),
    ("17:15-21", "Abraão falou de Ismael, seu filho, e pediu que ele vivesse diante de Deus. Deus respondeu que abençoaria Ismael também. Ele teria muitos descendentes. Mas a aliança anunciada seria estabelecida com o filho de Sara.", "Abraham lifts his face with a caring, pleading expression, palms open; Ishmael remains offscreen", ["abraham"]),
    ("17:15-21", "Deus disse que Sara teria um filho e que o nome dele seria Isaque. A aliança continuaria com ele. Deus também anunciou um tempo: Sara teria o menino naquela época do ano seguinte.", "Abraham listens intently beside his tent, gaze lifted just above the horizon; no visible divine figure", ["abraham"]),
    ("17:15-21", "Por enquanto, Isaque era o filho prometido. A notícia falava do futuro. Abraão havia ouvido o nome e o tempo anunciado por Deus. Ainda havia espera pela frente, antes de conhecer aquele menino.", "Wide quiet view of Abraham alone outside the family tents in late afternoon; no baby objects", ["abraham"]),
    ("18:1-15", "Depois, junto aos carvalhos de Manre, Abraão estava sentado à entrada da tenda, no calor do dia. Ao levantar os olhos, viu três homens perto dele. Abraão correu para recebê-los e se curvou.", "Abraham approaches three ordinary adult travelers near shady oak trees, bowing in welcome; no halos or divine attributes", ["abraham"]),
    ("18:1-15", "Abraão ofereceu água para os visitantes lavarem os pés e descanso debaixo da árvore. Também ofereceu comida para que recuperassem as forças antes de seguir viagem. Os visitantes aceitaram. Era hora de preparar a refeição.", "Abraham gestures toward a water basin and shaded resting place for three ordinary travelers", ["abraham"]),
    ("18:1-15", "Abraão foi depressa até Sara, na tenda, e pediu que preparasse pães com farinha. Sara recebeu aquele pedido enquanto Abraão cuidava da refeição. A visita trouxe movimento ao acampamento naquele dia quente.", "Sarah kneads flour dough inside the open tent while Abraham speaks from the entrance; utensils on a low table", ["abraham", "sarah"]),
    ("18:1-15", "Abraão escolheu um bezerro e pediu a um rapaz que preparasse a carne. Depois, levou coalhada, leite e a comida aos visitantes. Enquanto eles comiam debaixo da árvore, Abraão ficou ali, pronto para atendê-los.", "Abraham places prepared food and milk bowls before three travelers seated under an oak; no slaughter or raw meat", ["abraham"]),
    ("18:1-15", "Os visitantes perguntaram onde estava Sara, sua mulher. Abraão respondeu que ela estava na tenda. Então veio o anúncio: no tempo indicado, no ano seguinte, Sara teria um filho. Ela escutava à entrada da tenda.", "Sarah listens from the tent entrance in foreground; Abraham and three ordinary travelers converse under the tree behind her", ["abraham", "sarah"]),
    ("18:1-15", "Abraão e Sara eram bem idosos. Sara já havia passado da idade de ter filhos. Ao ouvir a promessa, ela riu consigo mesma. Pensou em como aquilo poderia acontecer, depois de tantos anos.", "Close portrait of elderly Sarah partly behind the tent curtain, one hand near her lips, surprised private smile", ["sarah"]),
    ("18:1-15", "O Senhor perguntou a Abraão por que Sara havia rido. Então fez uma pergunta importante: haveria alguma coisa difícil demais para o Senhor? A promessa foi repetida. No tempo anunciado, Sara teria um filho.", "Abraham listens with renewed attention under the oak; tent visible behind; no person identified visually as God", ["abraham"]),
    ("18:1-15", "Sara ficou com medo e disse que não havia rido. Mas ouviu a resposta de que tinha rido, sim. A história mostra sua surpresa e seu receio. A promessa continuava, mesmo diante daquela reação.", "Sarah stands at the tent doorway with a hesitant expression, shoulders slightly drawn inward; gentle daylight", ["sarah"]),
    ("18:1-15", "Nossa história de hoje para aqui, antes do nascimento de Isaque. Abraão e Sara ouviram a promessa de um filho. Ele ainda era esperado. O anúncio do Senhor apontava para o tempo que viria.", "Abraham and Sarah stand beside their tent at sunset, looking ahead together; both elderly; no infant", ["abraham", "sarah"]),
    (None, "Pensando nessa história, podemos conversar sobre a espera. Esperar pode trazer perguntas, surpresa e até preocupação. Quando isso acontecer, podemos falar com um adulto de confiança sobre o que sentimos. Não precisamos esconder nossas dúvidas.", "Reflective closing illustration: Abraham and Sarah seated quietly outside their tent, warm dusk colors", ["abraham", "sarah"]),
    (None, "As estrelas lembram a promessa que Abrão ouviu. A tenda lembra a notícia que Sara escutou. Qual desses momentos você gostaria de conversar com sua família? Podemos ouvir uns aos outros com atenção e carinho.", "Wide closing view of Abraham and Sarah beside their tent as the first stars appear; peaceful hopeful mood", ["abraham", "sarah"]),
]


def build():
    ROOT.mkdir(exist_ok=True)
    segments = [dict(id=f"S{i:03d}", narration=text,
                     kind="biblical_paraphrase" if ref else "family_reflection",
                     source_refs=["Gênesis " + ref] if ref else [],
                     visual_action=action, characters=characters)
                for i, (ref, text, action, characters) in enumerate(COPY, 1)]
    script = dict(title="Estrelas, uma tenda e a promessa", audience=dict(min_age=6, max_age=10),
                  closing_duration_s=4, segments=segments,
                  narration="\n\n".join(s["narration"] for s in segments), evidence_mode="LIVE",
                  editorial_note="New EP8 copy v1. Human deployment approval must review the exact hash. Visual staging is illustrative; family reflections are not biblical claims.")
    (ROOT / "ep8_promise_v1.json").write_text(json.dumps(script, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    string = {"type": "string", "minLength": 1}
    money = dict(type="string", pattern=r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")
    checksum = dict(type="string", pattern="[0-9a-f]{64}")
    const = lambda x: dict(type="boolean" if isinstance(x, bool) else "integer" if isinstance(x, int) else "string", const=x)
    obj = lambda **kw: dict(type="object", additionalProperties=False, required=list(kw), properties=kw)
    timestamp = dict(type="number", minimum=1, maximum=4102444800)
    evidence = dict(authority=string, source_url=dict(type="string", pattern=r"https://[^\s]+"),
                    observed_at=timestamp, valid_until=timestamp)
    schema = obj(schema_version=const(1), adapter=const("src.hybrid.revision_live:factory"),
        endpoint=const("fal-ai/flux-2/klein/9b/edit"), image_cost=money, non_image_reserve=money,
        visual_license=string,
        script=obj(sha256=checksum, authority=string),
        image_price=obj(**evidence, endpoint=const("fal-ai/flux-2/klein/9b/edit"), amount=money,
            manifest_checksum=checksum, width=const(1280), height=const(720),
            num_images=const(1), output_format=const("png"), includes_reference_inputs=const(True)),
        tts=obj(provider=const("edge-tts"), voice=const("pt-BR-ThalitaNeural"), rate=const("-8%"),
                pitch=const("+1Hz"), boundary=const("WordBoundary"), cost=const("0"), authority=string),
        telegram=obj(chat_id=dict(type="string", pattern=r"-?[1-9][0-9]*"), authority=string, cost=const("0")),
        reviewer=obj(url=const("https://openrouter.ai/api/v1/chat/completions"),
            key_env=const("EP8_OPENROUTER_REVIEW_KEY"), model=dict(type="string", pattern=r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"),
            provider=string, authority=string, supports_images=const(True), supports_json_schema=const(True),
            context_tokens=dict(type="integer", minimum=4096, maximum=2000000),
            max_tokens=dict(type="integer", minimum=256, maximum=2048),
            price=obj(**evidence, prompt_per_million=money, completion_per_million=money,
                      image=const("0"), request=const("0"))))
    schema.update({"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "EP8 explicit LIVE deployment v1"})
    (ROOT / "revision_deployment.schema.json").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    def blank(spec):
        if "const" in spec: return spec["const"]
        if spec["type"] == "object": return {k: blank(v) for k, v in spec["properties"].items()}
        if spec["type"] in {"integer", "number"}: return 0
        return "REQUIRED"
    example = blank(schema)
    import hashlib
    example["script"]["sha256"] = hashlib.sha256((ROOT / "ep8_promise_v1.json").read_bytes()).hexdigest()
    (ROOT / "revision_deployment.example.json").write_text(json.dumps(example, indent=2) + "\n", encoding="utf-8")
    plan = obj(route=const("ep8-new-revision-v1"), revision=dict(type="integer", minimum=1), mode=const("LIVE"),
        request=string, references=obj(manifest=string, characters=obj(abraham=checksum, sarah=checksum), authority=string),
        predecessors=dict(type="array", items=dict(type="object", required=["sha256"], properties={"sha256": checksum})),
        bindings=dict(type="object", additionalProperties=checksum), adapter=const("src.hybrid.revision_live:factory"),
        deployment={"$ref": "revision_deployment.schema.json"}, chat_id=dict(type="string", pattern=r"-?[1-9][0-9]*"),
        image_workers=dict(type="integer", minimum=1, maximum=8), qa_workers=dict(type="integer", minimum=1, maximum=8),
        ffmpeg_threads=const(1), correction_waves=const(1), heroes=const(False), budget_usd=const("6"),
        provider_contract=const("transactional-executor-v1; exact-price-and-authority; recover-only"), publication_authorized=const(False))
    plan["properties"]["references"]["additionalProperties"] = True
    plan["properties"]["supersedes"] = checksum
    plan.update({"$schema": "https://json-schema.org/draft/2020-12/schema", "title": "EP8 LIVE plan (created by CLI, never hand-approved)"})
    (ROOT / "revision_plan.schema.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    build()
