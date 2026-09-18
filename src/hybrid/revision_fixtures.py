"""Offline fixtures for the public revision CLI; never production providers.

Synthetic audio and drawings exercise contracts, not artistic or speech quality.
No sockets, model clients, remote subprocesses or Telegram credentials are used.
"""
from __future__ import annotations

import asyncio
import math
import random
import struct
import wave
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from PIL import Image, ImageDraw

from src.hybrid.artifacts import FrozenAsset, Manifest, atomic_json, contact_sheet, digest, sha256
from src.hybrid.execution import ProviderResult


class TestDependencies:
    mode = "TEST"
    endpoint = "local-fixture/image-edit-v1"
    image_cost = Decimal(".001")

    def __init__(self, root, options=None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.options = options or {}
        self.active = self.maximum = 0
        self.images = self
        self.hero = self
        self.tts = self
        self.messenger = self

    async def author_script(self, plan, research):
        # Revision-specific narration, not a recycled EP8 script. Short TEST only.
        revision = plan["revision"]
        opening = ["Vamos olhar o céu com Abrão.", "Vamos acompanhar Abrão sob as estrelas."]
        texts = [opening[(revision - 1) % 2], "Abrão confiou na promessa de Deus.",
                 "Sara ouviu a promessa de um filho.",
                 f"Nesta história, podemos aprender a esperar com esperança e pensar em {revision} motivos para agradecer."]
        actions = ["Abram looks up at a star-filled sky beside his tent", "Abram rests a hand on his heart, looking hopeful",
                   "Sarah listens at the doorway of her tent; no infant", "Abraham and Sarah wait together outside their tent at sunset"]
        segments = [dict(id=f"S{i+1:03d}", narration=t, kind="biblical_paraphrase" if i < 3 else "family_reflection",
                         source_refs=["Gênesis 15:1-6" if i < 2 else "Gênesis 18:1-15"] if i < 3 else [],
                         visual_action=actions[i], characters=["abraham", "sarah"])
                    for i, t in enumerate(texts)]
        return dict(audience={"min_age": 6, "max_age": 10}, closing_duration_s=4,
                    segments=segments, narration="\n\n".join(texts), evidence_mode="TEST")

    async def synthesize(self, text, *, output_path, **kwargs):
        words, position = [], 0.0
        for word in text.split():
            length = .06 + len(word) * .004
            words.append(dict(word=word, start=position, end=position + length))
            position += length
        rate = 16000
        samples = round(position * rate)
        # Distinct revised text must produce distinct synthetic audio even when
        # its character count/duration happens to match a rejected predecessor.
        frequency = 220 + int(digest(text)[:8], 16) % 440
        with wave.open(str(output_path), "wb") as output:
            output.setparams((1, 2, rate, samples, "NONE", "not compressed"))
            output.writeframes(b"".join(struct.pack("<h", int(1500 * math.sin(i * 2 * math.pi * frequency / rate))) for i in range(samples)))
        return SimpleNamespace(success=True, audio_path=str(output_path), word_timestamps=words,
                               duration_seconds=samples / rate, metadata={"boundary_source": "TEST_WordBoundary"})

    def canonical(self, directory):
        assets = []
        for name, color in (("abraham", "sienna"), ("sarah", "teal")):
            path = directory / f"{name}.png"
            Image.new("RGB", (96, 96), color).save(path)
            assets.append(FrozenAsset.approve(path, "TEST canonical fixture", "TEST"))
        sheet = directory / "references.png"
        contact_sheet(assets, sheet)
        manifest = Manifest.freeze(assets, FrozenAsset.approve(sheet, "TEST", "TEST"), "TEST", "TEST")
        manifest.save(directory / "manifest.json")
        return dict(manifest=str(directory / "manifest.json"), characters={a: b.sha256 for a, b in zip(("abraham", "sarah"), assets)},
                    authority="TEST canonical fixture", episode="EP6")

    async def submit(self, job, request_id, checkpoint):
        self.active += 1
        self.maximum = max(self.maximum, self.active)
        try:
            path = self.root / f"{request_id}.png"
            checkpoint(provider_id=request_id, partial=path)
            await asyncio.sleep(0)  # let all bounded workers make progress
            self._draw(path, request_id)
            if self.options.get("crash_image_once") and not (self.root / "crashed").exists():
                (self.root / "crashed").touch()
                raise RuntimeError("injected image crash after durable provider checkpoint")
            atomic_json(self.root / "workers.json", dict(maximum=self.maximum))
            return ProviderResult(path, Decimal(0))
        finally:
            self.active -= 1

    def _draw(self, path, request_id):
        rng = random.Random(request_id)
        image = Image.new("RGB", (1280, 720), tuple(rng.randrange(20, 220) for _ in range(3)))
        draw = ImageDraw.Draw(image)
        for _ in range(25):
            x, y = rng.randrange(1190), rng.randrange(630)
            draw.ellipse((x, y, x + 80, y + 80), fill=tuple(rng.randrange(256) for _ in range(3)))
        draw.text((10, 10), "TEST ONLY - synthetic image", fill="white")
        image.save(path)

    async def recover(self, job, request_id, provider_id, partial, checkpoint):
        path = Path(partial)
        if not path.is_file():
            self._draw(path, request_id)
        return ProviderResult(path, Decimal(0))

    async def visual_qa(self, packet, scene, references):
        await asyncio.sleep(0)
        reject = self.options.get("reject_scene") == scene["scene_id"] and packet.get("wave", 0) == 0
        return dict(scene_id=scene["scene_id"], result_sha256=packet["result_sha256"],
                    approved=not reject, reviewer="TEST independent visual fixture")

    async def send_photo(self, chat_id, path, caption):
        return await self._send("thumbnail", path)

    async def send_video(self, chat_id, path, caption):
        return await self._send("video", path)

    async def _send(self, kind, path):
        target = self.root / f"telegram-{kind}.json"
        value = dict(kind=kind, sha256=sha256(path), message_id=1 if kind == "thumbnail" else 2, mode="TEST")
        atomic_json(target, value)
        return value["message_id"]

    async def recover_send(self, kind, path):
        import json
        target = self.root / f"telegram-{kind}.json"
        if not target.exists():
            raise RuntimeError("ambiguous fixture delivery")
        value = json.loads(target.read_text())
        if value["sha256"] != sha256(path):
            raise ValueError("fixture delivery changed")
        return value["message_id"]
