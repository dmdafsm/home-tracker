"""Feed-ul: filtrele și sortările din formular, caruselul și butoanele de pe card."""

import re

import pytest
import yaml
from fastapi.testclient import TestClient
from helpers import PROFILE, listing

from hometracker.config import Settings
from hometracker.feed import FeedFilters, feed
from hometracker.web import create_app


class Params(dict):
    def getlist(self, key):
        value = self.get(key, [])
        return value if isinstance(value, list) else [value]


def filters(**values) -> FeedFilters:
    params = Params(values)
    return FeedFilters.from_query(params, params.getlist)


@pytest.fixture
def apartments(setup):
    catalog, sessions, run = setup
    catalog["storia"] = [
        listing("storia", "a", [1, 2, 3], price_eur=140000, area_m2=40, rooms=1, publisher_type="owner"),
        listing("storia", "b", [4, 5, 6], price_eur=160000, area_m2=80, rooms=3, description="Balcon spre parc " * 3),
    ]
    catalog["olx"] = [listing("olx", "c", [7, 8, 9], price_eur=150000, area_m2=60, rooms=2, publisher_type="agency")]
    run()
    return sessions


def ids(sessions, chosen: FeedFilters) -> list[str]:
    with sessions() as session:
        return [item.listing.external_id for item in feed(session, PROFILE, chosen)]


def test_invalid_or_empty_values_mean_any():
    chosen = filters(price_min="", price_max="abc", rooms=["2", "9"], platform=["olx", "ebay"], sort="drop")
    assert (chosen.price_min, chosen.price_max, chosen.rooms, chosen.platforms, chosen.sort) == (
        None,
        None,
        ["2"],
        ["olx"],
        "score",
    )
    assert filters(price_min="150.000").price_min == 150000  # cu separatorul de mii
    assert filters().active_count == 0 and filters(q="x", rooms=["1"], saved="1").active_count == 3


@pytest.mark.parametrize(
    ("chosen", "expected"),
    [
        ({"price_min": "145000"}, {"b", "c"}),
        ({"price_max": "150000"}, {"a", "c"}),
        ({"area_min": "60"}, {"b", "c"}),
        ({"rooms": ["1", "3"]}, {"a", "b"}),
        ({"rooms": ["4"]}, set()),
        ({"platform": ["olx"]}, {"c"}),
        ({"publisher": "owner"}, {"a"}),
        ({"q": "BALCON"}, {"b"}),
    ],
)
def test_filters(apartments, chosen, expected):
    assert set(ids(apartments, filters(**chosen))) == expected


def test_sorts(apartments):
    assert ids(apartments, filters(sort="price_asc")) == ["a", "c", "b"]
    assert ids(apartments, filters(sort="price_desc")) == ["b", "c", "a"]
    assert ids(apartments, filters(sort="area_desc")) == ["b", "c", "a"]
    assert ids(apartments, filters(sort="price_m2")) == ["b", "c", "a"]  # 2.000, 2.500, 3.500 €/mp


def test_query_keeps_the_filters():
    chosen = filters(q="balcon", rooms=["2", "3"], saved="1", sort="newest")
    assert chosen.query("test") == "profile=test&q=balcon&rooms=2&rooms=3&saved=1&sort=newest"
    assert chosen.query("test", sort="score").endswith("saved=1")


@pytest.fixture
def client(apartments, tmp_path):
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "test.yaml").write_text(yaml.safe_dump(PROFILE.model_dump(mode="json")), encoding="utf-8")
    settings = Settings(database_url="sqlite://", data_dir=tmp_path, profiles_dir=profiles)
    return TestClient(create_app(settings, apartments), base_url="http://tracker.test")


def test_feed_page_form_carousel_and_card_actions(client):
    page = client.get("/?profile=test&rooms=2&rooms=3&sort=price_asc").text
    assert "2 apartamente" in page
    assert 'name="rooms" value="2" checked' in page and 'option value="price_asc" selected' in page
    assert '<details class="filters" open>' in page  # filtre active: panoul e deschis
    assert page.count('class="slide"') == 6  # câte 3 poze în caruselul fiecărui card
    assert "1 / 3" in page
    # Salvează și ascunde se întorc în feed cu aceleași filtre.
    actions = re.findall(r'action="(/property/\d+/label/(?:save|hide)\?next=[^"]+)"', page)
    assert len(actions) == 4 and all("next=/%3Fprofile%3Dtest%26rooms%3D2" in a for a in actions)
    assert 'target="_blank"' in page and "https://storia.test/b" in page
    response = client.post(
        actions[0].replace("&amp;", "&"), headers={"origin": "http://tracker.test"}, follow_redirects=False
    )
    assert re.fullmatch(r"/\?profile=test&rooms=2&rooms=3&sort=price_asc#p\d+", response.headers["location"])
    assert "Niciun apartament nu trece de filtrele alese" in client.get("/?price_max=1").text
