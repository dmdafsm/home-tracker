"""Anul construcției: dovezi din anunțuri, tipologie, blocul vecin și OpenStreetMap (răspunsuri sintetice)."""

import json

import pytest
from helpers import listing

from hometracker.buildyear import estimate, estimate_for, listing_evidence, same_block, typology_evidence
from hometracker.http import BlockedError
from hometracker.models import Listing, OsmBuilding
from hometracker.osm import cached, evidence, lookup, nearest_dated_building, parse_start_date, pending_locations


def row(i, title="Apartament", description="", extra=None, lat=None, lon=None, property_id=1) -> Listing:
    return Listing(
        id=i,
        platform="storia",
        title=title,
        description=description,
        extra=extra or {},
        latitude=lat,
        longitude=lon,
        property_id=property_id,
    )


# ------------------------------------------------------------ anunțuri


@pytest.mark.parametrize(
    ("description", "extra", "expected"),
    [
        ("", {"build_year": "1978"}, [(1978, 1978, "declared")]),
        ("An constructie: 1979, centrala proprie", {}, [(1979, 1979, "text")]),
        ("Bloc din 1975, bine întreținut", {}, [(1975, 1975, "text")]),
        ("Construit în 2008, finisaje noi", {}, [(2008, 2008, "text")]),
        ("Bloc din anii '70, renovat", {}, [(1970, 1979, "text")]),
        ("Bloc construit in 1978", {"build_year": "1978"}, [(1978, 1978, "declared")]),  # nu de două ori
        ("Sunați la 0722 1980 33", {"build_year": "anul trecut"}, []),
    ],
)
def test_listing_evidence(description, extra, expected):
    found = listing_evidence(row(1, description=description, extra=extra))
    assert [(item.low, item.high, item.source) for item in found] == expected


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("Apartament în ansamblu rezidențial, predare la cheie", (2005, 2030)),
        ("Bloc din panouri prefabricate, etaj 3", (1962, 1990)),
        ("Bloc de cărămidă, etaj 1 din 4", (1955, 1990)),
        ("Etajul 7 din 10, vedere spre parc", (1968, 1990)),
        ("Apartament luminos", None),
    ],
)
def test_typology(description, expected):
    found = typology_evidence([row(1, description=description)])
    assert ((found[0].low, found[0].high) if found else None) == expected


def test_two_agreeing_listings_give_medium_confidence():
    result = estimate([row(1, extra={"build_year": "1978"}), row(2, description="an constructie: 1979")])
    assert (result.interval, result.confidence) == ("1978–1979", "medie")
    assert result.summary == "1978–1979, din 2 anunțuri"


def test_one_listing_is_low_confidence_and_disagreement_is_shown():
    result = estimate([row(1, extra={"build_year": "1978"}), row(2, description="construit in 1995")])
    assert result.confidence == "mică"
    assert [item.low for item in result.evidence] == [1978]
    assert [item.low for item in result.conflicts] == [1995]


def test_typology_only_and_nothing_at_all():
    assert estimate([row(1, description="bloc din panouri")]).summary == "1962–1990, din tipologie"
    assert estimate([row(1, description="nimic despre bloc")]) is None


def test_openstreetmap_wins_and_flags_listings_that_disagree():
    osm = evidence_from(start_date="1976")
    result = estimate([row(1, extra={"build_year": "1977"}), row(2, description="construit in 2001")], osm=osm)
    assert (result.interval, result.confidence) == ("1976", "mare")
    assert [item.source for item in result.evidence] == ["osm", "declared"]
    assert [item.low for item in result.conflicts] == [2001]


def evidence_from(start_date):
    from hometracker.buildyear import Evidence

    low, high = parse_start_date(start_date)
    return Evidence(low, high, "osm", f"OpenStreetMap way/1: start_date={start_date}")


def test_same_block_uses_coordinates():
    own = row(1, lat=46.77000, lon=23.62000)
    near = row(2, lat=46.77020, lon=23.62010, property_id=2)  # ~23 m
    far = row(3, lat=46.77100, lon=23.62000, property_id=3)  # ~110 m
    unknown = row(4, property_id=4)
    assert same_block([own], [near, far, unknown]) == [near]


def test_estimate_for_reads_neighbours_and_the_osm_cache(setup):
    catalog, _notifier, sessions, run = setup
    here = {"latitude": 46.77000, "longitude": 23.62000}
    catalog["storia"] = [
        listing("storia", "s1", [1, 2, 3], **here),
        listing(
            "storia",
            "s2",
            [7, 8, 9],
            area_m2=70,
            latitude=46.77015,
            longitude=23.62005,
            description="Bloc din 1981, apartament renovat",
        ),
    ]
    run()
    with sessions() as session:
        own = session.query(Listing).filter_by(external_id="s1").one()
        result = estimate_for(session, [own])
        assert (result.interval, result.evidence[0].source) == ("1981", "text")  # din apartamentul vecin
        session.add(OsmBuilding(lat_key=46.77, lon_key=23.62, osm_id="way/9", start_date="1979", levels="5"))
        session.flush()
        result = estimate_for(session, [own])
        assert (result.interval, result.confidence) == ("1979", "mare")
        assert "5 niveluri" in result.evidence[0].detail


# --------------------------------------------------------- OpenStreetMap


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1978", (1978, 1978)),
        ("1978-05-01", (1978, 1978)),
        ("~1975", (1975, 1975)),
        ("1970s", (1970, 1979)),
        ("1975..1980", (1975, 1980)),
        ("C20", (1900, 1999)),
        ("necunoscut", None),
        (None, None),
    ],
)
def test_parse_start_date(value, expected):
    assert parse_start_date(value) == expected


OVERPASS = {
    "elements": [
        {"type": "way", "id": 1, "center": {"lat": 46.77001, "lon": 23.62001}, "tags": {"building": "apartments"}},
        {
            "type": "way",
            "id": 2,
            "center": {"lat": 46.77020, "lon": 23.62020},
            "tags": {"building": "apartments", "start_date": "1977", "building:levels": "5"},
        },
    ]
}


def test_nearest_dated_building_is_preferred():
    assert nearest_dated_building(OVERPASS, 46.77, 23.62)["id"] == 2
    assert nearest_dated_building({"elements": []}, 46.77, 23.62) is None


class FakeOverpass:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, **kwargs):
        self.urls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return type("Response", (), {"text": json.dumps(response)})()


def test_lookup_caches_results_including_nothing_found(setup):
    _catalog, _notifier, sessions, _run = setup
    client = FakeOverpass([OVERPASS, {"elements": []}])
    with sessions() as session:
        found = lookup(session, client, "https://overpass.test/api", 46.770001, 23.620004)
        assert (found.osm_id, found.start_date, found.levels) == ("way/2", "1977", "5")
        empty = lookup(session, client, "https://overpass.test/api", 46.8, 23.6)
        assert empty.osm_id is None
        assert cached(session, 46.77003, 23.62004) is found  # aceeași locație rotunjită
        url, kwargs = client.urls[0]
        assert "around%3A30%2C46.77%2C23.62" in url and "home-tracker" in kwargs["headers"]["User-Agent"]
        assert evidence(session, [row(1, lat=46.77, lon=23.62)]).low == 1977


def test_pending_locations_skip_cached_and_respect_the_limit(setup):
    catalog, _notifier, sessions, run = setup
    catalog["storia"] = [
        listing("storia", f"s{i}", [], area_m2=40 + 10 * i, latitude=46.77 + i / 100, longitude=23.62) for i in range(4)
    ]
    run()
    with sessions() as session:
        assert len(pending_locations(session, 10)) == 4
        assert len(pending_locations(session, 2)) == 2
        assert pending_locations(session, 0) == []
        session.add(OsmBuilding(lat_key=46.77, lon_key=23.62))
        session.flush()
        assert (46.77, 23.62) not in pending_locations(session, 10)


def test_pipeline_stops_osm_lookups_when_overpass_refuses(setup):
    from hometracker.config import Settings
    from hometracker.pipeline import Pipeline, RunStats

    catalog, notifier, sessions, run = setup
    catalog["storia"] = [
        listing("storia", f"s{i}", [], area_m2=40 + 10 * i, latitude=46.77 + i / 100, longitude=23.62) for i in range(3)
    ]
    run()
    client = FakeOverpass([OVERPASS, BlockedError("prea multe cereri")])
    pipeline = Pipeline(Settings(osm_lookups_per_run=5), sessions, client, notifier, adapters={})
    pipeline._osm_lookups(RunStats())
    assert len(client.urls) == 2  # a doua a fost refuzată, a treia nu s-a mai cerut
    with sessions() as session:
        assert session.query(OsmBuilding).count() == 1
