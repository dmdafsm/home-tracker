"""Căile pozelor: id-urile venite din pagini nu pot scrie în afara directorului de poze."""

from pathlib import Path

import pytest
from helpers import listing

from hometracker.models import Photo
from hometracker.photos import safe_component, save_image

UNSAFE_IDS = ["../../escape", "..", "a/b", "/etc/passwd", "", "x" * 65, "id cu spații", "apartament-ă"]


def test_ordinary_ids_keep_their_path(tmp_path):
    for external_id in ("7918283", "IDxdUf", "abc_DEF-123"):
        path = save_image(tmp_path / "photos", "storia", external_id, 3, b"jpeg")
        assert path == tmp_path / "photos" / "storia" / external_id / "03.jpg"
        assert path.read_bytes() == b"jpeg"


@pytest.mark.parametrize("external_id", UNSAFE_IDS)
def test_unsafe_ids_stay_inside_the_photos_directory(tmp_path, external_id):
    root = tmp_path / "photos"
    path = save_image(root, "storia", external_id, 0, b"jpeg")
    assert path.resolve().is_relative_to(root.resolve())
    assert path.parent.name == safe_component(external_id)
    assert path.parent.name.startswith("h-")
    assert path.parent.parent == root / "storia"


def test_hashed_names_are_stable_and_distinct():
    assert safe_component("../a") == safe_component("../a")
    assert len({safe_component(value) for value in UNSAFE_IDS}) == len(UNSAFE_IDS)


def test_invalid_platform_is_refused(tmp_path):
    with pytest.raises(ValueError, match="platformă"):
        save_image(tmp_path, "../storia", "1", 0, b"jpeg")


def test_pipeline_keeps_photos_of_a_hostile_id_inside_data(setup, tmp_path: Path):
    catalog, sessions, run = setup
    catalog["storia"] = [listing("storia", "../../escape", [1, 2, 3])]
    run()
    with sessions() as session:
        paths = [Path(photo.path) for photo in session.query(Photo) if photo.path]
    assert len(paths) == 3
    photos_dir = (tmp_path / "photos").resolve()
    assert all(p.resolve().is_relative_to(photos_dir) for p in paths)
    assert not (tmp_path.parent / "escape").exists()
