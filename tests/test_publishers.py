"""Publicatorii: identificare, anunțuri preluate, diferențe de preț, agenți deghizați și paginile web."""

import pytest
import yaml
from fastapi.testclient import TestClient
from helpers import PROFILE, listing

from hometracker.config import Settings
from hometracker.models import Listing, Publisher
from hometracker.publishers import DISGUISED_AGENT_PROPERTIES, link_missing_publishers, publisher_stats
from hometracker.web import create_app

OWNER = {"publisher_type": "owner", "publisher_name": "Maria Exemplu", "phone": "0722 111 222"}
AGENCY = {"publisher_type": "agency", "publisher_name": "Agenția Exemplu"}


def stats_by_name(sessions):
    with sessions() as session:
        return {s.publisher.name: s for s in publisher_stats(session)}


def test_publishers_are_identified_by_phone_then_by_name(setup):
    catalog, _notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], **OWNER), listing("storia", "s2", [7, 8, 9], area_m2=70)]
    catalog["olx"] = [listing("olx", "o1", [1, 2, 3], **{**OWNER, "publisher_name": "Maria"})]
    run()
    with sessions() as session:
        rows = {row.external_id: row for row in session.query(Listing)}
        assert rows["s1"].publisher_id == rows["o1"].publisher_id  # același telefon, nume diferit
        assert rows["s2"].publisher_id is None  # fără telefon și fără nume
        publisher = session.get(Publisher, rows["s1"].publisher_id)
        assert publisher.key.startswith("phone:") and publisher.declared_type == "owner"


def test_agency_declaration_wins(setup):
    catalog, _notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], publisher_name="Ion", publisher_type="owner")]
    run()
    catalog["storia"] = [listing("storia", "s2", [7, 8, 9], area_m2=70, publisher_name="ion", publisher_type="agency")]
    run()
    with sessions() as session:
        assert session.query(Publisher).one().declared_type == "agency"


def test_taken_over_share_and_price_difference(setup):
    catalog, _notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], **OWNER)]
    run()
    catalog["olx"] = [listing("olx", "o1", [1, 2, 3], price_eur=155000, **AGENCY)]
    run()
    stats = stats_by_name(sessions)
    agency, owner = stats["Agenția Exemplu"], stats["Maria Exemplu"]
    assert [item.external_id for item in agency.taken_over] == ["o1"]
    assert agency.taken_over_share == 1.0 and owner.taken_over_share == 0.0
    assert agency.average_price_difference == 5000
    assert owner.average_price_difference == -5000


def test_owner_phone_on_many_apartments_looks_like_an_agent(setup):
    catalog, _notifier, sessions, run = setup
    # Suprafețe care se contrazic: apartamente diferite, același „proprietar”.
    areas = [40 + 10 * i for i in range(DISGUISED_AGENT_PROPERTIES)]
    catalog["storia"] = [listing("storia", f"s{i}", [], area_m2=area, **OWNER) for i, area in enumerate(areas)]
    run()
    owner = stats_by_name(sessions)["Maria Exemplu"]
    assert len(owner.properties) == DISGUISED_AGENT_PROPERTIES
    assert owner.disguised_agent


def test_existing_listings_are_linked_on_the_next_run(setup):
    catalog, _notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], **OWNER)]
    run()
    with sessions() as session:
        session.query(Listing).update({"publisher_id": None})
        session.query(Publisher).delete()
        session.commit()
        assert link_missing_publishers(session) == 1
        session.commit()
    catalog["storia"] = []
    run()
    with sessions() as session:
        assert session.query(Listing).one().publisher_id is not None


@pytest.fixture
def client(setup, tmp_path):
    catalog, _notifier, sessions, run = setup
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "test.yaml").write_text(yaml.safe_dump(PROFILE.model_dump(mode="json")), encoding="utf-8")
    settings = Settings(database_url="sqlite://", data_dir=tmp_path, profiles_dir=profiles)
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], **OWNER)]
    catalog["olx"] = [listing("olx", "o1", [1, 2, 3], price_eur=155000, **AGENCY)]
    run()
    return sessions, TestClient(create_app(settings, sessions))


def test_publishers_pages(client):
    sessions, web = client
    page = web.get("/publishers")
    assert page.status_code == 200
    assert "Agenția Exemplu" in page.text and "Maria Exemplu" in page.text
    assert "+5.000 €" in page.text
    with sessions() as session:
        agency = session.query(Publisher).filter_by(name="Agenția Exemplu").one()
        property_id = session.query(Listing).first().property_id
    detail = web.get(f"/publisher/{agency.id}")
    assert detail.status_code == 200 and "preluat" in detail.text
    assert f'href="/publisher/{agency.id}"' in web.get(f"/property/{property_id}").text
    assert web.get("/publisher/999").status_code == 404
