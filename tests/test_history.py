"""Istoricul apartamentului: motivul legării, republicări, zile pe piață, prețuri și rezumatul zilnic."""

from datetime import UTC, datetime, timedelta

from helpers import PROFILE, listing

from hometracker.config import Settings
from hometracker.history import find_property_id, format_history, property_history
from hometracker.models import Listing
from hometracker.summary import build_summary, format_summary

NOW = datetime(2026, 10, 27, 18, 30, tzinfo=UTC)


def rows(sessions):
    with sessions() as session:
        return {row.external_id: row for row in session.query(Listing).all()}


def test_match_reason_is_recorded(setup):
    catalog, _notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    catalog["olx"] = [listing("olx", "o1", [2, 3, 4])]
    run()
    found = rows(sessions)
    assert (found["s1"].match_reason, found["s1"].shared_photos) == ("new", 0)
    assert (found["o1"].match_reason, found["o1"].shared_photos) == ("score", 2)
    assert found["o1"].match_signals == "photos:2,area,floor"
    assert found["o1"].match_score == 1.0 and found["o1"].matched_listing_id == found["s1"].id
    assert found["o1"].republished_from_id is None  # altă platformă


def test_same_publisher_reposting_under_a_new_id_is_a_republication(setup):
    catalog, notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], publisher_name="Agenția Exemplu")]
    run()
    catalog["storia"] = [listing("storia", "s2", [1, 2, 3], publisher_name="  agenția   exemplu ")]
    run()
    found = rows(sessions)
    assert found["s2"].property_id == found["s1"].property_id
    assert found["s2"].republished_from_id == found["s1"].id
    assert len(notifier.alerts) == 1  # republicarea nu e un apartament nou


def test_other_or_unknown_publishers_are_not_republications(setup):
    catalog, _notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], publisher_name="Proprietar Exemplu")]
    run()
    catalog["storia"] = [
        listing("storia", "s2", [1, 2, 3], publisher_name="Agenția Exemplu"),  # agenție care preia pozele
        listing("storia", "s3", [1, 2, 3]),  # publicator necunoscut
    ]
    run()
    found = rows(sessions)
    assert found["s2"].property_id == found["s3"].property_id == found["s1"].property_id
    assert found["s2"].republished_from_id is None
    assert found["s3"].republished_from_id is None


def test_history_timeline_prices_and_days_on_market(setup):
    catalog, _notifier, sessions, run = setup
    catalog["storia"] = [
        listing("storia", "s1", [1, 2, 3], publisher_name="Proprietar Exemplu", publisher_type="owner")
    ]
    run()
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=145000)]
    catalog["olx"] = [listing("olx", "o1", [2, 3, 4], price_eur=149000, publisher_type="agency")]
    run()
    with sessions() as session:  # prima apariție acum 12 zile
        first = session.query(Listing).filter_by(external_id="s1").one()
        first.first_seen = NOW - timedelta(days=12)
        session.commit()
        history = property_history(session, first.property_id)
        assert [item.external_id for item in history.listings] == ["s1", "o1"]
        assert history.first.external_id == "s1"
        assert history.days_on_market(NOW) == 12
        assert sorted((p.listing.external_id, p.price_eur) for p in history.prices) == [
            ("o1", 149000),
            ("s1", 145000),
            ("s1", 150000),
        ]
        assert history.price_range() == (145000, 149000)
        text = format_history(history, Settings(), NOW)
    assert "Pe piață de 12 zile, din 15.10.2026 (prima apariție: Storia, proprietar (Proprietar Exemplu))" in text
    assert "2 anunțuri, 2 active, 0 republicări" in text
    assert "Preț: 145.000 € – 149.000 €" in text
    assert "legat cu anunțul 1 (scor 1.00: 2 poze comune, aceeași suprafață, același etaj)" in text


def test_days_on_market_stop_when_every_listing_is_gone(setup):
    catalog, _notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    run()
    with sessions() as session:
        row = session.query(Listing).one()
        row.first_seen = NOW - timedelta(days=30)
        row.active, row.removed_at = False, NOW - timedelta(days=10)
        session.commit()
        assert property_history(session, row.property_id).days_on_market(NOW) == 20


def test_find_property_by_id_or_listing_url(setup):
    catalog, _notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    run()
    with sessions() as session:
        row = session.query(Listing).one()
        assert find_property_id(session, str(row.property_id)) == row.property_id
        assert find_property_id(session, row.url + "?utm=x") == row.property_id
        assert find_property_id(session, "https://storia.test/missing") is None


def test_daily_summary_lists_republications(setup):
    catalog, _notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], publisher_name="Agenția Exemplu")]
    run()
    catalog["storia"] = [listing("storia", "s2", [1, 2, 3], publisher_name="Agenția Exemplu")]
    run()
    with sessions() as session:
        now = datetime.now(UTC)
        summary = build_summary(session, PROFILE, now - timedelta(hours=1), now + timedelta(minutes=1), Settings())
        assert [item.external_id for item in summary.republished] == ["s2"]
        message = format_summary(summary, Settings())
    assert message.title.endswith(", 1 republicate")
    assert "Republicate sub un id nou (1):" in message.body
