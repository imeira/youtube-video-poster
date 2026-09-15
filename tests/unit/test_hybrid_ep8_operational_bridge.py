from decimal import Decimal
from pathlib import Path

from PIL import Image

from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
from src.hybrid.compiled import CompiledEpisode, FrameSpec, OperationalPipeline
from src.hybrid.execution import Executor
from src.hybrid.planner import Config


def frozen(path: Path, mode="TEST"):
    Image.new("RGB", (64, 64), "green").save(path)
    return FrozenAsset.approve(path, "qa", mode)


def test_operational_pipeline_keeps_imported_assets_out_of_dispatch_and_manifest(tmp_path):
    audio = frozen(tmp_path / "audio.png")
    source = frozen(tmp_path / "source.png")
    sheet = tmp_path / "sheet.png"
    contact_sheet((source,), sheet)
    manifest = Manifest.freeze((source,), FrozenAsset.approve(sheet, "qa", "TEST"), "qa", "TEST")
    episode = CompiledEpisode.compile(
        "EP8", audio,
        (FrameSpec("R001", 0, 1, "imported", "show"), FrameSpec("R032", 1, 2, "new", "show")),
    )
    imported = frozen(tmp_path / "imported.png")
    pipeline = OperationalPipeline(
        episode, Executor(tmp_path / "ledger.sqlite", Config(limit=Decimal("1"))), manifest,
        workspace=tmp_path / "compiled", endpoint="test", image_cost=Decimal(".1"),
        imported_assets={"R001": imported}, blocked_scenes={"R001"},
    )

    assert set(pipeline.run._baselines) == {"R032"}
    assert pipeline.imported_assets == {"R001": imported}
