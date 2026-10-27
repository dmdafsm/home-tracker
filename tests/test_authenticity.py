"""Scorul de autenticitate: fiecare semnal din plan, explicat."""

from datetime import timedelta

from helpers import listing

from hometracker.authenticity import MIN_COMPARABLES, Context, assess
from hometracker.history import property_history
from hometracker.models import Listing, utcnow
from hometracker.publishers import DISGUISED_AGENT_PROPERTIES

OWNER = {"publisher_type": "owner", "publisher_name": "Maria Exemplu", "phone": "0722 111 222"}
HERE = {"location_text": "Cluj-Napoca, Gheorgheni"}


def agency(n: int) -> dict:
    return {"publisher_type": "agency", "publisher_name": f"Agenția {n}", **HERE}


def assessment_of(sessions, external_id):
    with sessions() as session:
        row = session.query(Listing).filter_by(external_id=external_id).one()
        return assess(property_history(session, row.property_id), Context.build(session))


def codes(result) -> list[str]:
    return [signal.code for signal in result.signals]


def run_in_order(catalog, run, *items):
    for item in items:
        catalog[item.platform] = [item]
        run()
    catalog["storia"], catalog["olx"] = [], []


def test_single_owner_listing_has_no_signals(setup):
    catalog, sessions, run = setup
    run_in_order(catalog, run, listing("storia", "s1", [1, 2, 3], **OWNER, **HERE))
    result = assessment_of(sessions, "s1")
    assert result.score == 100 and result.signals == []
    assert result.notes  # prețul nu poate fi comparat cu nimic încă


def test_agencies_copying_the_owner(setup):
    catalog, sessions, run = setup
    run_in_order(
        catalog,
        run,
        listing("storia", "s1", [1, 2, 3], **OWNER, **HERE),
        listing("olx", "o1", [1, 2, 3], **agency(1)),
        listing("storia", "s2", [1, 2, 3], **agency(2)),
    )
    result = assessment_of(sessions, "s1")
    assert codes(result) == ["many_publishers", "agency_not_first"]
    assert result.score == 40
    detail = result.signals[1].detail
    assert detail == "Agenția 1, Agenția 2 au publicat după prima apariție (Storia, proprietar)"


def test_price_far_below_comparable_apartments(setup):
    catalog, sessions, run = setup
    # Fără poze comune, telefon sau coordonate, anunțurile rămân apartamente separate: 2.900 €/mp la parter.
    normal = [
        listing("storia", f"n{i}", [], floor="ground", area_m2=50, price_eur=145000, **HERE)
        for i in range(MIN_COMPARABLES)
    ]
    run_in_order(
        catalog, run, *normal, listing("olx", "bait", [], floor="ground", area_m2=82, price_eur=155000, **HERE)
    )
    result = assessment_of(sessions, "bait")
    assert codes(result) == ["below_median"]
    assert result.signals[0].detail == ("1.890 €/mp, cu 35% sub mediana de 2.900 €/mp a 5 apartamente comparabile")
    assert assessment_of(sessions, "n0").signals == []  # 2.900 €/mp față de aceeași mediană


def test_too_few_comparables_is_a_note_not_a_signal(setup):
    catalog, sessions, run = setup
    run_in_order(catalog, run, listing("storia", "s1", [], floor="ground", area_m2=82, price_eur=155000, **HERE))
    result = assessment_of(sessions, "s1")
    assert result.signals == []
    assert result.notes == ["prețul nu poate fi comparat încă: doar 0 apartamente asemănătoare"]


def test_owner_phone_on_many_apartments(setup):
    catalog, sessions, run = setup
    items = [
        listing("storia", f"s{i}", [], area_m2=40 + 10 * i, **OWNER, **HERE) for i in range(DISGUISED_AGENT_PROPERTIES)
    ]
    run_in_order(catalog, run, *items)
    assert "disguised_agent" in codes(assessment_of(sessions, "s0"))


def test_photos_from_a_listing_that_disappeared_long_ago(setup):
    catalog, sessions, run = setup
    run_in_order(catalog, run, listing("storia", "old", [1, 2, 3], **HERE))
    with sessions() as session:
        row = session.query(Listing).one()
        row.first_seen = utcnow() - timedelta(days=200)
        row.active, row.removed_at = False, utcnow() - timedelta(days=60)
        session.commit()
    run_in_order(catalog, run, listing("olx", "new", [1, 2, 3], **HERE))
    result = assessment_of(sessions, "new")
    assert "recycled_photos" in codes(result)
    assert "cu 60 zile înainte" in result.signals[codes(result).index("recycled_photos")].detail


def test_showcase_listing_republished_for_months_at_the_same_price(setup):
    catalog, sessions, run = setup
    run_in_order(
        catalog,
        run,
        listing("storia", "s1", [1, 2, 3], **agency(1)),
        listing("storia", "s2", [1, 2, 3], **agency(1)),
        listing("storia", "s3", [1, 2, 3], **agency(1)),
    )
    with sessions() as session:
        first = session.query(Listing).filter_by(external_id="s1").one()
        first.first_seen = utcnow() - timedelta(days=120)
        session.commit()
    assert "showcase" in codes(assessment_of(sessions, "s1"))


def test_many_photos_but_missing_details(setup):
    catalog, sessions, run = setup
    run_in_order(catalog, run, listing("storia", "s1", [1, 2, 3, 4, 5], floor=None))
    result = assessment_of(sessions, "s1")
    assert codes(result) == ["vague"]
    assert result.signals[0].detail == "multe poze, dar lipsește: adresă, etaj"
    assert result.score == 95


def test_same_publisher_different_prices_on_platforms(setup):
    catalog, sessions, run = setup
    run_in_order(
        catalog,
        run,
        listing("storia", "s1", [1, 2, 3], **agency(1)),
        listing("olx", "o1", [1, 2, 3], price_eur=158000, **agency(1)),
    )
    result = assessment_of(sessions, "s1")
    assert "inconsistent_prices" in codes(result)
