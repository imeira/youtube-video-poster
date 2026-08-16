"""
BENCHMARK B5 v2 — TTS A/B: Thalita + outras vozes pt-BR + word-timestamps
Fix: capture WordBoundary via stream(), not save()
"""
import asyncio
import edge_tts
import json
import os
import time
import subprocess

SAMPLE = (
    "Era uma vez, num pequeno reino chamado Israel, "
    "um jovem pastor chamado Davi. "
    "Ele era o mais novo de oito irmãos e cuidava das ovelhas do seu pai. "
    "Enquanto seus irmãos eram soldados do rei Saul, "
    "Davi ficava nos campos, tocando sua harpa e cantando para as ovelhas. "
    "Mas um dia, tudo mudou."
)

VOICES = {
    "Thalita": "pt-BR-ThalitaNeural",
    "Antonio": "pt-BR-AntonioNeural",
    "Francisca": "pt-BR-FranciscaNeural",
}

async def generate_voice(voice_name, voice_id, text, output_dir):
    audio_path = os.path.join(output_dir, f"tts_{voice_name}.mp3")
    srt_path = os.path.join(output_dir, f"tts_{voice_name}.srt")
    ts_path = os.path.join(output_dir, f"tts_{voice_name}_ts.json")

    word_boundaries = []
    audio_chunks = []

    t_start = time.time()

    # Use stream() to capture both audio and WordBoundary events simultaneously
    communicate = edge_tts.Communicate(text, voice_id, boundary="WordBoundary")
    submaker = edge_tts.SubMaker()

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            submaker.feed(chunk)
            word_boundaries.append({
                "offset_ms": chunk["offset"] / 10000,
                "duration_ms": chunk["duration"] / 10000,
                "text": chunk["text"],
            })

    t_gen = time.time() - t_start

    # Save audio
    with open(audio_path, "wb") as f:
        f.write(b"".join(audio_chunks))

    # Save SRT
    srt_content = submaker.get_srt()
    with open(srt_path, "w", encoding="utf-8") as f:
        f.write(srt_content)

    # Save timestamps
    with open(ts_path, "w", encoding="utf-8") as f:
        json.dump(word_boundaries, f, indent=2, ensure_ascii=False)

    # Get duration
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", audio_path],
        capture_output=True, text=True
    )
    duration_s = float(result.stdout.strip()) if result.returncode == 0 else None

    size_kb = os.path.getsize(audio_path) / 1024

    return {
        "voice": voice_name,
        "voice_id": voice_id,
        "gen_time_s": round(t_gen, 2),
        "audio_duration_s": round(duration_s, 2) if duration_s else None,
        "file_size_kb": round(size_kb, 1),
        "word_count": len(word_boundaries),
        "has_timestamps": len(word_boundaries) > 0,
        "first_5_timestamps": word_boundaries[:5],
        "audio_path": audio_path,
        "srt_path": srt_path,
    }

async def main():
    output_dir = os.path.join(os.environ.get("LOCALAPPDATA", "/tmp"), "Temp")
    results = []

    for name, voice_id in VOICES.items():
        print(f"\n--- Gerando TTS: {name} ({voice_id}) ---")
        try:
            r = await generate_voice(name, voice_id, SAMPLE, output_dir)
            results.append(r)
            print(f"  Gerado em {r['gen_time_s']}s")
            print(f"  Duração: {r['audio_duration_s']}s | Tamanho: {r['file_size_kb']}KB")
            print(f"  Word boundaries: {r['word_count']}")
            if r["first_5_timestamps"]:
                for wb in r["first_5_timestamps"]:
                    print(f"    {wb['offset_ms']:.0f}ms (+{wb['duration_ms']:.0f}ms): '{wb['text']}'")
        except Exception as e:
            print(f"  ERRO: {e}")
            results.append({"voice": name, "error": str(e)})

    out_path = os.path.join(output_dir, "benchmark_b5_tts_v2_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print("RESUMO B5 — TTS")
    print(f"{'='*60}")
    for r in results:
        if "error" in r:
            print(f"  {r['voice']}: ERRO — {r['error']}")
        else:
            print(f"  {r['voice']}: {r['gen_time_s']}s gen → {r['audio_duration_s']}s audio | "
                  f"{r['word_count']} words com timestamps | {r['file_size_kb']}KB")

asyncio.run(main())
