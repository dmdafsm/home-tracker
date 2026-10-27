"""Interfața web: feed, pagina de apartament, pozele și ce nu trebuie să ajungă în pagină."""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from helpers import PROFILE, listing

from hometracker.config import Settings
from hometracker.models import Listing, Photo
from hometracker.web import create_app


@pytest.fixture
def web(setup, tmp_path):
    catalog, _notifier, sessions, run = setup
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "test.yaml").write_text(yaml.safe_dump(PROFILE.model_dump(mode="json")), encoding="utf-8")
    settings = Settings(database_url="sqlite://", data_dir=tmp_path, profiles_dir=profiles)
    client = TestClient(create_app(settings, sessions))
    return catalog, sessions, run, client


def test_feed_shows_matching_apartments_only(web):
    catalog, _sessions, run, client = web
    catalog["storia"] = [
        listing("storia", "s1", [1, 2, 3]),
        listing("storia", "s2", [4, 5, 6], floor="4"),  # nu trece de filtrul de etaj
    ]
    run()
    page = client.get("/")
    assert page.status_code == 200
    assert "1 apartament" in page.text and "1 apartamente" not in page.text
    assert "150.000 €" in page.text and "etaj 1" in page.text
    assert 'href="/property/' in page.text and 'src="/photo/' in page.text
    assert "alertat" in page.text


def test_removed_apartments_are_hidden_unless_asked(web):
    catalog, sessions, run, client = web
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    run()
    with sessions() as session:
        session.query(Listing).update({"active": False})
        session.commit()
    assert "0 apartamente" in client.get("/").text
    page = client.get("/?removed=1")
    assert "1 apartament" in page.text and "dispărut" in page.text


def test_property_page_shows_listings_prices_and_chart(web):
    catalog, sessions, run, client = web
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    catalog["olx"] = [listing("olx", "o1", [2, 3, 4], price_eur=155000)]
    run()
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=145000)]
    run()
    with sessions() as session:
        property_id = session.query(Listing).filter_by(external_id="s1").one().property_id
    page = client.get(f"/property/{property_id}")
    assert page.status_code == 200
    assert "2 anunțuri, 2 active, 0 republicări" in page.text
    assert "<svg" in page.text and "Istoricul prețului" in page.text
    assert "145.000 €" in page.text and "155.000 €" in page.text
    assert "legat cu anunțul" in page.text
    assert 'href="https://storia.test/s1"' in page.text
    assert client.get("/property/999").status_code == 404


def test_photos_are_served_from_the_photos_directory_only(web, tmp_path):
    catalog, sessions, run, client = web
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    run()
    with sessions() as session:
        photo = session.query(Photo).filter(Photo.path.is_not(None)).first()
        photo_id = photo.id
        # Calea salvată nu contează: o cale falsă sau de pe alt sistem nu schimbă fișierul servit.
        photo.path = str(tmp_path / "secret.txt")
        session.commit()
    (tmp_path / "secret.txt").write_text("nu")
    response = client.get(f"/photo/{photo_id}")
    assert response.status_code == 200 and response.headers["content-type"] == "image/jpeg"
    assert response.content[:2] == b"\xff\xd8"
    assert client.get("/photo/99999").status_code == 404


def test_missing_photo_file_is_404(web, tmp_path):
    catalog, sessions, run, client = web
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    run()
    with sessions() as session:
        photo = session.query(Photo).filter(Photo.path.is_not(None)).first()
        Path(photo.path).unlink()
        photo_id = photo.id
    assert client.get(f"/photo/{photo_id}").status_code == 404


def test_page_content_is_escaped_and_links_are_http_only(web):
    catalog, sessions, run, client = web
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], title="Gheorgheni <script>alert(1)</script>")]
    run()
    with sessions() as session:
        row = session.query(Listing).one()
        row.url = "javascript:alert(1)"
        session.commit()
        property_id = row.property_id
    for page in (client.get("/"), client.get(f"/property/{property_id}")):
        assert "<script>alert(1)</script>" not in page.text
        assert "&lt;script&gt;" in page.text
    assert "javascript:" not in client.get(f"/property/{property_id}").text


def test_healthz_and_no_api_docs(web):
    *_, client = web
    assert client.get("/healthz").json() == {"ok": True}
    assert client.get("/docs").status_code == 404
