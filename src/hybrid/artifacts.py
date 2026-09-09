"""Individually approved assets and contact sheets bound to exact file hashes."""

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), default=str, allow_nan=False
        ).encode()
    ).hexdigest()


def sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, default=str, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


@dataclass(frozen=True)
class FrozenAsset:
    path: Path
    sha256: str
    reviewer: str
    mode: str

    @classmethod
    def approve(cls, path, reviewer, mode):
        if not reviewer.strip() or mode not in {"TEST", "LIVE"}:
            raise ValueError("explicit reviewer and mode required")
        path = Path(path).resolve()
        return cls(path, sha256(path), reviewer, mode)

    def verify(self, mode):
        if self.mode != mode or mode not in {"TEST", "LIVE"}:
            raise ValueError("asset mode mismatch")
        if not self.reviewer or sha256(self.path) != self.sha256:
            raise ValueError("approved asset hash mismatch")


def contact_sheet(assets, output):
    from PIL import Image, ImageDraw

    if not assets:
        raise ValueError("contact sheet requires assets")
    output = Path(output).resolve()
    if output in {asset.path for asset in assets}:
        raise ValueError("cannot overwrite references")
    canvas = Image.new(
        "RGB", (256 * min(4, len(assets)), 176 * ((len(assets) + 3) // 4)), "white"
    )
    draw = ImageDraw.Draw(canvas)
    for index, asset in enumerate(assets):
        asset.verify(assets[0].mode)
        with Image.open(asset.path) as original:
            thumb = original.convert("RGB")
            thumb.thumbnail((256, 144))
            x, y = index % 4 * 256, index // 4 * 176
            canvas.paste(thumb, (x, y))
            draw.text(
                (x + 4, y + 148), f"{index + 1}: {asset.sha256[:16]}", fill="black"
            )
    canvas.save(output)
    atomic_json(
        output.with_suffix(".binding.json"),
        {
            "assets": [a.sha256 for a in assets],
            "sheet": sha256(output),
        },
    )


@dataclass(frozen=True)
class Manifest:
    assets: tuple[FrozenAsset, ...]
    sheet: FrozenAsset
    reviewer: str
    mode: str

    @classmethod
    def freeze(cls, assets, sheet, reviewer, mode):
        result = cls(tuple(assets), sheet, reviewer, mode)
        result.verify(mode)
        return result

    @property
    def checksum(self):
        return digest(asdict(self))

    def save(self, path):
        self.verify(self.mode)
        atomic_json(path, {"manifest": asdict(self), "checksum": self.checksum})

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        raw = data["manifest"]

        def asset(value):
            return FrozenAsset(**{**value, "path": Path(value["path"])})

        result = cls(
            tuple(asset(a) for a in raw["assets"]),
            asset(raw["sheet"]),
            raw["reviewer"],
            raw["mode"],
        )
        if result.checksum != data["checksum"]:
            raise ValueError("manifest checksum mismatch")
        result.verify(result.mode)
        return result

    def verify(self, mode):
        if mode != self.mode or not self.reviewer or not self.assets:
            raise ValueError("manifest mode/reviewer/assets required")
        for asset in (*self.assets, self.sheet):
            asset.verify(mode)
        binding = json.loads(
            self.sheet.path.with_suffix(".binding.json").read_text(encoding="utf-8")
        )
        if binding != {
            "assets": [a.sha256 for a in self.assets],
            "sheet": self.sheet.sha256,
        }:
            raise ValueError("contact sheet hash binding mismatch")
