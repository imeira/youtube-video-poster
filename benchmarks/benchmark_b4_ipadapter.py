"""
BENCHMARK B4 — IP-Adapter: teste de consistência de personagem
Gera 10 imagens do mesmo personagem (Davi) usando IP-Adapter + SD 1.5.
Mede tempo, VRAM, e avalia consistência visual.
"""
import torch
import time
import os
import subprocess
import json

os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

def get_gpu_stats():
    try:
        result = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            parts = [x.strip() for x in result.stdout.strip().split(",")]
            return {"temp_c": float(parts[0]), "vram_used_mb": float(parts[2])}
    except:
        pass
    return None

def benchmark_ip_adapter():
    from diffusers import StableDiffusionPipeline, StableDiffusionImg2ImgPipeline
    from diffusers.utils import load_image

    print(f"\n{'='*60}")
    print(f"Benchmark B4: IP-Adapter + SD 1.5 — Consistência de Personagem")
    print(f"{'='*60}")

    output_dir = os.path.join(os.environ.get("LOCALAPPDATA", "/tmp"), "Temp")

    # Step 1: Generate a reference character image first (without IP-Adapter)
    print("\n1. Gerando imagem de referência do personagem Davi...")
    pipe = StableDiffusionPipeline.from_pretrained(
        "stable-diffusion-v1-5/stable-diffusion-v1-5",
        torch_dtype=torch.float16,
        safety_checker=None,
        requires_safety_checker=False,
    )

    # Load IP-Adapter
    print("2. Carregando IP-Adapter...")
    try:
        pipe.load_ip_adapter(
            "h94/IP-Adapter",
            subfolder="models",
            weight_name="ip-adapter_sd15.bin"
        )
        print("   IP-Adapter carregado!")
    except Exception as e:
        print(f"   IP-Adapter falhou: {e}")
        print("   Tentando via download manual...")

        # Alternative: download IP-Adapter weights
        from huggingface_hub import hf_hub_download
        ip_adapter_path = hf_hub_download(
            "h94/IP-Adapter",
            "models/ip-adapter_sd15.bin"
        )
        print(f"   Baixado: {ip_adapter_path}")
        pipe.load_ip_adapter(
            "h94/IP-Adapter",
            subfolder="models",
            weight_name="ip-adapter_sd15.bin"
        )
        print("   IP-Adapter carregado!")

    pipe.enable_sequential_cpu_offload()
    pipe.enable_attention_slicing()

    # Generate reference image (young David, shepherd boy)
    ref_prompt = "a young shepherd boy named David, about 12 years old, brown hair, wearing a simple tunic, holding a staff, biblical era, children's book illustration style, colorful, friendly"
    print(f"\n3. Gerando referência: '{ref_prompt[:60]}...'")

    t_start = time.time()
    ref_image = pipe(
        prompt=ref_prompt,
        num_inference_steps=20,
        height=512,
        width=512,
        generator=torch.Generator("cpu").manual_seed(100),
    ).images[0]
    t_ref = time.time() - t_start

    ref_path = os.path.join(output_dir, "b4_reference_davi.png")
    ref_image.save(ref_path)
    print(f"   Referência salva em {t_ref:.1f}s: {ref_path}")

    stats = get_gpu_stats()
    print(f"   GPU: {stats}")

    # Step 2: Generate 10 variations using IP-Adapter
    print(f"\n4. Gerando 10 variações com IP-Adapter (ip_adapter_scale=0.6)...")

    # Different scenarios for the same character
    scenarios = [
        "David the young shepherd boy standing in a green field with sheep, sunny day, children's book style",
        "David the young shepherd boy looking at the giant Goliath, dramatic scene, children's book style",
        "David the young shepherd boy playing a harp, peaceful evening, children's book style",
        "David the young shepherd boy picking up stones from a riverbed, determined look, children's book style",
        "David the young shepherd boy facing Goliath the giant, brave, children's book style",
        "David the young shepherd boy with his sheep at sunset, pastoral scene, children's book style",
        "David the young shepherd boy praying, kneeling, soft light, children's book style",
        "David the young shepherd boy holding a sling, confident, children's book style",
        "David the young shepherd boy talking to king Saul, palace setting, children's book style",
        "David the young shepherd boy celebrating victory, joyful, children's book style",
    ]

    results = []
    for i, prompt in enumerate(scenarios):
        print(f"\n  Imagem {i+1}/10: {prompt[:50]}...")
        t_start = time.time()

        try:
            image = pipe(
                prompt=prompt,
                ip_adapter_image=ref_image,
                ip_adapter_scale=0.6,
                num_inference_steps=20,
                height=512,
                width=512,
                generator=torch.Generator("cpu").manual_seed(200 + i),
            ).images[0]
            t_elapsed = time.time() - t_start

            stats = get_gpu_stats()
            save_path = os.path.join(output_dir, f"b4_davi_ipadapter_{i+1:02d}.png")
            image.save(save_path)

            result = {
                "image": i + 1,
                "prompt": prompt,
                "time_s": round(t_elapsed, 2),
                "temp_c": stats["temp_c"] if stats else None,
                "vram_used_mb": stats["vram_used_mb"] if stats else None,
                "path": save_path,
            }
            results.append(result)
            print(f"    {t_elapsed:.1f}s | Temp: {stats['temp_c'] if stats else '?'}C | VRAM: {stats['vram_used_mb'] if stats else '?'}MB")

        except Exception as e:
            print(f"    ERRO: {e}")
            results.append({"image": i + 1, "error": str(e)})

    avg_time = sum(r.get("time_s", 0) for r in results if "time_s" in r) / max(1, sum(1 for r in results if "time_s" in r))

    summary = {
        "reference_time_s": round(t_ref, 2),
        "ref_path": ref_path,
        "ip_adapter_scale": 0.6,
        "num_images": 10,
        "avg_time_per_image_s": round(avg_time, 2),
        "total_time_s": round(sum(r.get("time_s", 0) for r in results if "time_s" in r), 2),
        "individual_results": results,
    }

    out_path = os.path.join(output_dir, "benchmark_b4_ipadapter_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    print(f"\n{'='*60}")
    print(f"RESUMO B4 — IP-Adapter")
    print(f"{'='*60}")
    print(f"Referência: {t_ref:.1f}s")
    print(f"Tempo médio por imagem: {avg_time:.1f}s")
    print(f"Tempo total (10 imagens): {summary['total_time_s']:.1f}s")
    print(f"Resultados: {out_path}")

if __name__ == "__main__":
    benchmark_ip_adapter()
