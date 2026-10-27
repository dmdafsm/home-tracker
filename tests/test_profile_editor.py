"""Editorul de profiluri: validare, scriere atomică cu istoric, copiere și paginile web."""

from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from helpers import listing

from hometracker.config import Settings
from hometracker.profile_editor import ProfileError, copy_text, find, parse, read_files, save
from hometracker.profiles import load_profiles
from hometracker.web import create_app

TEXT = """# Profil de test, cu comentarii care trebuie să rămână.
name: test
filters:
  price_eur: { min: 130000, max: 165000 }
  floors: [ground, raised_ground, 1]
  text_any: [gheorgheni]
preferences:
  balcony: 25
sources:
  storia:
    url: "https://storia.test"
"""


@pytest.fixture
def profiles(tmp_path):
    directory = tmp_path / "profiles"
    directory.mkdir()
    (directory / "test.yaml").write_text(TEXT, encoding="utf-8")
    return directory


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("name: [", "YAML invalid"),
        ("- doar o listă", "dicționar"),
        ("name: Cu Spatii", "litere mici"),
        ("name: x\nfilters: { price_eur: { min: nu } }", "filters.price_eur.min"),
        ("name: x\nsources: { ebay: { url: 'https://ebay.test' } }", "platformă necunoscută ebay"),
        ("name: x\nsources: { storia: { url: 'javascript:alert(1)' } }", "https://"),
    ],
)
def test_invalid_profiles_are_explained(text, message):
    with pytest.raises(ProfileError, match=message):
        parse(text)


def test_save_keeps_comments_history_and_the_name(profiles):
    original = find(profiles, "test")
    edited = TEXT.replace("max: 165000", "max: 170000").replace("\n", "\r\n")
    save(profiles, edited, original=original)
    written = (profiles / "test.yaml").read_text(encoding="utf-8")
    assert "# Profil de test" in written and "max: 170000" in written and "\r" not in written
    assert [path.read_text(encoding="utf-8") for path in (profiles / ".history").iterdir()] == [TEXT]
    assert load_profiles(profiles)[0].filters.price_eur.max == 170000
    assert not list(profiles.glob(".*.tmp"))
    with pytest.raises(ProfileError, match="numele nu se schimbă"):
        save(profiles, TEXT.replace("name: test", "name: alt-nume"), original=find(profiles, "test"))


def test_new_profile_from_an_existing_one(profiles):
    text = copy_text(find(profiles, "test"), "test-copie")
    assert text.startswith("# Profil de test") and "name: test-copie" in text
    save(profiles, text, original=None)
    assert sorted(item.name for item in read_files(profiles)) == ["test", "test-copie"]
    with pytest.raises(ProfileError, match="există deja"):
        save(profiles, text, original=None)


def test_invalid_file_is_listed_with_its_error(profiles):
    (profiles / "stricat.yaml").write_text("name: [", encoding="utf-8")
    files = {item.name: item for item in read_files(profiles)}
    assert files["stricat"].profile is None and "YAML invalid" in files["stricat"].error


@pytest.fixture
def web(setup, profiles, tmp_path):
    catalog, sessions, run = setup
    catalog["storia"] = [
        listing("storia", "s1", [1, 2, 3]),
        listing("storia", "s2", [7, 8, 9], area_m2=70, price_eur=168000),
    ]
    run()
    settings = Settings(database_url="sqlite://", data_dir=tmp_path, profiles_dir=profiles)
    return TestClient(create_app(settings, sessions), base_url="http://tracker.test")


SAME_ORIGIN = {"origin": "http://tracker.test", "content-type": "application/x-www-form-urlencoded"}


def post(web, url, text, action, headers=SAME_ORIGIN):
    return web.post(url, content=urlencode({"text": text, "action": action}), headers=headers, follow_redirects=False)


def test_profile_pages(web, profiles):
    page = web.get("/profiles")
    assert page.status_code == 200 and 'href="/profiles/test"' in page.text
    assert "# Profil de test" in web.get("/profiles/test").text
    assert "name: test-copie" in web.get("/profiles/new?source=test").text
    assert web.get("/profiles/lipsa").status_code == 404

    preview = post(web, "/profiles/test", TEXT.replace("max: 165000", "max: 140000"), "preview")
    assert preview.status_code == 200 and "0 apartamente trec" in preview.text and "față de 1" in preview.text
    assert "max: 165000" in (profiles / "test.yaml").read_text(encoding="utf-8")  # verificarea nu salvează

    bad = post(web, "/profiles/test", "name: [", "save")
    assert bad.status_code == 422 and "YAML invalid" in bad.text and "name: [" in bad.text

    saved = post(web, "/profiles/test", TEXT.replace("max: 165000", "max: 170000"), "save")
    assert saved.status_code == 303 and saved.headers["location"] == "/profiles?saved=test"
    assert "max: 170000" in (profiles / "test.yaml").read_text(encoding="utf-8")

    created = post(web, "/profiles/new", TEXT.replace("name: test", "name: mama"), "save")
    assert created.status_code == 303 and (profiles / "mama.yaml").exists()


def test_profile_forms_require_same_origin_and_a_form(web, profiles):
    assert (
        post(web, "/profiles/test", TEXT, "save", headers={"content-type": SAME_ORIGIN["content-type"]}).status_code
        == 403
    )
    evil = {**SAME_ORIGIN, "origin": "https://evil.test"}
    assert post(web, "/profiles/test", TEXT, "save", headers=evil).status_code == 403
    assert (
        post(web, "/profiles/test", TEXT, "save", headers={**SAME_ORIGIN, "content-type": "text/plain"}).status_code
        == 415
    )
    assert not (profiles / ".history").exists()
