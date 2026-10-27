"""Reverificarea zilnică: prețuri schimbate și anunțuri dispărute, pentru anunțurile ieșite din căutare."""

from datetime import timedelta

import httpx
import pytest
from helpers import listing
from sqlalchemy import create_engine, text

from hometracker.adapters import ListingRemoved
from hometracker.http import BlockedError
from hometracker.models import Listing, ListingSnapshot, add_missing_columns, init_db, make_engine, utcnow


def age(sessions, days=2, **filters):
    """Mută în trecut ultima apariție în căutare (și ultima reverificare, dacă există)."""
    with sessions() as session:
        for row in session.query(Listing).filter_by(**filters):
            row.last_seen = utcnow() - timedelta(days=days)
            if row.checked_at is not None:
                row.checked_at = utcnow() - timedelta(days=days)
        session.commit()


def http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://storia.test/s1")
    return httpx.HTTPStatusError("eroare", request=request, response=httpx.Response(status, request=request))


def matched_and_left_search(catalog, sessions, run, **kwargs):
    """Un anunț potrivit, grupat, care apoi nu mai apare în primele pagini de căutare."""
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], **kwargs)]
    run()
    with sessions() as session:
        assert session.query(Listing).one().property_id is not None
    catalog["storia"] = []
    age(sessions)
    run.calls.clear()


def test_price_change_found_by_recheck(setup):
    catalog, sessions, run = setup
    matched_and_left_search(catalog, sessions, run)
    run.details["s1"] = listing("storia", "s1", [1, 2, 3], price_eur=140000)
    stats = run()
    assert run.calls == ["s1"]
    assert stats.rechecked == 1 and stats.removed == 0
    with sessions() as session:
        row = session.query(Listing).one()
        assert row.active and row.checked_at is not None and row.price_eur == 140000
        assert [s.price_eur for s in session.query(ListingSnapshot).order_by(ListingSnapshot.id)] == [150000, 140000]


def test_404_marks_listing_removed(setup):
    catalog, sessions, run = setup
    matched_and_left_search(catalog, sessions, run)
    run.details["s1"] = http_error(404)
    stats = run()
    assert stats.removed == 1
    with sessions() as session:
        row = session.query(Listing).one()
        assert not row.active and row.removed_at is not None


def test_adapter_removed_page_marks_listing_removed(setup):
    catalog, sessions, run = setup
    matched_and_left_search(catalog, sessions, run)
    run.details["s1"] = ListingRemoved("https://storia.test/s1")
    assert run().removed == 1


def test_server_error_keeps_listing_active_and_retries_next_day(setup):
    catalog, sessions, run = setup
    matched_and_left_search(catalog, sessions, run)
    run.details["s1"] = http_error(500)
    stats = run()
    assert stats.removed == 0 and stats.rechecked == 0
    run()  # tocmai a fost verificat: nu se reîncearcă la rularea următoare
    assert run.calls == ["s1"]
    age(sessions)
    run()
    assert run.calls == ["s1", "s1"]
    with sessions() as session:
        assert session.query(Listing).one().active


def test_listing_still_in_search_is_not_rechecked(setup):
    catalog, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    run()
    run.calls.clear()
    age(sessions)
    run()  # anunțul apare iar în căutare, deci last_seen e proaspăt
    assert run.calls == []


def test_recheck_once_per_day(setup):
    catalog, sessions, run = setup
    matched_and_left_search(catalog, sessions, run)
    run()
    run()
    assert run.calls == ["s1"]


def test_non_matching_listings_are_not_rechecked(setup):
    catalog, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], floor="4")]
    run()
    catalog["storia"] = []
    age(sessions)
    run.calls.clear()
    run()
    assert run.calls == []


def test_recheck_per_run_limit(setup):
    catalog, sessions, run = setup
    catalog["storia"] = [
        listing("storia", "s1", [1, 2, 3]),
        listing("storia", "s2", [4, 5, 6], price_eur=151000),
        listing("storia", "s3", [7, 8, 9], price_eur=152000),
    ]
    run()
    catalog["storia"] = []
    age(sessions)
    run.calls.clear()
    assert run(recheck_per_run=2).rechecked == 2
    assert run(recheck_per_run=2).rechecked == 1  # al treilea, la rularea următoare
    assert sorted(run.calls) == ["s1", "s2", "s3"]


def test_block_stops_recheck_on_that_platform(setup):
    catalog, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3]), listing("storia", "s2", [4, 5, 6], price_eur=151000)]
    run()
    catalog["storia"] = []
    age(sessions)
    run.calls.clear()
    run.details["s1"] = BlockedError("captcha")
    run.details["s2"] = BlockedError("captcha")
    stats = run()
    assert len(run.calls) == 1  # după prima blocare nu mai cerem nimic de la platformă
    assert stats.rechecked == 0
    with sessions() as session:
        assert all(row.checked_at is None for row in session.query(Listing))


def test_removed_listing_back_in_search_is_active_again(setup):
    catalog, sessions, run = setup
    matched_and_left_search(catalog, sessions, run)
    run.details["s1"] = http_error(410)
    run()
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    run()
    with sessions() as session:
        row = session.query(Listing).one()
        assert row.active and row.removed_at is None
        assert row.property_id is not None  # același apartament


def test_missing_columns_are_added_to_an_existing_database(tmp_path):
    url = f"sqlite:///{tmp_path / 'old.sqlite'}"
    sessions = init_db(make_engine(url))
    with sessions() as session:
        session.add(Listing(platform="storia", external_id="s1", url="https://storia.test/s1"))
        session.commit()
    with create_engine(url).begin() as connection:  # baza de date de dinainte de reverificare
        connection.execute(text("ALTER TABLE listing DROP COLUMN checked_at"))
        connection.execute(text("ALTER TABLE listing DROP COLUMN removed_at"))
    assert add_missing_columns(make_engine(url)) == ["listing.checked_at", "listing.removed_at"]
    assert add_missing_columns(make_engine(url)) == []  # a doua oară nu mai are ce adăuga
    sessions = init_db(make_engine(url))
    with sessions() as session:
        row = session.query(Listing).one()
        assert row.external_id == "s1" and row.checked_at is None


def test_new_required_column_is_refused(tmp_path):
    url = f"sqlite:///{tmp_path / 'old.sqlite'}"
    with create_engine(url).begin() as connection:
        connection.execute(text("CREATE TABLE listing (id INTEGER PRIMARY KEY)"))
    with pytest.raises(RuntimeError, match="trebuie să accepte NULL"):
        add_missing_columns(make_engine(url))
