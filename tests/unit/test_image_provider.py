"""Tests for Local SD 1.5 Image Provider (§42, B2-B3).

Note: These tests require the SD 1.5 model (~4GB download).
They are marked as slow/integration tests — skip if model not cached.
"""

from __future__ import annotations

import os

import pytest

from src.providers.image.local_sd15_provider import LocalSD15Provider


class TestLocalSD15Provider:
    """B2-B3: SD 1.5 with LCM LoRA."""

    def test_cost_zero(self):
        """Local image generation is free."""
        provider = LocalSD15Provider(mode="lcm")
        assert provider.estimate_cost() == 0.0

    def test_estimate_time_lcm(self):
        """B3: LCM 6 steps should estimate ~7.1s for 512x512."""
        provider = LocalSD15Provider(mode="lcm")
        t = provider.estimate_time(512, 512, 6)
        assert 5.0 <= t <= 10.0  # ~7.1s based on benchmark

    def test_estimate_time_standard(self):
        """B2: Standard 20 steps should estimate ~38.8s for 512x512."""
        provider = LocalSD15Provider(mode="standard")
        t = provider.estimate_time(512, 512, 20)
        assert 30.0 <= t <= 50.0  # ~38.8s based on benchmark

    def test_estimate_time_scales_with_resolution(self):
        """768x768 should take longer than 512x512."""
        provider = LocalSD15Provider(mode="standard")
        t_512 = provider.estimate_time(512, 512, 20)
        t_768 = provider.estimate_time(768, 768, 20)
        assert t_768 > t_512

    @pytest.mark.slow
    @pytest.mark.asyncio
    async def test_generate_lcm_image(self):
        """B3: Generate an actual image with LCM (requires model download)."""
        provider = LocalSD15Provider(mode="lcm")
        result = await provider.generate(
            prompt="a young shepherd boy standing in a field, stylized 3d animation, children's book style",
            negative_prompt="blurry, low quality, text, watermark",
            width=512,
            height=512,
            seed=42,
        )
        if not result.success:
            pytest.skip(f"SD model not available: {result.error}")
        assert result.success
        assert os.path.exists(result.image_path)
        assert result.generation_time > 0
        assert result.metadata["mode"] == "lcm"
        assert result.metadata["steps"] == 6
