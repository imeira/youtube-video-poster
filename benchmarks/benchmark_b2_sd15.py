"""
BENCHMARK B2 — Stable Diffusion 1.5 na GTX 1050 Ti Max-Q (Pascal sm_61, 4GB VRAM)
Estratégia: sequential CPU offload + attention slicing + expandable_segments
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
             "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total,power.draw,clocks.sm",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            parts = [x.strip() for x in result.stdout.strip().split(",")]
            return {
                "temp_c": float(parts[0]),
                "gpu_util_pct": float(parts[1]),
                "vram_used_mb": float(parts[2]),
                "vram_total_mb": float(parts[3]),
                "power_w": float(parts[4]) if parts[4] != "[N/A]" else None,
                "sm_clock_mhz": float(parts[5]) if parts[5] != "[N/A]" else None,
            }
    except:
        pass
    return None

def benchmark_sd15(resolution=512, num_images=3, steps=20, use_fp16=True):
    print(f"\n{'='*60}")
    print(f"Benchmark: SD 1.5 @ {resolution}x{resolution}, {steps} steps, {'fp16' if use_fp16 else 'fp32'}")
    print(f"Strategy: sequential_cpu_offload + attention_slicing + expandable_segments")
    print(f"{'='*60}")

    stats_before = get_gpu_stats()
    print(f"GPU antes: {stats_before}")

    from diffusers import StableDiffusionPipeline

    dtype = torch.float16 if use_fp16 else torch.float32
    print(f"\nCargando modelo SD 1.5 ({'fp16' if use_fp16 else 'fp32'})...")

    t_load_start = time.time()
    pipe = StableDiffusionPipeline.from_pretrained(
        "stable-diffusion-v1-5/stable-diffusion-v1-5",
        torch_dtype=dtype,
        safety_checker=None,
        requires_safety_checker=False,
    )

    # For 4GB VRAM: sequential offload moves ALL components to CPU, loads to GPU one at a time
    # This is slower but actually fits in 4GB
    pipe.enable_sequential_cpu_offload()
    pipe.enable_attention_slicing()

    t_load = time.time() - t_load_start
    print(f"Modelo carregado em {t_load:.1f}s")

    stats_loaded = get_gpu_stats()
    print(f"GPU após carga: {stats_loaded}")

    # Warmup
    print("\nWarmup (1 imagem, 5 steps)...")
    t_warmup_start = time.time()
    try:
        _ = pipe(
            prompt="a simple test image, blue sky",
            num_inference_steps=5,
            height=resolution,
            width=resolution,
        )
        t_warmup = time.time() - t_warmup_start
        print(f"Warmup OK em {t_warmup:.1f}s")
    except Exception as e:
        print(f"Warmup FAILED: {e}")
        return None

    stats_warmup = get_gpu_stats()
    print(f"GPU após warmup: {stats_warmup}")

    # Benchmark
    prompt = "a beautiful biblical illustration of David and Goliath, children's book art style, colorful, detailed"

    results = []
    max_temp = 0
    max_vram = 0

    for i in range(num_images):
        print(f"\n--- Imagem {i+1}/{num_images} ---")
        t_start = time.time()
        image = pipe(
            prompt=prompt,
            num_inference_steps=steps,
            height=resolution,
            width=resolution,
            generator=torch.Generator("cpu").manual_seed(42 + i),
        ).images[0]
        t_elapsed = time.time() - t_start

        stats_post = get_gpu_stats()
        if stats_post:
            max_temp = max(max_temp, stats_post["temp_c"])
            max_vram = max(max_vram, stats_post["vram_used_mb"])

        result = {
            "image": i + 1,
            "resolution": f"{resolution}x{resolution}",
            "steps": steps,
            "time_seconds": round(t_elapsed, 2),
            "temp_c": stats_post["temp_c"] if stats_post else None,
            "vram_used_mb": stats_post["vram_used_mb"] if stats_post else None,
        }
        results.append(result)
        print(f"Tempo: {t_elapsed:.1f}s | Temp: {stats_post['temp_c'] if stats_post else '?'}C | VRAM: {stats_post['vram_used_mb'] if stats_post else '?'}MB")

        save_path = os.path.join(os.environ.get("LOCALAPPDATA", "/tmp"), "Temp", f"bench_sd15_{resolution}_{i+1}.png")
        image.save(save_path)
        print(f"Salvo: {save_path}")

    avg_time = sum(r["time_seconds"] for r in results) / len(results)
    summary = {
        "resolution": f"{resolution}x{resolution}",
        "steps": steps,
        "dtype": "fp16" if use_fp16 else "fp32",
        "strategy": "sequential_cpu_offload + attention_slicing",
        "load_time_s": round(t_load, 2),
        "warmup_time_s": round(t_warmup, 2),
        "avg_time_per_image_s": round(avg_time, 2),
        "max_temp_c": max_temp,
        "max_vram_used_mb": max_vram,
        "individual_results": results,
    }
    print(f"\n--- RESUMO {resolution}x{resolution} ---")
    print(f"Carga: {t_load:.1f}s | Warmup: {t_warmup:.1f}s")
    print(f"Tempo medio por imagem: {avg_time:.1f}s")
    print(f"Temp max: {max_temp}C | VRAM max: {max_vram:.0f}MB")
    return summary


if __name__ == "__main__":
    all_results = []

    # Test 1: 512x512 fp16, 20 steps
    try:
        r512 = benchmark_sd15(resolution=512, num_images=3, steps=20, use_fp16=True)
        if r512:
            all_results.append(r512)
    except Exception as e:
        print(f"ERRO no benchmark 512: {e}")
        import traceback; traceback.print_exc()

    # Test 2: 768x768 fp16, 20 steps (may fail on 4GB)
    try:
        r768 = benchmark_sd15(resolution=768, num_images=2, steps=20, use_fp16=True)
        if r768:
            all_results.append(r768)
    except Exception as e:
        print(f"ERRO no benchmark 768: {e}")
        import traceback; traceback.print_exc()

    # Save results
    out_dir = os.path.join(os.environ.get("LOCALAPPDATA", "/tmp"), "Temp")
    output_path = os.path.join(out_dir, "benchmark_b2_results.json")
    with open(output_path, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResultados salvos em {output_path}")
    print(json.dumps(all_results, indent=2))
