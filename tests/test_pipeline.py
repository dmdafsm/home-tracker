"""Fluxul complet pe SQLite, cu un adapter și un notificator false (fără rețea)."""

from helpers import listing

from hometracker.models import Alert, Listing


def test_same_apartment_on_two_platforms_alerts_once(setup):
    catalog, notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    catalog["olx"] = [listing("olx", "o1", [2, 3, 4], price_eur=155000)]
    stats = run()
    assert stats.new == 2
    assert len(notifier.alerts) == 1
    assert notifier.alerts[0].title.startswith("Apartament nou · 150.000 €")
    with sessions() as session:
        props = {row.property_id for row in session.query(Listing).all()}
        assert len(props) == 1  # două poze comune → același apartament


def test_different_apartments_alert_separately(setup):
    catalog, notifier, _sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3]), listing("storia", "s2", [7, 8, 9])]
    run()
    assert len(notifier.alerts) == 2


def test_single_shared_photo_is_not_enough(setup):
    # Aceeași fațadă de bloc, apartamente diferite (etaj diferit).
    catalog, notifier, _sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], floor="1")]
    catalog["olx"] = [listing("olx", "o1", [1, 10, 11], floor="ground")]
    run()
    assert len(notifier.alerts) == 2


def test_rerun_does_not_repeat_alerts(setup):
    catalog, notifier, _sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3])]
    run()
    run()
    assert len(notifier.alerts) == 1


def test_price_drop_alerts(setup):
    catalog, notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=160000)]
    run()
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=150000)]
    run()
    assert len(notifier.alerts) == 2
    assert notifier.alerts[1].title.startswith("Preț scăzut cu 6%")
    # O scădere mică nu declanșează nimic.
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=149000)]
    run()
    assert len(notifier.alerts) == 2
    with sessions() as session:
        assert session.query(Alert).filter_by(kind="price_drop").count() == 1


def test_non_matching_listing_is_stored_without_alert(setup):
    catalog, notifier, sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2], floor="4")]
    run()
    assert notifier.alerts == []
    with sessions() as session:
        row = session.query(Listing).one()
        assert row.property_id is None and row.photos == []


def test_empty_source_sends_one_warning(setup):
    _catalog, notifier, _sessions, run = setup
    run()
    run()
    warnings = [m for m in notifier.sent if "problemă" in m.title]
    assert len(warnings) == 2  # câte una pe platformă, nu repetată la a doua rulare


def test_price_dropping_into_range_alerts_as_new(setup):
    catalog, notifier, _sessions, run = setup
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=175000)]
    run()
    assert notifier.alerts == []
    catalog["storia"] = [listing("storia", "s1", [1, 2, 3], price_eur=162000)]
    run()
    assert len(notifier.alerts) == 1
    assert notifier.alerts[0].title.startswith("Apartament nou · 162.000 €")


def test_alerts_go_to_the_profile_destinations(setup, tmp_path):
    from helpers import PROFILE, FakeClient, make_adapter

    from hometracker.config import Settings
    from hometracker.pipeline import Pipeline

    catalog, notifier, sessions, _run = setup
    catalog["storia"] = [listing("storia", "s1", [])]
    notify = PROFILE.notify.model_copy(update={"ntfy_topic": "mama", "email_to": "mama@example.test"})
    profile = PROFILE.model_copy(update={"notify": notify})
    adapters = {"storia": make_adapter("storia", catalog, {})}
    settings = Settings(data_dir=tmp_path, osm_lookups_per_run=0)
    Pipeline(settings, sessions, FakeClient({}), notifier, adapters=adapters).run([profile], 1)
    assert len(notifier.alerts) == 1
    assert notifier.destinations[-1] == {"ntfy_topic": "mama", "email_to": "mama@example.test"}
