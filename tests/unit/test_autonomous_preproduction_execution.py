"""Offline preproduction execution uses fixed production adapters only."""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from hashlib import sha256
from pathlib import Path

import pytest


def _source(path: Path, *, mode: str = "TEST") -> Path:
    source = {
        "schema": "local-biblical-source-v1",
        "mode": mode,
        "provenance": {
            "kind": "TEST_LOCAL_SOURCE" if mode == "TEST" else "LOCAL_SOURCE",
            "origin": "unit-test",
            "retrieved_at": "offline",
        },
        "research": {
            "story": "A promessa de um filho para Abraão e Sara",
            "references": [{"book": "Gênesis", "chapter": 15, "verses": "1-6"}],
            "narrative_classification": {
                "BIBLICAL_FACT": [
                    "Abrão confiou na promessa de Deus, mesmo sem ver todos os detalhes",
                    "Deus mudou o nome de Abrão para Abraão e o nome de Sarai para Sara",
                ]
            },
        },
    }
    path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    return path


def _audio(path: Path, duration: float) -> Path:
    """A labelled TEST fixture is a real decodable audio input, never narration."""
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}", "-ac", "2", str(path),
        ],
        check=True,
    )
    return path


def test_preproduction_executes_fixed_research_plan_script_qa_and_resumes_storyboard(tmp_path):
    from src.hybrid.preproduction import (
        advance_preproduction,
        load_preproduction,
        start_preproduction,
    )

    created = start_preproduction(tmp_path, b"Tema: promessa para criancas\n")
    source = _source(tmp_path / "source.json")

    first = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    assert first["status"] == "TTS_REQUIRED"
    revision = Path(first["active_revision_path"])
    request = json.loads((tmp_path / "PREPRODUCTION" / created["request_id"] / "request.json").read_text(encoding="utf-8"))
    assert request["execution"]["active_revision"] == f"revisions/{sha256(source.read_bytes()).hexdigest()[:16]}"
    assert (revision / "research" / "sources.json").is_file()
    assert (revision / "plan" / "editorial-plan.json").is_file()
    assert (revision / "script" / "script.json").is_file()
    assert (revision / "qa" / "script-qa.json").is_file()
    assert first["prices_usd"] is None
    assert first["paid_execution"] == "BLOCKED"

    script = json.loads((revision / "script" / "script.json").read_text(encoding="utf-8"))
    sentences = [segment["narration"] for segment in script["segments"]]
    timeline = tmp_path / "timeline.json"
    audio = _audio(tmp_path / "narration.TEST.wav", len(sentences) * 2.0)
    timeline.write_text(json.dumps({
        "schema": "local-timeline-evidence-v1", "mode": "TEST",
        "provenance": {"kind": "TEST_LOCAL_SOURCE", "origin": "unit-test", "retrieved_at": "offline"},
        "sentence_timestamps": [
            {"start": index * 2.0, "end": (index + 1) * 2.0, "duration": 2.0, "text": text}
            for index, text in enumerate(sentences)
        ],
        "audio_duration_s": len(sentences) * 2.0,
        "audio_sha256": sha256(audio.read_bytes()).hexdigest(),
        "script_sha256": sha256((revision / "script" / "script.json").read_bytes()).hexdigest(),
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
    }, ensure_ascii=False), encoding="utf-8")

    from tests.unit.test_autonomous_cli import run_cli

    code, completed = run_cli(
        "advance", "--workspace", str(tmp_path), "--request-id", created["request_id"],
        "--source-file", str(source), "--timeline-file", str(timeline), "--audio-file", str(audio),
        "--mode", "TEST",
    )
    assert code == 0, completed
    assert completed["status"] == "PREPRODUCTION_VALIDATED"
    assert (revision / "storyboard" / "scenes.json").is_file()
    assert (revision / "qa" / "storyboard.json").is_file()
    assert completed["stages"]["script"]["attempts"] == 1
    assert load_preproduction(tmp_path, created["request_id"])["status"] == "PREPRODUCTION_VALIDATED"

    script_path = revision / "script" / "script.json"
    original_script = script_path.read_bytes()
    script_path.write_bytes(b"{}")
    with pytest.raises(ValueError, match="script artifact was altered"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    script_path.write_bytes(original_script)

    changed_source = json.loads(source.read_text(encoding="utf-8"))
    changed_source["research"]["narrative_classification"]["BIBLICAL_FACT"].append(
        "Sara ouviu a promessa com surpresa.",
    )
    source.write_text(json.dumps(changed_source, ensure_ascii=False), encoding="utf-8")
    successor = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    assert successor["status"] == "TTS_REQUIRED"
    assert successor["active_revision_path"] != str(revision)
    assert (revision / "storyboard" / "scenes.json").is_file()


def test_preproduction_rejects_unlabelled_test_source_and_invalid_storyboard_evidence(tmp_path):
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    created = start_preproduction(tmp_path, b"Tema\n")
    source = _source(tmp_path / "source.json", mode="PREPRODUCTION")
    with pytest.raises(ValueError, match="TEST source"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")

    source = _source(tmp_path / "source-test.json")
    advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    audio = _audio(tmp_path / "narration.TEST.wav", 2)
    bad_timeline = tmp_path / "bad-timeline.json"
    bad_timeline.write_text(json.dumps({
        "schema": "local-timeline-evidence-v1", "mode": "TEST",
        "provenance": {"kind": "TEST_LOCAL_SOURCE", "origin": "unit-test", "retrieved_at": "offline"},
        "sentence_timestamps": [{"start": 0, "end": 2, "duration": 2, "text": "texto adulterado"}],
        "audio_duration_s": 2,
        "audio_sha256": sha256(audio.read_bytes()).hexdigest(),
        "script_sha256": sha256((tmp_path / "PREPRODUCTION" / created["request_id"] / "revisions" / sha256(source.read_bytes()).hexdigest()[:16] / "script" / "script.json").read_bytes()).hexdigest(),
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="timeline"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST", timeline_path=bad_timeline, audio_path=audio)


def test_preproduction_imports_hash_bound_audio_and_never_materializes_compiled_assets(tmp_path, monkeypatch):
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    # Defaults must ignore an ambient plan file: it is neither source evidence nor a transport double.
    ambient = tmp_path / "ambient-sol.json"
    ambient.write_text('{"scenes": [{"visual_prompt_en": "unbound"}]}', encoding="utf-8")
    monkeypatch.setenv("STUDIO_SOL_PLAN_PATH", str(ambient))
    created = start_preproduction(tmp_path, b"Tema\n")
    source = _source(tmp_path / "source.json")
    first = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    revision = Path(first["active_revision_path"])
    script_path = revision / "script" / "script.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    audio = _audio(tmp_path / "narration.TEST.wav", len(script["segments"]) * 2)
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps({
        "schema": "local-timeline-evidence-v1", "mode": "TEST",
        "provenance": {"kind": "TEST_LOCAL_SOURCE", "origin": "unit-test", "retrieved_at": "offline"},
        "sentence_timestamps": [{"start": i * 2, "end": (i + 1) * 2, "duration": 2, "text": segment["narration"]} for i, segment in enumerate(script["segments"])],
        "audio_duration_s": len(script["segments"]) * 2,
        "audio_sha256": sha256(audio.read_bytes()).hexdigest(),
        "script_sha256": sha256(script_path.read_bytes()).hexdigest(),
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
    }, ensure_ascii=False), encoding="utf-8")

    result = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST", timeline_path=timeline, audio_path=audio)

    assert result["status"] == "PREPRODUCTION_VALIDATED"
    assert result["compilation"]["status"] == "ASSETS_REQUIRED"
    assert (revision / "inputs" / "audio.wav").read_bytes() == audio.read_bytes()
    assert not (revision / "compiled.json").exists()
    assert not (revision / "manifest.json").exists()
    audio.write_bytes(b"altered")
    with pytest.raises(ValueError, match="audio"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST", timeline_path=timeline, audio_path=audio)


def test_preproduction_resume_preserves_validated_storyboard_without_requiring_tts_again(tmp_path):
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    created = start_preproduction(tmp_path, b"Tema\n")
    source = _source(tmp_path / "source.json")
    first = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    revision = Path(first["active_revision_path"])
    script = json.loads((revision / "script" / "script.json").read_text(encoding="utf-8"))
    audio = _audio(tmp_path / "narration.TEST.wav", len(script["segments"]) * 2)
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps({
        "schema": "local-timeline-evidence-v1", "mode": "TEST",
        "provenance": {"kind": "TEST_LOCAL_SOURCE", "origin": "unit-test", "retrieved_at": "offline"},
        "sentence_timestamps": [{"start": i * 2, "end": (i + 1) * 2, "duration": 2, "text": segment["narration"]} for i, segment in enumerate(script["segments"])],
        "audio_duration_s": len(script["segments"]) * 2,
        "audio_sha256": sha256(audio.read_bytes()).hexdigest(),
        "script_sha256": sha256((revision / "script" / "script.json").read_bytes()).hexdigest(),
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
    }, ensure_ascii=False), encoding="utf-8")
    completed = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST", timeline_path=timeline, audio_path=audio)

    resumed = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")

    assert completed["status"] == resumed["status"] == "PREPRODUCTION_VALIDATED"
    assert resumed["stages"]["storyboard"]["attempts"] == 1

    storyboard_qa = revision / "qa" / "storyboard.json"
    storyboard_qa.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="storyboard_qa artifact was altered"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")


def test_preproduction_load_and_advance_reject_reparse_inputs_before_any_read(tmp_path, monkeypatch):
    """Every preproduction read must reject lexical links before consuming bytes."""
    from src.hybrid.preproduction import (
        advance_preproduction,
        load_preproduction,
        start_preproduction,
    )

    created = start_preproduction(tmp_path, b"Boundary\n")
    source = _source(tmp_path / "source.json")
    root = tmp_path / "PREPRODUCTION" / created["request_id"]
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    prompt = root / "prompt.txt"
    prompt.unlink()
    prompt.symlink_to(outside)

    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text

    def forbid_outside_read(self, *args, **kwargs):
        if self in {prompt, outside}:
            raise AssertionError("forbidden linked input was read")
        return original_read_bytes(self, *args, **kwargs)

    def forbid_outside_text(self, *args, **kwargs):
        if self in {prompt, outside}:
            raise AssertionError("forbidden linked input was read")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", forbid_outside_read)
    monkeypatch.setattr(Path, "read_text", forbid_outside_text)
    with pytest.raises(ValueError, match="reparse"):
        load_preproduction(tmp_path, created["request_id"])
    with pytest.raises(ValueError, match="reparse"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")


def test_preproduction_advance_rejects_linked_source_before_read(tmp_path, monkeypatch):
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    created = start_preproduction(tmp_path, b"Boundary source\n")
    outside = tmp_path / "outside-source.json"
    _source(outside)
    source = tmp_path / "source.json"
    source.symlink_to(outside)
    original_read_bytes = Path.read_bytes

    def forbid_linked_source(self, *args, **kwargs):
        if self in {source, outside}:
            raise AssertionError("forbidden linked source was read")
        return original_read_bytes(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", forbid_linked_source)
    with pytest.raises(ValueError, match="reparse"):
        advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions are native reparse points")
def test_preproduction_load_rejects_native_windows_junction_before_read(tmp_path, monkeypatch):
    from src.hybrid.preproduction import load_preproduction, start_preproduction

    target = tmp_path / "target-workspace"
    created = start_preproduction(target, b"Native junction boundary\n")
    junction = tmp_path / "junction-workspace"
    created_junction = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
        check=False,
    )
    if created_junction.returncode:
        pytest.skip("native junction unavailable")
    request = junction / "PREPRODUCTION" / created["request_id"] / "request.json"
    original_read_text = Path.read_text

    def forbid_junction_read(self, *args, **kwargs):
        if self == request:
            raise AssertionError("forbidden junction input was read")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbid_junction_read)
    with pytest.raises(ValueError, match="reparse"):
        load_preproduction(junction, created["request_id"])


def test_preproduction_advance_serializes_same_request_and_preserves_one_progression(tmp_path, monkeypatch):
    from src.hybrid import preproduction
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    created = start_preproduction(tmp_path, b"Concurrent progression\n")
    source = _source(tmp_path / "source.json")
    original_run = preproduction.ScriptAgent.run
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    async def delayed_run(self, *args, **kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
        entered.set()
        assert release.wait(timeout=5)
        return await original_run(self, *args, **kwargs)

    monkeypatch.setattr(preproduction.ScriptAgent, "run", delayed_run)
    results: list[dict] = []
    errors: list[BaseException] = []

    def advance():
        try:
            results.append(advance_preproduction(tmp_path, created["request_id"], source, mode="TEST"))
        except Exception as error:  # noqa: BLE001 - propagate worker failures to the assertion
            errors.append(error)

    first = threading.Thread(target=advance)
    second = threading.Thread(target=advance)
    first.start()
    assert entered.wait(timeout=5)
    second.start()
    time.sleep(0.1)
    release.set()
    first.join(timeout=10)
    second.join(timeout=10)

    assert not errors
    assert len(results) == 2
    assert calls == 1
    assert {result["status"] for result in results} == {"TTS_REQUIRED"}
    assert {result["stages"]["script"]["attempts"] for result in results} == {1}
    request = json.loads((tmp_path / "PREPRODUCTION" / created["request_id"] / "request.json").read_text(encoding="utf-8"))
    assert request["package_sha256"] == sha256(json.dumps(
        {key: value for key, value in request.items() if key != "package_sha256"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def test_compile_preproduction_creates_hash_bound_test_packet_for_existing_compiled_start(tmp_path):
    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.preproduction import (
        advance_preproduction,
        compile_preproduction,
        start_preproduction,
    )

    created = start_preproduction(tmp_path, b"Tema\n")
    source = _source(tmp_path / "source.json")
    first = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    revision = Path(first["active_revision_path"])
    script = json.loads((revision / "script" / "script.json").read_text(encoding="utf-8"))
    audio = _audio(tmp_path / "narration.TEST.wav", len(script["segments"]) * 2)
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps({
        "schema": "local-timeline-evidence-v1", "mode": "TEST",
        "provenance": {"kind": "TEST_LOCAL_SOURCE", "origin": "unit-test", "retrieved_at": "offline"},
        "sentence_timestamps": [{"start": i * 2, "end": (i + 1) * 2, "duration": 2, "text": segment["narration"]} for i, segment in enumerate(script["segments"])],
        "audio_duration_s": len(script["segments"]) * 2,
        "audio_sha256": sha256(audio.read_bytes()).hexdigest(),
        "script_sha256": sha256((revision / "script" / "script.json").read_bytes()).hexdigest(),
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
    }, ensure_ascii=False), encoding="utf-8")
    advance_preproduction(tmp_path, created["request_id"], source, mode="TEST", timeline_path=timeline, audio_path=audio)

    assets = tmp_path / "assets"
    assets.mkdir()
    reference = assets / "reference.TEST.png"
    Image.new("RGB", (64, 64), "navy").save(reference)
    frozen = FrozenAsset.approve(reference, "TEST local fixture", "TEST")
    sheet = assets / "sheet.TEST.png"
    contact_sheet((frozen,), sheet)
    manifest = Manifest.freeze((frozen,), FrozenAsset.approve(sheet, "TEST local fixture", "TEST"), "TEST local fixture", "TEST")
    manifest_path = assets / "manifest.json"
    manifest.save(manifest_path)

    packet = compile_preproduction(tmp_path, created["request_id"], manifest_path)
    packet_root = Path(packet["packet_path"])
    assert packet["mode"] == "TEST"
    assert (packet_root / "compiled.json").is_file()
    assert (packet_root / "manifest.json").is_file()
    assert (packet_root / "policy.json").is_file()
    receipt = json.loads((packet_root / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["compilation_input_sha256"] == packet["compilation_input_sha256"]
    assert receipt["script_qa_sha256"] == sha256((revision / "qa" / "script-qa.json").read_bytes()).hexdigest()

    from tests.unit.test_autonomous_cli import run_cli

    code, compiled = run_cli("compile-preproduction", "--workspace", str(tmp_path), "--request-id", created["request_id"], "--manifest-file", str(manifest_path))
    assert code == 0 and compiled["receipt_sha256"] == packet["receipt_sha256"]
    code, started = run_cli("start", "--workspace", str(tmp_path / "compiled-run"), "--compiled-input", str(packet_root))
    assert code == 0 and started["state"] == "WAITING_THUMBNAIL_APPROVAL"
    code, thumb = run_cli("approve", "--workspace", str(tmp_path / "compiled-run"), "--request-id", started["request_id"], "--kind", "thumbnail", "--test-approval")
    assert code == 0 and thumb["state"] == "WAITING_VIDEO_APPROVAL"
    code, video = run_cli("approve", "--workspace", str(tmp_path / "compiled-run"), "--request-id", started["request_id"], "--kind", "video", "--test-approval")
    assert code == 0 and video["state"] == "READY_TO_PUBLISH"

    (revision / "inputs" / "source.json").write_bytes(b"mutated")
    with pytest.raises(ValueError, match="source"):
        compile_preproduction(tmp_path, created["request_id"], manifest_path)


def _validated_compile_request(tmp_path):
    """Create a real TEST revision and frozen local manifest for compiler boundaries."""
    from PIL import Image

    from src.hybrid.artifacts import FrozenAsset, Manifest, contact_sheet
    from src.hybrid.preproduction import advance_preproduction, start_preproduction

    created = start_preproduction(tmp_path, b"Boundary fixture\n")
    source = _source(tmp_path / "source.json")
    first = advance_preproduction(tmp_path, created["request_id"], source, mode="TEST")
    revision = Path(first["active_revision_path"])
    script = json.loads((revision / "script" / "script.json").read_text(encoding="utf-8"))
    audio = _audio(tmp_path / "narration.TEST.wav", len(script["segments"]) * 2)
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps({
        "schema": "local-timeline-evidence-v1", "mode": "TEST",
        "provenance": {"kind": "TEST_LOCAL_SOURCE", "origin": "unit-test", "retrieved_at": "offline"},
        "sentence_timestamps": [{"start": i * 2, "end": (i + 1) * 2, "duration": 2, "text": segment["narration"]} for i, segment in enumerate(script["segments"])],
        "audio_duration_s": len(script["segments"]) * 2,
        "audio_sha256": sha256(audio.read_bytes()).hexdigest(),
        "script_sha256": sha256((revision / "script" / "script.json").read_bytes()).hexdigest(),
        "source_sha256": sha256(source.read_bytes()).hexdigest(),
    }, ensure_ascii=False), encoding="utf-8")
    advance_preproduction(tmp_path, created["request_id"], source, mode="TEST", timeline_path=timeline, audio_path=audio)
    assets = tmp_path / "assets"
    assets.mkdir()
    reference = assets / "reference.TEST.png"
    Image.new("RGB", (64, 64), "navy").save(reference)
    frozen = FrozenAsset.approve(reference, "TEST local fixture", "TEST")
    sheet = assets / "sheet.TEST.png"
    contact_sheet((frozen,), sheet)
    manifest_path = assets / "manifest.json"
    Manifest.freeze((frozen,), FrozenAsset.approve(sheet, "TEST local fixture", "TEST"), "TEST local fixture", "TEST").save(manifest_path)
    return created, revision, manifest_path


@pytest.mark.parametrize("relative", ("prompt.txt", "inputs/audio.wav"))
def test_compile_preproduction_rejects_linked_pre_read_inputs(tmp_path, relative, monkeypatch):
    from src.hybrid.preproduction import compile_preproduction

    created, revision, manifest_path = _validated_compile_request(tmp_path)
    target = tmp_path / "outside-bytes"
    target.write_bytes(b"outside")
    path = tmp_path / "PREPRODUCTION" / created["request_id"] / relative if relative == "prompt.txt" else revision / relative
    path.unlink()
    path.symlink_to(target)
    original_read_bytes = Path.read_bytes

    def forbid_link_read(self):
        if self == path:
            raise AssertionError("forbidden linked input was read")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", forbid_link_read)
    with pytest.raises(ValueError, match="reparse"):
        compile_preproduction(tmp_path, created["request_id"], manifest_path)


def test_compile_preproduction_rejects_manifest_binding_before_manifest_load(tmp_path, monkeypatch):
    from src.hybrid.preproduction import compile_preproduction

    created, _revision, manifest_path = _validated_compile_request(tmp_path)
    binding = tmp_path / "assets" / "sheet.TEST.binding.json"
    target = tmp_path / "outside-binding.json"
    target.write_text("{}", encoding="utf-8")
    binding.unlink()
    binding.symlink_to(target)
    original_read_text = Path.read_text

    def forbid_binding_read(self, *args, **kwargs):
        if self == binding:
            raise AssertionError("forbidden linked binding was read")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbid_binding_read)
    with pytest.raises(ValueError, match="reparse"):
        compile_preproduction(tmp_path, created["request_id"], manifest_path)


def test_compile_preproduction_rejects_linked_existing_receipt_before_read(tmp_path, monkeypatch):
    from src.hybrid.preproduction import compile_preproduction

    created, revision, manifest_path = _validated_compile_request(tmp_path)
    compile_preproduction(tmp_path, created["request_id"], manifest_path)
    receipt = revision / "compiled-test" / "receipt.json"
    target = tmp_path / "outside-receipt.json"
    target.write_text("{}", encoding="utf-8")
    receipt.unlink()
    receipt.symlink_to(target)
    original_read_text = Path.read_text

    def forbid_receipt_read(self, *args, **kwargs):
        if self == receipt:
            raise AssertionError("forbidden linked receipt was read")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", forbid_receipt_read)
    with pytest.raises(ValueError, match="reparse"):
        compile_preproduction(tmp_path, created["request_id"], manifest_path)


def test_compile_preproduction_crash_stages_then_resumes_without_overwriting_final(tmp_path, monkeypatch):
    from src.hybrid import preproduction
    from src.hybrid.preproduction import compile_preproduction

    created, revision, manifest_path = _validated_compile_request(tmp_path)
    original_replace = preproduction.os.replace

    def crash_on_packet_publish(source, destination):
        if Path(destination).name == "compiled-test":
            raise RuntimeError("simulated crash before publish")
        return original_replace(source, destination)

    monkeypatch.setattr(preproduction.os, "replace", crash_on_packet_publish)
    with pytest.raises(RuntimeError, match="simulated crash"):
        compile_preproduction(tmp_path, created["request_id"], manifest_path)
    packet = revision / "compiled-test"
    assert not packet.exists()
    assert any(path.name.startswith(".compiled-test.staging-") for path in revision.iterdir())
    monkeypatch.setattr(preproduction.os, "replace", original_replace)
    resumed = compile_preproduction(tmp_path, created["request_id"], manifest_path)
    assert Path(resumed["packet_path"]) == packet
    receipt = (packet / "receipt.json").read_bytes()
    assert compile_preproduction(tmp_path, created["request_id"], manifest_path)["receipt_sha256"] == resumed["receipt_sha256"]
    assert (packet / "receipt.json").read_bytes() == receipt
