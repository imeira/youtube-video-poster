"""
BENCHMARK B3 — LCM LoRA: medir ganho de velocidade
Adiciona LCM LoRA ao SD 1.5 para permitir 4-8 steps em vez de 20.
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

def benchmark_lcm(resolution=512, steps_list=[4, 6, 8], num_images=2):
    from diffusers import StableDiffusionPipeline

    print(f"\n{'='*60}")
    print(f"Benchmark B3: SD 1.5 + LCM LoRA @ {resolution}x{resolution}")
    print(f"Steps to test: {steps_list}")
    print(f"{'='*60}")

    # Load base model
    print("\nCargando modelo SD 1.5...")
    pipe = StableDiffusionPipeline.from_pretrained(
        "stable-diffusion-v1-5/stable-diffusion-v1-5",
        torch_dtype=torch.float16,
        safety_checker=None,
        requires_safety_checker=False,
    )

    # Load LCM LoRA
    print("Cargando LCM LoRA...")
    from peft import LoraConfig
    from huggingface_hub import hf_hub_download
    
    # LCM LoRA from latent-consistency/lcm-lora-sdv1-5
    pipe.load_lora_weights("latent-consistency/lcm-lora-sdv1-5")
    
    # Apply sequential offload for 4GB
    pipe.enable_sequential_cpu_offload()
    pipe.enable_attention_slicing()

    # Use LCM scheduler
    from diffusers import LCMScheduler
    pipe.scheduler = LCMScheduler.from_config(pipe.scheduler.config)

    print("Modelo + LCM LoRA carregado")

    prompt = "a beautiful biblical illustration of David and Goliath, children's book art style, colorful, detailed"

    all_results = []

    for steps in steps_list:
        print(f"\n--- {steps} steps ---")
        times = []
        
        for i in range(num_images):
            t_start = time.time()
            image = pipe(
                prompt=prompt,
                num_inference_steps=steps,
                height=resolution,
                width=resolution,
                guidance_scale=1.0,  # LCM needs low guidance
                generator=torch.Generator("cpu").manual_seed(42 + i),
            ).images[0]
            t_elapsed = time.time() - t_start
            times.append(t_elapsed)
            
            stats = get_gpu_stats()
            print(f"  Imagem {i+1}: {t_elapsed:.1f}s | Temp: {stats['temp_c'] if stats else '?'}C | VRAM: {stats['vram_used_mb'] if stats else '?'}MB")
            
            save_path = os.path.join(os.environ.get("LOCALAPPDATA", "/tmp"), "Temp", 
                                    f"bench_lcm_{resolution}_{steps}steps_{i+1}.png")
            image.save(save_path)

        avg = sum(times) / len(times)
        result = {
            "resolution": f"{resolution}x{resolution}",
            "steps": steps,
            "avg_time_s": round(avg, 2),
            "individual_times_s": [round(t, 2) for t in times],
        }
        all_results.append(result)
        print(f"  Media: {avg:.1f}s por imagem ({steps} steps)")

    return all_results

if __name__ == "__main__":
    results = benchmark_lcm(resolution=512, steps_list=[4, 6, 8], num_images=2)
    
    out_path = os.path.join(os.environ.get("LOCALAPPDATA", "/tmp"), "Temp", "benchmark_b3_lcm_results.json")
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResultados: {json.dumps(results, indent=2)}")
