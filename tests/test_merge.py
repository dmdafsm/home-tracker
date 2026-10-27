"""„Posibil același apartament”: sugestii, unirea manuală, „nu e același” și paginile web."""

import pytest
import yaml
from fastapi.testclient import TestClient
from helpers import PROFILE, listing

from hometracker.config import Settings
from hometracker.labels import apply_label, split_listing
from hometracker.merge import mark_distinct, merge_properties, possible_duplicates
from hometracker.models import Alert, DistinctProperties, LabelEvent, Listing, Property, PropertyLabel
from hometracker.web import create_app

HERE = {"latitude": 46.76341, "longitude": 23.616245}


def agency(external_id, photos, name, **kwargs):
    # Fiecare agenție cu pozele și textul ei: deduplicarea automată nu are ce să unească.
    data = dict(rooms=3, publisher_type="agency", publisher_name=name, description=f"Text propriu {name} " * 5)
    data.update(kwargs)
    return listing("storia", external_id, photos, **data)


def properties(sessions):
    with sessions() as session:
        return {row.external_id: row.property_id for row in session.query(Listing)}


@pytest.fixture
def three_agencies(setup):
    catalog, notifier, sessions, run = setup
    catalog["storia"] = [
        agency("a", [1, 2, 3], "Agenția A", area_m2=61, price_eur=165000, **HERE),
        agency("b", [4, 5, 6], "Agenția B", area_m2=61, price_eur=164900, **HERE),
        agency("c", [7, 8, 9], "Agenția C", area_m2=65, price_eur=164900, latitude=46.7629, longitude=23.6138),
    ]
    run()
    return catalog, notifier, sessions, run


def test_compatible_apartments_are_suggested_not_merged(three_agencies):
    _catalog, notifier, sessions, _run = three_agencies
    ids = properties(sessions)
    assert len(set(ids.values())) == 3 and len(notifier.alerts) == 3
    with sessions() as session:
        found = possible_duplicates(session)
        assert [s.property_id for s in found[ids["a"]]] == [ids["b"], ids["c"]]  # b e la 0 m, c la ~70 m
        reasons = next(s.reasons for s in found[ids["a"]] if s.property_id == ids["c"])
        assert reasons[1:3] == ("3 camere", "61 / 65 mp") and reasons[-1].startswith("la ")
        assert possible_duplicates(session, [ids["b"]]).keys() >= {ids["b"]}


@pytest.mark.parametrize(
    "other",
    [
        {"floor": "2"},  # alt etaj
        {"rooms": 2},  # alte camere
        {"area_m2": 68},  # 7 mp diferență
        {"price_eur": 150000},  # peste 2%
        {"latitude": 46.7700, "longitude": 23.6300},  # ~1,3 km
        {"area_m2": None},  # suprafață necunoscută: nu e destul pentru o sugestie
    ],
)
def test_incompatible_apartments_are_not_suggested(setup, other):
    catalog, _notifier, sessions, run = setup
    catalog["storia"] = [
        agency("a", [1, 2, 3], "Agenția A", area_m2=61, price_eur=165000, **HERE),
        agency("b", [4, 5, 6], "Agenția B", **{"area_m2": 61, "price_eur": 164900, **HERE, **other}),
    ]
    run()
    with sessions() as session:
        assert possible_duplicates(session) == {}


def test_merge_moves_listings_alerts_and_labels(three_agencies):
    _catalog, notifier, sessions, run = three_agencies
    ids = properties(sessions)
    with sessions() as session:
        apply_label(session, ids["b"], "save")
        apply_label(session, ids["b"], "real")
        kept = merge_properties(session, ids["b"], ids["a"])
        session.commit()
    assert kept == min(ids["a"], ids["b"])
    with sessions() as session:
        assert session.get(Property, ids["b"]) is None
        moved = session.query(Listing).filter_by(external_id="b").one()
        assert (moved.property_id, moved.match_reason) == (kept, "manual")
        assert session.query(Alert).filter_by(property_id=kept, kind="new").count() == 2
        label = session.get(PropertyLabel, kept)
        assert (label.saved, label.verdict) == (True, "real")
        assert session.query(LabelEvent).filter_by(action="merge").count() == 1
        assert {s.property_id for s in possible_duplicates(session)[kept]} == {ids["c"]}
    sent = len(notifier.alerts)
    run()
    assert len(notifier.alerts) == sent  # unirea nu trimite din nou alerta „nou”


def test_not_the_same_stops_the_suggestion(three_agencies):
    _catalog, _notifier, sessions, _run = three_agencies
    ids = properties(sessions)
    with sessions() as session:
        mark_distinct(session, ids["c"], ids["a"])
        mark_distinct(session, ids["a"], ids["c"])  # a doua oară nu dublează
        session.commit()
        assert session.query(DistinctProperties).count() == 1
        assert {s.property_id for s in possible_duplicates(session)[ids["a"]]} == {ids["b"]}
        # Unirea lui a cu b păstrează decizia pentru c.
        kept = merge_properties(session, ids["a"], ids["b"])
        session.commit()
        assert ids["c"] not in {s.property_id for s in possible_duplicates(session).get(kept, [])}
        with pytest.raises(ValueError):
            merge_properties(session, kept, kept)
        with pytest.raises(LookupError):
            mark_distinct(session, kept, 999)


def test_split_is_remembered_as_not_the_same(three_agencies):
    _catalog, _notifier, sessions, _run = three_agencies
    ids = properties(sessions)
    with sessions() as session:
        kept = merge_properties(session, ids["a"], ids["b"])
        session.commit()
        moved = session.query(Listing).filter_by(external_id="b").one()
        new = split_listing(session, moved.id)
        session.commit()
        assert new not in {s.property_id for s in possible_duplicates(session).get(kept, [])}


@pytest.fixture
def web(three_agencies, tmp_path):
    _catalog, _notifier, sessions, _run = three_agencies
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "test.yaml").write_text(yaml.safe_dump(PROFILE.model_dump(mode="json")), encoding="utf-8")
    settings = Settings(database_url="sqlite://", data_dir=tmp_path, profiles_dir=profiles)
    return sessions, TestClient(create_app(settings, sessions), base_url="http://tracker.test")


def test_merge_pages(web):
    sessions, client = web
    ids = properties(sessions)
    assert "posibil același cu 2 apartamente" in client.get("/").text
    page = client.get(f"/property/{ids['a']}").text
    assert "Posibil același apartament" in page and f'action="/property/{ids["a"]}/merge/{ids["b"]}"' in page
    url = f"/property/{ids['b']}/merge/{ids['a']}"
    assert client.post(url).status_code == 403
    assert client.post(url, headers={"origin": "https://evil.test"}).status_code == 403
    same = {"origin": "http://tracker.test"}
    done = client.post(url, headers=same, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"] == f"/property/{min(ids['a'], ids['b'])}"
    assert client.post(url, headers=same).status_code == 404  # b nu mai există
    distinct = client.post(f"/property/{ids['a']}/distinct/{ids['c']}", headers=same, follow_redirects=False)
    assert distinct.status_code == 303
    assert "Posibil același apartament" not in client.get(f"/property/{ids['a']}").text
