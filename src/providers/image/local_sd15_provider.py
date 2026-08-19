"""Local Stable Diffusion 1.5 Image Provider — LCM-accelerated generation.

§42: CONSISTENT IMAGE > VIDEO
B2: SD 1.5 fp16 all-GPU: 38.8s @ 512²/20steps, VRAM 2.87 GB
B3: LCM-LoRA 6 steps: 7.1s @ 512², VRAM 3.00 GB (5.5× speedup)
B4: IP-Adapter with VAE on CPU: 65s, consistency 6/10

§40: Build own visual identity — avoid depending on artist/studio names.
§39: Use reference images, IP-Adapter, ControlNet, LoRA — not pure text-to-image.

IMPORTANT: Must set PYTHONPATH="" to avoid Hermes venv contamination (B1).
"""

from __future__ import annotations

import asyncio
import os
import time
import logging
from typing import Any

from src.providers.base import ImageProvider, ImageResult

logger = logging.getLogger(__name__)

# Hardware constraints (B2-B4)
_VENV_PYTHON = r"C:\Users\meira\hermes-studio-venv\Scripts\python.exe"


class LocalSD15Provider(ImageProvider):
    """Local Stable Diffusion 1.5 with LCM-LoRA acceleration.

    Modes:
        - 'lcm': Fast mode (6 steps, 7.1s/image) — for most scenes
        - 'standard': Quality mode (20 steps, 38.8s/image) — for CRITICAL scenes
        - 'ip_adapter': Character consistency (VAE on CPU, 65s/image)

    VRAM: 2.87-3.34 GB of 4.00 GB available.
    """

    def __init__(self, mode: str = "lcm"):
        self.mode = mode
        self._pipe = None
        self._loaded_mode: str | None = None

    def estimate_cost(self, **params) -> float:
        """Local generation is free."""
        return 0.0

    def estimate_time(self, width: int, height: int, steps: int) -> float:
        """Estimate generation time based on benchmark data."""
        pixels = width * height
        base_512 = 512 * 512
        # B3: 6 steps @ 512² = 7.1s (1.07s/step)
        # B2: 20 steps @ 512² = 38.8s (1.82s/step)
        if steps <= 8:
            per_step = 1.07  # LCM mode
        else:
            per_step = 1.82  # Standard mode
        # Scale by pixel count (approximate)
        scale = pixels / base_512
        return per_step * steps * scale

    def _load_pipeline(self):
        """Load the SD pipeline if not already loaded."""
        if self._pipe is not None and self._loaded_mode == self.mode:
            return

        import torch
        from diffusers import StableDiffusionPipeline, LCMScheduler

        logger.info(f"Loading SD 1.5 (mode={self.mode})...")

        self._pipe = StableDiffusionPipeline.from_pretrained(
            "runwayml/stable-diffusion-v1-5",
            torch_dtype=torch.float16,
            safety_checker=None,
            requires_safety_checker=False,
            use_safetensors=True,
        ).to("cuda")

        if self.mode == "lcm":
            # B3: LCM LoRA for 5.5× speedup
            self._pipe.load_lora_weights("latent-consistency/lcm-lora-sdv1-5")
            self._pipe.scheduler = LCMScheduler.from_config(self._pipe.scheduler.config)
            logger.info("LCM LoRA loaded (6 steps, ~7.1s/image)")
        elif self.mode == "ip_adapter":
            # B4: IP-Adapter for character consistency
            # VAE must be on CPU to avoid OOM with IP-Adapter
            self._pipe.vae.to("cpu").to(torch.float32)
            # Monkey-patch VAE decode for dtype safety
            original_decode = self._pipe.vae.decode
            def patched_decode(z, return_dict=False, generator=None):
                z = z.to("cpu").to(torch.float32)
                return original_decode(z, return_dict=return_dict, generator=generator)
            self._pipe.vae.decode = patched_decode

            self._pipe.load_ip_adapter(
                "h94/IP-Adapter",
                subfolder="models",
                weight_name="ip-adapter_sd15.bin",
            )
            logger.info("IP-Adapter loaded (VAE on CPU, ~65s/image)")

        self._loaded_mode = self.mode
        vram = torch.cuda.memory_allocated() / 1024**3
        logger.info(f"VRAM after load: {vram:.2f} GB")

    async def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 512,
        height: int = 512,
        reference_images: list[str] | None = None,
        seed: int | None = None,
    ) -> ImageResult:
        """Generate an image from a prompt.

        Args:
            prompt: Positive prompt.
            negative_prompt: Negative prompt.
            width: Image width (512 or 768).
            height: Image height.
            reference_images: Reference images for IP-Adapter mode.
            seed: Reproducibility seed.

        Returns:
            ImageResult with image_path and metadata.
        """
        import torch
        from diffusers.utils import load_image

        self._load_pipeline()

        # Determine generation parameters based on mode
        if self.mode == "lcm":
            steps = 6
            guidance = 1.0
            cross_attn_kwargs = {}
            ip_adapter_image = None
        elif self.mode == "ip_adapter":
            steps = 20
            guidance = 7.5
            cross_attn_kwargs = {"scale": 0.5}
            ip_adapter_image = load_image(reference_images[0]) if reference_images else None
        else:  # standard
            steps = 20
            guidance = 7.5
            cross_attn_kwargs = {}
            ip_adapter_image = None

        if seed is not None:
            generator = torch.Generator(device="cuda").manual_seed(seed)
        else:
            generator = None

        t_start = time.time()

        try:
            result = self._pipe(
                prompt=prompt,
                negative_prompt=negative_prompt,
                num_inference_steps=steps,
                height=height,
                width=width,
                guidance_scale=guidance,
                generator=generator,
                cross_attention_kwargs=cross_attn_kwargs if cross_attn_kwargs else None,
                ip_adapter_image=ip_adapter_image if ip_adapter_image else None,
            )
            image = result.images[0]
            gen_time = time.time() - t_start

            # Save to temp path
            output_dir = os.environ.get("STUDIO_IMAGE_DIR", os.path.expanduser("~/AppData/Local/Temp/studio_images"))
            os.makedirs(output_dir, exist_ok=True)
            img_path = os.path.join(output_dir, f"gen_{int(time.time())}_{seed or 0}.png")
            image.save(img_path)

            vram_peak = torch.cuda.max_memory_allocated() / 1024**3

            return ImageResult(
                success=True,
                image_path=img_path,
                seed=seed or 0,
                generation_time=gen_time,
                cost=0.0,
                metadata={
                    "mode": self.mode,
                    "steps": steps,
                    "width": width,
                    "height": height,
                    "vram_peak_gb": round(vram_peak, 2),
                    "guidance_scale": guidance,
                },
            )
        except torch.cuda.OutOfMemoryError as e:
            logger.error(f"OOM generating image: {e}")
            return ImageResult(success=False, error=f"OOM: {e}")
        except Exception as e:
            logger.error(f"Image generation failed: {e}")
            return ImageResult(success=False, error=str(e))

    async def execute(self, **params) -> ImageResult:
        """Execute image generation."""
        return await self.generate(**params)
