from pathlib import Path

from src.character_bible import get_canonical_character


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_adam_is_loaded_from_approved_canonical_card():
    adam = get_canonical_character("Adão", repo_root=REPO_ROOT)

    assert adam["status"] == "approved"
    assert adam["identity_lock"]["hair"] == "castanho-escuro, ondulado, até os ombros"
    assert adam["reference_path"] == REPO_ROOT / "assets/characters/creation/adam/face_v1.png"
    assert adam["reference_path"].is_file()


def test_legacy_description_uses_adam_canonical_identity():
    from src.character_bible import get_character_description

    adam = get_character_description("Adão")

    assert "ondulado" in adam["visual_description"]
    assert "até os ombros" in adam["visual_description"]
    assert "short dark brown hair" not in adam["visual_description"]
    assert adam["reference_path"].name == "face_v1.png"
