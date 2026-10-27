"""Etichetele: salvat, ascuns, real/fals, „nu e același apartament” și butoanele din interfață."""

import pytest
import yaml
from fastapi.testclient import TestClient
from helpers import PROFILE, listing

from hometracker.config import Settings
from hometracker.labels import apply_label, label_for, split_listing
from hometracker.models import LabelEvent, Listing
from hometracker.web import create_app


def property_of(sessions, external_id) -> int:
    with sessions() as session:
        return session.query(Listing).filter_by(external_id=external_id).one().property_id


def test_labels_change_state_and_are_logged(setup):
    catalog, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    run()
    property_id = property_of(sessions, "s1")
    with sessions() as session:
        for action in ("save", "hide", "real", "fake"):
            apply_label(session, property_id, action)
        label = label_for(session, property_id)
        assert (label.saved, label.hidden, label.verdict) == (True, True, "fake")
        apply_label(session, property_id, "clear_verdict")
        apply_label(session, property_id, "unhide")
        assert (label.hidden, label.verdict) == (False, None)
        assert [e.action for e in session.query(LabelEvent).order_by(LabelEvent.id)] == [
            "save",
            "hide",
            "real",
            "fake",
            "clear_verdict",
            "unhide",
        ]
        with pytest.raises(ValueError):
            apply_label(session, property_id, "delete")
        with pytest.raises(LookupError):
            apply_label(session, 999, "save")


def test_not_the_same_apartment_splits_the_listing(setup):
    catalog, sessions, run = setup
    agency = {"publisher_name": "Agenția Exemplu", "publisher_type": "agency"}
    for item in (listing("storia", "s1", [1, 2, 3], **agency), listing("storia", "s2", [1, 2, 3], **agency)):
        catalog["storia"] = [item]
        run()
    with sessions() as session:
        rows = {row.external_id: row for row in session.query(Listing)}
        assert rows["s2"].republished_from_id == rows["s1"].id
        old = rows["s1"].property_id
        new = split_listing(session, rows["s1"].id)
        session.commit()
        rows = {row.external_id: row for row in session.query(Listing)}
        assert rows["s1"].property_id == new != old and rows["s2"].property_id == old
        assert rows["s1"].match_reason == "manual"
        assert rows["s2"].republished_from_id is None
        event = session.query(LabelEvent).one()
        assert (event.action, event.property_id, event.listing_id) == ("not_same", old, rows["s1"].id)
        with pytest.raises(ValueError):
            split_listing(session, rows["s1"].id)  # acum e singur


@pytest.fixture
def web(setup, tmp_path):
    catalog, sessions, run = setup
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "test.yaml").write_text(yaml.safe_dump(PROFILE.model_dump(mode="json")), encoding="utf-8")
    settings = Settings(database_url="sqlite://", data_dir=tmp_path, profiles_dir=profiles)
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    catalog["olx"] = [listing("olx", "o1", [1, 2, 3])]
    run()
    return sessions, TestClient(create_app(settings, sessions), base_url="http://tracker.test")


SAME_ORIGIN = {"origin": "http://tracker.test"}


def test_label_buttons_require_same_origin(web):
    sessions, client = web
    property_id = property_of(sessions, "s1")
    url = f"/property/{property_id}/label/save"
    assert client.post(url).status_code == 403  # fără Origin/Referer
    assert client.post(url, headers={"origin": "https://evil.test"}).status_code == 403
    assert client.post(url, headers={"origin": "null"}).status_code == 403  # fără Referer, „null” nu ajunge
    # Ce trimite un browser cu politica same-origin a paginilor: Origin și Referer de pe același site.
    browser = {"origin": "http://tracker.test", "referer": f"http://tracker.test/property/{property_id}"}
    assert client.post(url, headers=browser, follow_redirects=False).status_code == 303
    response = client.post(url, headers=SAME_ORIGIN, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == f"/property/{property_id}"
    assert client.post(f"/property/{property_id}/label/delete", headers=SAME_ORIGIN).status_code == 400
    assert client.post("/property/999/label/save", headers=SAME_ORIGIN).status_code == 404
    page = client.get(f"/property/{property_id}")
    assert "salvat ✓" in page.text and f'action="/property/{property_id}/label/unsave"' in page.text


def test_feed_filters_saved_and_hidden(web):
    sessions, client = web
    property_id = property_of(sessions, "s1")
    assert "1 apartament" in client.get("/").text
    assert "0 apartamente" in client.get("/?saved=1").text
    client.post(f"/property/{property_id}/label/save", headers=SAME_ORIGIN)
    assert "1 apartament" in client.get("/?saved=1").text
    client.post(f"/property/{property_id}/label/hide", headers=SAME_ORIGIN)
    assert "0 apartamente" in client.get("/").text
    assert "ascuns" in client.get("/?hidden=1").text


def test_split_button_moves_the_listing(web):
    sessions, client = web
    with sessions() as session:
        listing_id = session.query(Listing).filter_by(external_id="o1").one().id
    assert client.post(f"/listing/{listing_id}/split").status_code == 403
    response = client.post(f"/listing/{listing_id}/split", headers=SAME_ORIGIN, follow_redirects=False)
    assert response.status_code == 303
    assert property_of(sessions, "o1") != property_of(sessions, "s1")
    assert client.post(f"/listing/{listing_id}/split", headers=SAME_ORIGIN).status_code == 400
