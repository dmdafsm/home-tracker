"""Scorul de autenticitate: fiecare semnal din plan, explicat, și pragul pentru alertele instant."""

from datetime import timedelta

from helpers import PROFILE, FakeClient, listing, make_adapter, picture

from hometracker.authenticity import MIN_COMPARABLES, Context, assess
from hometracker.config import Settings
from hometracker.history import property_history
from hometracker.models import Listing, init_db, make_engine, utcnow
from hometracker.pipeline import Pipeline
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
    catalog, _notifier, sessions, run = setup
    run_in_order(catalog, run, listing("storia", "s1", [1, 2, 3], **OWNER, **HERE))
    result = assessment_of(sessions, "s1")
    assert result.score == 100 and result.signals == []
    assert result.notes  # prețul nu poate fi comparat cu nimic încă


def test_agencies_copying_the_owner(setup):
    catalog, _notifier, sessions, run = setup
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
    assert detail == "Agenția 1, Agenția 2 a publicat după prima apariție (Storia, proprietar)"


def test_price_far_below_comparable_apartments(setup):
    catalog, _notifier, sessions, run = setup
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
    catalog, _notifier, sessions, run = setup
    run_in_order(catalog, run, listing("storia", "s1", [], floor="ground", area_m2=82, price_eur=155000, **HERE))
    result = assessment_of(sessions, "s1")
    assert result.signals == []
    assert result.notes == ["prețul nu poate fi comparat încă: doar 0 apartamente asemănătoare"]


def test_owner_phone_on_many_apartments(setup):
    catalog, _notifier, sessions, run = setup
    items = [
        listing("storia", f"s{i}", [], area_m2=40 + 10 * i, **OWNER, **HERE) for i in range(DISGUISED_AGENT_PROPERTIES)
    ]
    run_in_order(catalog, run, *items)
    assert "disguised_agent" in codes(assessment_of(sessions, "s0"))


def test_photos_from_a_listing_that_disappeared_long_ago(setup):
    catalog, _notifier, sessions, run = setup
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
    catalog, _notifier, sessions, run = setup
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
    catalog, _notifier, sessions, run = setup
    run_in_order(catalog, run, listing("storia", "s1", [1, 2, 3, 4, 5], floor=None))
    result = assessment_of(sessions, "s1")
    assert codes(result) == ["vague"]
    assert result.signals[0].detail == "multe poze, dar lipsește: adresă, etaj"
    assert result.score == 95


def test_same_publisher_different_prices_on_platforms(setup):
    catalog, _notifier, sessions, run = setup
    run_in_order(
        catalog,
        run,
        listing("storia", "s1", [1, 2, 3], **agency(1)),
        listing("olx", "o1", [1, 2, 3], price_eur=158000, **agency(1)),
    )
    result = assessment_of(sessions, "s1")
    assert "inconsistent_prices" in codes(result)


def test_low_authenticity_blocks_the_instant_alert(setup, tmp_path):
    catalog, notifier, _sessions, run = setup
    strict = PROFILE.model_copy(update={"notify": PROFILE.notify.model_copy(update={"min_authenticity": 100})})
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3, 4, 5], floor=None)]  # scor 95
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'strict.sqlite'}", data_dir=tmp_path)
    sessions = init_db(make_engine(settings.database_url))
    images = {f"https://img.test/{i}.jpg": picture(i) for i in range(1, 20)}
    adapters = {name: make_adapter(name, catalog, {}) for name in catalog}
    Pipeline(settings, sessions, FakeClient(images), notifier, adapters=adapters).run([strict], 1)
    assert notifier.alerts == []
    run()  # profilul obișnuit (min_authenticity 0) trimite alerta, cu scorul în mesaj
    assert len(notifier.alerts) == 1
    assert "Autenticitate 95: atragere de clienți" in notifier.alerts[0].body
